"""Claude-powered update analyzer.

For each unprocessed update row:
  1. Build a thesis-aware prompt
  2. Call Claude Sonnet 4.5
  3. Parse JSON, store in `analyses`, mark update processed
  4. If urgency=high, hand off to alerts

Also exposes `analyze_text(ticker, text)` for the on-demand dashboard
panel and `explain_move(ticker)` for the "why is X moving today?" button.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from dotenv import load_dotenv

from .config import UNIVERSE
from .db import (
    feed_rows,
    insert_analysis,
    mark_processed,
    recent_analyses,
    recent_updates,
    unprocessed_updates,
)
from .theses import thesis_for

load_dotenv()
log = logging.getLogger("analyzer")

MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")


def _client():
    from anthropic import Anthropic
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set. Add it to .env")
    return Anthropic(api_key=api_key)


# ---------------------------- prompt builders ---------------------------

def _thesis_block(ticker: str | None) -> str:
    if not ticker:
        return (
            "Company: (not in core universe — sector signal only)\n"
            "Thesis: n/a\nWatch: n/a\nBreaks: n/a"
        )
    th = thesis_for(ticker)
    meta = UNIVERSE.get(ticker, {})
    if not th:
        return (
            f"Company: {meta.get('name', ticker)} ({ticker})\n"
            f"Sector: {meta.get('sector','')}\n"
            "Thesis: Watchlist — no held position yet.\n"
            "Watch: same as sector.\nBreaks: n/a."
        )
    return (
        f"Company: {th['name']} ({ticker})\n"
        f"Sector: {meta.get('sector','')}\n"
        f"Investment thesis: {th['thesis']}\n"
        f"What we watch: {'; '.join(th['watch'])}\n"
        f"Thesis breaks if: {'; '.join(th['breaks'])}"
    )


SYSTEM_PROMPT = (
    "You are a senior buyside analyst at an Indian family office. "
    "Read updates through the explicit investment thesis provided. "
    "Reply with valid JSON only — no prose before or after."
)


def _build_user_prompt(*, ticker: str | None, source: str,
                       headline: str, body: str | None) -> str:
    th = _thesis_block(ticker)
    body = body or ""
    return f"""{th}

New update from {source}:
HEADLINE: {headline}
BODY: {body[:4000]}

Provide structured JSON output with exactly these keys:
{{
  "impact": "positive" | "negative" | "neutral",
  "urgency": "high" | "medium" | "low",
  "thesis_effect": "strengthens" | "weakens" | "unchanged",
  "action": "add" | "hold" | "trim" | "investigate" | "no_action",
  "reasoning": "2-3 sentence buyside read",
  "follow_ups": ["question 1", "question 2"]
}}

Rules:
- "high" urgency = price-moving same-session (regulatory action, results
  beat/miss, M&A, leadership change, large litigation, fraud, downgrade).
