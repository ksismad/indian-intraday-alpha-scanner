from __future__ import annotations

from datetime import datetime, timezone
import pandas as pd
import streamlit as st
from scanner_engine import broad, deep, market, nse_universe

st.set_page_config(page_title="Indian Intraday Alpha Scanner", page_icon="📈", layout="wide")
st.title("Indian Intraday Alpha Scanner")
st.caption("Full NSE EQ universe → rule-based discovery shortlist → deep analysis of every shortlisted share")

with st.sidebar:
    workers = st.slider("Broad workers", 2, 6, 4)
    deep_workers = st.slider("Deep workers", 1, 4, 2)
    min_shortlist_score = st.number_input("Minimum discovery score", 0.0, 100.0, 45.0, 1.0)
    min_atr = st.number_input("Minimum 15m ATR %", 0.5, 10.0, 1.8, 0.1)
    min_rr = st.number_input("Minimum R:R", 1.0, 5.0, 2.0, 0.1)
    news_gate = st.number_input("News gate", 0.0, 0.9, 0.0, 0.05)
    st.caption("No fixed 25/100 share cap. Every current NSE EQ symbol is scanned in discovery; every discovery-shortlisted name is sent through deep validation in controlled batches.")

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
        # Avoid a Streamlit render for every completed HTTP request; that can cause the blank/black
        # screen seen when the user raises the deep count while the app is already under load.
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

    # Discovery shortlist: no arbitrary top-N limit. The actual 15m ATR and 5m volume conditions
    # are applied by deep() against genuine intraday candles. Do not use the daily broad ATR as a
    # substitute for the intraday ATR requirement.
    shortlist = broad_df[pd.to_numeric(broad_df["BroadScore"], errors="coerce").ge(float(min_shortlist_score))].copy()
    shortlist = shortlist.sort_values("BroadScore", ascending=False).reset_index(drop=True)

    st.success(f"{len(broad_df):,} names scanned in discovery. {len(shortlist):,} discovery-shortlisted names will all receive deep analysis.")
    if shortlist.empty:
        return broad_df, shortlist, pd.DataFrame(), mkt

    deep_progress = st.progress(0.0, text=f"Deep analysis 0/{len(shortlist)}")
    live = st.empty()
    completed = []
    batch_size = 10

    # Every shortlisted share is analysed, but request concurrency is deliberately bounded.
    # This avoids launching hundreds of 5m+15m+60m+news requests at once.
    for start in range(0, len(shortlist), batch_size):
        batch = shortlist.iloc[start:start + batch_size].copy()
        result = deep(
            batch,
            mkt,
            min_atr=float(min_atr),
            min_vol=1.5,
            min_rr=float(min_rr),
            news_gate=float(news_gate),
            workers=int(deep_workers),
            on_update=None,
        )
        completed.append(result if not result.empty else pd.DataFrame())

        valid_parts = [x for x in completed if not x.empty]
        if valid_parts:
            current = pd.concat(valid_parts, ignore_index=True)
            current = current.drop_duplicates("Symbol", keep="last").sort_values(["Reliability", "R:R"], ascending=False).reset_index(drop=True)
            live.dataframe(current[signal_cols(current)], use_container_width=True, hide_index=True)

        done = min(start + len(batch), len(shortlist))
        deep_progress.progress(done / max(len(shortlist), 1), text=f"Deep analysis {done:,}/{len(shortlist):,} shortlisted shares completed")

    valid_parts = [x for x in completed if not x.empty]
    signals = pd.concat(valid_parts, ignore_index=True) if valid_parts else pd.DataFrame()
    if not signals.empty:
        signals = signals.drop_duplicates("Symbol", keep="last").sort_values(["Reliability", "R:R"], ascending=False).reset_index(drop=True)
    deep_progress.progress(1.0, text=f"Deep analysis complete: {len(shortlist):,}/{len(shortlist):,}")
    return broad_df, shortlist, signals, mkt


@st.fragment(run_every="60s")
def dashboard():
    st.info("The broad stage scans the complete current NSE EQ universe. There is no fixed stock-count cap. The discovery shortlist is score-gated, and every shortlisted name receives genuine 5m + 15m + 1h deep validation in controlled batches.")

    run_now = st.button("Run complete universe scan now", type="primary", use_container_width=True)
    if st.session_state.last_scan is None or run_now:
        if st.session_state.scanning:
            st.warning("A scan is already running. Wait for the current cycle to finish.")
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
        st.subheader("Complete Discovery Shortlist")
        sdf = st.session_state.shortlist.rename(columns={"price":"Price", "atr_pct":"Daily ATR %", "volshock":"Daily Volume Shock"})
        cols = [c for c in ["Symbol","Price","Daily ATR %","Daily Volume Shock","BroadScore"] if c in sdf.columns]
        st.dataframe(sdf[cols], use_container_width=True, hide_index=True)

    st.subheader("Deep Analysis — Every Shortlisted Share")
    signals = st.session_state.signals
    if signals.empty:
        st.info("No shortlisted share currently has a fully validated setup. A deep rejection is not converted into a trade signal.")
    else:
        st.dataframe(signals[signal_cols(signals)], use_container_width=True, hide_index=True)
        why = [c for c in ["Symbol","Grade","Reliability","Why","Catalyst","News Freshness Min","DataSource","Updated UTC"] if c in signals.columns]
        st.subheader("Deep-qualified setups")
        st.dataframe(signals[why].head(50), use_container_width=True, hide_index=True)
        st.download_button("Download deep-analysis results CSV", signals.to_csv(index=False).encode(), "intraday_deep_analysis.csv", "text/csv")

    st.caption("Streamlit-only on-demand mode. Full current NSE EQ universe in discovery; every rule-based shortlist member is deep-analysed. UI rendering and network concurrency are bounded to prevent blackout/throttling.")

dashboard()
