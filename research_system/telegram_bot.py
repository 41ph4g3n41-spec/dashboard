"""Interactive Telegram bot — your phone interface to the research system.

Long-polling. Single-process. No async framework. Just direct HTTPS to
api.telegram.org. Run with:

    python -m research_system.telegram_bot

Commands (auth-locked to TELEGRAM_CHAT_ID env var):
    /brief           today's morning brief
    /feed [N]        last N (default 10) Claude-analysed items
    /high            last 24h high-urgency items only
    /why TICKER      "why is X moving today?" explainer
    /thesis TICKER   thesis card for a holding
    /universe        portfolio + watchlist with last prices
    /price TICKER    daily OHLCV + % change for any holding
    /pull            run all fetchers + analyzer right now
    /help            this help
"""

from __future__ import annotations

import json
import logging
import os
import time

import requests
from dotenv import load_dotenv

from .analyzer import analyze_text, explain_move, run as run_analyzer
from .config import PORTFOLIO, UNIVERSE, WATCHLIST
from .db import connect, ensure_db, latest_brief, latest_prices
from .fetchers import bse_fetcher, nse_fetcher, pib_fetcher, price_fetcher, rss_fetcher
from .theses import THESES

load_dotenv()
log = logging.getLogger("telegram_bot")
API = "https://api.telegram.org/bot{tok}/{m}"


def _tok() -> str:
    t = os.getenv("TELEGRAM_BOT_TOKEN")
    if not t:
        raise RuntimeError("TELEGRAM_BOT_TOKEN missing — add to .env")
    return t


def _allowed_chat_ids() -> set[str]:
    raw = os.getenv("TELEGRAM_CHAT_ID", "")
    return {x.strip() for x in raw.split(",") if x.strip()}


def _send(chat_id: int | str, text: str) -> None:
    try:
        requests.post(
            API.format(tok=_tok(), m="sendMessage"),
            data={
                "chat_id": chat_id,
                "text": text[:4000],
                "disable_web_page_preview": "true",
            },
            timeout=15,
        )
    except Exception as e:
        log.warning("telegram send failed: %s", e)


# ---------------------------- command handlers ---------------------------

def cmd_help() -> str:
    return (
        "Commands:\n"
        "/brief — today's morning brief\n"
        "/feed [N] — last N analysed items (default 10)\n"
        "/high — last 24h high-urgency items\n"
        "/why TICKER — why is X moving today?\n"
        "/thesis TICKER — thesis card\n"
        "/universe — full universe with last prices\n"
        "/price TICKER — last close + %\n"
        "/pull — run all fetchers + analyser now\n"
        "/help — this menu"
    )


def cmd_brief() -> str:
    b = latest_brief()
    if not b:
        return "No brief stored yet. Wait for 7:30 IST or run morning_brief."
    return f"Morning Brief — {b['date']}\n\n{b['content'][:3800]}"


def cmd_feed(n: int = 10) -> str:
    with connect() as cx:
        rows = cx.execute(
            """SELECT u.ticker, u.source, u.headline, u.fetched_at,
                      a.impact, a.urgency, a.action, a.reasoning
               FROM updates u LEFT JOIN analyses a ON a.update_id = u.id
               ORDER BY u.fetched_at DESC LIMIT ?""", (n,),
        ).fetchall()
    if not rows:
        return "No items yet. Try /pull."
    out = [f"Latest {len(rows)} items:"]
    for r in rows:
        tk = r["ticker"] or "—"
        urg = (r["urgency"] or "—").upper()
        imp = (r["impact"] or "—")
        out.append(f"• [{tk}] {urg}/{imp}  {r['headline'][:120]}")
        if r["reasoning"]:
            out.append(f"   → {r['reasoning'][:200]}")
    return "\n".join(out)


def cmd_high() -> str:
    with connect() as cx:
        rows = cx.execute(
            """SELECT u.ticker, u.headline, u.url, a.impact, a.thesis_effect,
                      a.action, a.reasoning, a.created_at
               FROM analyses a JOIN updates u ON u.id = a.update_id
               WHERE a.urgency='high'
                 AND a.created_at >= datetime('now','-24 hours')
               ORDER BY a.created_at DESC LIMIT 20"""
        ).fetchall()
    if not rows:
        return "No high-urgency items in last 24h. Quiet tape."
    out = ["🔴 High-urgency last 24h:"]
    for r in rows:
        tk = r["ticker"] or "—"
        out.append(f"• [{tk}] {r['impact']} / thesis-{r['thesis_effect']} / {r['action']}")
        out.append(f"   {r['headline'][:140]}")
        out.append(f"   → {r['reasoning'][:200]}")
        if r["url"]:
            out.append(f"   {r['url']}")
    return "\n".join(out)


def cmd_why(arg: str) -> str:
    tk = (arg or "").strip().upper()
    if tk not in UNIVERSE:
        return f"Unknown ticker '{tk}'. Try /universe to see valid ones."
    try:
        out = explain_move(tk)
    except Exception as e:
        return f"Failed: {e}"
    mv = out.get("_move") or {}
    head = f"{tk} — "
    if mv:
        head += f"{mv.get('close')} ({mv.get('pct_change'):+.2f}%) on {mv.get('asof')}"
    else:
        head += "price unavailable"
    lines = [
        head,
        f"Verdict: {out.get('verdict','?')}  (confidence: {out.get('confidence','?')})",
        f"Primary driver: {out.get('primary_driver','—')}",
        "Evidence:",
    ]
    for s in out.get("supporting_evidence", []):
        lines.append(f"  • {s}")
    lines.append("Watch next:")
    for s in out.get("what_to_watch_next", []):
        lines.append(f"  • {s}")
    return "\n".join(lines)


