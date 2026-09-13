# Indian Intraday Alpha Scanner

A data-driven Indian-equity intraday research terminal.

## Architecture

1. **Full NSE EQ universe** — pulls the current exchange equity security master rather than a fixed watchlist. This is an exchange universe, not a promise that every symbol is intraday-enabled at every broker.
2. **Broad pass** — parallel daily scan for liquidity proxy, ATR/volatility and recent movement.
3. **Deep pass** — 5-minute + 1-hour EMA/trend confirmation, breakout and recent price response.
4. **Market response** — compares each stock's 15-minute move with NIFTY/SENSEX and rewards relative strength.
5. **News engine** — recent Google News RSS headlines are scored with VADER. News can be a gate or a ranking weight.
6. **Risk engine** — 1.5× ATR stop and 3× ATR target, creating the requested 1:2 baseline R:R structure.
7. **Reliability score** — 0–100 ranking using trend alignment, volume shock, relative strength, news alignment, market regime and broad liquidity.
8. **Continuous UI** — Streamlit refreshes the dashboard every 60 seconds while open.
9. **Long-running worker** — `worker.py` can continuously rescan on a VPS/container and write the latest ranked signals to `runtime/results.json`.
10. **Live WebSocket adapter** — `live_feed.py` contains an optional Upstox MarketDataStreamerV3 adapter. Keep access tokens in server secrets, never in source control.

## Highest reliability first

The UI sorts by Reliability and then R:R. It displays Grade, Direction, Entry, Stop Loss, Target, R:R, ATR, volume shock, 5m/1h trend, breakout, relative strength, news score and market regime.

## Streamlit deployment

1. Deploy `app.py` from `main` on Streamlit Community Cloud.
2. Select Python 3.12 in Advanced settings.
3. Reboot/rebuild after dependency changes.

## Continuous server deployment

For a 24×7 scanner process, use the included `Dockerfile` or run:

```bash
pip install -r requirements.txt
python worker.py
```

Useful environment variables:

```text
SCAN_INTERVAL_SECONDS=60
RUN_FOREVER=1
RESULTS_FILE=runtime/results.json
```

A VPS/container is preferable to a sleeping web-app runtime for the always-on worker.

## Real-time feed

Upstox's current Market Data Feed V3 provides real-time WebSocket market updates and supports auto-reconnect. The included adapter follows that model. For full-universe real-time coverage, obtain the broker's instrument master and subscribe according to its instrument/subscription limits. Do not put broker credentials into GitHub. citeturn358060search0turn358060search1turn622565search0

## Important

This is an analytics/research system, not a guaranteed-profit or autonomous order execution system. Free/public feeds can be delayed, rate-limited or unavailable. Validate exchange/broker eligibility, data latency, liquidity, surveillance restrictions and risk before trading.
