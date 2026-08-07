# Candlestick Pattern Screener

A Streamlit app that scans a watchlist of stocks, detects the 20 classic
candlestick patterns (hammer, engulfing, morning/evening star, three white
soldiers, etc.), and combines them with **trend, volume, momentum, and
support/resistance context** into a single -100 to +100 buy/sell score —
with a plain-English breakdown of why.

⚠️ **Educational tool only. Not financial advice.**

## How the scoring works

Raw candlestick patterns are a weak signal on their own, so each detected
pattern's base weight is adjusted by:

- **Trend alignment** (EMA20 vs EMA50) — a bullish reversal pattern scores
  much higher after a downtrend than in the middle of a range.
- **Volume** — patterns on above-average volume are weighted higher.
- **Momentum** — RSI oversold/overbought and MACD histogram direction add
  or subtract points when they agree with the pattern's bias.
- **Support/Resistance** — patterns forming near the 20-day
  support/resistance band get a small bonus.

See `scoring.py` for the exact formula and `patterns.py` for pattern
detection rules.

## Files

| File | Purpose |
|---|---|
| `app.py` | Streamlit UI — watchlist scanner + detail chart view |
| `data_fetcher.py` | Pulls OHLCV data from Yahoo Finance (`yfinance`) |
| `indicators.py` | Moving averages, RSI, MACD, ATR, Bollinger Bands, volume, trend, S/R |
| `patterns.py` | Detects single/double/triple candlestick patterns |
| `scoring.py` | Combines patterns + context into the composite score |
| `test_logic.py` | Quick sanity test using synthetic data (no internet needed) |

## NSE / Indian market support

Pick **India (NSE)** or **India (BSE)** from the *Market* dropdown in the
sidebar and type plain symbols (`RELIANCE`, `TCS`, `INFY`, `HDFCBANK`,
`ICICIBANK`, ...) — the `.NS` / `.BO` suffix Yahoo Finance requires is added
automatically. You can also type the full suffixed symbol yourself if you
prefer.

## Point-in-time analysis

Tick **"Analyze as of a specific date/time"** in the sidebar to score the
candle as it stood on a chosen day (and time, for intraday intervals)
instead of always scoring the most recent candle. This is useful for
reviewing "what would the app have said on this date" without any lookahead
— the chart and score only ever use data up to that candle.

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

Then open the local URL Streamlit prints (usually `http://localhost:8501`).

## Push to GitHub

```bash
git init
git add .
git commit -m "Initial commit: candlestick pattern screener"
git branch -M main
git remote add origin https://github.com/<your-username>/<your-repo>.git
git push -u origin main
```

## Deploy free on Streamlit Community Cloud

1. Push this repo to GitHub (steps above).
2. Go to [share.streamlit.io](https://share.streamlit.io) and sign in with GitHub.
3. Click **New app**, select your repo/branch, and set the main file to `app.py`.
4. Click **Deploy**. It will install `requirements.txt` automatically and give
   you a public URL you can open from your phone.

## Ideas to make predictions more reliable (next steps)

- **Backtest each pattern per ticker/sector** — track actual forward returns
  (next 3/5/10 days) after each pattern historically fires, and use the
  measured win rate instead of a fixed base weight.
- **Multi-timeframe confirmation** — only trust a daily signal if the weekly
  trend agrees.
- **Sector/market context** — compare against SPY or the stock's sector ETF;
  signals fighting the broader market are less reliable.
- **Combine with fundamentals** — earnings dates, valuation, news sentiment.
- **Position sizing / stop-loss suggestions** based on ATR, so the app gives
  risk guidance alongside the signal, not just a direction.
- **Alerting** — run the scan on a schedule (e.g. GitHub Actions cron) and
  send yourself a message when a high-score setup appears.

## Tuning

Pattern-matching thresholds live at the top of `patterns.py`
(`DOJI_BODY_PCT`, `SMALL_BODY_PCT`, `LONG_WICK_MULT`, etc.) — loosen or
tighten them if you find the detector too strict or too permissive for a
given stock's typical volatility.
