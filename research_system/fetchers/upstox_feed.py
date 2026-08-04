"""Upstox Market Data Feed V3 — live websocket streamer.

A single background thread owns the socket and writes normalised ticks into
an in-process store; readers (the Streamlit UI, the scheduler, alerting) take
snapshots off it. Streamlit re-runs its script on every interaction, so the
streamer is deliberately a module-level singleton — the connection survives
reruns instead of being torn down and re-handshaked several times a second.

Protocol (as implemented by the official SDK 2.28.0):

  * connect to ``wss://api.upstox.com/v3/feed/market-data-feed`` sending
    ``Authorization: Bearer <token>``. Older integrations first call
    ``/v3/feed/market-data-feed/authorize`` for a pre-signed URI; we fall
    back to that automatically if the direct handshake is rejected.
  * requests are JSON sent as **binary** frames::

        {"guid": "<uuid>", "method": "sub",
         "data": {"instrumentKeys": ["NSE_INDEX|Nifty 50"], "mode": "full"}}

    methods: ``sub`` | ``unsub`` | ``change_mode``
  * responses are protobuf ``FeedResponse`` frames, decoded by
    :mod:`.upstox_proto` (no protobuf runtime needed).

Modes and their documented instrument caps:

  =============== ====== =================================================
  mode            cap    payload
  =============== ====== =================================================
  ltpc            5000   last price, last traded qty/time, prev close
  full            2000   LTPC + 5-level depth + day/intraday OHLC + OI/IV
  option_greeks   3000   LTPC + best bid/ask + greeks + OI/IV
  full_d30          50   as ``full`` but 30-level market depth
  =============== ====== =================================================

The caps are enforced client-side so an over-subscription surfaces as a
clear error here rather than an opaque socket close from the server.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from typing import Any, Callable, Iterable, Sequence

from .upstox_proto import DecodeError, decode_feed_response
from .upstox_rest import UpstoxAuthError, UpstoxError, _get, access_token

log = logging.getLogger("fetchers.upstox_feed")

WS_URL = "wss://api.upstox.com/v3/feed/market-data-feed"
AUTHORIZE_PATH = "/v3/feed/market-data-feed/authorize"

MODE_LTPC = "ltpc"
MODE_FULL = "full"
MODE_GREEKS = "option_greeks"
MODE_D30 = "full_d30"

MODE_LIMITS = {
    MODE_LTPC: 5000,
    MODE_FULL: 2000,
    MODE_GREEKS: 3000,
    MODE_D30: 50,
}
VALID_MODES = tuple(MODE_LIMITS)


class FeedNotAvailable(RuntimeError):
    """`websocket-client` is not installed, or no token is configured."""


# --------------------------- normalisation ------------------------------
def _pct(ltp: float | None, close: float | None) -> float | None:
    if ltp is None or not close:
        return None
    return (ltp - close) / close * 100.0


def normalise_feed(key: str, feed: dict[str, Any]) -> dict[str, Any]:
    """Flatten one protobuf ``Feed`` into a single tick dict.

    The four feed shapes (``ltpc``, ``fullFeed.marketFF``,
    ``fullFeed.indexFF``, ``firstLevelWithGreeks``) all collapse onto the
    same keys, so the UI never has to branch on subscription mode.
    """
    out: dict[str, Any] = {
        "instrument_key": key,
        "mode": feed.get("requestMode"),
        "received_at": time.time(),
    }

    ltpc: dict[str, Any] = {}
    full = feed.get("fullFeed") or {}
    market_ff = full.get("marketFF") or {}
    index_ff = full.get("indexFF") or {}
    greeks_ff = feed.get("firstLevelWithGreeks") or {}

    if feed.get("ltpc"):
        ltpc = feed["ltpc"]
    elif market_ff.get("ltpc"):
        ltpc = market_ff["ltpc"]
    elif index_ff.get("ltpc"):
        ltpc = index_ff["ltpc"]
    elif greeks_ff.get("ltpc"):
        ltpc = greeks_ff["ltpc"]

    ltp = ltpc.get("ltp")
    close = ltpc.get("cp")
    out["ltp"] = ltp
    out["prev_close"] = close
    out["ltt"] = ltpc.get("ltt")
    out["ltq"] = ltpc.get("ltq")
    if ltp is not None and close:
        out["change"] = ltp - close
        out["pct_change"] = _pct(ltp, close)

    # OHLC bars: day bar is interval "1d"; intraday bars are I1 / I30.
    bars = (market_ff.get("marketOHLC") or index_ff.get("marketOHLC") or {}).get("ohlc") or []
    if bars:
        out["ohlc"] = {b.get("interval", "?"): b for b in bars}
        day = out["ohlc"].get("1d") or bars[-1]
        out["open"] = day.get("open")
        out["high"] = day.get("high")
        out["low"] = day.get("low")
        out["volume"] = day.get("vol")

    for src in (market_ff, greeks_ff):
        for f in ("atp", "vtt", "oi", "iv", "tbq", "tsq"):
            if src.get(f) is not None and out.get(f) is None:
                out[f] = src[f]

    greeks = market_ff.get("optionGreeks") or greeks_ff.get("optionGreeks")
    if greeks:
        out["greeks"] = greeks

    depth = (market_ff.get("marketLevel") or {}).get("bidAskQuote")
    if depth:
        out["depth"] = depth
        out["bid"] = depth[0].get("bidP")
        out["ask"] = depth[0].get("askP")
        out["bid_qty"] = depth[0].get("bidQ")
        out["ask_qty"] = depth[0].get("askQ")
    elif greeks_ff.get("firstDepth"):
        d = greeks_ff["firstDepth"]
        out["depth"] = [d]
        out["bid"] = d.get("bidP")
        out["ask"] = d.get("askP")
        out["bid_qty"] = d.get("bidQ")
        out["ask_qty"] = d.get("askQ")

    return out


# --------------------------- the streamer -------------------------------
class MarketFeed:
    """Thread-safe live feed. One instance per process (see :func:`get_feed`)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._ticks: dict[str, dict[str, Any]] = {}
        self._segment_status: dict[str, str] = {}
        self._subs: dict[str, str] = {}          # instrument_key -> mode
        self._ws = None
        self._thread: threading.Thread | None = None
        self._want_running = False
        self._connected = False
        self._last_msg_at: float | None = None
        self._last_error: str | None = None
        self._msg_count = 0
        self._reconnects = 0
        self._backoff = 1.0
        self._listeners: list[Callable[[dict[str, dict]], None]] = []

    # -- status ----------------------------------------------------------
    @property
    def connected(self) -> bool:
        return self._connected

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "connected": self._connected,
                "running": self._want_running,
                "subscriptions": len(self._subs),
                "instruments_with_data": len(self._ticks),
                "messages": self._msg_count,
                "reconnects": self._reconnects,
                "last_message_at": self._last_msg_at,
                "last_error": self._last_error,
                "segment_status": dict(self._segment_status),
            }

    # -- reads -----------------------------------------------------------
    def snapshot(self, keys: Iterable[str] | None = None) -> dict[str, dict]:
        """Copy of the current ticks, optionally restricted to `keys`."""
        with self._lock:
            if keys is None:
                return {k: dict(v) for k, v in self._ticks.items()}
            return {k: dict(self._ticks[k]) for k in keys if k in self._ticks}

    def get(self, key: str) -> dict | None:
        with self._lock:
            t = self._ticks.get(key)
            return dict(t) if t else None

    def segment_status(self) -> dict[str, str]:
        with self._lock:
            return dict(self._segment_status)

    def on_ticks(self, fn: Callable[[dict[str, dict]], None]) -> None:
        """Register a callback fired with each batch of normalised ticks."""
        with self._lock:
            self._listeners.append(fn)

    # -- lifecycle -------------------------------------------------------
    def start(self) -> None:
        """Open the socket if it is not already up. Returns immediately."""
        if not access_token():
            raise FeedNotAvailable(
                "UPSTOX_ACCESS_TOKEN is not set — cannot open the market feed."
            )
        try:
            import websocket  # noqa: F401
        except ImportError as e:  # pragma: no cover - depends on install
            raise FeedNotAvailable(
                "websocket-client is required for the live feed. "
                "pip install websocket-client"
            ) from e

        with self._lock:
            if self._want_running and self._thread and self._thread.is_alive():
                return
            self._want_running = True
            self._thread = threading.Thread(
                target=self._run_forever, name="upstox-market-feed", daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._want_running = False
            ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception as e:  # pragma: no cover - best effort teardown
                log.debug("error closing socket: %s", e)

    def wait_until_connected(self, timeout: float = 10.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._connected:
                return True
            if self._last_error and "token" in (self._last_error or "").lower():
                return False
            time.sleep(0.1)
        return self._connected

    # -- subscriptions ---------------------------------------------------
    def subscribe(self, instrument_keys: Sequence[str], mode: str = MODE_FULL) -> None:
        """Add instruments at `mode`. Safe to call before the socket is open."""
        if mode not in VALID_MODES:
            raise ValueError(f"invalid mode {mode!r}; expected one of {VALID_MODES}")
        keys = [k for k in dict.fromkeys(instrument_keys) if k]
        if not keys:
            return

        with self._lock:
            planned = {k: m for k, m in self._subs.items() if k not in keys}
            for k in keys:
                planned[k] = mode
            count = sum(1 for m in planned.values() if m == mode)
            if count > MODE_LIMITS[mode]:
                raise ValueError(
                    f"subscribing {count} instruments in mode {mode!r} exceeds the "
                    f"Upstox limit of {MODE_LIMITS[mode]}"
                )
            self._subs = planned

        self._send({"instrumentKeys": keys, "mode": mode}, "sub")

    def unsubscribe(self, instrument_keys: Sequence[str]) -> None:
        keys = [k for k in dict.fromkeys(instrument_keys) if k]
        if not keys:
            return
        with self._lock:
            for k in keys:
                self._subs.pop(k, None)
                self._ticks.pop(k, None)
        self._send({"instrumentKeys": keys}, "unsub")

    def change_mode(self, instrument_keys: Sequence[str], mode: str) -> None:
        if mode not in VALID_MODES:
            raise ValueError(f"invalid mode {mode!r}; expected one of {VALID_MODES}")
        keys = [k for k in dict.fromkeys(instrument_keys) if k]
        if not keys:
            return
        with self._lock:
            for k in keys:
                self._subs[k] = mode
        self._send({"instrumentKeys": keys, "mode": mode}, "change_mode")

    def subscriptions(self) -> dict[str, str]:
        with self._lock:
            return dict(self._subs)

    def _send(self, data: dict[str, Any], method: str) -> None:
        """Emit a request frame. No-op while disconnected — `_resubscribe`
        replays the full subscription set once the socket comes back."""
        with self._lock:
            ws = self._ws
            connected = self._connected
        if not (ws and connected):
            return
        payload = json.dumps(
            {"guid": str(uuid.uuid4()), "method": method, "data": data}
        ).encode("utf-8")
        try:
            import websocket

            ws.send(payload, opcode=websocket.ABNF.OPCODE_BINARY)
        except Exception as e:
            log.warning("failed to send %s request: %s", method, e)

    def _resubscribe(self) -> None:
        """Replay every subscription, grouped by mode, after (re)connect."""
        with self._lock:
            by_mode: dict[str, list[str]] = {}
            for key, mode in self._subs.items():
                by_mode.setdefault(mode, []).append(key)
        for mode, keys in by_mode.items():
            self._send({"instrumentKeys": keys, "mode": mode}, "sub")

    # -- socket ----------------------------------------------------------
    def _ws_url(self) -> str:
        """Direct URL, falling back to the pre-signed authorize redirect."""
        try:
            body = _get(AUTHORIZE_PATH)
            uri = (body.get("data") or {}).get("authorized_redirect_uri")
            if uri:
                return uri
        except UpstoxAuthError:
            raise
        except UpstoxError as e:
            log.info("authorize endpoint unavailable (%s) — connecting directly", e)
        return WS_URL

    def _run_forever(self) -> None:
        import websocket

        self._backoff = 1.0
        while self._want_running:
            try:
                url = self._ws_url()
            except UpstoxAuthError as e:
                with self._lock:
                    self._last_error = str(e)
                    self._want_running = False
                log.error("market feed auth failed: %s", e)
                return

            headers = {"Authorization": f"Bearer {access_token()}"}
            # NOTE: the official SDK passes cert_reqs=CERT_NONE here. We keep
            # certificate verification on — there is no reason to accept an
            # unauthenticated peer for a feed carrying a bearer token.
            ws = websocket.WebSocketApp(
                url,
                header=headers,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
            )
            with self._lock:
                self._ws = ws

            try:
                ws.run_forever(ping_interval=30, ping_timeout=10)
            except Exception as e:  # pragma: no cover - transport level
                log.warning("feed transport error: %s", e)
                with self._lock:
                    self._last_error = str(e)

            unauthorised = False
            with self._lock:
                self._connected = False
                if not self._want_running:
                    break
                # A 401 will repeat on every retry — stop instead of spinning.
                unauthorised = "401" in (self._last_error or "")
                if unauthorised:
                    self._want_running = False
                else:
                    self._reconnects += 1
            if unauthorised:
                log.error("market feed stopped: token rejected (401)")
                return

            log.info("market feed disconnected — reconnecting in %.0fs", self._backoff)
            slept = 0.0
            while slept < self._backoff and self._want_running:
                time.sleep(0.25)
                slept += 0.25
            self._backoff = min(self._backoff * 2, 30.0)

    def _on_open(self, ws) -> None:
        with self._lock:
            self._connected = True
            self._last_error = None
            # A healthy connect clears the penalty, so a drop hours from now
            # retries promptly instead of inheriting an old 30s backoff.
            self._backoff = 1.0
        log.info("market feed connected")
        self._resubscribe()

    def _on_message(self, ws, message) -> None:
        if isinstance(message, str):
            message = message.encode("utf-8")
        try:
            decoded = decode_feed_response(message)
        except DecodeError as e:
            log.warning("undecodable feed frame (%d bytes): %s", len(message), e)
            return

        batch: dict[str, dict] = {}
        for key, feed in (decoded.get("feeds") or {}).items():
            batch[key] = normalise_feed(key, feed)

        seg = (decoded.get("marketInfo") or {}).get("segmentStatus") or {}

        with self._lock:
            self._msg_count += 1
            self._last_msg_at = time.time()
            for key, tick in batch.items():
                # Frames are partial: a tick may omit fields present earlier
                # (e.g. depth on a quote-only update), so merge rather than
                # replace, dropping keys whose value is None.
                fresh = {k: v for k, v in tick.items() if v is not None}
                prev = self._ticks.get(key)
                if prev:
                    prev.update(fresh)
                else:
                    self._ticks[key] = fresh
            if seg:
                self._segment_status.update(seg)
            listeners = list(self._listeners)

        for fn in listeners:
            try:
                fn(batch)
            except Exception as e:
                log.warning("tick listener raised: %s", e)

    def _on_error(self, ws, error) -> None:
        with self._lock:
            self._last_error = str(error)
        log.warning("market feed error: %s", error)

    def _on_close(self, ws, status_code, msg) -> None:
        with self._lock:
            self._connected = False
        log.info("market feed closed (%s %s)", status_code, msg)


# --------------------------- process singleton --------------------------
_feed: MarketFeed | None = None
_feed_lock = threading.Lock()


def get_feed() -> MarketFeed:
    """The process-wide :class:`MarketFeed`, created on first use."""
    global _feed
    with _feed_lock:
        if _feed is None:
            _feed = MarketFeed()
        return _feed


def reset_feed() -> None:
    """Tear down the singleton — used when the access token changes."""
    global _feed
    with _feed_lock:
        if _feed is not None:
            _feed.stop()
        _feed = None


# --------------------------- CLI ----------------------------------------
def _main() -> int:
    """Smoke-test a token and the socket:

        python -m research_system.fetchers.upstox_feed
        python -m research_system.fetchers.upstox_feed --mode ltpc \
            "NSE_INDEX|Nifty 50" "NSE_INDEX|Nifty Bank"
    """
    import argparse
    from datetime import datetime

    ap = argparse.ArgumentParser(description="Stream Upstox live ticks to stdout.")
    ap.add_argument("instruments", nargs="*",
                    default=["NSE_INDEX|Nifty 50", "NSE_INDEX|Nifty Bank"],
                    help="instrument keys (default: Nifty 50 + Nifty Bank)")
    ap.add_argument("--mode", default=MODE_FULL, choices=VALID_MODES)
    ap.add_argument("--seconds", type=int, default=30, help="how long to stream")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    feed = get_feed()
    try:
        feed.start()
    except FeedNotAvailable as e:
        print(f"cannot start: {e}")
        return 2

    if not feed.wait_until_connected(timeout=15):
        print(f"failed to connect. last error: {feed.status()['last_error']}")
        return 1

    feed.subscribe(args.instruments, args.mode)
    print(f"connected — streaming {len(args.instruments)} instruments "
          f"in {args.mode!r} for {args.seconds}s\n")

    deadline = time.time() + args.seconds
    while time.time() < deadline:
        time.sleep(2)
        for key, t in sorted(feed.snapshot().items()):
            ltt = (datetime.fromtimestamp(t["ltt"] / 1000).strftime("%H:%M:%S")
                   if t.get("ltt") else "—")
            ltp = t.get("ltp")
            pct = t.get("pct_change")
            ltp_s = f"{ltp:,.2f}" if ltp is not None else "—"
            pct_s = f"{pct:+.2f}%" if pct is not None else "—"
            print(f"{key:<34} {ltp_s:>12} {pct_s:>8}  "
                  f"ltt={ltt}  vol={t.get('volume') or 0:,}")
        print("-" * 78)

    feed.stop()
    s = feed.status()
    print(f"\n{s['messages']:,} frames, {s['reconnects']} reconnects, "
          f"{s['instruments_with_data']} instruments with data")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
