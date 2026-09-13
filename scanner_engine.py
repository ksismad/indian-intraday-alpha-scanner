from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import quote_plus

import numpy as np
import pandas as pd
import requests
from feedparser import parse as parse_feed
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

NSE_MASTER = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/153 Safari/537.36"}
FALLBACK = ["RELIANCE","TCS","HDFCBANK","ICICIBANK","SBIN","INFY","LT","AXISBANK","KOTAKBANK","BHARTIARTL","ITC","TATASTEEL","JIOFIN","IREDA","OLAELEC","ZOMATO","HAL","BAJFINANCE","SUNPHARMA","ADANIENT"]
VADER = SentimentIntensityAnalyzer()


def nse_universe() -> pd.DataFrame:
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
        return out.drop_duplicates("Symbol").reset_index(drop=True)
    except Exception:
        return pd.DataFrame({"Symbol": FALLBACK, "Series": "EQ"})


def chart(ticker: str, interval: str, period: str) -> pd.DataFrame:
    try:
        r = requests.get(
            YAHOO.format(ticker=quote_plus(ticker)), headers=UA,
            params={"range": period, "interval": interval, "events": "div,splits"}, timeout=10,
        )
        r.raise_for_status()
        result = ((r.json().get("chart") or {}).get("result") or [None])[0]
        if not result or not result.get("timestamp"):
            return pd.DataFrame()
        q = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        idx = pd.to_datetime(result["timestamp"], unit="s", utc=True)
        return pd.DataFrame({
            "Open": q.get("open", []), "High": q.get("high", []), "Low": q.get("low", []),
            "Close": q.get("close", []), "Volume": q.get("volume", [])
        }, index=idx).dropna(subset=["Close"]).sort_index()
    except Exception:
        return pd.DataFrame()


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    prev = df["Close"].shift(1)
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - prev).abs(), (df["Low"] - prev).abs()], axis=1).max(axis=1)
    return tr.rolling(window).mean()


def indicators(df: pd.DataFrame) -> dict:
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
    hi20, lo20 = x["High"].iloc[-21:-1].max(), x["Low"].iloc[-21:-1].min()
    breakout = "UP" if p > hi20 else ("DOWN" if p < lo20 else "NONE")
    ret1 = (p / x["Close"].iloc[-2] - 1) * 100 if len(x) >= 2 else np.nan
    ret5 = (p / x["Close"].iloc[-6] - 1) * 100 if len(x) >= 6 else np.nan
    ret15 = (p / x["Close"].iloc[-16] - 1) * 100 if len(x) >= 16 else np.nan
    return {"price": p, "atr": atrv, "atr_pct": atrv / p * 100, "volume": vol, "volshock": volshock,
            "trend": trend, "breakout": breakout, "ret1": ret1, "ret5": ret5, "ret15": ret15,
            "bar_time": x.index[-1].isoformat()}


def news(symbol: str, limit: int = 6) -> tuple[float, list[str]]:
    try:
        url = "https://news.google.com/rss/search?q=" + quote_plus(symbol + " share price India") + "&hl=en-IN&gl=IN&ceid=IN:en"
        feed = parse_feed(url)
        hs = [str(e.get("title", "")).strip() for e in feed.entries[:limit] if e.get("title")]
        ss = [VADER.polarity_scores(h)["compound"] for h in hs]
        return (float(np.mean(ss)) if ss else 0.0), hs
    except Exception:
        return 0.0, []


