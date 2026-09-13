from __future__ import annotations

from datetime import datetime, timezone
import numpy as np
import pandas as pd
import streamlit as st

from scanner_engine import broad, deep, market, nse_universe, symbols

st.set_page_config(page_title="Indian Intraday Alpha Scanner", page_icon="📈", layout="wide")

st.title("Indian Intraday Alpha Scanner")
st.caption("Full NSE equity universe → liquidity/volatility pre-filter → 5m + 1h confirmation → news + market response → reliability ranking")

DEFAULT_BSE = "500325,532540,500180,543320,500470"

@st.cache_data(ttl=1800, show_spinner=False)
def load_universe():
    return nse_universe()

@st.cache_data(ttl=900, show_spinner=False)
def broad_cached(syms, exchange, workers):
    return broad(list(syms), exchange=exchange, workers=workers)

@st.cache_data(ttl=55, show_spinner=False)
def deep_cached(candidates_json, market_json, min_atr, min_vol, min_rr, news_gate, workers):
    cand = pd.read_json(candidates_json, orient="split")
    return deep(cand, market_json, min_atr=min_atr, min_vol=min_vol, min_rr=min_rr, news_gate=news_gate, workers=workers)

with st.sidebar:
    st.header("Universe")
    mode = st.radio("NSE universe", ["Full NSE EQ", "Top N by liquidity"], index=0)
    top_n = st.number_input("Top N broad candidates", min_value=50, max_value=1000, value=250, step=50, disabled=mode == "Full NSE EQ")
    bse_text = st.text_area("BSE scrip codes (optional)", DEFAULT_BSE, height=100)
    st.caption("NSE universe is pulled from the exchange securities master. Broker-specific intraday eligibility can differ by broker/product.")

    st.header("Signal Rules")
    min_atr = st.number_input("Minimum ATR %", 0.5, 10.0, 1.8, 0.1)
    min_vol = st.number_input("Minimum 5m volume shock", 0.5, 10.0, 1.5, 0.1)
    min_rr = st.number_input("Minimum R:R", 1.0, 5.0, 2.0, 0.1)
    news_gate = st.number_input("Strong news gate (0 = news is a weight)", 0.0, 0.9, 0.0, 0.05)
    refresh = st.toggle("Auto refresh", True)
    workers = st.slider("Parallel workers", 4, 24, 12)

st.info("Live-style mode refreshes during the market session. It does not place orders. Public/free data can be delayed or rate-limited, so reliability is a ranking score, not a guarantee.")

@st.fragment(run_every="60s")
def live_panel():
    now = datetime.now(timezone.utc)
    st.write(f"Last engine refresh (UTC): {now.strftime('%Y-%m-%d %H:%M:%S')}")
    if not refresh:
        st.caption("Auto refresh is disabled. Use the browser refresh button or enable Auto refresh.")

    uni = load_universe()
    nse_syms = uni["Symbol"].tolist()
    if mode == "Top N by liquidity":
        broad_df = broad_cached(tuple(nse_syms), "NS", workers)
        broad_df = broad_df.head(int(top_n)).copy()
    else:
        broad_df = broad_cached(tuple(nse_syms), "NS", workers)
        broad_df = broad_df.head(400).copy()

    mkt = market()
    if broad_df.empty:
        st.error("The broad market-data pass returned no candidates. Check data-source availability.")
        return

    st.subheader("Market Response")
    a, b, c = st.columns(3)
    a.metric("NIFTY/SENSEX regime", mkt["regime"])
    b.metric("15m market move", f"{mkt['move15']:+.3f}%")
    c.metric("Exchange universe", f"{len(nse_syms):,} NSE EQ symbols")

    # Deep pass focuses the expensive intraday/news calculations on the most tradeable names.
    deep_df = deep_cached(broad_df.to_json(orient="split"), mkt, float(min_atr), float(min_vol), float(min_rr), float(news_gate), int(workers))

    if deep_df.empty:
        st.warning("No A/B-grade setup currently meets the confluence rules. Capital preserved; the engine keeps watching.")
    else:
        st.subheader("Highest Reliability → Lowest")
        cols = ["Grade","Reliability","Symbol","Direction","Price","Entry","Stop Loss","Target","R:R","ATR %","Volume Shock","5m Trend","1h Trend","Breakout","Relative Strength","News Score","Market Regime"]
        st.dataframe(deep_df[cols], use_container_width=True, hide_index=True)

        left, right = st.columns(2)
        with left:
            st.subheader("Top 10")
            st.dataframe(deep_df.head(10)[cols], use_container_width=True, hide_index=True)
        with right:
            st.subheader("Signal Logic")
            st.dataframe(deep_df.head(10)[["Symbol","Why","Catalyst"]], use_container_width=True, hide_index=True)

        st.subheader("Detailed Result")
        st.dataframe(deep_df, use_container_width=True, hide_index=True)
        st.download_button("Download current signals CSV", deep_df.to_csv(index=False).encode("utf-8"), "intraday_signals.csv", "text/csv")

live_panel()

st.markdown("---")
st.caption("Strategic model: liquidity + ATR + 5m/1h trend alignment + breakout/relative strength + news sentiment + market regime + ATR-based 1:2 target/stop structure. NSE publishes an exchange security master and real-time RSS feeds; exchange/broker eligibility and data latency should be validated independently before trading.")
