"""Global markets snapshot — indices, futures, FX, commodities, crypto, rates.

Upstox is an Indian broker: its instrument master covers NSE/BSE cash, NSE
and BSE F&O, MCX commodities and CDS currency pairs, and nothing outside
India. So the live Indian book comes off the Upstox websocket
(:mod:`.upstox_feed`) and everything global is sourced here from Yahoo
Finance, which the repo already depends on for the daily price snapshot.

Coverage is intentionally wide — US/Europe/Asia cash indices, the US index
futures that trade nearly around the clock, energy and metals, major FX
crosses, benchmark yields and crypto — so the dashboard shows a live global
tape while Indian markets are shut.

Latency note: Yahoo is quote-delayed for most cash indices (typically 15
minutes; index futures and crypto are effectively real time). Every row
carries its own ``delayed`` flag so the UI can label it honestly rather than
implying tick-by-tick parity with the Upstox feed.
"""

from __future__ import annotations

import logging
import time
from typing import Any

log = logging.getLogger("fetchers.global_feed")

# symbol -> (display name, group, delayed?)
GLOBAL_UNIVERSE: dict[str, tuple[str, str, bool]] = {
    # --- US cash indices ---
    "^GSPC": ("S&P 500", "US Indices", True),
    "^IXIC": ("Nasdaq Composite", "US Indices", True),
    "^DJI": ("Dow Jones", "US Indices", True),
    "^RUT": ("Russell 2000", "US Indices", True),
    "^VIX": ("VIX", "US Indices", True),
    # --- US index futures (near 24h) ---
    "ES=F": ("S&P 500 E-mini", "US Futures", False),
    "NQ=F": ("Nasdaq 100 E-mini", "US Futures", False),
    "YM=F": ("Dow E-mini", "US Futures", False),
    "RTY=F": ("Russell 2000 E-mini", "US Futures", False),
    # --- Europe ---
    "^FTSE": ("FTSE 100", "Europe", True),
    "^GDAXI": ("DAX", "Europe", True),
    "^FCHI": ("CAC 40", "Europe", True),
    "^STOXX50E": ("Euro Stoxx 50", "Europe", True),
    # --- Asia-Pacific ---
    "^N225": ("Nikkei 225", "Asia", True),
    "^HSI": ("Hang Seng", "Asia", True),
    "000001.SS": ("Shanghai Composite", "Asia", True),
    "^KS11": ("KOSPI", "Asia", True),
    "^TWII": ("Taiwan Weighted", "Asia", True),
    "^AXJO": ("ASX 200", "Asia", True),
    # --- India (cross-check against the Upstox feed) ---
    "^NSEI": ("Nifty 50", "India", True),
    "^BSESN": ("Sensex", "India", True),
    "^NSEBANK": ("Nifty Bank", "India", True),
    "^INDIAVIX": ("India VIX", "India", True),
    # --- Energy & metals ---
    "CL=F": ("WTI Crude", "Commodities", False),
    "BZ=F": ("Brent Crude", "Commodities", False),
    "NG=F": ("Natural Gas", "Commodities", False),
    "GC=F": ("Gold", "Commodities", False),
    "SI=F": ("Silver", "Commodities", False),
    "HG=F": ("Copper", "Commodities", False),
    "ZW=F": ("Wheat", "Commodities", False),
    # --- FX ---
    "INR=X": ("USD/INR", "FX", False),
    "DX-Y.NYB": ("Dollar Index", "FX", False),
    "EURUSD=X": ("EUR/USD", "FX", False),
    "GBPUSD=X": ("GBP/USD", "FX", False),
    "JPY=X": ("USD/JPY", "FX", False),
    "CNY=X": ("USD/CNY", "FX", False),
    # --- Rates ---
    "^TNX": ("US 10Y Yield", "Rates", True),
    "^TYX": ("US 30Y Yield", "Rates", True),
    "^FVX": ("US 5Y Yield", "Rates", True),
    # --- Crypto ---
    "BTC-USD": ("Bitcoin", "Crypto", False),
    "ETH-USD": ("Ethereum", "Crypto", False),
}

GROUPS = ["US Indices", "US Futures", "Europe", "Asia", "India",
          "Commodities", "FX", "Rates", "Crypto"]

