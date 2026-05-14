"""NSE corporate announcements + results calendar fetcher.

NSE's API requires a warm-up cookie call against the homepage before
the JSON endpoints respond. We do that once per session.
"""

from __future__ import annotations

import logging
import time
from typing import Iterable

import requests

from ..config import UNIVERSE
from ..db import insert_update
from .util import make_session

log = logging.getLogger("fetchers.nse")

NSE_HOME = "https://www.nseindia.com/"
NSE_ANN_URL = "https://www.nseindia.com/api/corporate-announcements"
NSE_RESULTS_URL = "https://www.nseindia.com/api/corporates-financial-results"


def _warmup(session: requests.Session) -> None:
    """Hit the homepage so NSE sets the required cookies on the session."""
    session.get(NSE_HOME, timeout=15)
    time.sleep(0.5)
    # second call sometimes needed
    session.get("https://www.nseindia.com/corporates/announcements", timeout=15)
    time.sleep(0.3)


def _safe_get_json(session: requests.Session, url: str, params: dict,
                   tries: int = 3) -> dict | list | None:
    last_exc: Exception | None = None
    for attempt in range(tries):
        try:
            r = session.get(url, params=params, timeout=20)
            if r.status_code in (401, 403):
                # cookie likely expired — warm up and retry
                _warmup(session)
                time.sleep(0.8)
                last_exc = requests.HTTPError(f"{r.status_code} on {url}")
                continue
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            last_exc = e
            time.sleep(1.5 * (attempt + 1))
    log.warning("NSE GET giving up %s: %s", url, last_exc)
    return None


def fetch_announcements(days: int = 1) -> int:
    """Pull corporate announcements for portfolio tickers. Returns # new rows."""
    session = make_session()
    session.headers["Referer"] = "https://www.nseindia.com/corporates/announcements"
    _warmup(session)

    inserted = 0
    for ticker, meta in UNIVERSE.items():
        symbol = meta.get("nse")
        if not symbol:
            continue
        params = {
            "index": "equities",
            "symbol": symbol,
            "from_date": _date_n_days_ago(days),
            "to_date": _today_str(),
        }
        data = _safe_get_json(session, NSE_ANN_URL, params)
        if not data:
            continue
        rows: Iterable[dict] = data if isinstance(data, list) else data.get("rows", [])
        for r in rows:
            headline = (r.get("desc") or r.get("subject") or "").strip()
            body = (r.get("attchmntText") or r.get("smIndustry") or "").strip()
            url = r.get("attchmntFile") or ""
            if url and not url.startswith("http"):
                url = "https://archives.nseindia.com/" + url.lstrip("/")
            if not headline:
                continue
            new_id = insert_update(
                ticker=ticker,
                source="nse",
                type_="announcement",
                headline=f"[{symbol}] {headline}",
                body=body,
                url=url,
            )
            if new_id:
                inserted += 1
        time.sleep(0.4)  # be polite

    log.info("NSE announcements: %d new rows", inserted)
    return inserted


def fetch_results_calendar() -> list[dict]:
    """Pull upcoming results calendar for universe tickers (in-memory)."""
    session = make_session()
    session.headers["Referer"] = "https://www.nseindia.com/companies-listing/corporate-filings-financial-results"
    _warmup(session)
    params = {"index": "equities", "period": "Quarterly"}
    data = _safe_get_json(session, NSE_RESULTS_URL, params)
    if not data:
        return []
    rows = data if isinstance(data, list) else data.get("rows", data.get("data", []))
    wanted = {m["nse"]: tk for tk, m in UNIVERSE.items() if m.get("nse")}
    out: list[dict] = []
    for r in rows:
        sym = (r.get("symbol") or r.get("Symbol") or "").upper()
        if sym in wanted:
            out.append({
                "ticker": wanted[sym],
                "symbol": sym,
                "as_on": r.get("toDate") or r.get("relDate") or r.get("Period") or "",
                "broadcast": r.get("broadcastTimestamp") or r.get("reldate") or "",
                "raw": r,
            })
    return out


def _date_n_days_ago(n: int) -> str:
    from datetime import datetime, timedelta
    return (datetime.now() - timedelta(days=n)).strftime("%d-%m-%Y")


def _today_str() -> str:
    from datetime import datetime
    return datetime.now().strftime("%d-%m-%Y")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    n = fetch_announcements(days=2)
    print(f"NSE announcements inserted: {n}")
    cal = fetch_results_calendar()
    print(f"NSE results-calendar entries for universe: {len(cal)}")
    for c in cal[:5]:
        print(" -", c["ticker"], c["as_on"])
