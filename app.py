from __future__ import annotations

from datetime import datetime, timezone
import pandas as pd
import streamlit as st
from scanner_engine import broad, deep, market, nse_universe, FALLBACK

st.set_page_config(page_title="Indian Intraday Alpha Scanner", page_icon="📈", layout="wide")
st.title("Indian Intraday Alpha Scanner")
st.caption("Full NSE EQ universe → rotating broad scan → 15m + 1h confirmation → 5m trigger → market/news response → highest reliability first")

with st.sidebar:
    st.header("Continuous Engine")
    batch_size = st.number_input("Universe batch per cycle", 20, 150, 60, 10)
    deep_limit = st.number_input("Deep-analysis candidates", 5, 100, 15, 5)
    workers = st.slider("Parallel workers", 4, 24, 16)
    min_atr = st.number_input("Minimum ATR %", 0.5, 10.0, 1.8, 0.1)
    min_vol = st.number_input("Minimum 5m volume shock", 0.5, 10.0, 1.5, 0.1)
    min_rr = st.number_input("Minimum R:R", 1.0, 5.0, 2.0, 0.1)
    news_gate = st.number_input("Strong news gate (0 = news is a weight)", 0.0, 0.9, 0.0, 0.05)
    auto_refresh = st.toggle("Continuous refresh", True)
    st.caption("Public HTTP mode is live-style, not guaranteed tick-by-tick. For true streaming, configure the separate broker/WebSocket worker.")

if "cursor" not in st.session_state:
    st.session_state.cursor = 0
if "broad_cache" not in st.session_state:
    st.session_state.broad_cache = {}
if "signals" not in st.session_state:
    st.session_state.signals = pd.DataFrame()

@st.cache_data(ttl=1800, show_spinner=False)
def load_universe():
    return nse_universe()

def merge_broad(frame: pd.DataFrame):
    if frame.empty:
        return
    for row in frame.to_dict("records"):
        st.session_state.broad_cache[row["Symbol"]] = row

def update_provisional(place, frame: pd.DataFrame, done: int, total: int):
    place.info(f"Broad universe scan: {done}/{total} completed. Showing current highest-potential names while the scan continues.")
    if frame.empty:
        return
    merge_broad(frame)
    top = pd.DataFrame(st.session_state.broad_cache.values()).sort_values("BroadScore", ascending=False).head(10).copy()
    display = top.rename(columns={"price":"Price", "atr_pct":"ATR %", "volshock":"Volume Shock", "ret15":"15m Return %"})
    place.dataframe(display[["Symbol", "Price", "ATR %", "Volume Shock", "15m Return %", "BroadScore"]], use_container_width=True, hide_index=True)

@st.fragment(run_every="60s" if auto_refresh else None)
def dashboard():
    st.write(f"Heartbeat UTC: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}")
    uni = load_universe()
    universe = uni["Symbol"].tolist() if not uni.empty else FALLBACK
    market_state = market()

    total = len(universe)
    start = st.session_state.cursor % total
    batch = [universe[(start + i) % total] for i in range(min(int(batch_size), total))]
    st.session_state.cursor = (start + len(batch)) % total

    st.subheader("Market Response")
    a, b, c, d = st.columns(4)
    a.metric("NIFTY/SENSEX regime", market_state.get("regime", "UNKNOWN"))
    b.metric("15m market move", f"{market_state.get('move15', 0):+.3f}%")
    c.metric("NSE EQ universe", f"{len(universe):,}")
    d.metric("Scanned & retained", f"{len(st.session_state.broad_cache):,}")

    progress_box = st.empty()
    provisional_box = st.empty()
    signal_box = st.empty()
    status_box = st.empty()

    broad_df = broad(batch, exchange="NS", workers=workers, on_update=lambda d,t,f: update_provisional(provisional_box, f, d, t))
    merge_broad(broad_df)
    broad_cache_df = pd.DataFrame(st.session_state.broad_cache.values())
    if broad_cache_df.empty:
        st.error("No market data returned yet. The next cycle will retry automatically.")
        return
    broad_cache_df = broad_cache_df.sort_values("BroadScore", ascending=False).reset_index(drop=True)
    progress_box.success(f"Broad batch complete: {len(broad_df):,} data-valid names. The scanner now deep-validates the strongest rolling candidates.")

    st.subheader("Rolling Broad Candidates")
    rolling_display = broad_cache_df.head(25).rename(columns={"price":"Price", "atr_pct":"ATR %", "volshock":"Volume Shock", "ret15":"15m Return %"})
    st.dataframe(rolling_display[["Symbol", "Price", "ATR %", "Volume Shock", "15m Return %", "BroadScore"]], use_container_width=True, hide_index=True)

    candidates = broad_cache_df.head(int(deep_limit)).copy()
    status_box.info(f"Deep validation running on top {len(candidates):,} rolling candidates. Fully validated setups appear as they qualify.")

    def on_deep(done, t, partial):
        progress_box.progress(done / max(t, 1), text=f"Deep validation: {done:,}/{t:,}")
        if partial.empty:
            signal_box.caption("Deep validation is running — waiting for the first fully qualified setup…")
            return
        merged = partial.sort_values(["Reliability", "R:R"], ascending=False).reset_index(drop=True)
        st.session_state.signals = merged.copy()
        cols = ["Grade","Reliability","Symbol","Direction","Price","Entry","Stop Loss","Target","R:R","ATR %","Volume Shock","5m Trend","15m Trend","1h Trend","5m Breakout","15m Breakout","Relative Strength","News Score","Market Regime"]
        signal_box.dataframe(merged[cols], use_container_width=True, hide_index=True)
        status_box.success(f"LIVE: {len(merged)} validated setup(s) selected so far. The rolling search continues.")

    signals = deep(candidates, market_state, min_atr=float(min_atr), min_vol=float(min_vol), min_rr=float(min_rr), news_gate=float(news_gate), workers=workers, on_update=on_deep)
    progress_box.progress(1.0, text="Deep validation complete — next 60-second cycle will rotate into the next universe batch.")

    final = signals if not signals.empty else st.session_state.signals
    if final.empty:
        st.warning("No fully validated setup currently meets the confluence rules. The scanner continues watching.")
        return
    st.subheader("Highest Reliability → Lowest")
    cols = ["Grade","Reliability","Symbol","Direction","Price","Entry","Stop Loss","Target","R:R","ATR %","Volume Shock","5m Trend","15m Trend","1h Trend","5m Breakout","15m Breakout","Relative Strength","News Score","Market Regime"]
    st.dataframe(final[cols], use_container_width=True, hide_index=True)
    st.subheader("Why each setup qualified")
    st.dataframe(final.head(20)[["Symbol","Grade","Reliability","Why","Catalyst"]], use_container_width=True, hide_index=True)
    st.download_button("Download current validated signals CSV", final.to_csv(index=False).encode(), "intraday_signals.csv", "text/csv")

    st.caption("The universe is scanned in rotating batches so the dashboard can show provisional and validated candidates without restarting a full-universe request storm every 60 seconds. Results remain in the rolling board between cycles.")

dashboard()
