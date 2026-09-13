from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from threading import Lock
from urllib.parse import quote_plus

import numpy as np
import pandas as pd
import requests
from feedparser import parse as parse_feed
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

NSE_MASTER = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/153 Safari/537.36"}
FALLBACK: list[str] = []  # Compatibility only. Never used as a synthetic market universe.
VADER = SentimentIntensityAnalyzer()
NEWS_CACHE: dict[str, tuple[float, tuple[float, list[str], float | None]]] = {}
NEWS_LOCK = Lock()
NEWS_TTL_SECONDS = 300


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def nse_universe() -> pd.DataFrame:
    """Return the current NSE EQ universe. Never invent a fallback universe."""
    try:
        r = requests.get(NSE_MASTER, headers=UA, timeout=20)
        r.raise_for_status()
        raw = pd.read_csv(pd.io.common.BytesIO(r.content))
        names = {str(c).strip().upper(): c for c in raw.columns}
        sc, ser = names.get("SYMBOL"), names.get("SERIES")
        if not sc:
            raise ValueError("NSE security master has no SYMBOL column")
        out = pd.DataFrame({"Symbol": raw[sc].astype(str).str.upper().str.strip()})
        if ser:
            out["Series"] = raw[ser].astype(str).str.upper().str.strip()
            out = out[out["Series"].eq("EQ")]
        out = out[out["Symbol"].str.match(r"^[A-Z0-9&._-]+$")]
        out = out.drop_duplicates("Symbol").reset_index(drop=True)
        return out
    except Exception:
        return pd.DataFrame(columns=["Symbol", "Series"])


def chart(ticker: str, interval: str, period: str) -> pd.DataFrame:
    """Fetch only the requested timeframe. Intraday failures do not fall back to daily data."""
    try:
        r = requests.get(
            YAHOO.format(ticker=quote_plus(ticker)),
            headers=UA,
            params={"range": period, "interval": interval, "events": "div,splits"},
            timeout=10,
        )
        r.raise_for_status()
        result = ((r.json().get("chart") or {}).get("result") or [None])[0]
        if not result or not result.get("timestamp"):
            return pd.DataFrame()
        quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        idx = pd.to_datetime(result["timestamp"], unit="s", utc=True)
        return pd.DataFrame(
            {
                "Open": quote.get("open", []),
                "High": quote.get("high", []),
                "Low": quote.get("low", []),
                "Close": quote.get("close", []),
                "Volume": quote.get("volume", []),
            },
            index=idx,
        ).dropna(subset=["Close"]).sort_index()
    except Exception:
        return pd.DataFrame()


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    prev = df["Close"].shift(1)
    tr = pd.concat(
        [df["High"] - df["Low"], (df["High"] - prev).abs(), (df["Low"] - prev).abs()],
        axis=1,
    ).max(axis=1)
    return tr.rolling(window, min_periods=window).mean()


def _period_bars(interval: str, minutes: int) -> int:
    mapping = {"1m": 1, "2m": 2, "5m": 5, "15m": 15, "30m": 30, "60m": 60, "90m": 90}
    base = mapping.get(interval, 1)
    return max(1, int(np.ceil(minutes / base)))


def indicators(df: pd.DataFrame, interval: str = "15m") -> dict:
    if len(df) < 30:
        return {}
    x = df.copy()
    x["ATR"] = atr(x, 14)
    x["EMA9"] = ema(x["Close"], 9)
    x["EMA21"] = ema(x["Close"], 21)
    last = x.iloc[-1]
    try:
        p, atrv = float(last["Close"]), float(last["ATR"])
    except (TypeError, ValueError):
        return {}
    if p <= 0 or not np.isfinite(atrv):
        return {}

    prior_vol = pd.to_numeric(x["Volume"].iloc[-21:-1], errors="coerce").mean()
    vol = float(last["Volume"]) if pd.notna(last["Volume"]) else 0.0
    volshock = vol / prior_vol if prior_vol and prior_vol > 0 else np.nan
    trend = "LONG" if p > last["EMA9"] > last["EMA21"] else ("SHORT" if p < last["EMA9"] < last["EMA21"] else "NEUTRAL")
    hi20 = x["High"].iloc[-21:-1].max()
    lo20 = x["Low"].iloc[-21:-1].min()
    breakout = "UP" if p > hi20 else ("DOWN" if p < lo20 else "NONE")

    def pct_change_bars(bars: int) -> float:
        if len(x) <= bars:
            return np.nan
        return float((p / x["Close"].iloc[-1 - bars] - 1) * 100)

    ret5 = pct_change_bars(_period_bars(interval, 5))
    ret15 = pct_change_bars(_period_bars(interval, 15))
    return {
        "price": p,
        "atr": atrv,
        "atr_pct": atrv / p * 100,
        "volume": vol,
        "volshock": volshock,
        "trend": trend,
        "breakout": breakout,
        "ret1": pct_change_bars(1),
        "ret5": ret5,
        "ret15": ret15,
        "bar_time": x.index[-1].isoformat(),
        "interval": interval,
    }


