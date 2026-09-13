# Indian Intraday Alpha Scanner

A live-style Streamlit research terminal for Indian equities.

## New architecture

1. **Full NSE EQ universe** — loads the current NSE equity security master instead of a short hard-coded watchlist. NSE publishes the securities-available-for-trading list and security master. The scanner treats this as the exchange universe, not a broker-specific promise of intraday eligibility.
2. **Broad filter** — scans daily data for liquidity proxy, ATR/volatility and recent movement.
3. **Deep filter** — runs 5-minute + 1-hour trend confirmation on the highest-ranked candidates.
4. **Market response** — compares the stock's recent move with NIFTY/SENSEX response and rewards relative strength in the same direction.
5. **News engine** — pulls recent Google News RSS headlines and scores them with VADER. Strong news can be used as a gate; otherwise it acts as a ranking weight.
6. **Risk engine** — keeps the requested 1.5x ATR stop and 3x ATR target, producing a 1:2 baseline R:R.
7. **Reliability ranking** — combines trend alignment, volume shock, relative strength, news alignment, market regime and broad liquidity score into a 0-100 reliability grade.
8. **Continuous display** — dashboard refreshes every 60 seconds while open. During weekends/holidays the market leg will be stale/closed and the app should not invent live prices.

## Reliability order

The dashboard sorts **highest reliability to lowest** and shows Grade, Reliability, Direction, Entry, Stop Loss, Target, R:R, 5m trend, 1h trend, breakout, relative strength, news score and market regime.

## Deploy on Streamlit Community Cloud

1. Open https://share.streamlit.io/
2. Sign in with GitHub.
3. Choose `ksismad/indian-intraday-alpha-scanner`.
4. Branch: `main`.
5. Main file: `app.py`.
6. Use Python 3.12 in Advanced settings.
7. Deploy. If the app was created before the latest commits, use **Manage app → Reboot** or recreate it so the dependency/environment is rebuilt.

## Local run

```bash
python -m venv .venv
.venv\\Scripts\\activate
pip install -r requirements.txt
streamlit run app.py
```

## Important data limitation

This project is designed for a **live-style scanner**, not a guaranteed tick-by-tick exchange feed. Public/free data sources may be delayed, rate-limited or unavailable. True production real-time coverage of the entire NSE/BSE universe requires an appropriate market-data feed or broker API. Broker-specific intraday eligibility, short-selling rules and surveillance restrictions must also be checked separately.

The app does not place broker orders and is not a guaranteed-profit system.