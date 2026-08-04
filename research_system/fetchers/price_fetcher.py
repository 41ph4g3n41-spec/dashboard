"""Daily + intraday prices, from Upstox when configured, else yfinance.

- snapshot_universe(): writes last close OHLCV per ticker into `prices` table
- intraday_move(ticker): returns dict of today's % move, intraday OHLC, vol
- macro_snapshot(): ^GSPC, ^IXIC, ^DJI, ^N225, INR=X, BZ=F, GC=F, ^NSEI, ^BSESN

Source selection: if UPSTOX_ACCESS_TOKEN is set we prefer Upstox — it is the
user's own broker feed, so it covers recently-listed names that Yahoo is
slow to carry, and needs no scraping. yfinance stays the fallback and remains
the only source for `macro_snapshot()` (global indices, FX and commodities
aren't in an Indian broker's equity feed).

yfinance is intentionally NOT imported at module top — it's a 12-second
import. We defer it inside each function so the dashboard cold-start
doesn't pay that cost when the user hasn't asked for prices yet.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from ..config import UNIVERSE
from ..db import upsert_price

log = logging.getLogger("fetchers.price")

MACRO_TICKERS = {
    "S&P 500": "^GSPC",
    "Nasdaq": "^IXIC",
    "Dow": "^DJI",
    "Nikkei": "^N225",
    "Hang Seng": "^HSI",
    "USD/INR": "INR=X",
    "Brent": "BZ=F",
    "Gold": "GC=F",
    "Nifty 50": "^NSEI",
    "Sensex": "^BSESN",
    "Nifty Bank": "^NSEBANK",
}


def snapshot_universe() -> int:
    """Snapshot the universe from the best available source.

    Tries Upstox first when a token is configured; falls back to yfinance if
    it isn't, if the token is dead, or if the call returned nothing usable.
    Returns the number of rows written.
    """
    from . import upstox_fetcher as ux

    if ux.is_configured():
        try:
            rows = ux.snapshot_universe()
            if rows:
                return rows
            log.warning("Upstox snapshot wrote 0 rows — falling back to yfinance")
        except ux.UpstoxAuthError as e:
            log.warning("Upstox token unusable (%s) — falling back to yfinance", e)
        except ux.UpstoxError as e:
            log.warning("Upstox snapshot failed (%s) — falling back to yfinance", e)

    return _snapshot_universe_yf()


def _snapshot_universe_yf() -> int:
    """Pull last 5d daily OHLCV for the whole universe. Returns # rows written."""
    import pandas as pd
    import yfinance as yf
    syms = [m["yahoo"] for m in UNIVERSE.values() if m.get("yahoo")]
    sym_to_ticker = {m["yahoo"]: tk for tk, m in UNIVERSE.items() if m.get("yahoo")}
    if not syms:
        return 0
    try:
        df = yf.download(
            tickers=" ".join(syms),
            period="5d",
            interval="1d",
            group_by="ticker",
            progress=False,
            threads=True,
            auto_adjust=False,
        )
    except Exception as e:
        log.error("yfinance snapshot failed: %s", e)
        return 0

    if df is None or df.empty:
        log.warning("yfinance returned empty frame")
        return 0

    is_multi = isinstance(df.columns, pd.MultiIndex)
    rows = 0
    for sym in syms:
        try:
            if is_multi:
                if sym not in df.columns.get_level_values(0):
                    continue
                sub = df[sym].dropna(how="all")
            else:
                sub = df.dropna(how="all")
        except Exception as e:
            log.debug("slice %s skip: %s", sym, e)
            continue
        if sub is None or sub.empty:
            continue
        for asof, r in sub.iterrows():
            try:
                close = r.get("Close")
                if pd.isna(close):
                    continue
                upsert_price(
                    ticker=sym_to_ticker[sym],
                    asof=asof.strftime("%Y-%m-%d"),
                    o=float(r.get("Open") or 0),
                    h=float(r.get("High") or 0),
                    l=float(r.get("Low") or 0),
                    c=float(close),
                    v=int(r.get("Volume") or 0),
                )
                rows += 1
            except Exception as e:
                log.debug("price write skip %s: %s", sym, e)
    log.info("price snapshot wrote %d rows", rows)
    return rows


def intraday_move(ticker: str) -> dict | None:
    """Today's move for one name — Upstox when available, else yfinance."""
    from . import upstox_fetcher as ux

    if ux.is_configured():
        try:
            move = ux.intraday_move(ticker)
            if move:
                return move
        except ux.UpstoxError as e:
            log.warning("Upstox intraday_move %s failed (%s) — using yfinance",
                        ticker, e)

    return _intraday_move_yf(ticker)


def _intraday_move_yf(ticker: str) -> dict | None:
    import yfinance as yf
    meta = UNIVERSE.get(ticker.upper())
    if not meta or not meta.get("yahoo"):
        return None
    try:
        t = yf.Ticker(meta["yahoo"])
        hist = t.history(period="5d", interval="1d")
        if hist.empty:
            return None
        last = hist.iloc[-1]
        prev = hist.iloc[-2] if len(hist) > 1 else last
        chg = (float(last["Close"]) - float(prev["Close"])) / float(prev["Close"]) * 100.0
        return {
            "ticker": ticker,
            "source": "yfinance",
            "yahoo": meta["yahoo"],
            "asof": str(hist.index[-1].date()),
            "open": float(last["Open"]),
            "high": float(last["High"]),
            "low": float(last["Low"]),
            "close": float(last["Close"]),
            "prev_close": float(prev["Close"]),
            "pct_change": round(chg, 2),
            "volume": int(last["Volume"]),
        }
    except Exception as e:
        log.warning("intraday_move %s failed: %s", ticker, e)
        return None


def macro_snapshot() -> dict[str, dict]:
    import yfinance as yf
    out: dict[str, dict] = {}
    for label, sym in MACRO_TICKERS.items():
        try:
            t = yf.Ticker(sym)
            h = t.history(period="5d", interval="1d")
            if h.empty:
                continue
            last = h.iloc[-1]
            prev = h.iloc[-2] if len(h) > 1 else last
            chg = (float(last["Close"]) - float(prev["Close"])) / float(prev["Close"]) * 100.0
            out[label] = {
                "symbol": sym,
                "close": float(last["Close"]),
                "pct_change": round(chg, 2),
                "asof": str(h.index[-1].date()),
            }
        except Exception as e:
            log.debug("macro %s skipped: %s", sym, e)
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    n = snapshot_universe()
    print(f"Snapshot rows: {n}")
    print("HATSUN intraday:", intraday_move("HATSUN"))
    print("Macro:", macro_snapshot())