def news(symbol: str, limit: int = 6) -> tuple[float, list[str], float | None]:
    """Return sentiment plus the age in minutes of the newest headline."""
    now = utc_now().timestamp()
    with NEWS_LOCK:
        cached = NEWS_CACHE.get(symbol)
        if cached and now - cached[0] < NEWS_TTL_SECONDS:
            return cached[1]
    try:
        url = "https://news.google.com/rss/search?q=" + quote_plus(symbol + " share price India") + "&hl=en-IN&gl=IN&ceid=IN:en"
        feed = parse_feed(url)
        headlines: list[str] = []
        scores: list[float] = []
        newest_age: float | None = None
        now_dt = utc_now()
        for entry in feed.entries[:limit]:
            title = str(entry.get("title", "")).strip()
            if not title:
                continue
            published = entry.get("published") or entry.get("updated")
            age_minutes: float | None = None
            if published:
                try:
                    dt = parsedate_to_datetime(str(published)).astimezone(timezone.utc)
                    age_minutes = max(0.0, (now_dt - dt).total_seconds() / 60.0)
                except Exception:
                    age_minutes = None
            headlines.append(title)
            scores.append(float(VADER.polarity_scores(title)["compound"]))
            if age_minutes is not None:
                newest_age = age_minutes if newest_age is None else min(newest_age, age_minutes)
        result = (float(np.mean(scores)) if scores else 0.0, headlines, newest_age)
        with NEWS_LOCK:
            NEWS_CACHE[symbol] = (now, result)
        return result
    except Exception:
        return 0.0, [], None


def market() -> dict:
    values, moves = {}, []
    for name, ticker in (("NIFTY", "^NSEI"), ("SENSEX", "^BSESN")):
        df = chart(ticker, "5m", "5d")
        snap = indicators(df, "5m") if not df.empty else {}
        values[name] = snap
        if np.isfinite(snap.get("ret15", np.nan)):
            moves.append(snap["ret15"])
    move15 = float(np.mean(moves)) if moves else np.nan
    regime = "BULLISH" if np.isfinite(move15) and move15 > 0.25 else ("BEARISH" if np.isfinite(move15) and move15 < -0.25 else "NEUTRAL")
    return {
        "move15": move15 if np.isfinite(move15) else None,
        "regime": regime if moves else "NO_DATA",
        "indices": values,
        "updated": utc_now().isoformat(timespec="seconds"),
    }


def broad(symbol_list: list[str], exchange="NS", workers=16, cap=None, on_update=None) -> pd.DataFrame:
    todo = symbol_list[:cap] if cap else symbol_list
    rows, done, total = [], 0, len(todo)

    def one(symbol: str):
        ticker = f"{symbol}.{exchange}"
        df = chart(ticker, "1d", "4mo")
        snap = indicators(df, "1d")
        if not snap:
            return None
        dollar_volume = snap["price"] * snap["volume"]
        daily_move = abs(snap["ret5"]) if np.isfinite(snap["ret5"]) else 0.0
        score = (
            min(np.log1p(max(dollar_volume, 0)) / 30, 1) * 45
            + min(max(snap["atr_pct"], 0) / 8, 1) * 30
            + min(daily_move / 8, 1) * 25
        )
        return {
            "Symbol": symbol,
            "Ticker": ticker,
            **snap,
            "DollarVolume": dollar_volume,
            "BroadScore": score,
            "DataSource": "YAHOO_HTTP",
        }

    with ThreadPoolExecutor(max_workers=min(workers, max(1, total))) as ex:
        futures = {ex.submit(one, symbol): symbol for symbol in todo}
        for future in as_completed(futures):
            done += 1
            try:
                result = future.result()
                if result:
                    rows.append(result)
            except Exception:
                pass
            if on_update:
                partial = pd.DataFrame(rows)
                if not partial.empty:
                    partial = partial.sort_values("BroadScore", ascending=False).reset_index(drop=True)
                on_update(done, total, partial)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("BroadScore", ascending=False).reset_index(drop=True)


