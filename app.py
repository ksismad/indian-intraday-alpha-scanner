from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="Indian Intraday Alpha Scanner", page_icon="📈", layout="wide")
st.title("Indian Intraday Alpha Scanner")
st.caption("Worker-driven dashboard → 15m + 1h confirmation → 5m trigger → market/news response → reliability ranking")

RESULTS_FILE = Path(os.getenv("RESULTS_FILE", "runtime/results.json"))
RESULTS_URL = os.getenv("RESULTS_URL", "").strip()
STALE_AFTER_SECONDS = max(30, int(os.getenv("RESULTS_STALE_SECONDS", "180")))


def load_results() -> dict:
    if RESULTS_URL:
        try:
            response = requests.get(RESULTS_URL, timeout=5)
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    try:
        return json.loads(RESULTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


@st.fragment(run_every="5s")
def dashboard():
    payload = load_results()
    updated = payload.get("updated")
    status = payload.get("status", "NO_RESULT")
    signals = pd.DataFrame(payload.get("signals", []))
    market = payload.get("market") or {}

    if not payload:
        st.error("Worker results are unavailable. No market data is assumed and no signal is generated.")
        st.caption("Start the continuous worker and point this dashboard to its runtime/results.json or RESULTS_URL.")
        return

    age = None
    if updated:
        try:
            dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
            age = max(0, (datetime.now(timezone.utc) - dt).total_seconds())
        except Exception:
            age = None

    fresh = age is not None and age <= STALE_AFTER_SECONDS
    state_label = status if fresh else "STALE_RESULTS"

    st.write(f"Worker heartbeat: {updated or 'unknown'} UTC")
    a, b, c, d = st.columns(4)
    a.metric("Worker status", state_label)
    b.metric("Market regime", market.get("regime", "UNKNOWN"))
    move15 = market.get("move15")
    b1 = f"{move15:+.3f}%" if isinstance(move15, (int, float)) else "N/A"
    c.metric("15m market move", b1)
    c1 = payload.get("universe_count", 0)
    d.metric("NSE EQ universe", f"{int(c1):,}")

    if not fresh:
        st.warning("Displayed worker results are stale. The dashboard will not invent replacement market data or signals.")

    st.subheader("Current Validated Signals")
    if signals.empty:
        st.info("No fully validated setup is currently available. The worker continues scanning.")
        return

    cols = [
        "Grade", "Reliability", "Symbol", "Direction", "Price", "Entry", "Stop Loss", "Target", "R:R",
        "ATR %", "Execution ATR %", "Volume Shock", "5m Trend", "15m Trend", "1h Trend",
        "5m Breakout", "15m Breakout", "Relative Strength", "News Score", "News Status", "Market Regime",
        "Broker Eligibility",
    ]
    visible = [c for c in cols if c in signals.columns]
    st.dataframe(signals[visible], use_container_width=True, hide_index=True)

    st.subheader("Why each setup qualified")
    why_cols = [c for c in ["Symbol", "Grade", "Reliability", "Why", "Catalyst", "News Freshness Min", "DataSource", "Updated UTC"] if c in signals.columns]
    st.dataframe(signals.head(20)[why_cols], use_container_width=True, hide_index=True)
    st.download_button(
        "Download current validated signals CSV",
        signals.to_csv(index=False).encode(),
        "intraday_signals.csv",
        "text/csv",
    )

    st.caption(
        "The dashboard is read-only. The continuous worker owns scanning, freshness, signal expiry and persistence. "
        "The UI never generates synthetic market data and never replaces unavailable intraday candles with daily data."
    )


dashboard()
