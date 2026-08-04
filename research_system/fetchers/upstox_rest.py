"""Upstox REST market-data client — quotes, option chain, instruments master.

Covers the read-only market endpoints the live dashboard needs:

  * ``/v3/market-quote/ltp``            last traded price + close
  * ``/v3/market-quote/ohlc``           OHLC at a chosen interval
  * ``/v3/market-quote/option-greek``   delta/gamma/theta/vega/rho + IV
  * ``/v2/market-quote/quotes``         full quote incl. 5-level depth
  * ``/v2/option/chain``                option chain for an underlying+expiry
  * ``/v2/option/contract``             tradable option contracts / expiries
  * ``/v2/market/status/{exchange}``    per-exchange market status
  * instruments master (assets.upstox.com) for symbol -> instrument_key

Auth is a bearer access token read from ``UPSTOX_ACCESS_TOKEN``. Tokens are
short-lived — Upstox expires them daily at 03:30 IST — so every call raises
:class:`UpstoxAuthError` on 401 and the UI surfaces a re-login prompt rather
than silently showing stale numbers.

The websocket feed in ``upstox_feed`` is the primary source of live ticks;
this module supplies the cold-start snapshot (so tiles are populated before
the first tick lands) and everything the socket does not carry, such as the
option chain and the instrument universe.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

import requests

try:
    from dotenv import load_dotenv

    load_dotenv()  # same convention as the other modules: .env for local runs
except ImportError:  # pragma: no cover - dotenv is a declared dependency
    pass

log = logging.getLogger("fetchers.upstox_rest")

API = "https://api.upstox.com"
ASSETS = "https://assets.upstox.com/market-quote/instruments/exchange"

# Documented per-request instrument caps. The server is authoritative; these
# only decide how we chunk so a large universe does not get rejected wholesale.
MAX_KEYS_PER_QUOTE_CALL = 500

# Where the (large) instruments master is cached between runs.
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "upstox"
INSTRUMENT_CACHE_TTL = 20 * 3600  # master is regenerated once daily

_TIMEOUT = (6, 30)  # (connect, read)


class UpstoxError(RuntimeError):
    """Any non-2xx response from the Upstox API."""


class UpstoxAuthError(UpstoxError):
    """401/403 — the access token is missing, expired, or revoked."""


def access_token() -> str | None:
    """Bearer token from the environment. ``None`` when unconfigured."""
    tok = (os.environ.get("UPSTOX_ACCESS_TOKEN") or "").strip()
    return tok or None


def is_configured() -> bool:
    return access_token() is not None


def _headers() -> dict[str, str]:
    tok = access_token()
    if not tok:
        raise UpstoxAuthError(
            "UPSTOX_ACCESS_TOKEN is not set. Add it to .env (local) or "
            "Streamlit secrets (Cloud)."
        )
    return {"Authorization": f"Bearer {tok}", "Accept": "application/json"}


def _get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"{API}{path}"
    try:
        r = requests.get(url, headers=_headers(), params=params, timeout=_TIMEOUT)
    except requests.RequestException as e:
        raise UpstoxError(f"network error calling {path}: {e}") from e

    if r.status_code in (401, 403):
        raise UpstoxAuthError(
            f"Upstox rejected the access token ({r.status_code}). Tokens expire "
            f"daily at 03:30 IST — generate a fresh one and update "
            f"UPSTOX_ACCESS_TOKEN. Response: {r.text[:300]}"
        )
    if r.status_code >= 400:
        raise UpstoxError(f"{path} -> HTTP {r.status_code}: {r.text[:400]}")
    try:
        return r.json()
    except ValueError as e:
        raise UpstoxError(f"{path} returned non-JSON: {r.text[:200]}") from e


def _chunks(seq: Sequence[str], size: int) -> Iterable[Sequence[str]]:
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def _quote_call(path: str, keys: Sequence[str], extra: dict | None = None) -> dict:
    """Run a quote endpoint over an arbitrarily long key list, merging results."""
    merged: dict[str, Any] = {}
    for chunk in _chunks(list(keys), MAX_KEYS_PER_QUOTE_CALL):
        params = {"instrument_key": ",".join(chunk)}
        if extra:
            params.update(extra)
        body = _get(path, params)
        data = body.get("data") or {}
        if isinstance(data, dict):
            merged.update(data)
    return merged


# --------------------------- quotes -------------------------------------
def ltp(instrument_keys: Sequence[str]) -> dict[str, dict]:
    """Last traded price for each key. Response is keyed by trading symbol."""
    if not instrument_keys:
        return {}
    return _quote_call("/v3/market-quote/ltp", instrument_keys)


def ohlc(instrument_keys: Sequence[str], interval: str = "1d") -> dict[str, dict]:
    """OHLC + LTP. `interval` is one of 1d, I1, I30 (v3 accepted values)."""
    if not instrument_keys:
        return {}
    return _quote_call("/v3/market-quote/ohlc", instrument_keys, {"interval": interval})


def option_greeks(instrument_keys: Sequence[str]) -> dict[str, dict]:
    """Greeks + IV for option instrument keys."""
    if not instrument_keys:
        return {}
    return _quote_call("/v3/market-quote/option-greek", instrument_keys)


def full_quote(instrument_keys: Sequence[str]) -> dict[str, dict]:
    """Full quote (v2) — includes OHLC, depth, OI, volume, circuit limits."""
    if not instrument_keys:
        return {}
    return _quote_call("/v2/market-quote/quotes", instrument_keys)


# --------------------------- options ------------------------------------
def option_chain(instrument_key: str, expiry_date: str) -> list[dict]:
    """Full option chain for an underlying. `expiry_date` is YYYY-MM-DD."""
    body = _get(
        "/v2/option/chain",
        {"instrument_key": instrument_key, "expiry_date": expiry_date},
    )
    data = body.get("data")
    return data if isinstance(data, list) else []


def option_contracts(instrument_key: str, expiry_date: str | None = None) -> list[dict]:
    """Tradable option contracts for an underlying; omit expiry to list all."""
    params: dict[str, Any] = {"instrument_key": instrument_key}
    if expiry_date:
        params["expiry_date"] = expiry_date
    body = _get("/v2/option/contract", params)
    data = body.get("data")
    return data if isinstance(data, list) else []


def option_expiries(instrument_key: str) -> list[str]:
    """Sorted unique expiry dates available for an underlying."""
    seen = {
        c.get("expiry")
        for c in option_contracts(instrument_key)
        if c.get("expiry")
    }
    return sorted(seen)


# --------------------------- market status ------------------------------
def market_status(exchange: str) -> dict[str, Any]:
    """Status for one exchange, e.g. NSE / BSE / MCX / NFO / CDS."""
    body = _get(f"/v2/market/status/{exchange}")
    data = body.get("data")
    return data if isinstance(data, dict) else {}


# --------------------------- instruments master -------------------------
# Per-exchange masters keep the download small; "complete" is ~100 MB
# uncompressed and only worth pulling when the user wants everything.
MASTER_FILES = {
    "NSE": "NSE.json.gz",
    "BSE": "BSE.json.gz",
    "MCX": "MCX.json.gz",
    "complete": "complete.json.gz",
}


def _cache_path(name: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{name}.json"


def download_instruments(exchange: str = "NSE", force: bool = False) -> list[dict]:
    """Fetch (and disk-cache) the instruments master for one exchange.

    The master is a public asset — no bearer token needed — and Upstox
    regenerates it daily, so a cached copy is reused for 20 hours.
    """
    fname = MASTER_FILES.get(exchange)
    if not fname:
        raise ValueError(
            f"unknown exchange {exchange!r}; expected one of {sorted(MASTER_FILES)}"
        )

    cache = _cache_path(exchange)
    if not force and cache.exists():
        age = time.time() - cache.stat().st_mtime
        if age < INSTRUMENT_CACHE_TTL:
            try:
                return json.loads(cache.read_text())
            except (ValueError, OSError) as e:
                log.warning("instrument cache %s unreadable (%s) — refetching", cache, e)

    url = f"{ASSETS}/{fname}"
    log.info("downloading instruments master %s", url)
    try:
        r = requests.get(url, timeout=(6, 120))
        r.raise_for_status()
    except requests.RequestException as e:
        # Fall back to a stale cache rather than leaving the UI with nothing.
        if cache.exists():
            log.warning("master download failed (%s) — using stale cache", e)
            try:
                return json.loads(cache.read_text())
            except (ValueError, OSError):
                pass
        raise UpstoxError(f"could not download instruments master: {e}") from e

    try:
        rows = json.loads(gzip.decompress(r.content))
    except (OSError, ValueError) as e:
        raise UpstoxError(f"instruments master for {exchange} was not valid gzip JSON: {e}") from e

    if not isinstance(rows, list):
        raise UpstoxError(f"instruments master for {exchange} was not a JSON list")

    try:
        cache.write_text(json.dumps(rows))
    except OSError as e:
        log.warning("could not cache instruments master: %s", e)
    return rows


def _seg(row: dict) -> str:
    return (row.get("segment") or row.get("exchange") or "").upper()


def _itype(row: dict) -> str:
    return (row.get("instrument_type") or "").upper()


def filter_instruments(
    rows: Iterable[dict],
    *,
    segments: Sequence[str] | None = None,
    instrument_types: Sequence[str] | None = None,
    name_contains: str | None = None,
) -> list[dict]:
    """Narrow a master list by segment / instrument type / name substring."""
    seg_set = {s.upper() for s in segments} if segments else None
    type_set = {t.upper() for t in instrument_types} if instrument_types else None
    needle = name_contains.upper() if name_contains else None

    out = []
    for r in rows:
        if seg_set and _seg(r) not in seg_set:
            continue
        if type_set and _itype(r) not in type_set:
            continue
        if needle:
            hay = f"{r.get('name','')} {r.get('trading_symbol','')}".upper()
            if needle not in hay:
                continue
        out.append(r)
    return out


def index_by_symbol(rows: Iterable[dict]) -> dict[str, str]:
    """Map ``trading_symbol`` -> ``instrument_key`` for quick resolution."""
    return {
        r["trading_symbol"]: r["instrument_key"]
        for r in rows
        if r.get("trading_symbol") and r.get("instrument_key")
    }