_cache: dict[str, Any] = {"at": 0.0, "rows": []}


def snapshot(symbols: list[str] | None = None, ttl: int = 30) -> list[dict]:
    """Latest price + day change for the global universe.

    Results are cached for `ttl` seconds — Streamlit reruns aggressively and
    Yahoo rate-limits, so repeated reruns must not each trigger a download.
    Returns ``[]`` (never raises) when the network or yfinance is unavailable,
    so a global outage cannot take the whole dashboard down.
    """
    syms = symbols or list(GLOBAL_UNIVERSE)
    now = time.time()
    if not symbols and _cache["rows"] and now - _cache["at"] < ttl:
        return _cache["rows"]

    try:
        import yfinance as yf
    except ImportError:
        log.warning("yfinance not installed — global markets unavailable")
        return []

    rows: list[dict] = []
    try:
        # One batched request; 1-minute bars give an intraday-fresh last price.
        data = yf.download(
            tickers=" ".join(syms),
            period="2d",
            interval="1m",
            group_by="ticker",
            progress=False,
            threads=True,
            auto_adjust=False,
            prepost=True,
        )
    except Exception as e:
        log.warning("global snapshot download failed: %s", e)
        return _cache["rows"] if not symbols else []

    if data is None or getattr(data, "empty", True):
        return _cache["rows"] if not symbols else []

    import pandas as pd

    multi = isinstance(data.columns, pd.MultiIndex)
    # Previous session close, for a true day-change on a 1m intraday series.
    prev_closes = _prev_closes(syms)

    for sym in syms:
        name, group, delayed = GLOBAL_UNIVERSE.get(sym, (sym, "Other", True))
        try:
            sub = data[sym] if multi else data
            sub = sub.dropna(how="all")
            if sub.empty:
                continue
            closes = sub["Close"].dropna()
            if closes.empty:
                continue
            last = float(closes.iloc[-1])
            prev = prev_closes.get(sym)
            if prev is None:
                # Fall back to the first bar in the window.
                prev = float(closes.iloc[0])
            chg = last - prev
            rows.append({
                "symbol": sym,
                "name": name,
                "group": group,
                "delayed": delayed,
                "last": last,
                "prev_close": prev,
                "change": chg,
                "pct_change": (chg / prev * 100.0) if prev else None,
                "day_high": float(sub["High"].max()) if "High" in sub else None,
                "day_low": float(sub["Low"].min()) if "Low" in sub else None,
                "asof": str(closes.index[-1]),
            })
        except Exception as e:
            log.debug("global row %s skipped: %s", sym, e)

    if not symbols and rows:
        _cache["rows"] = rows
        _cache["at"] = now
    return rows


def _prev_closes(syms: list[str]) -> dict[str, float]:
    """Prior-session close per symbol, so % change matches what screens show."""
    try:
        import pandas as pd
        import yfinance as yf

        daily = yf.download(
            tickers=" ".join(syms), period="5d", interval="1d",
            group_by="ticker", progress=False, threads=True, auto_adjust=False,
        )
    except Exception as e:
        log.debug("prev-close lookup failed: %s", e)
        return {}

    if daily is None or getattr(daily, "empty", True):
        return {}

    out: dict[str, float] = {}
    multi = isinstance(daily.columns, pd.MultiIndex)
    for sym in syms:
        try:
            sub = daily[sym] if multi else daily
            closes = sub["Close"].dropna()
            if len(closes) >= 2:
                out[sym] = float(closes.iloc[-2])
            elif len(closes) == 1:
                out[sym] = float(closes.iloc[-1])
        except Exception:
            continue
    return out


def by_group(rows: list[dict] | None = None) -> dict[str, list[dict]]:
    """Group rows for sectioned rendering, preserving :data:`GROUPS` order."""
    rows = rows if rows is not None else snapshot()
    out: dict[str, list[dict]] = {g: [] for g in GROUPS}
    for r in rows:
        out.setdefault(r["group"], []).append(r)
    return {g: v for g, v in out.items() if v}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    for grp, items in by_group().items():
        print(f"\n== {grp} ==")
        for r in items:
            pct = r["pct_change"]
            print(f"  {r['name']:<22} {r['last']:>12,.2f}  "
                  f"{pct:+.2f}%" if pct is not None else f"  {r['name']}")
