import io
import re
import time
from urllib.parse import quote_plus

import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf
import feedparser
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

st.set_page_config(page_title="Indian Intraday Alpha Scanner", page_icon="📈", layout="wide")

DEFAULT_NSE = "RELIANCE,TCS,HDFCBANK,ICICIBANK,SBIN,INFY,LT,AXISBANK,KOTAKBANK,BHARTIARTL,ITC,TATASTEEL,JIOFIN,IREDA,OLAELEC,ZOMATO,HAL,BAJFINANCE,SUNPHARMA,ADANIENT"
DEFAULT_BSE = "500325,532540,500180,543320,500470"

analyzer = SentimentIntensityAnalyzer()


def clean_symbols(text):
    return [x.strip().upper() for x in re.split(r"[,\n\s]+", text) if x.strip()]


def safe_float(x):
    try:
        return float(x)
    except Exception:
        return np.nan


def download(ticker, period, interval):
    try:
        df = yf.download(ticker, period=period, interval=interval, auto_adjust=False, progress=False, threads=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if df.empty:
            return pd.DataFrame()
        needed = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
        return df[needed].dropna(how="all")
    except Exception:
        return pd.DataFrame()


def atr(df, window=14):
    prev = df["Close"].shift(1)
    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - prev).abs(),
        (df["Low"] - prev).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(window).mean()


def ema(series, span):
    return series.ewm(span=span, adjust=False).mean()


def trend_signal(df):
    if len(df) < 30:
        return None
    x = df.copy()
    x["EMA9"] = ema(x["Close"], 9)
    x["EMA21"] = ema(x["Close"], 21)
    last = x.iloc[-1]
    if last["Close"] > last["EMA9"] and last["Close"] > last["EMA21"] and last["EMA9"] > last["EMA21"]:
        return "LONG"
    if last["Close"] < last["EMA9"] and last["Close"] < last["EMA21"] and last["EMA9"] < last["EMA21"]:
        return "SHORT"
    return "NEUTRAL"


def volume_shock(df, lookback=20):
    if len(df) < lookback + 1:
        return np.nan
    avg = df["Volume"].iloc[-lookback - 1:-1].mean()
    return (df["Volume"].iloc[-1] / avg) if avg and avg > 0 else np.nan


def breakout_score(df, lookback=20):
    if len(df) < lookback + 2:
        return 0.0, "NONE"
    last = df.iloc[-1]
    prior = df.iloc[-lookback - 1:-1]
    hi = prior["High"].max()
    lo = prior["Low"].min()
    if last["Close"] > hi:
        return 1.0, "UP_BREAKOUT"
    if last["Close"] < lo:
        return -1.0, "DOWN_BREAKOUT"
    return 0.0, "NONE"


def news_sentiment(symbol, max_items=5):
    clean = re.sub(r"[^A-Za-z0-9 ]", " ", symbol).strip()
    url = f"https://news.google.com/rss/search?q={quote_plus(clean + ' share price')}&hl=en-IN&gl=IN&ceid=IN:en"
    try:
        feed = feedparser.parse(url)
        scores, headlines = [], []
        for entry in feed.entries[:max_items]:
            title = entry.get("title", "")
            if title:
                headlines.append(title)
                scores.append(analyzer.polarity_scores(title)["compound"])
        return (float(np.mean(scores)) if scores else 0.0), headlines
    except Exception:
        return 0.0, []


def market_regime():
    n = download("^NSEI", "5d", "5m")
    b = download("^BSESN", "5d", "5m")
    vals = []
    for df in (n, b):
        if len(df) >= 2:
            vals.append((df["Close"].iloc[-1] / df["Close"].iloc[-2]) - 1.0)
    if not vals:
        return "UNKNOWN", np.nan
    avg = float(np.mean(vals))
    if avg > 0.001:
        return "BULLISH", avg * 100
    if avg < -0.001:
        return "BEARISH", avg * 100
    return "NEUTRAL", avg * 100


