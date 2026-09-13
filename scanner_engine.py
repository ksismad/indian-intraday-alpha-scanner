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


def symbols(text: str) -> list[str]:
    return sorted(set(x.strip().upper() for x in re.split(r"[,\s\n]+", text or "") if x.strip()))


def nse_universe() -> pd.DataFrame:
    try:
        r = requests.get(NSE_MASTER, headers=UA, timeout=20)
        r.raise_for_status()
        raw = pd.read_csv(pd.io.common.BytesIO(r.content))
        names = {str(c).strip().upper(): c for c in raw.columns}
        sc = names.get("SYMBOL")
        ser = names.get("SERIES")
        if not sc:
            raise ValueError("SYMBOL column missing")
        out = pd.DataFrame({"Symbol": raw[sc].astype(str).str.upper().str.strip()})
        if ser:
            out["Series"] = raw[ser].astype(str).str.upper().str.strip()
            out = out[out.Series.eq("EQ")]
        out = out[out.Symbol.str.match(r"^[A-Z0-9&._-]+$")]
        return out.drop_duplicates("Symbol").reset_index(drop=True)
    except Exception:
        return pd.DataFrame({"Symbol": FALLBACK, "Series": "EQ"})


def chart(ticker: str, interval: str, period: str) -> pd.DataFrame:
    try:
        r = requests.get(YAHOO.format(ticker=quote_plus(ticker)), headers=UA,
                         params={"range": period, "interval": interval, "events": "div,splits"}, timeout=12)
        r.raise_for_status()
        result = ((r.json().get("chart") or {}).get("result") or [None])[0]
        if not result or not result.get("timestamp"):
            return pd.DataFrame()
        q = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        idx = pd.to_datetime(result["timestamp"], unit="s", utc=True)
        return pd.DataFrame({"Open": q.get("open", []), "High": q.get("high", []),
                             "Low": q.get("low", []), "Close": q.get("close", []),
                             "Volume": q.get("volume", [])}, index=idx).dropna(subset=["Close"])
    except Exception:
        return pd.DataFrame()


def indicators(df: pd.DataFrame) -> dict:
    if len(df) < 30:
        return {}
    x = df.copy()
    prev = x.Close.shift(1)
    tr = pd.concat([x.High-x.Low, (x.High-prev).abs(), (x.Low-prev).abs()], axis=1).max(axis=1)
    x["ATR"] = tr.rolling(14).mean()
    x["EMA9"] = x.Close.ewm(span=9, adjust=False).mean()
    x["EMA21"] = x.Close.ewm(span=21, adjust=False).mean()
    last = x.iloc[-1]
    p = float(last.Close)
    atr = float(last.ATR) if pd.notna(last.ATR) else np.nan
    av = x.Volume.iloc[-21:-1].mean()
    volshock = float(last.Volume/av) if av and av > 0 else np.nan
    trend = "LONG" if p > last.EMA9 > last.EMA21 else ("SHORT" if p < last.EMA9 < last.EMA21 else "NEUTRAL")
    hi20, lo20 = x.High.iloc[-21:-1].max(), x.Low.iloc[-21:-1].min()
    breakout = "UP" if p > hi20 else ("DOWN" if p < lo20 else "NONE")
    ret15 = (p/x.Close.iloc[-16]-1)*100 if len(x) >= 16 else np.nan
    ret5 = (p/x.Close.iloc[-6]-1)*100 if len(x) >= 6 else np.nan
    return {"price":p,"atr":atr,"atr_pct":atr/p*100 if p else np.nan,"volume":float(last.Volume),"volshock":volshock,"trend":trend,"breakout":breakout,"ret15":ret15,"ret5":ret5}


def news(symbol: str, limit: int = 6) -> tuple[float,list[str]]:
    try:
        url = "https://news.google.com/rss/search?q=" + quote_plus(symbol + " share price India") + "&hl=en-IN&gl=IN&ceid=IN:en"
        feed = parse_feed(url)
        hs = [str(e.get("title","")).strip() for e in feed.entries[:limit] if e.get("title")]
        ss = [VADER.polarity_scores(h)["compound"] for h in hs]
        return (float(np.mean(ss)) if ss else 0.0, hs)
    except Exception:
        return 0.0, []


def market() -> dict:
    vals=[]
    for ticker in ("^NSEI","^BSESN"):
        d=chart(ticker,"5m","2d")
        s=indicators(d) if not d.empty else {}
        if np.isfinite(s.get("ret15",np.nan)): vals.append(s["ret15"])
    m=float(np.mean(vals)) if vals else 0.0
    return {"move15":m,"regime":"BULLISH" if m>0.25 else ("BEARISH" if m<-0.25 else "NEUTRAL"),"updated":datetime.now(timezone.utc).isoformat(timespec="seconds")}


