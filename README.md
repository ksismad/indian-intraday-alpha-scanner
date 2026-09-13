# Indian Intraday Alpha Scanner

A Streamlit-only Indian-equity intraday research scanner.

## How it works

- Open the Streamlit app and the first scan starts automatically.
- The app scans the current NSE EQ security master in rotating batches to avoid a full-universe request storm.
- Strong rolling candidates are deep-validated using separate 5-minute, 15-minute and 60-minute candles.
- 15-minute + 1-hour EMA 9/21 alignment confirms direction; the 5-minute series is used for execution trigger, ATR and volume shock.
- NIFTY/SENSEX response, relative strength and recent news are used for ranking/confirmation.
- Missing market data fails closed: no synthetic candles and no trade signal.
- Results appear progressively while the scan is running.
- The page rescans while it remains open; closing the page stops the on-demand scan for that session.

## Streamlit deployment

Deploy `app.py` from this GitHub repository on Streamlit Community Cloud. No Render service, external worker, VPS, results API, or `RESULTS_URL` is required for this mode.

## Local Windows

```bat
python -m venv .venv
.venv\\Scripts\\activate
pip install -r requirements.txt
streamlit run app.py
```

## Data boundary

The scanner uses public HTTP market/news sources in research mode. These sources can be delayed, rate-limited or unavailable, so the application intentionally produces no signal when required current data is missing.

## Risk boundary

The scanner is for research and decision support. It does not place orders, guarantee outcomes, or verify broker-specific intraday eligibility. Validate liquidity, security restrictions, execution slippage and broker eligibility independently before trading.
