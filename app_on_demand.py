from __future__ import annotations

from datetime import datetime, timezone
import pandas as pd
import streamlit as st
from scanner_engine import broad, deep, market, nse_universe

st.set_page_config(page_title="Indian Intraday Alpha Scanner", page_icon="📈", layout="wide")
st.title("Indian Intraday Alpha Scanner")
st.caption("On-demand complete current NSE EQ universe → 15m + 1h confirmation → 5m trigger → market/news response → reliability ranking")

with st.sidebar:
    deep_limit = st.number_input("Deep candidates", 5, 100, 25, 5)
    workers = st.slider("Parallel workers", 2, 8, 6)
    min_atr = st.number_input("Minimum 15m ATR %", 0.5, 10.0, 1.8, 0.1)
    min_vol = st.number_input("Minimum 5m volume shock", 0.5, 10.0, 1.5, 0.1)
    min_rr = st.number_input("Minimum R:R", 1.0, 5.0, 2.0, 0.1)
    news_gate = st.number_input("News gate", 0.0, 0.9, 0.0, 0.05)
    st.select_slider("Refresh while open", [60, 90, 120, 180], value=60)
    st.caption("No fixed stock-count cap. Every current NSE EQ symbol returned by the exchange security master is sent to the broad scanner. New EQ listings are included after the security master refreshes.")

@st.cache_data(ttl=900, show_spinner=False)
def load_universe():
    return nse_universe()

for key, default in {
    "cache": {}, "signals": pd.DataFrame(), "last_scan": None,
    "market": {}, "broad": pd.DataFrame()
}.items():
    if key not in st.session_state:
        st.session_state[key] = default


def scan_cycle():
    uni = load_universe()
    if uni.empty:
        return pd.DataFrame(), pd.DataFrame(), {"regime": "NO_DATA", "move15": None}
    symbols = uni["Symbol"].dropna().astype(str).str.upper().drop_duplicates().tolist()
    mkt = market()
    progress = st.progress(0.0, text=f"Broad scan 0/{len(symbols)} — complete current NSE EQ universe")
    preview = st.empty()

    def on_broad(done, total, partial):
        progress.progress(done / max(total, 1), text=f"Broad scan {done:,}/{total:,} — complete current NSE EQ universe")
        for row in partial.to_dict("records"):
            st.session_state.cache[row["Symbol"]] = row
        if st.session_state.cache:
            view = pd.DataFrame(st.session_state.cache.values()).sort_values("BroadScore", ascending=False).head(30)
            view = view.rename(columns={"price":"Price", "atr_pct":"ATR %", "volshock":"Volume Shock", "ret5":"Recent Return %"})
            cols = [c for c in ["Symbol","Price","ATR %","Volume Shock","Recent Return %","BroadScore"] if c in view.columns]
            preview.dataframe(view[cols], use_container_width=True, hide_index=True)

    broad_df = broad(symbols, exchange="NS", workers=int(workers), on_update=on_broad)
    if broad_df.empty:
        return broad_df, pd.DataFrame(), mkt
    for row in broad_df.to_dict("records"):
        st.session_state.cache[row["Symbol"]] = row

    # The broad scan has no fixed symbol-count cap. Deep analysis is deliberately
    # selective because each deep candidate needs 5m + 15m + 60m data plus news.
    pool = pd.DataFrame(st.session_state.cache.values()).sort_values("BroadScore", ascending=False)
    candidates = pool.head(min(int(deep_limit), len(pool))).copy()
    p2 = st.progress(0.0, text=f"Deep validation 0/{len(candidates)}")
    box = st.empty()

    def on_deep(done, total, partial):
        p2.progress(done / max(total, 1), text=f"Deep validation {done:,}/{total:,}")
        if not partial.empty:
            view = partial.sort_values(["Reliability", "R:R"], ascending=False).reset_index(drop=True)
            st.session_state.signals = view
            cols = [c for c in ["Grade","Reliability","Symbol","Direction","Price","Entry","Stop Loss","Target","R:R","ATR %","Execution ATR %","Volume Shock","5m Trend","15m Trend","1h Trend","5m Breakout","15m Breakout","Relative Strength","News Score","News Status","Market Regime","Broker Eligibility"] if c in view.columns]
            box.dataframe(view[cols], use_container_width=True, hide_index=True)

    result = deep(candidates, mkt, min_atr=float(min_atr), min_vol=float(min_vol), min_rr=float(min_rr), news_gate=float(news_gate), workers=max(2, int(workers // 2)), on_update=on_deep)
    return broad_df, result, mkt


@st.fragment(run_every="60s")
def dashboard():
    st.info("FULL CURRENT UNIVERSE: no fixed 500/800/1,000-stock cap. Every current NSE EQ security from the exchange security master is scanned in the broad stage. Future EQ additions are picked up automatically after the universe refresh.")
    if st.session_state.last_scan is None or st.button("Run complete NSE universe scan now", type="primary", use_container_width=True):
        broad_df, signals, mkt = scan_cycle()
        st.session_state.broad = broad_df
        st.session_state.market = mkt
        st.session_state.signals = signals if not signals.empty else pd.DataFrame()
        st.session_state.last_scan = datetime.now(timezone.utc)

    mkt = st.session_state.market
    st.write(f"Last scan UTC: {st.session_state.last_scan.isoformat(timespec='seconds') if st.session_state.last_scan else 'waiting'}")
    a, b, c, d = st.columns(4)
    a.metric("Market regime", mkt.get("regime", "UNKNOWN"))
    move = mkt.get("move15")
    b.metric("15m market move", f"{move:+.3f}%" if isinstance(move, (int, float)) else "N/A")
    c.metric("Current NSE EQ universe", f"{len(load_universe()):,}")
    d.metric("Broad data-valid", f"{len(st.session_state.cache):,}")

    if not st.session_state.broad.empty:
        st.subheader("Latest Broad Results")
        bdf = st.session_state.broad.head(50).rename(columns={"price":"Price","atr_pct":"ATR %","volshock":"Volume Shock","ret5":"Recent Return %"})
        cols = [c for c in ["Symbol","Price","ATR %","Volume Shock","Recent Return %","BroadScore"] if c in bdf.columns]
        st.dataframe(bdf[cols], use_container_width=True, hide_index=True)

    st.subheader("Highest Reliability → Lowest")
    signals = st.session_state.signals
    if signals.empty:
        st.info("No fully validated setup currently meets the rules. The full-universe scanner continues while the page is open.")
    else:
        cols = [c for c in ["Grade","Reliability","Symbol","Direction","Price","Entry","Stop Loss","Target","R:R","ATR %","Execution ATR %","Volume Shock","5m Trend","15m Trend","1h Trend","5m Breakout","15m Breakout","Relative Strength","News Score","News Status","Market Regime","Broker Eligibility"] if c in signals.columns]
        st.dataframe(signals[cols], use_container_width=True, hide_index=True)
        why = [c for c in ["Symbol","Grade","Reliability","Why","Catalyst","News Freshness Min","DataSource","Updated UTC"] if c in signals.columns]
        st.subheader("Why each setup qualified")
        st.dataframe(signals.head(20)[why], use_container_width=True, hide_index=True)
        st.download_button("Download current validated signals CSV", signals.to_csv(index=False).encode(), "intraday_signals.csv", "text/csv")

    st.caption("Streamlit-only on-demand mode. Broad scan = complete current NSE EQ universe, without a fixed stock-count limit. Deep validation remains selective to keep the live session practical. No synthetic market data is used.")

dashboard()
