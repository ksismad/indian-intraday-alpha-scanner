# Indian Intraday Alpha Scanner

Streamlit dashboard implementing the supplied NSE/BSE intraday framework.

## Features
- NSE + BSE watchlists
- Recent-listing detection
- 14-period ATR volatility filter
- Intraday volume shock filter
- 5-minute and 1-hour EMA 9/21 trend alignment
- Breakout detection
- Google News RSS + VADER sentiment catalyst
- Long and short setups
- 1.5x ATR stop / 3x ATR target
- Minimum R:R filter and Alpha Score ranking
- NIFTY/SENSEX market regime context
- CSV export

## Deploy on Streamlit Community Cloud
1. Open https://share.streamlit.io/
2. Sign in with GitHub.
3. Choose repository `ksismad/indian-intraday-alpha-scanner`.
4. Select branch `main`.
5. Set main file to `app.py`.
6. Click Deploy.

The repository already contains `app.py` and `requirements.txt`, so dependencies are installed automatically.

## Local run
```bash
python -m venv .venv
.venv\\Scripts\\activate
pip install -r requirements.txt
streamlit run app.py
```

## Important
This is an analytics/research tool. It does not place broker orders and is not a guaranteed-profit signal system. Public market/news data can be delayed or rate limited; validate signals before trading.