def deep(
    candidates: pd.DataFrame,
    mkt: dict,
    min_atr=1.8,
    min_vol=1.5,
    min_rr=2.0,
    news_gate=0.0,
    workers=12,
    on_update=None,
) -> pd.DataFrame:
    if candidates.empty:
        return pd.DataFrame()
    out, done, total = [], 0, len(candidates)

    def one(row):
        symbol = row["Symbol"]
        ticker = row["Ticker"]
        m5 = chart(ticker, "5m", "5d")
        m15 = chart(ticker, "15m", "60d")
        h1 = chart(ticker, "60m", "60d")
        if m5.empty or m15.empty or h1.empty:
            return None
        a5, a15, a1 = indicators(m5, "5m"), indicators(m15, "15m"), indicators(h1, "60m")
        if not a5 or not a15 or not a1:
            return None
        price = a5["price"]
        exec_atr = a5["atr"]
        volatility_atr_pct = a15["atr_pct"]
        volshock = a5["volshock"]
        if not all(np.isfinite(x) for x in (price, exec_atr, volatility_atr_pct, volshock)):
            return None
        if volatility_atr_pct < min_atr or volshock < min_vol:
            return None

        market_move15 = mkt.get("move15")
        if market_move15 is None or not np.isfinite(market_move15):
            return None
        relative_strength = a5["ret15"] - market_move15
        long_ok = (
            a15["trend"] == "LONG"
            and a1["trend"] == "LONG"
            and relative_strength > 0
            and a5["ret5"] > 0
            and (a5["breakout"] == "UP" or a15["breakout"] == "UP")
        )
        short_ok = (
            a15["trend"] == "SHORT"
            and a1["trend"] == "SHORT"
            and relative_strength < 0
            and a5["ret5"] < 0
            and (a5["breakout"] == "DOWN" or a15["breakout"] == "DOWN")
        )
        if not (long_ok or short_ok):
            return None

        direction = "LONG" if long_ok else "SHORT"
        news_score, headlines, newest_age = news(symbol)
        news_live = newest_age is not None and newest_age <= 120
        news_aligned = (direction == "LONG" and news_score >= 0) or (direction == "SHORT" and news_score <= 0)
        if news_gate and (not news_live or abs(news_score) < news_gate or not news_aligned):
            return None

        sl = price - 1.5 * exec_atr if direction == "LONG" else price + 1.5 * exec_atr
        target = price + 3.0 * exec_atr if direction == "LONG" else price - 3.0 * exec_atr
        risk = abs(price - sl)
        reward = abs(target - price)
        rr = reward / risk if risk else 0.0
        if rr < min_rr:
            return None

        market_aligned = (direction == "LONG" and mkt["regime"] == "BULLISH") or (direction == "SHORT" and mkt["regime"] == "BEARISH")
        reliability = (
            25
            + min(max(volshock - 1, 0) / 2, 1) * 20
            + min(abs(relative_strength), 1) * 18
            + (min(abs(news_score) / 0.8, 1) * 10 if news_live else 0)
            + (10 if market_aligned else 4)
            + min(max(float(row.get("BroadScore", 0)) / 100, 0), 1) * 12
            + (5 if news_live and news_aligned else 0)
        )
        reliability = min(reliability, 100)
        grade = "A+" if reliability >= 90 else ("A" if reliability >= 80 else ("B" if reliability >= 70 else "C"))

        return {
            "Symbol": symbol,
            "Ticker": ticker,
            "Direction": direction,
            "Reliability": round(reliability, 1),
            "Grade": grade,
            "Price": round(price, 2),
            "Entry": f"{price*0.998:.2f} - {price*1.002:.2f}",
            "Stop Loss": round(sl, 2),
            "Target": round(target, 2),
            "R:R": round(rr, 2),
            "ATR %": round(volatility_atr_pct, 2),
            "Execution ATR %": round(exec_atr / price * 100, 2),
            "Volume Shock": round(volshock, 2),
            "5m Trend": a5["trend"],
            "15m Trend": a15["trend"],
            "1h Trend": a1["trend"],
            "5m Breakout": a5["breakout"],
            "15m Breakout": a15["breakout"],
            "15m Stock %": round(a5["ret15"], 3),
            "15m Market %": round(market_move15, 3),
            "Relative Strength": round(relative_strength, 3),
            "News Score": round(news_score, 2),
            "News Freshness Min": round(newest_age, 1) if newest_age is not None else None,
            "News Status": "RECENT" if news_live else ("STALE" if headlines else "NO_HEADLINE"),
            "Market Regime": mkt["regime"],
            "Broker Eligibility": "NOT_VERIFIED",
            "Catalyst": " | ".join(headlines[:2]) if headlines else "No matching headline",
            "Why": (
                f"{a15['trend']} 15m + {a1['trend']} 1h; actual 5m trigger {a5['ret5']:+.2f}%; "
                f"volume {volshock:.1f}x; RS {relative_strength:+.2f}%; news {news_score:+.2f}; market {mkt['regime']}"
            ),
            "DataSource": "YAHOO_HTTP",
            "Updated UTC": utc_now().isoformat(timespec="seconds"),
        }

    with ThreadPoolExecutor(max_workers=min(workers, max(1, total))) as ex:
        futures = [ex.submit(one, row) for _, row in candidates.iterrows()]
        for future in as_completed(futures):
            done += 1
            try:
                result = future.result()
                if result:
                    out.append(result)
            except Exception:
                pass
            if on_update:
                partial = pd.DataFrame(out)
                if not partial.empty:
                    partial = partial.sort_values(["Reliability", "R:R"], ascending=False).reset_index(drop=True)
                on_update(done, total, partial)

    if not out:
        return pd.DataFrame()
    return pd.DataFrame(out).sort_values(["Reliability", "R:R"], ascending=False).reset_index(drop=True)