- If the update doesn't touch the thesis, urgency=low and thesis_effect=unchanged.
- Be conservative with "add" / "trim" — default to "investigate" or "no_action".
- follow_ups must be specific questions a junior analyst can answer in <1 day.
"""


_JSON_RE = re.compile(r"\{[\s\S]*\}")


def _extract_json(s: str) -> dict[str, Any]:
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.IGNORECASE)
    m = _JSON_RE.search(s)
    if not m:
        raise ValueError(f"no JSON in response: {s[:200]}")
    return json.loads(m.group(0))


# ---------------------------- core API ----------------------------------

def analyze_update_row(row) -> dict | None:
    """Analyze one updates-row. Returns the parsed JSON or None on failure."""
    prompt = _build_user_prompt(
        ticker=row["ticker"],
        source=row["source"],
        headline=row["headline"],
        body=row["body"],
    )
    client = _client()
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=600,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text
        data = _extract_json(text)
    except Exception as e:
        log.error("Claude call failed for update %s: %s", row["id"], e)
        return None

    impact = (data.get("impact") or "neutral").lower()
    urgency = (data.get("urgency") or "low").lower()
    thesis_effect = (data.get("thesis_effect") or "unchanged").lower()
    action = (data.get("action") or "no_action").lower()
    reasoning = (data.get("reasoning") or "").strip()
    follow_ups = data.get("follow_ups") or []
    if not isinstance(follow_ups, list):
        follow_ups = [str(follow_ups)]

    insert_analysis(
        update_id=row["id"],
        ticker=row["ticker"],
        impact=impact,
        urgency=urgency,
        thesis_effect=thesis_effect,
        action=action,
        reasoning=reasoning,
        follow_ups=follow_ups,
    )
    mark_processed(row["id"])
    log.info("analysed update %s [%s] impact=%s urgency=%s",
             row["id"], row["ticker"], impact, urgency)
    return {
        "impact": impact, "urgency": urgency, "thesis_effect": thesis_effect,
        "action": action, "reasoning": reasoning, "follow_ups": follow_ups,
    }


def run(batch: int = 25) -> int:
    """Process up to `batch` unprocessed updates. Returns # analysed."""
    rows = unprocessed_updates(limit=batch)
    if not rows:
        return 0
    high_alerts: list[tuple] = []
    n = 0
    for row in rows:
        r = analyze_update_row(row)
        if r is None:
            mark_processed(row["id"])  # don't loop forever on a bad row
            continue
        n += 1
        if r["urgency"] == "high":
            high_alerts.append((row, r))
    if high_alerts:
        try:
            from . import alerts
            for row, r in high_alerts:
                alerts.send_high_alert(row, r)
        except Exception as e:
            log.warning("alerts send failed: %s", e)
    return n


# -------------------- on-demand: free-text analyze ----------------------

def analyze_text(ticker: str | None, text: str) -> dict:
    """Used by the dashboard 'Analyze' tab — paste-in research."""
    prompt = _build_user_prompt(
        ticker=ticker,
        source="user_paste",
        headline=text.split("\n", 1)[0][:300],
        body=text,
    )
    client = _client()
    resp = client.messages.create(
        model=MODEL,
        max_tokens=700,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    return _extract_json(resp.content[0].text)


# -------------------- on-demand: explain today's move -------------------

def explain_move(ticker: str) -> dict:
    """Why is `ticker` up/down today? Uses today's price move + recent updates."""
    from .fetchers.price_fetcher import intraday_move, macro_snapshot
    move = intraday_move(ticker)
    updates = recent_updates(hours=48, ticker=ticker)
    macros = macro_snapshot()
    th = _thesis_block(ticker)

    bullets = "\n".join(
        f"- [{u['source']}] {u['headline']}" for u in updates[:15]
    ) or "- (no fresh news/announcements found in last 48h)"
    macro_str = ", ".join(
        f"{k} {v['pct_change']:+.2f}%" for k, v in macros.items()
    ) or "(macro snapshot unavailable)"

    move_line = (
        f"Today: {move['close']:.2f} ({move['pct_change']:+.2f}%) vs prev {move['prev_close']:.2f}"
        if move else "Price move: unavailable from data source."
    )

    user_prompt = f"""{th}

{move_line}
Macro tape: {macro_str}

Recent updates (last 48h):
{bullets}

Question: Why is {ticker} moving today? Give a buyside read.

Return JSON with keys:
{{
  "verdict": "stock-specific" | "sector" | "macro" | "unclear",
  "primary_driver": "1-line cause",
  "supporting_evidence": ["bullet", "bullet"],
  "what_to_watch_next": ["bullet", "bullet"],
  "confidence": "high" | "medium" | "low"
}}
No prose outside JSON."""

    client = _client()
    resp = client.messages.create(
        model=MODEL,
        max_tokens=600,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    out = _extract_json(resp.content[0].text)
    out["_move"] = move
    out["_macro"] = macros
    out["_updates_used"] = len(updates)
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    n = run(batch=10)
    print(f"analysed {n}")
