"""Live Markets page — Upstox websocket ticks for India, Yahoo for the world.

Rendered by :func:`render` into a tab of the main dashboard.

Streamlit re-runs the whole script on every widget interaction, which is a
poor fit for a streaming socket. Two things make it work:

  * the feed is a process-level singleton (:func:`upstox_feed.get_feed`), so
    reruns reattach to the live connection instead of re-handshaking;
  * the price boards are ``st.fragment``s with ``run_every``, so only the
    tiles repaint on each tick cycle — the rest of the page (and the rest of
    the dashboard's tabs) stays put.

What is live vs. delayed is labelled in the UI rather than assumed: Upstox
ticks are real time, the global board is Yahoo-sourced and mostly 15-minute
delayed for cash indices.
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st

from .fetchers import global_feed, upstox_feed, upstox_rest, upstox_universe

log = logging.getLogger("live_markets")
IST = ZoneInfo("Asia/Kolkata")

CSS = """
<style>
  .mkt-grid   { display:flex; flex-wrap:wrap; gap:10px; margin:6px 0 14px 0; }
  .mkt-tile   { background:#161b22; border:1px solid #30363d; border-radius:8px;
                padding:10px 14px; min-width:168px; flex:1 1 168px; }
  .mkt-name   { color:#94a3b8; font-size:11px; text-transform:uppercase;
                letter-spacing:.04em; white-space:nowrap; overflow:hidden;
                text-overflow:ellipsis; }
  .mkt-price  { color:#f1f5f9; font-size:21px; font-weight:600;
                font-family:ui-monospace, monospace; margin-top:2px; }
  .mkt-chg    { font-size:12px; font-family:ui-monospace, monospace; margin-top:1px; }
  .mkt-up     { color:#22c55e; }
  .mkt-down   { color:#ef4444; }
  .mkt-flat   { color:#9ca3af; }
  .mkt-sub    { color:#64748b; font-size:10px; margin-top:3px; }
  .dot        { display:inline-block; width:8px; height:8px; border-radius:50%;
                margin-right:6px; vertical-align:middle; }
  .dot-live   { background:#22c55e; box-shadow:0 0 6px #22c55e; }
  .dot-idle   { background:#eab308; }
  .dot-dead   { background:#ef4444; }
  .status-bar { background:#161b22; border:1px solid #30363d; border-radius:8px;
                padding:8px 14px; font-size:12px; color:#94a3b8; margin-bottom:12px; }
</style>
"""

_MODE_HELP = {
    upstox_feed.MODE_LTPC: "Last price only — lightest, up to 5,000 instruments",
    upstox_feed.MODE_FULL: "Full quote: 5-level depth, OHLC, OI, IV — up to 2,000",
    upstox_feed.MODE_GREEKS: "Best bid/ask + greeks + IV — up to 3,000",
    upstox_feed.MODE_D30: "30-level depth — up to 50 instruments",
}


# --------------------------- helpers ------------------------------------
def _fmt(v, dp: int = 2) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):,.{dp}f}"
    except (TypeError, ValueError):
        return str(v)


def _tile(name: str, last, pct, change=None, sub: str = "") -> str:
    cls = "mkt-flat"
    arrow = ""
    if pct is not None:
        try:
            cls = "mkt-up" if float(pct) > 0 else "mkt-down" if float(pct) < 0 else "mkt-flat"
            arrow = "▲ " if float(pct) > 0 else "▼ " if float(pct) < 0 else ""
        except (TypeError, ValueError):
            pass
    chg = ""
    if pct is not None:
        chg = f"{arrow}{_fmt(change)} ({float(pct):+.2f}%)" if change is not None \
              else f"{arrow}{float(pct):+.2f}%"
    return (
        f"<div class='mkt-tile'><div class='mkt-name'>{name}</div>"
        f"<div class='mkt-price'>{_fmt(last)}</div>"
        f"<div class='mkt-chg {cls}'>{chg or '—'}</div>"
        f"{f'<div class=mkt-sub>{sub}</div>' if sub else ''}</div>"
    )


def _grid(tiles: list[str]) -> str:
    return f"<div class='mkt-grid'>{''.join(tiles)}</div>"


def _ticks_to_rows(feed, labels: dict[str, str]) -> list[dict]:
    """Build a display table from the current tick store."""
    snap = feed.snapshot(labels.keys())
    rows = []
    for key, label in labels.items():
        t = snap.get(key)
        if not t:
            rows.append({"Instrument": label, "LTP": None, "Chg %": None})
            continue
        row = {
            "Instrument": label,
            "LTP": t.get("ltp"),
            "Chg": t.get("change"),
            "Chg %": t.get("pct_change"),
            "Open": t.get("open"),
            "High": t.get("high"),
            "Low": t.get("low"),
            "Prev Close": t.get("prev_close"),
            "Volume": t.get("volume"),
            "Bid": t.get("bid"),
            "Ask": t.get("ask"),
        }
        if t.get("oi") is not None:
            row["OI"] = t.get("oi")
        if t.get("iv") is not None:
            row["IV"] = t.get("iv")
        g = t.get("greeks") or {}
        if g:
            row.update({
                "Delta": g.get("delta"), "Gamma": g.get("gamma"),
                "Theta": g.get("theta"), "Vega": g.get("vega"),
            })
        rows.append(row)
    return rows


def _style_table(rows: list[dict]):
    import pandas as pd

    if not rows:
        st.caption("No instruments selected.")
        return
    df = pd.DataFrame(rows)
    num_cols = [c for c in df.columns if c != "Instrument"]
    for c in num_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    def colour(v):
        if v is None or (isinstance(v, float) and v != v):
            return ""
        return "color:#22c55e" if v > 0 else "color:#ef4444" if v < 0 else ""

    styled = df.style.format(
        {c: "{:,.2f}" for c in num_cols if c not in ("Volume", "OI")}
        | {c: "{:,.0f}" for c in ("Volume", "OI") if c in df.columns},
        na_rep="—",
    )
    for c in ("Chg", "Chg %"):
        if c in df.columns:
            styled = styled.map(colour, subset=[c])
    st.dataframe(styled, width="stretch", hide_index=True)


# --------------------------- cached lookups -----------------------------
@st.cache_data(ttl=3600, show_spinner="Loading Upstox instrument master…")
def _cached_indices() -> dict[str, str]:
    return upstox_universe.headline_indices()


@st.cache_data(ttl=3600, show_spinner="Loading futures contracts…")
def _cached_futures(exchange: str, underlyings: tuple[str, ...] | None) -> list[dict]:
    rows = upstox_universe.near_month_futures(
        exchange, list(underlyings) if underlyings else None
    )
    # _expiry_date is a date object — drop it so the result stays cacheable.
    return [{k: v for k, v in r.items() if k != "_expiry_date"} for r in rows]


@st.cache_data(ttl=3600, show_spinner="Resolving portfolio instruments…")
def _cached_portfolio_keys() -> dict[str, str]:
    return upstox_universe.portfolio_keys()


@st.cache_data(ttl=3600, show_spinner="Loading option contracts…")
def _cached_expiries(exchange: str, underlying: str) -> list[str]:
    rows = upstox_universe.options(exchange, [underlying])
    return sorted({
        r["_expiry_date"].isoformat() for r in rows if r.get("_expiry_date")
    })


@st.cache_data(ttl=900, show_spinner="Loading strikes…")
def _cached_strikes(exchange: str, underlying: str, expiry: str,
                    spot: float | None, around: int) -> list[dict]:
    rows = upstox_universe.option_strikes(
        underlying, expiry, exchange, spot=spot, around=around
    )
    return [{k: v for k, v in r.items() if k != "_expiry_date"} for r in rows]


# --------------------------- page ---------------------------------------
def render() -> None:
    st.markdown(CSS, unsafe_allow_html=True)

    if not upstox_rest.is_configured():
        _render_setup_help()
        return

    feed = upstox_feed.get_feed()

    with st.expander("Feed controls", expanded=not feed.connected):
        c1, c2, c3, c4 = st.columns([1, 1, 1, 2])
        with c1:
            if st.button("Connect", width="stretch", type="primary"):
                try:
                    feed.start()
                    feed.wait_until_connected(timeout=8)
                except upstox_feed.FeedNotAvailable as e:
                    st.error(str(e))
        with c2:
            if st.button("Disconnect", width="stretch"):
                feed.stop()
        with c3:
            refresh = st.selectbox("Refresh", [1, 2, 5, 10], index=1,
                                   format_func=lambda s: f"{s}s")
        with c4:
            mode = st.selectbox(
                "Subscription mode", list(upstox_feed.VALID_MODES),
                index=list(upstox_feed.VALID_MODES).index(upstox_feed.MODE_FULL),
                help="\n".join(f"{k}: {v}" for k, v in _MODE_HELP.items()),
            )
        st.caption(_MODE_HELP[mode])

    # Auto-connect on first visit so the board is live without a click.
    if not feed.connected and not st.session_state.get("_mkt_autostart_done"):
        st.session_state["_mkt_autostart_done"] = True
        try:
            feed.start()
            feed.wait_until_connected(timeout=8)
        except upstox_feed.FeedNotAvailable as e:
            st.warning(str(e))

    _render_status(feed)

    sub_tabs = st.tabs(["Indices", "Portfolio & Equities", "Futures",
                        "Options", "Global"])

    with sub_tabs[0]:
        _render_indices(feed, mode, refresh)
    with sub_tabs[1]:
        _render_equities(feed, mode, refresh)
    with sub_tabs[2]:
        _render_futures(feed, mode, refresh)
    with sub_tabs[3]:
        _render_options(feed, refresh)
    with sub_tabs[4]:
        _render_global(refresh)


def _render_setup_help() -> None:
    st.subheader("Live market data — not configured")
    st.info(
        "This page streams real-time Indian market data from the "
        "**Upstox Market Data Feed V3** websocket. It needs an access token."
    )
    st.markdown(
        """
**Local run** — add to `.env`:

```
UPSTOX_ACCESS_TOKEN=your-token-here
```

**Streamlit Cloud** — App ▸ Settings ▸ Secrets:

```toml
UPSTOX_ACCESS_TOKEN = "your-token-here"
```

Get a token from the [Upstox developer console](https://account.upstox.com/developer/apps)
via the OAuth login flow.

> Upstox access tokens expire **daily at 03:30 IST** — this page will show an
> auth error and need a fresh token each trading day.
"""
    )


def _render_status(feed) -> None:
    s = feed.status()
    if s["connected"]:
        dot, label = "dot-live", "LIVE"
    elif s["running"]:
        dot, label = "dot-idle", "CONNECTING"
    else:
        dot, label = "dot-dead", "DISCONNECTED"

    last = "—"
    if s["last_message_at"]:
        last = datetime.fromtimestamp(s["last_message_at"], IST).strftime("%H:%M:%S")

    seg = s.get("segment_status") or {}
    seg_txt = "  ".join(f"{k}: {v}" for k, v in sorted(seg.items())[:6]) or "—"

    st.markdown(
        f"<div class='status-bar'><span class='dot {dot}'></span>"
        f"<b>{label}</b> &nbsp;·&nbsp; {s['subscriptions']} subscribed &nbsp;·&nbsp; "
        f"{s['instruments_with_data']} with data &nbsp;·&nbsp; "
        f"{s['messages']:,} frames &nbsp;·&nbsp; last tick {last} IST"
        f"<br><span style='color:#64748b'>Segments — {seg_txt}</span></div>",
        unsafe_allow_html=True,
    )
    if s["last_error"]:
        st.warning(f"Feed error: {s['last_error']}")


def _render_indices(feed, mode: str, refresh: int) -> None:
    try:
        idx = _cached_indices()
    except Exception as e:
        st.error(f"Could not load index list: {e}")
        return

    chosen = st.multiselect("Indices", list(idx), default=list(idx), key="mkt_idx")
    keys = {idx[c]: c for c in chosen}
    if keys and feed.connected:
        _subscribe(feed, list(keys), mode)

    @st.fragment(run_every=refresh)
    def board():
        snap = feed.snapshot(keys.keys())
        tiles = []
        for key, label in keys.items():
            t = snap.get(key) or {}
            tiles.append(_tile(
                label, t.get("ltp"), t.get("pct_change"), t.get("change"),
                sub=f"O {_fmt(t.get('open'))}  H {_fmt(t.get('high'))}  "
                    f"L {_fmt(t.get('low'))}" if t.get("open") else "",
            ))
        st.markdown(_grid(tiles), unsafe_allow_html=True)
        _style_table(_ticks_to_rows(feed, keys))

    board()


def _render_equities(feed, mode: str, refresh: int) -> None:
    try:
        pf = _cached_portfolio_keys()
    except Exception as e:
        st.error(f"Could not resolve portfolio instruments: {e}")
        return

    if not pf:
        st.caption("No portfolio tickers resolved against the NSE master.")
        return

    chosen = st.multiselect("Tickers", list(pf), default=list(pf), key="mkt_eq")
    keys = {pf[c]: c for c in chosen}
    if keys and feed.connected:
        _subscribe(feed, list(keys), mode)

    @st.fragment(run_every=refresh)
    def board():
        snap = feed.snapshot(keys.keys())
        tiles = [
            _tile(label, (snap.get(key) or {}).get("ltp"),
                  (snap.get(key) or {}).get("pct_change"),
                  (snap.get(key) or {}).get("change"))
            for key, label in keys.items()
        ]
        st.markdown(_grid(tiles), unsafe_allow_html=True)
        _style_table(_ticks_to_rows(feed, keys))

    board()


def _render_futures(feed, mode: str, refresh: int) -> None:
    c1, c2 = st.columns([1, 2])
    with c1:
        exch = st.selectbox("Exchange", ["NSE", "MCX", "BSE"], key="mkt_fut_exch")
    default_under = (upstox_universe.DEFAULT_COMMODITIES if exch == "MCX"
                     else upstox_universe.DEFAULT_FO_UNDERLYINGS)
    with c2:
        unders = st.multiselect("Underlyings", default_under, default=default_under,
                                key="mkt_fut_und",
                                help="Near-month contract per underlying.")

    try:
        rows = _cached_futures(exch, tuple(unders) if unders else None)
    except Exception as e:
        st.error(f"Could not load futures: {e}")
        return

    if not rows:
        st.caption("No unexpired futures found for that selection.")
        return

    labels = upstox_universe.label_map(rows)
    if feed.connected:
        _subscribe(feed, list(labels), mode)

    @st.fragment(run_every=refresh)
    def board():
        snap = feed.snapshot(labels.keys())
        tiles = [
            _tile(lbl, (snap.get(k) or {}).get("ltp"),
                  (snap.get(k) or {}).get("pct_change"),
                  (snap.get(k) or {}).get("change"),
                  sub=f"OI {_fmt((snap.get(k) or {}).get('oi'), 0)}")
            for k, lbl in labels.items()
        ]
        st.markdown(_grid(tiles), unsafe_allow_html=True)
        _style_table(_ticks_to_rows(feed, labels))

    board()


def _render_options(feed, refresh: int) -> None:
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        exch = st.selectbox("Exchange", ["NSE", "MCX", "BSE"], key="mkt_opt_exch")
    default_under = (upstox_universe.DEFAULT_COMMODITIES if exch == "MCX"
                     else upstox_universe.DEFAULT_FO_UNDERLYINGS)
    with c2:
        under = st.selectbox("Underlying", default_under, key="mkt_opt_und")

    try:
        expiries = _cached_expiries(exch, under)
    except Exception as e:
        st.error(f"Could not load option contracts: {e}")
        return
    if not expiries:
        st.caption(f"No unexpired {under} options found on {exch}.")
        return

    with c3:
        expiry = st.selectbox("Expiry", expiries, key="mkt_opt_exp")
    with c4:
        around = st.slider("Strikes around ATM", 3, 25, 10, key="mkt_opt_n")

    # Centre the chain on the money. Index underlyings have a spot feed;
    # stock and commodity underlyings don't, so use the near-month future.
    spot = None
    key = upstox_universe.spot_key(under)
    if key:
        if feed.connected:
            _subscribe(feed, [key], upstox_feed.MODE_LTPC)
        spot = (feed.get(key) or {}).get("ltp")
    if spot is None:
        try:
            near = _cached_futures(exch, (under,))
        except Exception:
            near = []
        for row in near:
            spot = (feed.get(row["instrument_key"]) or {}).get("ltp")
            if spot is not None:
                break

    try:
        rows = _cached_strikes(exch, under, expiry, spot, around)
    except Exception as e:
        st.error(f"Could not load strikes: {e}")
        return
    if not rows:
        st.caption("No strikes for that expiry.")
        return

    labels = upstox_universe.label_map(rows)
    limit = upstox_feed.MODE_LIMITS[upstox_feed.MODE_GREEKS]
    if len(labels) > limit:
        st.warning(f"{len(labels)} strikes exceeds the {limit}-instrument "
                   f"option_greeks limit — narrowing to the first {limit}.")
        labels = dict(list(labels.items())[:limit])

    if feed.connected:
        _subscribe(feed, list(labels), upstox_feed.MODE_GREEKS)
    st.caption(f"{len(labels)} contracts streaming in `option_greeks` mode"
               + (f" · spot {_fmt(spot)}" if spot else ""))

    @st.fragment(run_every=refresh)
    def board():
        _style_table(_ticks_to_rows(feed, labels))

    board()


def _render_global(refresh: int) -> None:
    st.caption(
        "Upstox carries Indian instruments only, so the global tape is sourced "
        "from Yahoo Finance. Cash indices are typically 15-minute delayed; "
        "index futures, FX and crypto are effectively real time."
    )

    @st.fragment(run_every=max(refresh, 15))
    def board():
        try:
            grouped = global_feed.by_group()
        except Exception as e:
            st.error(f"Global snapshot failed: {e}")
            return
        if not grouped:
            st.caption("No global data available right now.")
            return
        for group, items in grouped.items():
            delayed = any(i["delayed"] for i in items)
            st.markdown(f"**{group}**" + (" &nbsp;<span class='mkt-sub'>delayed</span>"
                                          if delayed else ""),
                        unsafe_allow_html=True)
            st.markdown(_grid([
                _tile(i["name"], i["last"], i["pct_change"], i["change"])
                for i in items
            ]), unsafe_allow_html=True)

    board()


def _subscribe(feed, keys: list[str], mode: str) -> None:
    """Subscribe only what changed, so reruns don't re-send the whole set."""
    current = feed.subscriptions()
    new = [k for k in keys if current.get(k) != mode]
    if not new:
        return
    try:
        feed.subscribe(new, mode)
    except ValueError as e:
        st.warning(str(e))
    except Exception as e:
        log.warning("subscribe failed: %s", e)
        st.warning(f"Subscription failed: {e}")
