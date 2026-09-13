from __future__ import annotations

from datetime import datetime, timezone
import pandas as pd
import streamlit as st
from scanner_engine import broad, deep, market, nse_universe

st.set_page_config(page_title="Indian Intraday Alpha Scanner", page_icon="📈", layout="wide")
st.title("Indian Intraday Alpha Scanner")
st.caption("On-demand Streamlit scanner: scan starts when opened and refreshes while the page remains open.")

with st.sidebar:
    batch_size = st.number_input("Universe batch", 20, 100, 50, 10)
    deep_limit = st.number_input("Deep candidates", 5, 30, 15, 5)
    workers = st.slider("Parallel workers", 2, 8, 6)
    min_atr = st.number_input("Minimum 15m ATR %", 0.5, 10.0, 1.8, 0.1)
    min_vol = st.number_input("Minimum 5m volume shock", 0.5, 10.0, 1.5, 0.1)
    min_rr = st.number_input("Minimum R:R", 1.0, 5.0, 2.0, 0.1)
    news_gate = st.number_input("News gate", 0.0, 0.9, 0.0, 0.05)
    refresh_seconds = st.select_slider("Refresh while open", [30, 60, 90, 120], value=60)

@st.cache_data(ttl=1800, show_spinner=False)
def load_universe():
    return nse_universe()

if "cursor" not in st.session_state:
    st.session_state.cursor = 0
if "cache" not in st.session_state:
    st.session_state.cache = {}
if "signals" not in st.session_state:
    st.session_state.signals = pd.DataFrame()
if "last_scan" not in st.session_state:
    st.session_state.last_scan = None
if "market" not in st.session_state:
    st.session_state.market = {}
if "broad" not in st.session_state:
    st.session_state.broad = pd.DataFrame()


def scan_cycle():
    uni = load_universe()
    if uni.empty:
        return pd.DataFrame(), pd.DataFrame(), {"regime": "NO_DATA", "move15": None}
    symbols = uni["Symbol"].tolist()
    n = len(symbols)
    start = st.session_state.cursor % n
    batch = [symbols[(start + i) % n] for i in range(min(int(batch_size), n))]
    st.session_state.cursor = (start + len(batch)) % n

    mkt = market()
    p1 = st.progress(0.0, text=f"Broad scan 0/{len(batch)}")

    def broad_update(done, total, partial):
        p1.progress(done / max(total, 1), text=f"Broad scan {done}/{total}")
        if not partial.empty:
            for row in partial.to_dict("records"):
                st.session_state.cache[row["Symbol"]] = row

    current = broad(batch, exchange="NS", workers=int(workers), on_update=broad_update)
    pool = pd.DataFrame(st.session_state.cache.values())
    if pool.empty:
        return current, pd.DataFrame(), mkt
    pool = pool.sort_values("BroadScore", ascending=False).head(int(deep_limit)).copy()

    p2 = st.progress(0.0, text=f"Deep validation 0/{len(pool)}")
    box = st.empty()

    def deep_update(done, total, partial):
        p2.progress(done / max(total, 1), text=f"Deep validation {done}/{total}")
        if not partial.empty:
            view = partial.sort_values(["Reliability", "R:R"], ascending=False).reset_index(drop=True)
            st.session_state.signals = view
            cols = [c for c in ["Grade","Reliability","Symbol","Direction","Price","Entry","Stop Loss","Target","R:R","ATR %","Execution ATR %","Volume Shock","5m Trend","15m Trend","1h Trend","5m Breakout","15m Breakout","Relative Strength","News Score","News Status","Market Regime","Broker Eligibility"] if c in view.columns]
            box.dataframe(view[cols], use_container_width=True, hide_index=True)

    result = deep(pool, mkt, min_atr=float(min_atr), min_vol=float(min_vol), min_rr=float(min_rr), news_gate=float(news_gate), workers=max(2, int(workers // 2)), on_update=deep_update)
    return current, result, mkt


@st.fragment(run_every="60s")
def dashboard():
    run_now = st.button("Run scan now", type="primary", use_container_width=True)
    first = st.session_state.last_scan is None
    if first or run_now:
        broad_df, signals, mkt = scan_cycle()
        st.session_state.broad = broad_df
        st.session_state.market = mkt
        st.session_state.last_scan = datetime.now(timezone.utc)
        if not signals.empty:
            st.session_state.signals = signals

    mkt = st.session_state.market
    st.write(f"Last scan UTC: {st.session_state.last_scan.isoformat(timespec='seconds') if st.session_state.last_scan else 'waiting'}")
    a, b, c = st.columns(3)
    a.metric("Market regime", mkt.get("regime", "UNKNOWN"))
    move = mkt.get("move15")
    b.metric("15m market move", f"{move:+.3f}%" if isinstance(move, (int, float)) else "N/A")
    c.metric("NSE EQ universe", f"{len(load_universe()):,}")

    if not st.session_state.broad.empty:
        st.subheader("Current Broad Candidates")
        bdf = st.session_state.broad.head(25).rename(columns={"price":"Price","atr_pct":"ATR %","volshock":"Volume Shock","ret5":"Recent Return %"})
        cols = [c for c in ["Symbol","Price","ATR %","Volume Shock","Recent Return %","BroadScore"] if c in bdf.columns]
        st.dataframe(bdf[cols], use_container_width=True, hide_index=True)

    st.subheader("Highest Reliability → Lowest")
    signals = st.session_state.signals
    if signals.empty:
        st.info("No fully validated setup currently meets the rules. The app will rescan while open.")
    else:
        cols = [c for c in ["Grade","Reliability","Symbol","Direction","Price","Entry","Stop Loss","Target","R:R","ATR %","Execution ATR %","Volume Shock","5m Trend","15m Trend","1h Trend","5m Breakout","15m Breakout","Relative Strength","News Score","News Status","Market Regime","Broker Eligibility"] if c in signals.columns]
        st.dataframe(signals[cols], use_container_width=True, hide_index=True)
        why = [c for c in ["Symbol","Grade","Reliability","Why","Catalyst","News Freshness Min","DataSource","Updated UTC"] if c in signals.columns]
        st.subheader("Why each setup qualified")
        st.dataframe(signals.head(20)[why], use_container_width=True, hide_index=True)
        st.download_button("Download current validated signals CSV", signals.to_csv(index=False).encode(), "intraday_signals.csv", "text/csv")

    st.caption("Streamlit-only mode. Scanning runs only while the page is open. Missing current market data produces no trade signal. No worker, Render service, VPS, RESULTS_URL, or synthetic fallback is used by this entrypoint.")

dashboard()
