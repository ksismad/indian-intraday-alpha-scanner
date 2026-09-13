# Indian Intraday Alpha Scanner

A data-driven Indian-equity intraday research terminal.

## Architecture

1. **Full NSE EQ universe** — pulls the current exchange equity security master rather than a fixed watchlist. This is the exchange universe; broker-specific intraday eligibility is a separate live check.
2. **Rotating broad pass** — scans the universe in small batches for liquidity proxy, ATR/volatility and recent movement. The rolling cache prevents a full-universe request storm every 60 seconds.
3. **Deep pass** — uses **15-minute + 1-hour EMA 9/21 confirmation** and a 5-minute execution trigger, plus breakout and recent price response.
4. **Market response** — compares each stock's 15-minute move with NIFTY/SENSEX and rewards relative strength in the same direction.
5. **News engine** — recent Google News RSS headlines are scored with VADER. News is a confirmation/ranking input, not a direction selector.
6. **Risk engine** — 1.5× ATR stop and 3× ATR target, creating the requested 1:2 baseline R:R structure.
7. **Reliability score** — 0–100 ranking using multi-timeframe alignment, volume shock, relative strength, news alignment, market regime and broad liquidity.
8. **Progressive UI** — provisional candidates and then fully validated setups are displayed as each scan stage completes.
9. **Continuous UI** — Streamlit refreshes the rolling scanner every 60 seconds while open.
10. **Long-running worker** — `worker.py` can continuously rotate through the universe on a VPS/container and write rolling results to `runtime/results.json`.
11. **Optional live WebSocket adapter** — `live_feed.py` contains an opt-in Upstox V3 adapter. Credentials belong in server secrets, never source control.

## Reliability order

The UI sorts by Reliability and then R:R. It displays Grade, Direction, Entry, Stop Loss, Target, R:R, ATR, volume shock, 5m/15m/1h trend, breakouts, relative strength, news score and market regime.

## Streamlit deployment

1. Deploy `app.py` from `main` on Streamlit Community Cloud.
2. Select Python 3.12 in Advanced settings.
3. Reboot/rebuild after code or dependency changes.

## Always-on deployment

For a 24×7 scanner process, use the included Dockerfile or run:

```bash
pip install -r requirements.txt
python worker.py
```

Useful environment variables:

```text
SCAN_INTERVAL_SECONDS=60
UNIVERSE_BATCH_SIZE=60
DEEP_LIMIT=15
WORKERS=16
MIN_ATR_PCT=1.8
MIN_VOL_SHOCK=1.5
MIN_RR=2.0
NEWS_GATE=0
RUN_FOREVER=1
RESULTS_FILE=runtime/results.json
STATE_FILE=runtime/scanner_state.json
```

A VPS/container is preferable to a web-app runtime for the always-on worker.

## Data and live-feed boundary

The public HTTP path is a live-style research mode, not a guaranteed tick-by-tick exchange feed. The optional WebSocket adapter is the production direction for real-time updates. A true full-universe streaming deployment must use the chosen broker/data provider's current instrument master and subscription limits.

The scanner does not place broker orders and does not claim guaranteed outcomes. Validate data freshness, liquidity, exchange/broker eligibility, surveillance restrictions and risk controls before trading.
