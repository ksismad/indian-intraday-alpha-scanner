from __future__ import annotations

from datetime import datetime, timezone
import streamlit as st
import pandas as pd

from scanner_engine import broad, deep, market, nse_universe, FALLBACK

st.set_page_config(page_title="Indian Intraday Alpha Scanner", page_icon="📈", layout="wide")
st.title("Indian Intraday Alpha Scanner")
st.caption("Full NSE EQ universe → progressive filtering → market response + news → live reliability ranking")

@st.cache_data(ttl=1800, show_spinner=False)
def load_universe():
    return nse_universe()

with st.sidebar:
    st.header("Engine")
    deep_limit = st.number_input("Deep-analysis candidates", 50, 800, 250, 50)
    workers = st.slider("Parallel workers", 4, 24, 16)
    min_atr = st.number_input("Minimum ATR %", 0.5, 10.0, 1.5, 0.1)
    min_vol = st.number_input("Minimum 5m volume shock", 0.5, 10.0, 1.3, 0.1)
    min_rr = st.number_input("Minimum R:R", 1.0, 5.0, 2.0, 0.1)
    news_gate = st.number_input("Strong news gate (0 = weight only)", 0.0, 0.9, 0.0, 0.05)
    auto_refresh = st.toggle("Continuous refresh", True)
    show_progress = st.toggle("Show live scan progress", True)

# A session-level rolling board keeps recently selected setups visible while the
# next universe/deep scan is still running.
if "live_board" not in st.session_state:
    st.session_state.live_board = pd.DataFrame()

@st.fragment(run_every="60s" if auto_refresh else None)
def dashboard():
    st.write(f"Heartbeat UTC: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}")

    uni = load_universe()
    symbols = uni["Symbol"].tolist() if not uni.empty else FALLBACK
    mkt = market()

    st.subheader("Market Response")
    a,b,c=st.columns(3)
    a.metric("NIFTY/SENSEX regime", mkt.get("regime", "UNKNOWN"))
    b.metric("15m market move", f"{mkt.get('move15', 0):+.3f}%")
    c.metric("NSE EQ universe", f"{len(symbols):,}")

    progress_box = st.empty() if show_progress else None
    early_box = st.empty()
    signal_box = st.empty()
    status_box = st.empty()

    def on_broad(done, total, partial):
        if progress_box:
            progress_box.progress(done / max(total, 1), text=f"Universe scan: {done:,}/{total:,}")
        if not partial.empty:
            preview = partial.head(12).copy()
            status_box.info(f"Scanning universe… currently found {len(partial):,} data-valid symbols. Best provisional candidates are being evaluated next.")
            early_cols = [c for c in ["Symbol","price","atr_pct","volshock","ret15","BroadScore"] if c in preview.columns]
            early_box.dataframe(preview[early_cols], use_container_width=True, hide_index=True)

    broad_df = broad(symbols, exchange="NS", workers=workers, on_update=on_broad)
    if progress_box:
        progress_box.progress(1.0, text=f"Universe scan complete: {len(broad_df):,} data-valid symbols")

    if broad_df.empty:
        st.error("No market data returned. The engine will retry automatically.")
        return

    candidates = broad_df.head(int(deep_limit)).copy()
    status_box.info(f"Deep analysis started on top {len(candidates):,} candidates. Qualified signals will appear immediately as each one completes.")

    def on_deep(done, total, partial):
        if progress_box:
            progress_box.progress(done / max(total, 1), text=f"Deep analysis: {done:,}/{total:,}")
        if partial.empty:
            signal_box.caption("Deep analysis is running — waiting for the first fully qualified setup…")
            return
        merged = partial.sort_values(["Reliability","R:R"], ascending=False).reset_index(drop=True)
        st.session_state.live_board = merged.copy()
        cols=["Grade","Reliability","Symbol","Direction","Price","Entry","Stop Loss","Target","R:R","ATR %","Volume Shock","5m Trend","1h Trend","Breakout","Relative Strength","News Score","Market Regime"]
        signal_box.dataframe(merged[cols], use_container_width=True, hide_index=True)
        status_box.success(f"LIVE: {len(merged)} setup(s) selected so far. Search continues — new stronger setups can replace the ranking.")

    signals = deep(candidates, mkt, min_atr=float(min_atr), min_vol=float(min_vol), min_rr=float(min_rr), news_gate=float(news_gate), workers=workers, on_update=on_deep)

    if progress_box:
        progress_box.progress(1.0, text="Deep analysis complete — next refresh will rescan the universe")

    final = signals if not signals.empty else st.session_state.live_board
    c1,c2,c3=st.columns(3)
    c1.metric("Broad candidates", f"{len(broad_df):,}")
    c2.metric("Deep scanned", f"{len(candidates):,}")
    c3.metric("Current selected", f"{len(final):,}")

    if final.empty:
        st.warning("No qualifying setup currently. Capital preserved; scanner continues watching.")
    else:
        st.subheader("Highest Reliability → Lowest")
        cols=["Grade","Reliability","Symbol","Direction","Price","Entry","Stop Loss","Target","R:R","ATR %","Volume Shock","5m Trend","1h Trend","Breakout","Relative Strength","News Score","Market Regime"]
        st.dataframe(final[cols], use_container_width=True, hide_index=True)
        st.subheader("Why each selected setup qualified")
        st.dataframe(final.head(20)[["Symbol","Grade","Reliability","Why","Catalyst"]], use_container_width=True, hide_index=True)
        st.download_button("Download current signals CSV", final.to_csv(index=False).encode(), "intraday_signals.csv", "text/csv")


dashboard()
st.caption("Research/analytics only. Live-style refresh does not mean a guaranteed tick-by-tick exchange feed. Data sources can be delayed/rate-limited; broker intraday eligibility, surveillance restrictions and short-selling rules must be checked independently. The app does not place orders.")