def market() -> dict:
    values, moves = {}, []
    for name, ticker in (("NIFTY", "^NSEI"), ("SENSEX", "^BSESN")):
        df = chart(ticker, "5m", "5d")
        snap = indicators(df) if not df.empty else {}
        values[name] = snap
        if np.isfinite(snap.get("ret15", np.nan)):
            moves.append(snap["ret15"])
    move15 = float(np.mean(moves)) if moves else 0.0
    regime = "BULLISH" if move15 > 0.25 else ("BEARISH" if move15 < -0.25 else "NEUTRAL")
    return {"move15": move15, "regime": regime, "indices": values, "updated": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def broad(symbol_list: list[str], exchange="NS", workers=16, cap=None, on_update=None) -> pd.DataFrame:
    todo = symbol_list[:cap] if cap else symbol_list
    rows, done, total = [], 0, len(todo)
    def one(s):
        df = chart(f"{s}.{exchange}", "1d", "4mo")
        snap = indicators(df)
        if not snap:
            return None
        dollar_volume = snap["price"] * snap["volume"]
        score = (min(np.log1p(max(dollar_volume, 0)) / 30, 1) * 45
                 + min(max(snap["atr_pct"], 0) / 8, 1) * 30
                 + min(abs(snap["ret15"]) / 5, 1) * 25)
        return {"Symbol": s, "Ticker": f"{s}.{exchange}", **snap, "DollarVolume": dollar_volume, "BroadScore": score}
    with ThreadPoolExecutor(max_workers=min(workers, max(1, total))) as ex:
        futures = {ex.submit(one, s): s for s in todo}
        for future in as_completed(futures):
            done += 1
            try:
                result = future.result()
                if result:
                    rows.append(result)
            except Exception:
                pass
            if on_update:
                snap = pd.DataFrame(rows)
                if not snap.empty:
                    snap = snap.sort_values("BroadScore", ascending=False).reset_index(drop=True)
                on_update(done, total, snap)
    return pd.DataFrame(rows).sort_values("BroadScore", ascending=False).reset_index(drop=True) if rows else pd.DataFrame()


def deep(candidates: pd.DataFrame, mkt: dict, min_atr=1.8, min_vol=1.5, min_rr=2.0, news_gate=0.0, workers=12, on_update=None) -> pd.DataFrame:
    if candidates.empty:
        return pd.DataFrame()
    out, done, total = [], 0, len(candidates)
    def one(r):
        m5 = chart(r["Ticker"], "5m", "5d")
        m15 = chart(r["Ticker"], "15m", "60d")
        h1 = chart(r["Ticker"], "60m", "60d")
        if m5.empty or m15.empty or h1.empty:
            return None
        a5, a15, a1 = indicators(m5), indicators(m15), indicators(h1)
        try:
            price, atrv, volshock = a5["price"], r["atr"], a5["volshock"]
        except KeyError:
            return None
        if not all(np.isfinite(x) for x in (price, atrv, volshock)):
            return None
        live_atr_pct = atrv / price * 100
        if live_atr_pct < min_atr or volshock < min_vol:
            return None
        relative_strength = a5["ret15"] - mkt["move15"]
        long_ok = (a15["trend"] == "LONG" and a1["trend"] == "LONG" and relative_strength > 0
                   and a5["ret5"] > 0 and (a5["breakout"] == "UP" or a15["breakout"] == "UP"))
        short_ok = (a15["trend"] == "SHORT" and a1["trend"] == "SHORT" and relative_strength < 0
                    and a5["ret5"] < 0 and (a5["breakout"] == "DOWN" or a15["breakout"] == "DOWN"))
        if not (long_ok or short_ok):
            return None
        direction = "LONG" if long_ok else "SHORT"
        news_score, headlines = news(r["Symbol"])
        if news_gate and abs(news_score) < news_gate:
            return None
        news_aligned = (direction == "LONG" and news_score >= 0) or (direction == "SHORT" and news_score <= 0)
        if news_gate and not news_aligned:
            return None
        sl = price - 1.5 * atrv if direction == "LONG" else price + 1.5 * atrv
        target = price + 3.0 * atrv if direction == "LONG" else price - 3.0 * atrv
        risk, reward = abs(price - sl), abs(target - price)
        rr = reward / risk if risk else 0.0
        if rr < min_rr:
            return None
        market_aligned = (direction == "LONG" and mkt["regime"] == "BULLISH") or (direction == "SHORT" and mkt["regime"] == "BEARISH")
        reliability = (25 + min(max(volshock - 1, 0) / 2, 1) * 20 + min(abs(relative_strength), 1) * 18
                       + (min(abs(news_score) / 0.8, 1) * 15 if news_score else 0)
                       + (10 if market_aligned else 4) + min(max(float(r.get("BroadScore", 0)) / 100, 0), 1) * 12
                       + (5 if news_aligned else 0))
        reliability = min(reliability, 100)
        grade = "A+" if reliability >= 90 else ("A" if reliability >= 80 else ("B" if reliability >= 70 else "C"))
        return {"Symbol": r["Symbol"], "Ticker": r["Ticker"], "Direction": direction,
                "Reliability": round(reliability, 1), "Grade": grade, "Price": round(price, 2),
                "Entry": f"{price*0.998:.2f} - {price*1.002:.2f}", "Stop Loss": round(sl, 2), "Target": round(target, 2),
                "R:R": round(rr, 2), "ATR %": round(live_atr_pct, 2), "Volume Shock": round(volshock, 2),
                "5m Trend": a5["trend"], "15m Trend": a15["trend"], "1h Trend": a1["trend"],
                "5m Breakout": a5["breakout"], "15m Breakout": a15["breakout"],
                "15m Stock %": round(a15["ret15"], 3), "15m Market %": round(mkt["move15"], 3),
                "Relative Strength": round(relative_strength, 3), "News Score": round(news_score, 2),
                "Market Regime": mkt["regime"], "Catalyst": " | ".join(headlines[:2]) if headlines else "No matching headline",
                "Why": f"{a15['trend']} 15m + {a1['trend']} 1h; 5m trigger {a5['ret5']:+.2f}%; volume {volshock:.1f}x; RS {relative_strength:+.2f}%; news {news_score:+.2f}; market {mkt['regime']}",
                "Updated UTC": datetime.now(timezone.utc).isoformat(timespec="seconds")}
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
                snap = pd.DataFrame(out)
                if not snap.empty:
                    snap = snap.sort_values(["Reliability", "R:R"], ascending=False).reset_index(drop=True)
                on_update(done, total, snap)
    return pd.DataFrame(out).sort_values(["Reliability", "R:R"], ascending=False).reset_index(drop=True) if out else pd.DataFrame()
