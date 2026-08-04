"""Upstox (Uplink API v2) — read-only portfolio + market-quote client.

Read-only by construction: every call in this module is a GET against a
`user/`, `portfolio/` or `market-quote/` endpoint. There is deliberately no
code path here that can place, modify or cancel an order.

What it gives the rest of the system:

  * `holdings()`      — real demat holdings: qty, average cost, LTP, P&L
  * `positions()`     — intraday positions (read-only)
  * `funds()`         — available margin / used margin
  * `quote_map()`     — live OHLC + LTP for our universe, keyed by our ticker
  * `snapshot_universe()` — writes those quotes into the `prices` table,
                            same contract as `price_fetcher.snapshot_universe`

Auth: set `UPSTOX_ACCESS_TOKEN` in `.env` (VPS) or Streamlit secrets (Cloud).
The token is read from the environment on every call — it is never written to
disk, never logged, and never embedded in an exception message.

Resolving our tickers to Upstox instrument keys
-----------------------------------------------
Upstox addresses instruments as `NSE_EQ|<ISIN>`, but `config.py` only knows
NSE trading symbols. Resolution order, first hit wins:

  1. `upstox_key` in the holding's config meta  (explicit override)
  2. `NSE_EQ|<isin>` built from an `isin` field in the meta
  3. lookup of the NSE trading symbol in Upstox's public instrument master,
     downloaded once and cached under `data/` for a week

Step 3 needs no credentials — it is a public asset file — so ticker
resolution keeps working even when the token has expired.
"""

from __future__ import annotations

import base64
import gzip
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import requests

from .. import config
from ..config import ROOT
from ..db import upsert_price

log = logging.getLogger("fetchers.upstox")

BASE_URL = "https://api.upstox.com/v2"

# Public (unauthenticated) instrument master for the NSE segment.
INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
INSTRUMENT_CACHE = ROOT / "data" / "upstox_instruments.json"
INSTRUMENT_CACHE_TTL = 7 * 24 * 3600      # a week; symbol→ISIN rarely churns

TIMEOUT = 15
# Upstox allows 500 instrument keys per market-quote call; stay well under.
QUOTE_CHUNK = 100

_ENV_VAR = "UPSTOX_ACCESS_TOKEN"


class UpstoxError(RuntimeError):
    """Any Upstox call that did not come back as a usable success payload."""


class UpstoxAuthError(UpstoxError):
    """Token missing, expired or rejected — distinct so callers can fall back
    to yfinance quietly instead of surfacing a scary error."""


# --------------------------------------------------------------------------
# token handling
# --------------------------------------------------------------------------

def access_token() -> str | None:
    """The configured token, or None. Read fresh each call so a rotated
    secret takes effect without a restart."""
    tok = (os.getenv(_ENV_VAR) or "").strip()
    return tok or None


def is_configured() -> bool:
    return access_token() is not None


def _redact(text: str) -> str:
    """Belt-and-braces: strip the token out of anything we log or raise.

    Guarded on length — a short/placeholder token would otherwise match
    common substrings and shred the message we're trying to surface.
    Real Upstox tokens are JWTs, hundreds of characters long.
    """
    tok = access_token()
    if tok and len(tok) >= 8 and tok in text:
        text = text.replace(tok, "<redacted>")
    return text