def scan_symbol(symbol, exchange, min_atr_pct, min_volume_shock, min_rr, require_news, ipo_days):
    ticker = f"{symbol}.{exchange}"
    daily = download(ticker, "120d", "1d")
    intraday_5m = download(ticker, "5d", "5m")
    intraday_1h = download(ticker, "60d", "60m")
    if daily.empty or intraday_5m.empty or intraday_1h.empty:
        return None
    if len(daily) < 20 or len(intraday_5m) < 30 or len(intraday_1h) < 30:
        return None

    daily = daily.copy()
    daily["ATR"] = atr(daily, 14)
    current_price = safe_float(intraday_5m["Close"].iloc[-1])
    current_atr = safe_float(daily["ATR"].iloc[-1])
    if not np.isfinite(current_price) or current_price <= 0 or not np.isfinite(current_atr):
        return None

    atr_pct = current_atr / current_price * 100
    vol_shock = volume_shock(intraday_5m)
    t5m = trend_signal(intraday_5m)
    t1h = trend_signal(intraday_1h)
    bscore, breakout = breakout_score(intraday_5m)
    is_new = len(daily) < ipo_days
    sentiment, headlines = news_sentiment(symbol)

    if atr_pct < min_atr_pct or not np.isfinite(vol_shock) or vol_shock < min_volume_shock:
        return None

    direction = None
    if t5m == "LONG" and t1h == "LONG":
        if (bscore > 0 or not require_news) and (not require_news or sentiment >= 0.60):
            direction = "LONG"
    elif t5m == "SHORT" and t1h == "SHORT":
        if (bscore < 0 or not require_news) and (not require_news or sentiment <= -0.60):
            direction = "SHORT"
    elif is_new and t5m == t1h and t5m in ("LONG", "SHORT") and (not require_news or abs(sentiment) >= 0.60):
        direction = t5m

    if not direction:
        return None

    if direction == "LONG":
        stop = current_price - 1.5 * current_atr
        target = current_price + 3.0 * current_atr
    else:
        stop = current_price + 1.5 * current_atr
        target = current_price - 3.0 * current_atr

    risk = abs(current_price - stop)
    reward = abs(target - current_price)
    rr = reward / risk if risk > 0 else 0
    if rr < min_rr:
        return None

    market, market_move = market_regime()
    alignment = (direction == "LONG" and market == "BULLISH") or (direction == "SHORT" and market == "BEARISH")
    alpha = rr * (1 + min(abs(sentiment), 1.0) * 0.35) * (1 + min(max(vol_shock - 1, 0), 4) * 0.08)
    if alignment:
        alpha *= 1.06
    if (direction == "LONG" and sentiment > 0.6) or (direction == "SHORT" and sentiment < -0.6):
        alpha *= 1.08

    return {
        "Ticker": ticker,
        "Symbol": symbol,
        "Type": "NEW IPO/RECENT" if is_new else "LEGACY",
        "Direction": direction,
        "Price": round(current_price, 2),
        "Entry Zone": f"{current_price * 0.998:.2f} - {current_price * 1.002:.2f}",
        "Stop Loss": round(stop, 2),
        "Target": round(target, 2),
        "R:R": round(rr, 2),
        "ATR %": round(atr_pct, 2),
        "Vol Shock": round(vol_shock, 2),
        "5m Trend": t5m,
        "1h Trend": t1h,
        "Breakout": breakout,
        "News": round(sentiment, 2),
        "Market": market,
        "Market Move %": round(market_move, 3) if np.isfinite(market_move) else np.nan,
        "Volume": int(safe_float(intraday_5m["Volume"].iloc[-1])) if np.isfinite(safe_float(intraday_5m["Volume"].iloc[-1])) else 0,
        "Expected Move %": round(abs(target - current_price) / current_price * 100, 2),
        "Risk %": round(risk / current_price * 100, 2),
        "Alpha Score": round(alpha, 3),
        "Catalyst": " | ".join(headlines[:2]) if headlines else "No matching headline",
    }


@st.cache_data(ttl=240, show_spinner=False)
def run_scan(nse_text, bse_text, min_atr_pct, min_volume_shock, min_rr, require_news, ipo_days):
    nse = clean_symbols(nse_text)
    bse = clean_symbols(bse_text)
    rows = []
    progress = st.session_state.get("scan_progress")
    universe = [(s, "NS") for s in nse] + [(s, "BO") for s in bse]
    total = len(universe)
    for i, (symbol, exchange) in enumerate(universe, start=1):
        result = scan_symbol(symbol, exchange, min_atr_pct, min_volume_shock, min_rr, require_news, ipo_days)
        if result:
            rows.append(result)
        if progress is not None:
            progress.progress(i / max(total, 1))
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["Alpha Score", "R:R"], ascending=False).reset_index(drop=True)
    return df


st.title("Indian Intraday Alpha Scanner")
st.caption("NSE + BSE | Liquidity + Volatility + 5m/1h Trend + News Catalyst + ATR Risk/Reward")

with st.sidebar:
    st.header("Scanner Settings")
    nse_text = st.text_area("NSE symbols", DEFAULT_NSE, height=140)
    bse_text = st.text_area("BSE symbols", DEFAULT_BSE, height=100)
    min_atr_pct = st.number_input("Minimum ATR %", 0.5, 10.0, 1.8, 0.1)
    min_volume_shock = st.number_input("Minimum volume shock", 0.5, 10.0, 1.5, 0.1)
    min_rr = st.number_input("Minimum R:R", 1.0, 5.0, 2.0, 0.1)
    require_news = st.checkbox("Require news catalyst", value=False)
    ipo_days = st.number_input("Recent-listing day threshold", 20, 120, 60, 5)
    st.info("Signals are research outputs, not guaranteed trading recommendations.")

run = st.button("RUN SCANNER", type="primary", use_container_width=True)

if run:
    st.session_state.scan_progress = st.progress(0)
    with st.spinner("Scanning NSE/BSE universe..."):
        df = run_scan(nse_text, bse_text, min_atr_pct, min_volume_shock, min_rr, require_news, ipo_days)
    st.session_state.scan_progress.empty()
    st.session_state["results"] = df
else:
    df = st.session_state.get("results", pd.DataFrame())

if df.empty:
    st.warning("No qualifying setups yet. Run the scanner or relax the filters.")
else:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Qualified Setups", len(df))
    c2.metric("Long", int((df["Direction"] == "LONG").sum()))
    c3.metric("Short", int((df["Direction"] == "SHORT").sum()))
    c4.metric("Best Alpha", float(df["Alpha Score"].max()))

    st.subheader("Ranked Intraday Opportunities")
    display_cols = ["Symbol", "Type", "Direction", "Price", "Entry Zone", "Stop Loss", "Target", "R:R", "ATR %", "Vol Shock", "5m Trend", "1h Trend", "Breakout", "News", "Market", "Alpha Score"]
    st.dataframe(df[display_cols], use_container_width=True, hide_index=True)

    st.subheader("Top 5 Setups")
    st.dataframe(df.head(5), use_container_width=True, hide_index=True)

    csv = df.to_csv(index=False).encode("utf-8")
    st.download_button("Download CSV", csv, "intraday_alpha_results.csv", "text/csv", use_container_width=True)

st.caption("Data availability, latency and rate limits depend on the public data sources. Validate signals independently before any trade.")
