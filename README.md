# Indian Intraday Alpha Scanner

A data-driven Indian-equity intraday research terminal with a worker-owned scan loop and a read-only dashboard.

## Architecture

1. **Current NSE EQ universe** — pulls the current NSE equity security master. If the security master is unavailable, the scanner stops/degrades to `NO_UNIVERSE_DATA`; it never invents a replacement universe.
2. **Rotating broad pass** — scans manageable universe batches and retains only fresh results for a bounded cache window. Stale candidates are discarded.
3. **True multi-timeframe deep pass** — separately requests **5-minute, 15-minute and 60-minute** candles. No intraday timeframe is replaced by daily data when an intraday request fails.
4. **15-minute market response** — benchmark movement is calculated from three completed/available 5-minute intervals, rather than confusing 15 bars with 15 minutes.
5. **Volume shock** — latest bar volume is compared with the previous 20 bars, excluding the current bar from the baseline.
6. **Risk engine** — 5-minute ATR is used for execution risk sizing; 15-minute ATR% is used for the volatility filter.
7. **News engine** — recent Google News RSS headlines are scored and timestamped. A headline is labelled `RECENT` only when its published/updated time is within the configured freshness window. News is a confirmation/ranking input, not a standalone direction selector.
8. **Reliability score** — ranks multi-timeframe alignment, volume shock, relative strength, news freshness/alignment, market regime and broad liquidity.
9. **Broker eligibility boundary** — broker intraday eligibility is explicitly returned as `NOT_VERIFIED` until a live broker/security eligibility source is integrated. The scanner does not make universal MIS or margin claims.
10. **Progressive worker output** — the continuous worker writes atomic JSON snapshots while scanning so the dashboard can show the latest validated state without owning the scan.
11. **Stale-signal protection** — signals are replaced each deep-validation cycle and do not remain visible indefinitely after validation has failed or data becomes stale.
12. **No synthetic signal path** — there is no synthetic-price fallback for qualifying trades. Missing market data results in no signal.
13. **Optional live WebSocket adapter** — `live_feed.py` contains an opt-in Upstox V3 adapter. Credentials belong in server secrets, never source control. A broker websocket is the production direction for tick/stream-driven execution monitoring.

## Dashboard

`app.py` is now read-only. It reads `runtime/results.json` by default, or a JSON endpoint configured with `RESULTS_URL`. It does **not** trigger a fresh full-market scan from the browser, so opening multiple tabs does not multiply market-data requests.

For local same-host operation:

```bash
python worker.py
streamlit run app.py
```

Both processes can read the same `runtime/results.json` on a VPS or shared volume.

## Production deployment with Render

The repository includes `render.yaml`, which defines a Docker web service that runs `api_server.py`, supervises `worker.py`, exposes `/results` and `/health`, and stores runtime state under a persistent disk. Render supports Docker services, health checks, environment variables and persistent disks; current compute plans and pricing are selected in the Render account. citeturn249477search0turn477873search1turn249477search1turn574098search1

### Deploy

1. In Render, create a **Blueprint** from this GitHub repository and select `render.yaml`.
2. Keep one instance for this scanner service. The Blueprint uses Docker, a persistent `/app/runtime` disk, and `/health` as the health check.
3. After deployment, open `https://YOUR-SERVICE.onrender.com/health` and confirm `worker_running` is `true`.
4. Open `https://YOUR-SERVICE.onrender.com/results` and wait for the first successful scan payload.
5. In Streamlit Community Cloud, set this environment variable/secret:

```text
RESULTS_URL=https://YOUR-SERVICE.onrender.com/results
RESULTS_STALE_SECONDS=180
```

The Streamlit dashboard will then poll the worker API every 5 seconds while the worker continues scanning independently.

Render web services must bind to `0.0.0.0`; the supplied Dockerfile/API uses port `10000`. Render can automatically redeploy a linked Git branch when new commits are pushed. citeturn477873search2turn477873search1

### Environment variables

The Blueprint contains non-secret scanner configuration defaults. Do not commit broker access tokens or other credentials; add secrets through Render's environment/secret settings. citeturn249477search3

## Always-on deployment

The API server is the production entrypoint. For a plain VPS/container without the API layer, `python worker.py` remains supported.

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
BROAD_CACHE_TTL_MINUTES=45
SIGNAL_TTL_MINUTES=10
RUN_FOREVER=1
RESULTS_FILE=/app/runtime/results.json
STATE_FILE=/app/runtime/scanner_state.json
RESULTS_STALE_SECONDS=180
START_WORKER=1
```

## Reliability order

The UI sorts by Reliability and then R:R. It displays Grade, Direction, Entry, Stop Loss, Target, R:R, ATR, execution ATR, volume shock, 5m/15m/1h trend, breakouts, relative strength, news score/freshness and market regime.

## Data and live-feed boundary

The public HTTP path is **live-style research data**, not a guaranteed tick-by-tick exchange feed. Public endpoints can be delayed, rate-limited or unavailable. The scanner intentionally fails closed: no current intraday candles means no qualified signal.

The optional broker/WebSocket adapter is separate from the public HTTP scanner. A true streaming deployment must use the chosen broker/data provider's current instrument master, subscription limits and eligibility rules.

## Risk / trading boundary

The scanner is for research and decision support. It does not place orders and does not guarantee outcomes. Validate data freshness, liquidity, exchange/broker eligibility, surveillance restrictions, execution slippage and risk controls independently before trading.
