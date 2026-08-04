"""Resolve tradable Indian instruments to Upstox ``instrument_key`` values.

Everything the live feed subscribes to is addressed by instrument key —
``NSE_INDEX|Nifty 50``, ``NSE_EQ|INE002A01018``, ``NSE_FO|46833`` — which
means the dashboard needs a way to go from "Nifty Bank futures" or a
portfolio ticker like ``HATSUN`` to the right key.

The instruments master (see :mod:`.upstox_rest`) is the source of truth and
is refreshed daily. The curated index keys below are only a fallback for a
cold start with no network, so the headline tiles still render.

Segments in the master:

  ``NSE_INDEX`` / ``BSE_INDEX``   index levels
  ``NSE_EQ``    / ``BSE_EQ``      cash equities
  ``NSE_FO``                      index & stock futures and options
  ``BSE_FO``                      Sensex/Bankex derivatives
  ``MCX_FO``                      commodity futures & options
  ``NCD_FO`` / ``CDS_FO``         currency derivatives
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Iterable, Sequence

from . import upstox_rest

log = logging.getLogger("fetchers.upstox_universe")

# Headline indices — used when the master has not been downloaded yet.
FALLBACK_INDICES: dict[str, str] = {
    "Nifty 50": "NSE_INDEX|Nifty 50",
    "Nifty Bank": "NSE_INDEX|Nifty Bank",
    "Nifty Fin Service": "NSE_INDEX|Nifty Fin Service",
    "Nifty Next 50": "NSE_INDEX|Nifty Next 50",
    "Nifty Midcap Select": "NSE_INDEX|NIFTY MID SELECT",
    "India VIX": "NSE_INDEX|India VIX",
    "Sensex": "BSE_INDEX|SENSEX",
    "Bankex": "BSE_INDEX|BANKEX",
}

# Underlyings whose derivatives the dashboard offers by default.
DEFAULT_FO_UNDERLYINGS = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"]

# F&O underlyings are named for the contract root ("NIFTY", "BANKNIFTY"),
# which is *not* the index's own name ("Nifty 50", "Nifty Bank"). Centring an
# option chain on the money needs this bridge — matching on the strings alone
# silently fails and leaves every chain uncentred.
UNDERLYING_SPOT_KEY = {
    "NIFTY": "NSE_INDEX|Nifty 50",
    "BANKNIFTY": "NSE_INDEX|Nifty Bank",
    "FINNIFTY": "NSE_INDEX|Nifty Fin Service",
    "MIDCPNIFTY": "NSE_INDEX|NIFTY MID SELECT",
    "NIFTYNXT50": "NSE_INDEX|Nifty Next 50",
    "SENSEX": "BSE_INDEX|SENSEX",
    "BANKEX": "BSE_INDEX|BANKEX",
}


def spot_key(underlying: str) -> str | None:
    """Instrument key carrying the spot price for an F&O underlying.

    Index underlyings map to their index feed. Stock and commodity
    underlyings have no index — callers should fall back to the near-month
    future's last price.
    """
    return UNDERLYING_SPOT_KEY.get(underlying.upper())

# Liquid MCX commodities — the closest thing to a "global" tape that trades
# on an Indian exchange, since they track international benchmarks.
DEFAULT_COMMODITIES = ["CRUDEOIL", "NATURALGAS", "GOLD", "GOLDM", "SILVER",
                       "SILVERM", "COPPER", "ZINC", "ALUMINIUM", "LEAD"]

FUTURE_TYPES = {"FUT", "FUTIDX", "FUTSTK", "FUTCOM", "FUTCUR"}
OPTION_TYPES = {"CE", "PE", "OPTIDX", "OPTSTK", "OPTFUT", "OPTCUR"}


def _load(exchange: str) -> list[dict]:
    try:
        return upstox_rest.download_instruments(exchange)
    except Exception as e:
        log.warning("instruments master for %s unavailable: %s", exchange, e)
        return []


def indices() -> dict[str, str]:
    """``display name -> instrument_key`` for NSE + BSE indices."""
    out: dict[str, str] = {}
    for exch in ("NSE", "BSE"):
        rows = upstox_rest.filter_instruments(
            _load(exch), segments=[f"{exch}_INDEX"]
        )
        for r in rows:
            name = r.get("trading_symbol") or r.get("name")
            key = r.get("instrument_key")
            if name and key:
                out[name] = key
    return out or dict(FALLBACK_INDICES)


def headline_indices() -> dict[str, str]:
    """The eight index tiles shown at the top of the live page."""
    live = indices()
    out: dict[str, str] = {}
    for label, fallback_key in FALLBACK_INDICES.items():
        # Prefer the master's own key; fall back to the curated constant.
        match = next(
            (k for name, k in live.items() if name.lower() == label.lower()),
            None,
        )
        out[label] = match or fallback_key
    return out


def equities(exchange: str = "NSE") -> list[dict]:
    """All cash-segment equities on an exchange."""
    return upstox_rest.filter_instruments(
        _load(exchange), segments=[f"{exchange}_EQ"]
    )


def resolve_equity_keys(
    symbols: Sequence[str], exchange: str = "NSE"
) -> dict[str, str]:
    """Map NSE/BSE trading symbols to instrument keys (unmatched are dropped)."""
    wanted = {s.upper() for s in symbols}
    out: dict[str, str] = {}
    for r in equities(exchange):
        sym = (r.get("trading_symbol") or "").upper()
        if sym in wanted and r.get("instrument_key"):
            out[sym] = r["instrument_key"]
    missing = wanted - set(out)
    if missing:
        log.info("no %s instrument for: %s", exchange, ", ".join(sorted(missing)))
    return out


def _expiry_date(row: dict) -> date | None:
    """Master stores expiry as epoch-ms, or occasionally as YYYY-MM-DD."""
    raw = row.get("expiry")
    if raw in (None, ""):
        return None
    try:
        if isinstance(raw, (int, float)) or str(raw).isdigit():
            return datetime.fromtimestamp(int(raw) / 1000).date()
        return datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
    except (ValueError, OSError, OverflowError):
        return None


def derivatives(
    exchange: str = "NSE",
    *,
    underlyings: Iterable[str] | None = None,
    kind: str = "futures",
    include_expired: bool = False,
) -> list[dict]:
    """Futures or options contracts, newest expiry first.

    `kind` is ``"futures"`` or ``"options"``. `underlyings` filters on the
    contract's ``name`` / ``asset_symbol`` (e.g. ``NIFTY``, ``CRUDEOIL``).
    """
    want_types = FUTURE_TYPES if kind == "futures" else OPTION_TYPES
    segs = [f"{exchange}_FO"]
    rows = upstox_rest.filter_instruments(_load(exchange), segments=segs)

    names = {u.upper() for u in underlyings} if underlyings else None
    today = date.today()
    out = []
    for r in rows:
        itype = (r.get("instrument_type") or "").upper()
        if itype not in want_types:
            continue
        if names:
            label = (r.get("asset_symbol") or r.get("name") or "").upper()
            if label not in names:
                continue
        exp = _expiry_date(r)
        if not include_expired and exp and exp < today:
            continue
        r = dict(r)
        r["_expiry_date"] = exp
        out.append(r)

    out.sort(key=lambda x: (x["_expiry_date"] or date.max,
                            x.get("trading_symbol") or ""))
    return out


def futures(exchange: str = "NSE", underlyings: Iterable[str] | None = None) -> list[dict]:
    return derivatives(exchange, underlyings=underlyings, kind="futures")


def options(exchange: str = "NSE", underlyings: Iterable[str] | None = None) -> list[dict]:
    return derivatives(exchange, underlyings=underlyings, kind="options")


def near_month_futures(
    exchange: str = "NSE", underlyings: Iterable[str] | None = None
) -> list[dict]:
    """One contract per underlying — the nearest unexpired expiry."""
    seen: dict[str, dict] = {}
    for r in futures(exchange, underlyings):
        label = (r.get("asset_symbol") or r.get("name") or "").upper()
        if label and label not in seen:
            seen[label] = r
    return list(seen.values())


def option_strikes(
    underlying: str,
    expiry: date | str | None = None,
    exchange: str = "NSE",
    *,
    spot: float | None = None,
    around: int = 10,
) -> list[dict]:
    """Option contracts for one underlying and expiry, optionally ATM-centred.

    `around` keeps the N strikes either side of `spot` — an index chain runs
    to hundreds of strikes, well past what a single websocket mode allows.
    """
    rows = options(exchange, [underlying])
    if not rows:
        return []

    if expiry is None:
        expiry = min(
            (r["_expiry_date"] for r in rows if r.get("_expiry_date")),
            default=None,
        )
    elif isinstance(expiry, str):
        expiry = datetime.strptime(expiry[:10], "%Y-%m-%d").date()

    if expiry is not None:
        rows = [r for r in rows if r.get("_expiry_date") == expiry]

    if spot is not None and rows:
        def strike(r: dict) -> float:
            try:
                return float(r.get("strike_price") or 0)
            except (TypeError, ValueError):
                return 0.0

        uniq = sorted({strike(r) for r in rows if strike(r)})
        if uniq:
            atm = min(uniq, key=lambda s: abs(s - spot))
            i = uniq.index(atm)
            keep = set(uniq[max(0, i - around) : i + around + 1])
            rows = [r for r in rows if strike(r) in keep]

    rows.sort(key=lambda r: (float(r.get("strike_price") or 0),
                             (r.get("instrument_type") or "")))
    return rows


def keys_of(rows: Iterable[dict]) -> list[str]:
    """Extract ``instrument_key`` values from master rows."""
    return [r["instrument_key"] for r in rows if r.get("instrument_key")]


def label_map(rows: Iterable[dict]) -> dict[str, str]:
    """``instrument_key -> trading_symbol`` for display."""
    return {
        r["instrument_key"]: (r.get("trading_symbol") or r["instrument_key"])
        for r in rows
        if r.get("instrument_key")
    }


def portfolio_keys() -> dict[str, str]:
    """Instrument keys for the tickers configured in ``config.UNIVERSE``.

    Returns ``ticker -> instrument_key`` using each entry's NSE symbol.
    """
    from ..config import UNIVERSE

    nse_syms = {
        meta["nse"].upper(): tk
        for tk, meta in UNIVERSE.items()
        if meta.get("nse")
    }
    resolved = resolve_equity_keys(list(nse_syms))
    return {nse_syms[sym]: key for sym, key in resolved.items()}


def summarise_master() -> dict[str, Any]:
    """Instrument counts per segment/type — shown on the live page footer."""
    counts: dict[str, int] = {}
    for exch in ("NSE", "BSE", "MCX"):
        for r in _load(exch):
            seg = (r.get("segment") or r.get("exchange") or "?").upper()
            counts[seg] = counts.get(seg, 0) + 1
    return counts