def broad(symbol_list: list[str], exchange="NS", workers=16, cap=None) -> pd.DataFrame:
    todo=symbol_list[:cap] if cap else symbol_list
    rows=[]
    def one(s):
        d=chart(f"{s}.{exchange}","1d","4mo")
        a=indicators(d)
        if not a: return None
        dv=a["price"]*a["volume"]
        score=min(np.log1p(max(dv,0))/30,1)*50 + min(max(a["atr_pct"],0)/8,1)*30 + min(abs(a["ret15"])/5,1)*20
        return {"Symbol":s,"Ticker":f"{s}.{exchange}",**a,"DollarVolume":dv,"BroadScore":score}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        fs=[ex.submit(one,s) for s in todo]
        for f in as_completed(fs):
            try:
                x=f.result()
                if x: rows.append(x)
            except Exception: pass
    return pd.DataFrame(rows).sort_values("BroadScore",ascending=False).reset_index(drop=True) if rows else pd.DataFrame()


def deep(candidates: pd.DataFrame, mkt: dict, min_atr=1.8, min_vol=1.5, min_rr=2.0, news_gate=0.0, workers=12) -> pd.DataFrame:
    if candidates.empty: return pd.DataFrame()
    out=[]
    def one(r):
        m5,h1=chart(r.Ticker,"5m","5d"),chart(r.Ticker,"60m","60d")
        if m5.empty or h1.empty: return None
        a5,a1=indicators(m5),indicators(h1)
        p,at,vs=a5.get("price"),r.get("atr"),a5.get("volshock")
        if not all(np.isfinite(x) for x in (p,at,vs)) or a5["atr_pct"]<min_atr or vs<min_vol: return None
        rs=a5["ret15"]-mkt["move15"]
        direction="LONG" if a5["trend"]==a1["trend"]=="LONG" and rs>0 and (a5["breakout"]=="UP" or a5["ret15"]>0) else None
        if direction is None and a5["trend"]==a1["trend"]=="SHORT" and rs<0 and (a5["breakout"]=="DOWN" or a5["ret15"]<0): direction="SHORT"
        if not direction: return None
        ns,hs=news(r.Symbol)
        if news_gate and abs(ns)<news_gate: return None
        aligned=(direction=="LONG" and ns>=0) or (direction=="SHORT" and ns<=0)
        if news_gate and not aligned: return None
        sl=p-1.5*at if direction=="LONG" else p+1.5*at
        target=p+3*at if direction=="LONG" else p-3*at
        rr=abs(target-p)/abs(p-sl)
        if rr<min_rr: return None
        market_ok=(direction=="LONG" and mkt["regime"]=="BULLISH") or (direction=="SHORT" and mkt["regime"]=="BEARISH")
        rel=25 + min(max(vs-1,0)/2,1)*20 + min(abs(rs),1)*18 + min(abs(ns)/0.8,1)*15 + (10 if market_ok else 4) + min(r.BroadScore/100,1)*12
        if aligned: rel+=5
        rel=min(rel,100)
        return {"Symbol":r.Symbol,"Ticker":r.Ticker,"Direction":direction,"Reliability":round(rel,1),"Grade":"A+" if rel>=90 else ("A" if rel>=80 else ("B" if rel>=70 else "C")),"Price":round(p,2),"Entry":f"{p*.998:.2f} - {p*1.002:.2f}","Stop Loss":round(sl,2),"Target":round(target,2),"R:R":round(rr,2),"ATR %":round(a5["atr_pct"],2),"Volume Shock":round(vs,2),"5m Trend":a5["trend"],"1h Trend":a1["trend"],"Breakout":a5["breakout"],"15m Stock %":round(a5["ret15"],3),"15m Market %":round(mkt["move15"],3),"Relative Strength":round(rs,3),"News Score":round(ns,2),"Market Regime":mkt["regime"],"Catalyst":" | ".join(hs[:2]) or "No matching headline","Why":f"{a5['trend']} 5m+1h; volume {vs:.1f}x; relative 15m {rs:+.2f}%; news {ns:+.2f}; market {mkt['regime']}","Updated UTC":datetime.now(timezone.utc).isoformat(timespec="seconds")}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        fs=[ex.submit(one,r) for _,r in candidates.iterrows()]
        for f in as_completed(fs):
            try:
                x=f.result()
                if x: out.append(x)
            except Exception: pass
    return pd.DataFrame(out).sort_values(["Reliability","R:R"],ascending=False).reset_index(drop=True) if out else pd.DataFrame()
