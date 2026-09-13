from __future__ import annotations
from datetime import datetime, timezone
import streamlit as st
from scanner_engine import broad_daily_candidates, deep_scan, fetch_nse_universe, market_snapshot, FALLBACK_NSE

st.set_page_config(page_title="Indian Intraday Alpha Scanner", page_icon="📈", layout="wide")
st.title("Indian Intraday Alpha Scanner")
st.caption("Full NSE EQ universe → automatic filters → market response + news → highest reliability first")

@st.cache_data(ttl=1800, show_spinner=False)
def load_universe():
    return fetch_nse_universe()

with st.sidebar:
    st.header("Engine")
    deep_limit = st.number_input("Deep-analysis candidates", 50, 1000, 400, 50)
    workers = st.slider("Parallel workers", 4, 24, 16)
    min_atr = st.number_input("Minimum ATR %", 0.5, 10.0, 1.5, 0.1)
    min_vol = st.number_input("Minimum 5m volume shock", 0.5, 10.0, 1.3, 0.1)
    min_rr = st.number_input("Minimum R:R", 1.0, 5.0, 2.0, 0.1)
    news_gate = st.number_input("Strong news gate", 0.0, 0.9, 0.0, 0.05)
    auto_refresh = st.toggle("Continuous refresh", True)

@st.fragment(run_every="60s" if auto_refresh else None)
def dashboard():
    st.write(f"Heartbeat UTC: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}")
    uni = load_universe()
    symbols = uni["Symbol"].tolist() if not uni.empty else FALLBACK_NSE
    market = market_snapshot()
    broad = broad_daily_candidates(symbols, "NS", workers=workers)
    if broad.empty:
        st.error("No market data returned. The engine will retry automatically.")
        return
    candidates = broad.head(int(deep_limit)).copy()
    signals = deep_scan(candidates, market, min_atr_pct=float(min_atr), min_vol_shock=float(min_vol), min_rr=float(min_rr), news_threshold=float(news_gate), workers=workers)
    c1,c2,c3,c4=st.columns(4)
    c1.metric("NSE EQ universe", f"{len(symbols):,}")
    c2.metric("Broad candidates", f"{len(broad):,}")
    c3.metric("Qualified setups", f"{len(signals):,}")
    c4.metric("Market regime", market.get("regime", "UNKNOWN"))
    if signals.empty:
        st.warning("No qualifying setup right now. Capital preserved; scanner continues watching.")
        return
    cols=["Grade","Reliability","Symbol","Direction","Price","Entry","Stop Loss","Target","R:R","ATR %","Volume Shock","5m Trend","1h Trend","Breakout","Relative Strength","News Score","Market Regime"]
    st.subheader("Highest Reliability → Lowest")
    st.dataframe(signals[cols], use_container_width=True, hide_index=True)
    st.subheader("Why each setup qualified")
    st.dataframe(signals.head(20)[["Symbol","Grade","Reliability","Why","Catalyst"]], use_container_width=True, hide_index=True)
    st.download_button("Download current signals CSV", signals.to_csv(index=False).encode(), "intraday_signals.csv", "text/csv")

dashboard()
st.caption("Research/analytics only. The scanner ranks evidence; it does not guarantee outcomes or place orders. For true large-universe tick streaming, configure a licensed/broker WebSocket on a server; do not put broker credentials in source code.")