def token_info() -> dict[str, Any]:
    """Decode the JWT payload for display — expiry, subject, plan flags.

    The signature is NOT verified: we only hold the public half of this
    token and Upstox is the one that validates it. This is purely so the
    dashboard can say "expires in 42 days" instead of failing at 3 a.m.
    Never returns the token itself.
    """
    tok = access_token()
    if not tok:
        return {"configured": False}

    info: dict[str, Any] = {"configured": True}
    try:
        payload_b64 = tok.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)          # restore padding
        claims = json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception:
        # Opaque or malformed token — still usable, we just can't introspect.
        return info | {"readable": False}

    exp = claims.get("exp")
    info.update(
        readable=True,
        user_id=claims.get("sub"),
        issuer=claims.get("iss"),
        is_plus_plan=claims.get("isPlusPlan"),
        is_extended=claims.get("isExtended"),
        is_multi_client=claims.get("isMultiClient"),
    )
    if isinstance(exp, (int, float)):
        expires_at = datetime.fromtimestamp(exp, tz=timezone.utc)
        remaining = exp - time.time()
        info.update(
            expires_at=expires_at.strftime("%Y-%m-%d %H:%M UTC"),
            expired=remaining <= 0,
            days_left=int(remaining // 86400),
        )
    return info


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def _get(path: str, params: dict | None = None) -> Any:
    """GET an Upstox endpoint and return its `data` payload.

    Raises UpstoxAuthError on 401/403 so callers can fall back silently,
    UpstoxError on anything else.
    """
    tok = access_token()
    if not tok:
        raise UpstoxAuthError(
            f"{_ENV_VAR} is not set — add it to .env (VPS) or "
            "Streamlit secrets (Cloud)."
        )

    url = f"{BASE_URL}{path}"
    try:
        r = requests.get(
            url,
            params=params or {},
            headers={
                "Authorization": f"Bearer {tok}",
                "Accept": "application/json",
            },
            timeout=TIMEOUT,
        )
    except requests.RequestException as e:
        raise UpstoxError(_redact(f"Upstox {path} unreachable: {e}")) from None

    if r.status_code in (401, 403):
        raise UpstoxAuthError(
            f"Upstox rejected the token ({r.status_code}) on {path}. "
            "It has probably expired — generate a new one and update "
            f"{_ENV_VAR}."
        )
    if r.status_code == 429:
        raise UpstoxError(f"Upstox rate limit hit on {path} — back off and retry.")
    if r.status_code >= 400:
        raise UpstoxError(_redact(f"Upstox {path} returned HTTP {r.status_code}: "
                                  f"{r.text[:300]}"))

    try:
        body = r.json()
    except ValueError:
        raise UpstoxError(f"Upstox {path} returned non-JSON body.") from None

    if body.get("status") != "success":
        errs = body.get("errors") or []
        msg = "; ".join(str(e.get("message") or e) for e in errs) or str(body)[:300]
        raise UpstoxError(_redact(f"Upstox {path} error: {msg}"))

    return body.get("data")


# --------------------------------------------------------------------------
# account endpoints
# --------------------------------------------------------------------------

def profile() -> dict:
    """Account profile — used by the dashboard to prove the token works."""
    return _get("/user/profile") or {}


def funds(segment: str | None = None) -> dict:
    """Fund + margin balances. `segment` is 'SEC' (equity/F&O) or 'COM'."""
    params = {"segment": segment} if segment else None
    return _get("/user/get-funds-and-margin", params) or {}


def _f(v: Any) -> float:
    """Upstox sends numbers as float, int, str or null depending on field."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def holdings() -> list[dict]:
    """Long-term (demat) holdings, normalised to a stable shape.

    `pnl` is taken straight from Upstox where present; where it is absent we
    derive it from qty × (ltp − avg), which is the same number Upstox
    computes, so the column is never blank.
    """
    raw = _get("/portfolio/long-term-holdings") or []
    out: list[dict] = []
    for h in raw:
        qty = _f(h.get("quantity"))
        avg = _f(h.get("average_price"))
        ltp = _f(h.get("last_price"))
        invested = qty * avg
        current = qty * ltp
        pnl = _f(h.get("pnl")) or (current - invested)
        out.append({
            "symbol": h.get("trading_symbol") or h.get("tradingsymbol"),
            "company": h.get("company_name"),
            "isin": h.get("isin"),
            "exchange": h.get("exchange"),
            "instrument_key": h.get("instrument_token"),
            "quantity": qty,
            "t1_quantity": _f(h.get("t1_quantity")),
            "avg_price": avg,
            "last_price": ltp,
            "invested": round(invested, 2),
            "current_value": round(current, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl / invested * 100, 2) if invested else 0.0,
            "day_change": _f(h.get("day_change")),
            "day_change_pct": _f(h.get("day_change_percentage")),
            "product": h.get("product"),
        })
    out.sort(key=lambda r: r["current_value"], reverse=True)
    return out


def positions() -> list[dict]:
    """Intraday / short-term positions, normalised. Read-only."""
    raw = _get("/portfolio/short-term-positions") or []
    out: list[dict] = []
    for p in raw:
        out.append({
            "symbol": p.get("trading_symbol") or p.get("tradingsymbol"),
            "exchange": p.get("exchange"),
            "instrument_key": p.get("instrument_token"),
            "product": p.get("product"),
            "quantity": _f(p.get("quantity")),
            "avg_price": _f(p.get("average_price")),
            "last_price": _f(p.get("last_price")),
            "pnl": _f(p.get("pnl")),
            "realised": _f(p.get("realised")),
            "unrealised": _f(p.get("unrealised")),
        })
    return out


def portfolio_summary() -> dict:
    """Aggregate of `holdings()` — the numbers worth putting on a header row."""
    hs = holdings()
    invested = sum(h["invested"] for h in hs)
    current = sum(h["current_value"] for h in hs)
    pnl = current - invested
    day = sum(h["day_change"] * h["quantity"] for h in hs)
    return {
        "count": len(hs),
        "invested": round(invested, 2),
        "current_value": round(current, 2),
        "pnl": round(pnl, 2),
        "pnl_pct": round(pnl / invested * 100, 2) if invested else 0.0,
        "day_pnl": round(day, 2),
    }


# --------------------------------------------------------------------------
# instrument master (public, no auth)
# --------------------------------------------------------------------------

def _load_instrument_cache() -> dict[str, str] | None:
    try:
        if not INSTRUMENT_CACHE.exists():
            return None
        if time.time() - INSTRUMENT_CACHE.stat().st_mtime > INSTRUMENT_CACHE_TTL:
            return None
        return json.loads(INSTRUMENT_CACHE.read_text())
    except Exception as e:
        log.debug("instrument cache unreadable: %s", e)
        return None


def instrument_map(force: bool = False) -> dict[str, str]:
    """{NSE trading symbol -> instrument_key} for NSE equity.

    Cached on disk for a week. Returns {} if the download fails — callers
    treat that as "can't resolve", not as a hard error.
    """
    if not force:
        cached = _load_instrument_cache()
        if cached is not None:
            return cached

    try:
        r = requests.get(INSTRUMENTS_URL, timeout=60)
        r.raise_for_status()
        rows = json.loads(gzip.decompress(r.content))
    except Exception as e:
        log.warning("Upstox instrument master download failed: %s", e)
        return _load_instrument_cache() or {}     # stale beats nothing

    mapping = {
        row["trading_symbol"]: row["instrument_key"]
        for row in rows
        if row.get("segment") == "NSE_EQ"
        and row.get("trading_symbol")
        and row.get("instrument_key")
    }
    try:
        INSTRUMENT_CACHE.parent.mkdir(parents=True, exist_ok=True)
        INSTRUMENT_CACHE.write_text(json.dumps(mapping))
    except Exception as e:
        log.debug("instrument cache write skipped: %s", e)

    log.info("Upstox instrument master: %d NSE_EQ symbols", len(mapping))
    return mapping


def instrument_key_for(ticker: str, _map: dict[str, str] | None = None) -> str | None:
    """Resolve one of our universe tickers to an Upstox instrument key.

    `config.UNIVERSE` is looked up at call time, not bound at import: the
    Holdings tab rewrites it via `save_overrides` + reload, and a stale
    module-level reference would silently miss newly-added names.
    """
    meta = config.UNIVERSE.get(ticker.upper())
    if not meta:
        return None
    if meta.get("upstox_key"):                       # explicit override
        return meta["upstox_key"]
    if meta.get("isin"):
        return f"NSE_EQ|{meta['isin']}"
    nse = meta.get("nse")
    if not nse:
        return None
    lookup = instrument_map() if _map is None else _map
    return lookup.get(nse.upper())


def universe_instrument_keys() -> dict[str, str]:
    """{our ticker -> instrument key} for every universe name we can resolve.

    Unlisted / pre-IPO watchlist names simply won't resolve; that's expected
    and they're skipped rather than logged as errors.
    """
    lookup = instrument_map()
    out: dict[str, str] = {}
    for tk in config.UNIVERSE:
        key = instrument_key_for(tk, _map=lookup)
        if key:
            out[tk] = key
    missing = [t for t in config.UNIVERSE if t not in out]
    if missing:
        log.info("no Upstox instrument for %d name(s): %s",
                 len(missing), ", ".join(sorted(missing)))
    return out


# --------------------------------------------------------------------------
# market quotes
# --------------------------------------------------------------------------

def _chunks(seq: Sequence, n: int) -> Iterable[Sequence]:
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def quotes(instrument_keys: Sequence[str]) -> dict[str, dict]:
    """Full OHLC quotes. Response is keyed by Upstox's own
    `EXCHANGE:SYMBOL` label, so each entry carries `instrument_token`
    to map back."""
    if not instrument_keys:
        return {}
    merged: dict[str, dict] = {}
    for chunk in _chunks(list(instrument_keys), QUOTE_CHUNK):
        data = _get("/market-quote/quotes",
                    {"instrument_key": ",".join(chunk)}) or {}
        merged.update(data)
    return merged


def ltp(instrument_keys: Sequence[str]) -> dict[str, float]:
    """Last traded price only — cheaper than a full quote."""
    if not instrument_keys:
        return {}
    out: dict[str, float] = {}
    for chunk in _chunks(list(instrument_keys), QUOTE_CHUNK):
        data = _get("/market-quote/ltp",
                    {"instrument_key": ",".join(chunk)}) or {}
        for entry in data.values():
            key = entry.get("instrument_token")
            if key:
                out[key] = _f(entry.get("last_price"))
    return out


def quote_map() -> dict[str, dict]:
    """Live quotes for the universe, re-keyed by *our* ticker.

    Note on `close`: Upstox's `ohlc.close` is the **previous** session's
    close while the market is open, so we report `last_price` as the
    current close and keep `ohlc.close` separately as `prev_close`.
    """
    keys = universe_instrument_keys()
    if not keys:
        return {}
    by_key = {v: k for k, v in keys.items()}
    raw = quotes(list(keys.values()))

    out: dict[str, dict] = {}
    for entry in raw.values():
        ticker = by_key.get(entry.get("instrument_token"))
        if not ticker:
            continue
        ohlc = entry.get("ohlc") or {}
        last = _f(entry.get("last_price"))
        prev = _f(ohlc.get("close"))
        out[ticker] = {
            "ticker": ticker,
            "symbol": entry.get("symbol"),
            "instrument_key": entry.get("instrument_token"),
            "open": _f(ohlc.get("open")),
            "high": _f(ohlc.get("high")),
            "low": _f(ohlc.get("low")),
            "close": last,
            "prev_close": prev,
            "pct_change": round((last - prev) / prev * 100, 2) if prev else 0.0,
            "volume": int(_f(entry.get("volume"))),
            "timestamp": entry.get("timestamp"),
        }
    return out


def intraday_move(ticker: str) -> dict | None:
    """Same shape `price_fetcher.intraday_move` returns, sourced from Upstox."""
    q = quote_map().get(ticker.upper())
    if not q:
        return None
    return {
        "ticker": ticker.upper(),
        "source": "upstox",
        "asof": (q.get("timestamp") or "")[:10] or _today_ist(),
        "open": q["open"],
        "high": q["high"],
        "low": q["low"],
        "close": q["close"],
        "prev_close": q["prev_close"],
        "pct_change": q["pct_change"],
        "volume": q["volume"],
    }


def _today_ist() -> str:
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d")


def snapshot_universe() -> int:
    """Write one row per resolvable universe name into `prices`.

    Same return contract as `price_fetcher.snapshot_universe`: number of
    rows written, 0 if the source could not be used.
    """
    qm = quote_map()
    rows = 0
    for ticker, q in qm.items():
        asof = (q.get("timestamp") or "")[:10] or _today_ist()
        try:
            upsert_price(
                ticker=ticker,
                asof=asof,
                o=q["open"], h=q["high"], l=q["low"],
                c=q["close"], v=q["volume"],
            )
            rows += 1
        except Exception as e:
            log.debug("upstox price write skip %s: %s", ticker, e)
    log.info("upstox price snapshot wrote %d rows", rows)
    return rows


# --------------------------------------------------------------------------
# holdings -> universe suggestions
# --------------------------------------------------------------------------

def suggest_universe_overrides() -> dict[str, dict]:
    """Turn real demat holdings into `config.save_overrides` shaped metadata.

    Only returns names **not** already in the universe, so accepting the
    suggestion never clobbers a hand-tuned entry (sector, aliases, BSE code).
    The dashboard shows these for review — nothing is written automatically.
    """
    existing_nse = {
        (m.get("nse") or "").upper() for m in config.UNIVERSE.values()
    }
    out: dict[str, dict] = {}
    for h in holdings():
        sym = (h.get("symbol") or "").upper()
        if not sym or sym in existing_nse or sym in config.UNIVERSE:
            continue
        out[sym] = {
            "name": h.get("company") or sym,
            "sector": "",                       # Upstox doesn't classify
            "nse": sym,
            "bse_code": None,
            "yahoo": f"{sym}.NS",
            "isin": h.get("isin"),
            "upstox_key": h.get("instrument_key"),
            "aliases": [h.get("company")] if h.get("company") else [],
        }
    return out


def health() -> dict:
    """One-shot connectivity check for the dashboard's status strip.

    Never raises — the dashboard renders whatever came back.
    """
    out: dict[str, Any] = {"token": token_info()}
    if not is_configured():
        out["ok"] = False
        out["message"] = f"{_ENV_VAR} not set"
        return out
    try:
        p = profile()
        out.update(
            ok=True,
            user_name=p.get("user_name"),
            email=p.get("email"),
            broker=p.get("broker"),
            exchanges=p.get("exchanges"),
            is_active=p.get("is_active"),
        )
    except UpstoxError as e:
        out.update(ok=False, message=str(e))
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    print("token:", json.dumps(token_info(), indent=2))
    print("health:", json.dumps(health(), indent=2, default=str))
    if is_configured():
        print("holdings:", json.dumps(holdings(), indent=2))
        print("summary:", json.dumps(portfolio_summary(), indent=2))
        print("snapshot rows:", snapshot_universe())