def cmd_thesis(arg: str) -> str:
    tk = (arg or "").strip().upper()
    if tk not in THESES:
        return f"No thesis card for {tk}. Portfolio tickers: {', '.join(THESES)}"
    th = THESES[tk]
    lines = [
        f"{th['name']} ({tk})",
        "",
        f"Thesis: {th['thesis']}",
        "",
        "Watch:",
    ]
    lines += [f"  • {w}" for w in th["watch"]]
    lines += ["", "Breaks if:"]
    lines += [f"  • {w}" for w in th["breaks"]]
    lines += ["", f"Catalyst: {th.get('catalyst','—')}"]
    return "\n".join(lines)


def cmd_universe() -> str:
    prices = latest_prices()
    lines = ["📋 Portfolio:"]
    for tk in sorted(PORTFOLIO.keys()):
        p = prices.get(tk)
        px = f"{p['close']:.2f}" if p else "—"
        lines.append(f"  {tk:<10} {px:>10}   {PORTFOLIO[tk]['sector']}")
    lines.append("\n👁  Watchlist:")
    for tk in sorted(WATCHLIST.keys()):
        p = prices.get(tk)
        px = f"{p['close']:.2f}" if p else "—"
        lines.append(f"  {tk:<10} {px:>10}   {WATCHLIST[tk]['sector']}")
    return "\n".join(lines)


def cmd_price(arg: str) -> str:
    tk = (arg or "").strip().upper()
    if tk not in UNIVERSE:
        return f"Unknown ticker '{tk}'."
    mv = price_fetcher.intraday_move(tk)
    if not mv:
        return f"{tk}: price unavailable from data source."
    return (
        f"{tk} on {mv['asof']}\n"
        f"  Close  {mv['close']:.2f} ({mv['pct_change']:+.2f}%)\n"
        f"  OHL    {mv['open']:.2f} / {mv['high']:.2f} / {mv['low']:.2f}\n"
        f"  Volume {mv['volume']:,}"
    )


def cmd_pull() -> str:
    counts: dict[str, int] = {}
    try: counts["rss"] = rss_fetcher.fetch_all()
    except Exception as e: counts["rss"] = -1; log.exception("rss: %s", e)
    try: counts["pib"] = pib_fetcher.fetch_all()
    except Exception as e: counts["pib"] = -1; log.exception("pib: %s", e)
    try: counts["nse"] = nse_fetcher.fetch_announcements(days=1)
    except Exception as e: counts["nse"] = -1; log.exception("nse: %s", e)
    try: counts["bse"] = bse_fetcher.fetch_announcements(days=1)
    except Exception as e: counts["bse"] = -1; log.exception("bse: %s", e)
    try: counts["analysed"] = run_analyzer(batch=50)
    except Exception as e: counts["analysed"] = -1; log.exception("analyse: %s", e)
    return "Pull complete:\n" + "\n".join(f"  {k}: {v}" for k, v in counts.items())


# ---------------------------- dispatcher ---------------------------------

HANDLERS = {
    "/help": lambda _: cmd_help(),
    "/start": lambda _: "Hi! " + cmd_help(),
    "/brief": lambda _: cmd_brief(),
    "/feed": lambda a: cmd_feed(int(a) if a and a.isdigit() else 10),
    "/high": lambda _: cmd_high(),
    "/why": cmd_why,
    "/thesis": cmd_thesis,
    "/universe": lambda _: cmd_universe(),
    "/price": cmd_price,
    "/pull": lambda _: cmd_pull(),
}


def _handle(text: str) -> str:
    text = (text or "").strip()
    if not text.startswith("/"):
        return "Send /help for commands."
    parts = text.split(maxsplit=1)
    cmd = parts[0].split("@", 1)[0].lower()        # strip @BotName suffix
    arg = parts[1] if len(parts) > 1 else ""
    h = HANDLERS.get(cmd)
    if not h:
        return f"Unknown command {cmd}. Send /help."
    try:
        return h(arg)
    except Exception as e:
        log.exception("handler %s failed", cmd)
        return f"Failed: {e}"


# ---------------------------- long-polling loop --------------------------

def run() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s — %(message)s")
    ensure_db()
    allowed = _allowed_chat_ids()
    if not allowed:
        log.warning("TELEGRAM_CHAT_ID not set — bot will reply to any chat. "
                    "Set TELEGRAM_CHAT_ID for safety.")
    offset = 0
    log.info("Telegram bot starting (allowed chats: %s)", allowed or "ALL")
    while True:
        try:
            r = requests.get(
                API.format(tok=_tok(), m="getUpdates"),
                params={"timeout": 25, "offset": offset},
                timeout=30,
            )
            data = r.json()
        except Exception as e:
            log.warning("getUpdates failed: %s", e)
            time.sleep(3)
            continue
        if not data.get("ok"):
            log.warning("telegram api not ok: %s", data)
            time.sleep(3)
            continue
        for upd in data.get("result", []):
            offset = upd["update_id"] + 1
            msg = upd.get("message") or upd.get("edited_message")
            if not msg:
                continue
            chat_id = str(msg["chat"]["id"])
            if allowed and chat_id not in allowed:
                log.info("blocked chat_id=%s (not allowed)", chat_id)
                continue
            text = msg.get("text", "")
            log.info("CMD from %s: %s", chat_id, text[:80])
            reply = _handle(text)
            _send(chat_id, reply)


if __name__ == "__main__":
    run()
