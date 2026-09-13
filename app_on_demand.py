from __future__ import annotations

from datetime import datetime, timezone
import pandas as pd
import streamlit as st
from scanner_engine import broad, deep, market, nse_universe

st.set_page_config(page_title="Indian Intraday Alpha Scanner", page_icon="📈", layout="wide")
st.title("Indian Intraday Alpha Scanner")
st.caption("Full NSE EQ universe → rule-based shortlist → deep analysis of every shortlisted share")

with st.sidebar:
    workers = st.slider("Broad workers", 2, 6, 4)
    deep_workers = st.slider("Deep workers", 1, 4, 2)
    min_atr = st.number_input("Minimum 15m ATR %", 0.5, 10.0, 1.8, 0.1)
    min_shortlist_score = st.number_input("Minimum shortlist score", 0.0, 100.0, 45.0, 1.0)
    min_rr = st.number_input("Minimum R:R", 1.0, 5.0, 2.0, 0.1)
    news_gate = st.number_input("News gate", 0.0, 0.9, 0.0, 0.05)

@st.cache_data(ttl=900, show_spinner=False)
def load_universe():
    return nse_universe()

for key, default in {"signals": pd.DataFrame(), "shortlist": pd.DataFrame(), "broad": pd.DataFrame(), "market": {}, "last_scan": None, "scanning": False}.items():
    if key not in st.session_state:
        st.session_state[key] = default


def signal_cols(df):
    return [c for c in ["Grade","Reliability","Symbol","Direction","Price","Entry","Stop Loss","Target","R:R","ATR %","Execution ATR %","Volume Shock","5m Trend","15m Trend","1h Trend","5m Breakout","15m Breakout","Relative Strength","News Score","News Status","Market Regime","Broker Eligibility"] if c in df.columns]


