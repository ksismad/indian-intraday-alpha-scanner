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

## Always-on deployment

Use the included `Dockerfile` or run:

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
BROAD_CACHE_TTL_MINUTES=45
SIGNAL_TTL_MINUTES=10
RUN_FOREVER=1
RESULTS_FILE=runtime/results.json
STATE_FILE=runtime/scanner_state.json
RESULTS_STALE_SECONDS=180
RESULTS_URL=
```

A dedicated VPS/container is preferable to a shared web-app runtime for the always-on worker.

## Data and live-feed boundary

The public HTTP path is **live-style research data**, not a guaranteed tick-by-tick exchange feed. Public endpoints can be delayed, rate-limited or unavailable. The scanner intentionally fails closed: no current intraday candles means no qualified signal.

The optional broker/WebSocket adapter is separate from the public HTTP scanner. A true streaming deployment must use the chosen broker/data provider's current instrument master, subscription limits and eligibility rules.

## Risk / trading boundary

The scanner is for research and decision support. It does not place orders and does not guarantee outcomes. Validate data freshness, liquidity, exchange/broker eligibility, surveillance restrictions, execution slippage and risk controls independently before trading.
