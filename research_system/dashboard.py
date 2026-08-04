"""Streamlit dashboard — `streamlit run research_system/dashboard.py`.

Tabs:
 1. Morning Brief
 2. Live Feed (filter ticker / impact / urgency)
 3. Universe (price + last update per name)
 4. Thesis Cards
 5. Government Tracker (PIB-tagged by sector)
 6. Results Calendar
 7. Why is X moving? — on-demand explainer
 8. Analyze — paste-in text + ticker → Claude
 9. Holdings — add/remove tickers without touching code
10. Portfolio (Upstox) — real demat holdings, cost basis and P&L,
    read-only; needs UPSTOX_ACCESS_TOKEN, hidden behind a setup note
    when that isn't set
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from .config import PORTFOLIO, UNIVERSE, WATCHLIST, save_overrides
from .db import (
    connect,
    ensure_db,
    feed_rows,
    latest_brief,
    latest_prices,
)
from .theses import THESES

# NOTE: heavy imports (analyzer, fetchers) are lazy — only loaded on click.
# Keeping them at module top would force yfinance (12s) into every cold start.

logging.basicConfig(level=logging.INFO)
IST = ZoneInfo("Asia/Kolkata")


# ---- cached read-only DB queries (TTL refresh) -------------------------
@st.cache_data(ttl=30, show_spinner=False)
def cached_feed_rows(limit, ticker, impact, urgency):
    return [dict(r) for r in feed_rows(limit=limit, ticker=ticker,
                                        impact=impact, urgency=urgency)]


@st.cache_data(ttl=120, show_spinner=False)
def cached_latest_prices():
    return {tk: dict(r) for tk, r in latest_prices().items()}


@st.cache_data(ttl=120, show_spinner=False)
def cached_latest_brief():
    b = latest_brief()
    return dict(b) if b else None


@st.cache_data(ttl=60, show_spinner=False)
def cached_universe_table():
    """One DB query for all 18 holdings instead of 18 round-trips."""
    with connect() as cx:
        rows = cx.execute(
            """SELECT u.ticker AS t, MAX(u.fetched_at) AS last_at,
                      (SELECT headline FROM updates WHERE ticker = u.ticker
                       ORDER BY fetched_at DESC LIMIT 1) AS last_head
               FROM updates u WHERE u.ticker IS NOT NULL
               GROUP BY u.ticker"""
        ).fetchall()
    return {r["t"]: {"head": r["last_head"], "at": r["last_at"]} for r in rows}


@st.cache_data(ttl=60, show_spinner=False)
def cached_pib_rows():
    with connect() as cx:
        return [dict(r) for r in cx.execute(
            "SELECT * FROM updates WHERE source='pib' "
            "ORDER BY fetched_at DESC LIMIT 200"
        ).fetchall()]


@st.cache_data(ttl=120, show_spinner=False)
def cached_db_stats():
    with connect() as cx:
        u = cx.execute("SELECT COUNT(*) c FROM updates").fetchone()["c"]
        a = cx.execute("SELECT COUNT(*) c FROM analyses").fetchone()["c"]
        p = cx.execute("SELECT COUNT(*) c FROM prices").fetchone()["c"]
    return u, a, p


@st.cache_data(ttl=60, show_spinner=False)
def cached_thesis_recent(ticker: str):
    with connect() as cx:
        return [dict(r) for r in cx.execute(
            """SELECT a.impact, a.urgency, a.action, a.reasoning,
                      u.headline, u.fetched_at
               FROM analyses a JOIN updates u ON u.id = a.update_id
               WHERE a.ticker=? ORDER BY a.created_at DESC LIMIT 5""",
            (ticker,),
        ).fetchall()]


# ---- Upstox (read-only broker feed) ------------------------------------
# Short TTLs: these are live market values, but we still don't want a
# re-render on every widget interaction to hit the broker API.
@st.cache_data(ttl=60, show_spinner=False)
def cached_upstox_health():
    from .fetchers import upstox_fetcher as ux
    return ux.health()


@st.cache_data(ttl=60, show_spinner=False)
def cached_upstox_holdings():
    from .fetchers import upstox_fetcher as ux
    try:
        return {"ok": True, "holdings": ux.holdings(),
                "summary": ux.portfolio_summary()}
    except ux.UpstoxError as e:
        return {"ok": False, "error": str(e)}


@st.cache_data(ttl=60, show_spinner=False)
def cached_upstox_positions():
    from .fetchers import upstox_fetcher as ux
    try:
        return {"ok": True, "positions": ux.positions()}
    except ux.UpstoxError as e:
        return {"ok": False, "error": str(e)}


@st.cache_data(ttl=300, show_spinner=False)
def cached_upstox_funds():
    from .fetchers import upstox_fetcher as ux
    try:
        return {"ok": True, "funds": ux.funds()}
    except ux.UpstoxError as e:
        return {"ok": False, "error": str(e)}


@st.cache_data(ttl=300, show_spinner=False)
def cached_upstox_suggestions():
    from .fetchers import upstox_fetcher as ux
    try:
        return {"ok": True, "suggestions": ux.suggest_universe_overrides()}
    except ux.UpstoxError as e:
        return {"ok": False, "error": str(e)}


def _bust_cache():
    """Called after any sidebar fetch / analyser run."""
    st.cache_data.clear()

# --------------------------- page config / theme ------------------------
st.set_page_config(
    page_title="India Research Monitor",
    layout="wide",
    initial_sidebar_state="expanded",
)

CSS = """
<style>
  .stApp { background:#0e1117; color:#e6e6e6; }
  .impact-positive { color:#22c55e; font-weight:600; }
  .impact-negative { color:#ef4444; font-weight:600; }
  .impact-neutral  { color:#9ca3af; font-weight:600; }
  .urgency-high    { background:#7f1d1d; color:#fee2e2; padding:2px 6px; border-radius:4px; font-size:11px; }
  .urgency-medium  { background:#78350f; color:#fde68a; padding:2px 6px; border-radius:4px; font-size:11px; }
  .urgency-low     { background:#1f2937; color:#9ca3af; padding:2px 6px; border-radius:4px; font-size:11px; }
  .ticker-pill     { background:#1e293b; color:#93c5fd; padding:2px 8px; border-radius:10px; font-family: ui-monospace; font-size:11px; }
  .card            { background:#161b22; border:1px solid #30363d; border-radius:8px; padding:14px; margin-bottom:10px; }
  h2, h3, h4 { color:#f1f5f9 !important; }
  .source-tag { color:#64748b; font-size:11px; }
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)
ensure_db()

# --------------------------- sidebar ------------------------------------
with st.sidebar:
    st.header("India Research Monitor")
    st.caption(datetime.now(IST).strftime("%a %d %b %Y, %H:%M IST"))
    st.divider()
    st.subheader("Manual run")
    if st.button("Fetch RSS news", width='stretch'):
        with st.spinner("Pulling RSS..."):
            from .fetchers import rss_fetcher
            n = rss_fetcher.fetch_all()
        _bust_cache(); st.success(f"RSS inserted: {n}")
    if st.button("Fetch PIB", width='stretch'):
        with st.spinner("Pulling PIB..."):
            from .fetchers import pib_fetcher
            n = pib_fetcher.fetch_all()
        _bust_cache(); st.success(f"PIB inserted: {n}")
    if st.button("Fetch NSE announcements", width='stretch'):
        with st.spinner("Pulling NSE..."):
            from .fetchers import nse_fetcher
            n = nse_fetcher.fetch_announcements(days=2)
        _bust_cache(); st.success(f"NSE inserted: {n}")
    if st.button("Fetch BSE announcements", width='stretch'):
        with st.spinner("Pulling BSE..."):
            from .fetchers import bse_fetcher
            n = bse_fetcher.fetch_announcements(days=2)
        _bust_cache(); st.success(f"BSE inserted: {n}")
    if st.button("Snapshot prices", width='stretch'):
        with st.spinner("Pulling prices..."):
            from .fetchers import price_fetcher
            n = price_fetcher.snapshot_universe()
        _bust_cache(); st.success(f"Prices rows: {n}")
    st.divider()
    if st.button("Run analyser now", type="primary", width='stretch'):
        with st.spinner("Analysing unprocessed updates..."):
            from .analyzer import run as run_analyzer
            n = run_analyzer(batch=50)
        _bust_cache(); st.success(f"Analysed {n} updates")

    st.divider()
    u_count, a_count, p_count = cached_db_stats()
    st.caption(f"updates={u_count}  analyses={a_count}  price-rows={p_count}")


# --------------------------- helpers ------------------------------------
def impact_html(v: str | None) -> str:
    v = (v or "").lower()
    cls = "impact-neutral"
    if v == "positive":
        cls = "impact-positive"
    elif v == "negative":
        cls = "impact-negative"
    return f"<span class='{cls}'>{v or '—'}</span>"


def urgency_html(v: str | None) -> str:
    v = (v or "").lower() or "low"
    return f"<span class='urgency-{v}'>{v.upper()}</span>"


def ticker_pill(tk: str | None) -> str:
    return f"<span class='ticker-pill'>{tk or '—'}</span>"


# --------------------------- tabs ---------------------------------------
TABS = [
    "Morning Brief",
    "Live Feed",
    "Universe",
    "Thesis Cards",
    "Govt Tracker",
    "Results Calendar",
    "Why Is X Moving",
    "Analyze (paste)",
    "Holdings",
    "Portfolio (Upstox)",
]
tabs = st.tabs(TABS)


# --- 1. Morning Brief ---------------------------------------------------
with tabs[0]:
    st.subheader("Today's Morning Brief")
    brief = cached_latest_brief()
    if brief:
        st.caption(f"Date: {brief['date']} | generated {brief['created_at']}")
        st.markdown(brief["content"])
    else:
        st.info("No brief yet. Run morning_brief.py or wait for 7:30 IST.")
    if st.button("Regenerate brief now"):
        with st.spinner("Calling LLM..."):
            from .morning_brief import build_and_store
            text = build_and_store()
        _bust_cache(); st.markdown(text)


# --- 2. Live Feed -------------------------------------------------------
with tabs[1]:
    st.subheader("Live Feed — updates + Claude analysis")
    c1, c2, c3, c4 = st.columns(4)
    tk_options = ["(all)"] + sorted(UNIVERSE.keys())
    sel_ticker = c1.selectbox("Ticker", tk_options, index=0)
    sel_impact = c2.selectbox("Impact", ["(any)", "positive", "negative", "neutral"], index=0)
    sel_urg = c3.selectbox("Urgency", ["(any)", "high", "medium", "low"], index=0)
    sel_limit = c4.selectbox("Rows", [25, 50, 100, 200], index=1)

    rows = cached_feed_rows(
        sel_limit,
        None if sel_ticker == "(all)" else sel_ticker,
        None if sel_impact == "(any)" else sel_impact,
        None if sel_urg == "(any)" else sel_urg,
    )
    if not rows:
        st.info("No items match. Try widening filters or running fetchers from sidebar.")
    for r in rows:
        cols = st.columns([1, 6, 1, 1])
        with cols[0]:
            st.markdown(ticker_pill(r["ticker"]), unsafe_allow_html=True)
            st.markdown(f"<div class='source-tag'>{r['source']}</div>", unsafe_allow_html=True)
        with cols[1]:
            url = r["url"] or ""
            head = r["headline"]
            if url:
                st.markdown(f"**[{head}]({url})**")
            else:
                st.markdown(f"**{head}**")
            if r["reasoning"]:
                st.caption(r["reasoning"])
                fu = r["follow_ups"]
                if fu:
                    try:
                        items = json.loads(fu)
                        if items:
                            st.caption("Follow-ups: " + " · ".join(items))
                    except Exception:
                        pass
        with cols[2]:
            st.markdown(impact_html(r["impact"]), unsafe_allow_html=True)
            st.caption(r["action"] or "—")
        with cols[3]:
            st.markdown(urgency_html(r["urgency"]), unsafe_allow_html=True)
            st.caption(r["fetched_at"][11:16] if r["fetched_at"] else "")
        st.markdown("---")


# --- 3. Universe table --------------------------------------------------
with tabs[2]:
    st.subheader("Universe — 9 portfolio + 9 watchlist")
    prices = cached_latest_prices()
    last_by_ticker = cached_universe_table()
    data = []
    for tk, m in UNIVERSE.items():
        p = prices.get(tk)
        last = last_by_ticker.get(tk)
        bucket = "Portfolio" if tk in PORTFOLIO else "Watchlist"
        head = last["head"] if last else "—"
        if last and head and len(head) > 90:
            head = head[:90] + "…"
        data.append({
            "Bucket": bucket,
            "Ticker": tk,
            "Name": m["name"],
            "Sector": m["sector"],
            "Last Close": p["close"] if p else None,
            "As of": p["asof_date"] if p else "",
            "Last update": head,
            "Last update time": last["at"][:16] if last and last["at"] else "—",
        })
    df = pd.DataFrame(data).sort_values(["Bucket", "Ticker"]).reset_index(drop=True)
    st.dataframe(df, width='stretch', hide_index=True)


# --- 4. Thesis cards ----------------------------------------------------
with tabs[3]:
    st.subheader("Investment thesis cards (portfolio)")
    for tk, th in THESES.items():
        with st.container():
            st.markdown(f"### {th['name']} <span class='ticker-pill'>{tk}</span>",
                        unsafe_allow_html=True)
            cols = st.columns(2)
            with cols[0]:
                st.markdown(f"**Thesis**  \n{th['thesis']}")
                st.markdown(f"**Catalyst**  \n{th.get('catalyst','—')}")
            with cols[1]:
                st.markdown("**What we watch**")
                for w in th["watch"]:
                    st.markdown(f"- {w}")
                st.markdown("**Thesis breaks if**")
                for w in th["breaks"]:
                    st.markdown(f"- {w}")
            recent = cached_thesis_recent(tk)
            if recent:
                st.markdown("**Recent analyses**")
                for r in recent:
                    st.markdown(
                        f"- _{r['fetched_at'][:16]}_ "
                        + impact_html(r["impact"]) + " "
                        + urgency_html(r["urgency"]) + " "
                        + f"→ **{r['action']}** — {r['headline'][:120]}",
                        unsafe_allow_html=True,
                    )
            st.divider()


# --- 5. Government Tracker ---------------------------------------------
with tabs[4]:
    st.subheader("Government Tracker — PIB feed for our sectors")
    rows = cached_pib_rows()
    if not rows:
        st.info("No PIB items yet. Hit 'Fetch PIB' in the sidebar.")
    else:
        for r in rows:
            st.markdown(
                f"**[{r['headline']}]({r['url'] or '#'})**  \n"
                f"<span class='source-tag'>{r['fetched_at'][:16]}</span>",
                unsafe_allow_html=True,
            )
            if r["body"]:
                st.caption(r["body"][:300])
            st.markdown("---")


# --- 6. Results Calendar -----------------------------------------------
with tabs[5]:
    st.subheader("Upcoming results — universe")
    if st.button("Refresh from NSE", key="refresh_cal"):
        with st.spinner("Pulling NSE results calendar..."):
            from .fetchers import nse_fetcher
            cal = nse_fetcher.fetch_results_calendar()
        st.session_state["_results_cal"] = cal
    cal = st.session_state.get("_results_cal")
    if cal is None:
        st.info("Click 'Refresh from NSE' above to pull the calendar.")
    elif not cal:
        st.info("No upcoming results found for universe tickers right now.")
    else:
        df = pd.DataFrame([
            {"Ticker": c["ticker"], "NSE Symbol": c["symbol"],
             "As on": c["as_on"], "Broadcast": c["broadcast"]}
            for c in cal
        ])
        st.dataframe(df, width='stretch', hide_index=True)


# --- 7. Why is X moving today? -----------------------------------------
with tabs[6]:
    st.subheader("Why is X moving today?")
    c1, c2 = st.columns([2, 1])
    sel = c1.selectbox("Ticker", sorted(UNIVERSE.keys()), key="why_tk")
    go = c2.button("Explain move", type="primary")
    if go:
        with st.spinner("Pulling price, news, macro… asking the LLM."):
            try:
                from .analyzer import explain_move
                out = explain_move(sel)
            except Exception as e:
                st.error(f"Failed: {e}")
                out = None
        if out:
            mv = out.get("_move")
            if mv:
                clr = "impact-positive" if mv["pct_change"] > 0 else \
                      ("impact-negative" if mv["pct_change"] < 0 else "impact-neutral")
                st.markdown(
                    f"**{sel}** — <span class='{clr}'>{mv['close']:.2f} "
                    f"({mv['pct_change']:+.2f}%)</span> on {mv['asof']}",
                    unsafe_allow_html=True,
                )
            st.markdown(f"**Verdict:** {out.get('verdict','?')} "
                        f"· Confidence: {out.get('confidence','?')}")
            st.markdown(f"**Primary driver:** {out.get('primary_driver','—')}")
            st.markdown("**Supporting evidence**")
            for s in out.get("supporting_evidence", []):
                st.markdown(f"- {s}")
            st.markdown("**What to watch next**")
            for s in out.get("what_to_watch_next", []):
                st.markdown(f"- {s}")
            st.caption(f"updates considered: {out.get('_updates_used',0)} | "
                       f"macro snapshot: {len(out.get('_macro', {}))} indices")


# --- 8. Analyze (paste) -------------------------------------------------
with tabs[7]:
    st.subheader("Ad-hoc analysis — paste anything, pick a name, get the read")
    c1, c2 = st.columns([1, 3])
    sel = c1.selectbox("Ticker (optional)", ["(none)"] + sorted(UNIVERSE.keys()))
    txt = c2.text_area("Paste here", height=220,
                       placeholder="Annual report excerpt, broker note, news, transcript snippet…")
    if st.button("Run analysis", type="primary", disabled=not txt.strip()):
        with st.spinner("Calling the LLM..."):
            try:
                from .analyzer import analyze_text
                out = analyze_text(None if sel == "(none)" else sel, txt)
            except Exception as e:
                st.error(f"Failed: {e}")
                out = None
        if out:
            st.json(out)


# --- 9. Holdings editor -------------------------------------------------
with tabs[8]:
    st.subheader("Holdings editor — add / remove tickers without touching code")
    st.caption("Edits write to data/holdings_override.json. Restart "
               "scheduler/dashboard after changes for fetchers to pick them up.")

    st.markdown("#### Add a holding")
    with st.form("add_holding", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        tk = c1.text_input("Ticker (your shorthand)", max_chars=12,
                           help="e.g. RELIANCE, TCS")
        bucket = c1.selectbox("Bucket", ["portfolio", "watchlist"])
        nse = c2.text_input("NSE symbol", help="e.g. RELIANCE")
        bse = c2.text_input("BSE scrip code (optional)", help="e.g. 500325")
        yahoo = c3.text_input("Yahoo symbol", help="e.g. RELIANCE.NS")
        sector = c3.text_input("Sector", help="free text e.g. Energy / Oil & Gas")
        name = st.text_input("Full company name")
        aliases = st.text_input("Aliases (comma-separated)",
                                help="news headlines often use other names")
        submitted = st.form_submit_button("Save holding")
        if submitted:
            if not tk or not nse:
                st.error("Ticker and NSE symbol are required.")
            else:
                meta = {
                    "name": name or tk,
                    "sector": sector,
                    "nse": nse.upper(),
                    "bse_code": bse or None,
                    "yahoo": yahoo or f"{nse.upper()}.NS",
                    "aliases": [a.strip() for a in aliases.split(",") if a.strip()],
                }
                key = tk.upper()
                if bucket == "portfolio":
                    save_overrides(portfolio={key: meta})
                else:
                    save_overrides(watchlist={key: meta})
                st.success(f"Saved {key} to {bucket}. Restart processes to apply.")

    st.markdown("#### Remove a holding")
    rm = st.selectbox("Pick a ticker to remove",
                      ["(none)"] + sorted(UNIVERSE.keys()), key="rm_sel")
    if st.button("Remove", type="secondary") and rm != "(none)":
        save_overrides(remove=[rm])
        st.success(f"Marked {rm} for removal. Restart processes to apply.")

    st.markdown("#### Current universe")
    st.write({
        "Portfolio": sorted(PORTFOLIO.keys()),
        "Watchlist": sorted(WATCHLIST.keys()),
    })


# --- 10. Portfolio (Upstox) ---------------------------------------------
def _inr(v: float | None) -> str:
    """₹ with Indian thousands grouping, e.g. ₹12,34,567.89."""
    if v is None:
        return "—"
    neg, v = v < 0, abs(float(v))
    whole, frac = divmod(round(v, 2), 1)
    s = str(int(whole))
    if len(s) > 3:                       # last 3, then pairs: 12,34,567
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    return f"{'-' if neg else ''}₹{s}.{int(round(frac * 100)):02d}"


with tabs[9]:
    st.subheader("Portfolio — live from Upstox (read-only)")

    _health = cached_upstox_health()
    _tok = _health.get("token", {})

    # NOTE: this tab branches with if/else rather than st.stop(). st.stop()
    # aborts the *whole* script run, not just this tab.
    if not _tok.get("configured"):
        st.info(
            "**Upstox is not connected.** Set `UPSTOX_ACCESS_TOKEN` to pull "
            "your real demat holdings, cost basis and P&L here — and to use "
            "your broker feed for prices instead of Yahoo."
        )
        st.markdown(
            "- **Streamlit Cloud** — App ▸ Settings ▸ Secrets:\n"
            "  ```toml\n  UPSTOX_ACCESS_TOKEN = \"...\"\n  ```\n"
            "- **VPS / laptop** — add `UPSTOX_ACCESS_TOKEN=...` to `.env`\n\n"
            "Generate a token at "
            "[account.upstox.com/developer/apps](https://account.upstox.com/developer/apps).\n\n"
            "This tab only ever *reads* — holdings, positions, funds and "
            "quotes. It cannot place, modify or cancel orders."
        )
    else:
        # ---- token / connection status strip ---------------------------
        _c1, _c2, _c3 = st.columns([2, 2, 3])
        if _tok.get("expired"):
            _c1.error("Token expired")
        elif isinstance(_tok.get("days_left"), int) and _tok["days_left"] < 7:
            _c1.warning(f"Token expires in {_tok['days_left']}d")
        else:
            _c1.success("Token valid")
        if _tok.get("expires_at"):
            _c2.caption(f"Expires {_tok['expires_at']}")
        if _tok.get("user_id"):
            _c3.caption(f"Upstox user `{_tok['user_id']}`")

        if not _health.get("ok"):
            st.error(_health.get("message", "Upstox connection failed."))
            st.caption(
                "If the token expired, generate a fresh one and update "
                "`UPSTOX_ACCESS_TOKEN`. Prices fall back to Yahoo "
                "automatically meanwhile — the rest of the dashboard is "
                "unaffected."
            )
        else:
            st.caption(
                f"{_health.get('user_name') or '—'} · "
                f"{_health.get('broker') or 'Upstox'} · "
                f"{', '.join(_health.get('exchanges') or [])}"
            )
            if st.button("Refresh from Upstox"):
                _bust_cache()
                st.rerun()

            # ---- holdings -------------------------------------------------
            _hres = cached_upstox_holdings()
            if not _hres.get("ok"):
                st.error(_hres.get("error"))
            else:
                _holdings = _hres["holdings"]
                _summary = _hres["summary"]

                if not _holdings:
                    st.info("No long-term holdings in this Upstox account.")
                else:
                    _m = st.columns(4)
                    _m[0].metric("Invested", _inr(_summary["invested"]))
                    _m[1].metric("Current value", _inr(_summary["current_value"]))
                    _m[2].metric("Total P&L", _inr(_summary["pnl"]),
                                 delta=f"{_summary['pnl_pct']:+.2f}%")
                    _m[3].metric("Day P&L", _inr(_summary["day_pnl"]))

                    _tracked = {(m.get("nse") or "").upper()
                                for m in UNIVERSE.values()}
                    st.dataframe(pd.DataFrame([{
                        "Symbol": h["symbol"],
                        "Company": h["company"],
                        "Qty": h["quantity"],
                        "Avg cost": h["avg_price"],
                        "LTP": h["last_price"],
                        "Invested": h["invested"],
                        "Current": h["current_value"],
                        "P&L": h["pnl"],
                        "P&L %": h["pnl_pct"],
                        "Day %": h["day_change_pct"],
                        "Tracked": "✓" if (h["symbol"] or "").upper() in _tracked
                                   else "",
                    } for h in _holdings]), width='stretch', hide_index=True)

                # ---- positions --------------------------------------------
                _pres = cached_upstox_positions()
                if _pres.get("ok") and _pres["positions"]:
                    st.markdown("#### Open positions (intraday)")
                    st.dataframe(pd.DataFrame(_pres["positions"]),
                                 width='stretch', hide_index=True)
                elif not _pres.get("ok"):
                    st.caption(f"Positions unavailable: {_pres.get('error')}")

                # ---- funds ------------------------------------------------
                _fres = cached_upstox_funds()
                if _fres.get("ok") and _fres["funds"]:
                    with st.expander("Funds & margin"):
                        st.json(_fres["funds"])

                # ---- reconcile demat against the research universe --------
                st.markdown("#### Holdings not in the research universe")
                st.caption(
                    "Names you actually own that the monitor isn't tracking. "
                    "Adding one writes to data/holdings_override.json — same "
                    "as the Holdings tab. Fill in the sector afterwards; "
                    "Upstox doesn't classify."
                )
                _sres = cached_upstox_suggestions()
                if not _sres.get("ok"):
                    st.caption(f"Could not compute: {_sres.get('error')}")
                elif not _sres["suggestions"]:
                    st.success("Every Upstox holding is already in the universe.")
                else:
                    _sugg = _sres["suggestions"]
                    _pick = st.multiselect("Add to the universe",
                                           sorted(_sugg.keys()), key="ux_add_pick")
                    _bucket = st.radio("Bucket", ["portfolio", "watchlist"],
                                       horizontal=True, key="ux_add_bucket")
                    if st.button("Add selected", type="primary", disabled=not _pick):
                        save_overrides(**{_bucket: {k: _sugg[k] for k in _pick}})
                        _bust_cache()
                        st.success(
                            f"Added {', '.join(_pick)} to {_bucket}. Restart the "
                            "scheduler/dashboard for fetchers to pick them up."
                        )
                    st.dataframe(pd.DataFrame([
                        {"Symbol": k, "Company": v["name"], "ISIN": v.get("isin")}
                        for k, v in sorted(_sugg.items())
                    ]), width='stretch', hide_index=True)