def scan_all():
    uni = load_universe()
    if uni.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), {"regime": "NO_DATA", "move15": None}
    symbols = uni["Symbol"].dropna().astype(str).str.upper().drop_duplicates().tolist()
    mkt = market()
    progress = st.progress(0.0, text=f"Broad scan 0/{len(symbols)}")
    preview = st.empty()
    last_ui = 0

    def on_broad(done, total, partial):
        nonlocal last_ui
        step = max(25, total // 30)
        if done != total and done - last_ui < step:
            return
        last_ui = done
        progress.progress(done / max(total, 1), text=f"Broad scan {done:,}/{total:,} — complete NSE EQ universe")
        if not partial.empty:
            view = partial.sort_values("BroadScore", ascending=False).head(25)
            cols = [c for c in ["Symbol","price","atr_pct","volshock","BroadScore"] if c in view.columns]
            preview.dataframe(view[cols], use_container_width=True, hide_index=True)

    broad_df = broad(symbols, exchange="NS", workers=int(workers), on_update=on_broad)
    progress.progress(1.0, text=f"Broad scan complete: {len(broad_df):,} data-valid names")
    if broad_df.empty:
        return broad_df, pd.DataFrame(), pd.DataFrame(), mkt

    shortlist = broad_df[
        pd.to_numeric(broad_df["atr_pct"], errors="coerce").ge(float(min_atr))
        & pd.to_numeric(broad_df["BroadScore"], errors="coerce").ge(float(min_shortlist_score))
    ].sort_values("BroadScore", ascending=False).reset_index(drop=True)

    st.success(f"{len(broad_df):,} broad names scanned. {len(shortlist):,} names shortlisted. Every shortlisted name will receive deep analysis.")
    if shortlist.empty:
        return broad_df, shortlist, pd.DataFrame(), mkt

    deep_progress = st.progress(0.0, text=f"Deep analysis 0/{len(shortlist)}")
    live = st.empty()
    results = []
    batch_size = 10

    for start in range(0, len(shortlist), batch_size):
        batch = shortlist.iloc[start:start + batch_size].copy()
        result = deep(batch, mkt, min_atr=float(min_atr), min_vol=1.5, min_rr=float(min_rr), news_gate=float(news_gate), workers=int(deep_workers), on_update=None)
        if not result.empty:
            results.append(result)
            current = pd.concat(results, ignore_index=True).drop_duplicates("Symbol", keep="last").sort_values(["Reliability","R:R"], ascending=False).reset_index(drop=True)
            live.dataframe(current[signal_cols(current)], use_container_width=True, hide_index=True)
        complete = min(start + len(batch), len(shortlist))
        deep_progress.progress(complete / len(shortlist), text=f"Deep analysis {complete:,}/{len(shortlist):,}")

    signals = pd.concat(results, ignore_index=True) if results else pd.DataFrame()
    if not signals.empty:
        signals = signals.drop_duplicates("Symbol", keep="last").sort_values(["Reliability","R:R"], ascending=False).reset_index(drop=True)
    deep_progress.progress(1.0, text=f"Deep analysis complete: {len(shortlist):,}/{len(shortlist):,}")
    return broad_df, shortlist, signals, mkt


@st.fragment(run_every="60s")
def dashboard():
    st.info("The broad scan has no fixed stock-count cap. It covers the complete current NSE EQ security master. The deep stage analyzes every share passing the explicit shortlist rules, using controlled batches rather than a 25/100 hard cap.")
    if st.session_state.last_scan is None or st.button("Run complete universe scan now", type="primary", use_container_width=True):
        if st.session_state.scanning:
            st.warning("A scan is already running.")
        else:
            st.session_state.scanning = True
            try:
                broad_df, shortlist, signals, mkt = scan_all()
                st.session_state.broad = broad_df
                st.session_state.shortlist = shortlist
                st.session_state.signals = signals
                st.session_state.market = mkt
                st.session_state.last_scan = datetime.now(timezone.utc)
            finally:
                st.session_state.scanning = False

    mkt = st.session_state.market
    last = st.session_state.last_scan.isoformat(timespec="seconds") if st.session_state.last_scan else "waiting"
    st.write(f"Last scan UTC: {last}")
    a, b, c, d = st.columns(4)
    a.metric("Market regime", mkt.get("regime", "UNKNOWN"))
    move = mkt.get("move15")
    b.metric("15m market move", f"{move:+.3f}%" if isinstance(move, (int, float)) else "N/A")
    c.metric("Current NSE EQ universe", f"{len(load_universe()):,}")
    d.metric("Shortlisted → Deep", f"{len(st.session_state.shortlist):,}")

    if not st.session_state.shortlist.empty:
        st.subheader("Complete Rule-Based Shortlist")
        sdf = st.session_state.shortlist.rename(columns={"price":"Price","atr_pct":"ATR %","volshock":"Volume Shock"})
        cols = [c for c in ["Symbol","Price","ATR %","Volume Shock","BroadScore"] if c in sdf.columns]
        st.dataframe(sdf[cols], use_container_width=True, hide_index=True)

    st.subheader("Deep Analysis — Every Shortlisted Share")
    signals = st.session_state.signals
    if signals.empty:
        st.info("No shortlisted share currently has a fully validated setup. No signal is fabricated.")
    else:
        st.dataframe(signals[signal_cols(signals)], use_container_width=True, hide_index=True)
        why = [c for c in ["Symbol","Grade","Reliability","Why","Catalyst","News Freshness Min","DataSource","Updated UTC"] if c in signals.columns]
        st.dataframe(signals[why].head(50), use_container_width=True, hide_index=True)
        st.download_button("Download complete deep-analysis results CSV", signals.to_csv(index=False).encode(), "intraday_deep_analysis.csv", "text/csv")

    st.caption("Streamlit-only on-demand mode. Full current NSE EQ universe in broad scan; every rule-based shortlist member is deep-analysed. Controlled concurrency prevents the 25→100 UI blackout.")

dashboard()
