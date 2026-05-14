"""Daily + intraday prices via yfinance.

- snapshot_universe(): writes last close OHLCV per ticker into `prices` table
- intraday_move(ticker): returns dict of today's % move, intraday OHLC, vol
- macro_snapshot(): ^GSPC, ^IXIC, ^DJI, ^N225, INR=X, BZ=F, GC=F, ^NSEI, ^BSESN
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import yfinance as yf

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
    """Pull last 5d daily OHLCV for the whole universe. Returns # rows written."""
    import pandas as pd
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
