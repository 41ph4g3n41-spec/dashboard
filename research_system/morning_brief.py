"""7:30 AM IST morning brief.

Aggregates:
  - last 18h of updates + analyses (universe-only)
  - overnight macro snapshot (US close, INR, Brent, Asia open)
  - upcoming results-calendar items
Sends to Claude to produce buyside-grade brief, stores in daily_briefs,
optionally emails / Telegrams it.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from .alerts import email_send, telegram_send
from .config import UNIVERSE
from .db import recent_analyses, recent_updates, upsert_brief
from .fetchers.price_fetcher import macro_snapshot

load_dotenv()
log = logging.getLogger("morning_brief")
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")
IST = ZoneInfo("Asia/Kolkata")


SYSTEM = (
    "You are the morning research assistant at an Indian family office. "
    "Produce a buyside-grade morning brief. Concise. No fluff. No filler."
)


def _format_universe_lines() -> str:
    rows = recent_analyses(hours=18)
    if not rows:
        return "(no analysed updates in last 18h)"
    out = []
    for r in rows[:40]:
        tk = r["ticker"] or "—"
        out.append(
            f"- [{tk}] urgency={r['urgency']} impact={r['impact']} "
            f"thesis={r['thesis_effect']} action={r['action']}\n"
            f"   headline: {r['headline']}\n"
            f"   read: {r['reasoning']}"
        )
    return "\n".join(out)


def _format_raw_updates_no_analysis() -> str:
    """Updates that didn't get a full analysis (sector PIB, etc.)."""
    rows = recent_updates(hours=18)
    # filter to those with ticker None (sector signals) — already covered by analysis where ticker present
    sector_rows = [r for r in rows if not r["ticker"]][:20]
    if not sector_rows:
        return "(no sector signals in last 18h)"
    return "\n".join(f"- [{r['source']}] {r['headline']}" for r in sector_rows)


def _format_macro(macro: dict) -> str:
    if not macro:
        return "(macro snapshot unavailable)"
    return "\n".join(f"- {k}: {v['close']:.2f} ({v['pct_change']:+.2f}%)" for k, v in macro.items())


def _portfolio_list() -> str:
    return ", ".join(f"{tk} ({m['name']})" for tk, m in UNIVERSE.items())


def build_and_store(now_ist: datetime | None = None) -> str:
    from anthropic import Anthropic
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY missing")
    now_ist = now_ist or datetime.now(IST)

    macro = macro_snapshot()
    user_prompt = f"""Date (IST): {now_ist.strftime('%a %d %b %Y, %H:%M')}

Coverage universe:
{_portfolio_list()}

Overnight / Asia-open macro:
{_format_macro(macro)}

Universe updates with Claude analysis (last 18h):
{_format_universe_lines()}

Sector signals (no direct ticker match) from last 18h:
{_format_raw_updates_no_analysis()}

Produce the morning brief in this exact structure:

1. **Top 3 macro reads** (1 line each)
2. **Universe signals** — only my 18 names. Group portfolio vs watchlist.
3. **Priority action list** — ranked, 3-5 items, each with the ticker and a
   one-line concrete action ("investigate X", "trim Y", "wait for Z print").
4. **What to ignore** — name the noise we should not chase today.

Markdown. No filler sentences. Each section short and punchy."""

    client = Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=MODEL,
        max_tokens=1400,
        system=SYSTEM,
        messages=[{"role": "user", "content": user_prompt}],
    )
    text = "".join(
        getattr(b, "text", "") for b in (resp.content or [])
    ).strip()

    date_str = now_ist.strftime("%Y-%m-%d")
    upsert_brief(date_str, text)
    log.info("morning brief stored for %s (%d chars)", date_str, len(text))

    # Optional pushes — plain text (telegram Markdown breaks on _, *, etc)
    try:
        telegram_send(f"📰 Morning Brief — {date_str}\n\n{text}")
    except Exception as e:
        log.debug("telegram skip: %s", e)
    try:
        email_send(
            subject=f"Morning Brief {date_str}",
            html=f"<pre style='font-family:ui-monospace'>{text}</pre>",
        )
    except Exception as e:
        log.debug("email skip: %s", e)

    return text


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    out = build_and_store()
    print(out)
