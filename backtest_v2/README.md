# Nifty500 Day-Trading Screener

A Streamlit app that scans the Nifty500 universe using Yahoo Finance data,
scores each stock with a blend of RSI, MACD, ADX, Bollinger Bands, EMA
trend, volume, and candlestick patterns — then lets you shortlist the
interesting ones onto a faster-refreshing **Monitoring** watchlist.

> ⚠️ **This is a screening/research tool, not investment advice.** It
> summarizes textbook technical indicators into a score — it does not
> predict future price movement. Yahoo Finance data for NSE stocks can
> lag the real market by a few minutes, and is not a substitute for your
> broker/exchange terminal when actually placing trades. Size positions
> and manage risk according to your own judgement.

## How it works

1. **Screener tab** — pulls OHLCV candles (5m/15m/1h/1d, your choice) for
   the full Nifty500 (or a subset), computes indicators, and produces for
   every stock:
   - **Trade Score (0–100)** — 50 is neutral, >50 leans bullish, <50 leans
     bearish. It's a weighted blend of the indicators you've toggled on.
   - **Confidence (0–100)** — how much the active indicators *agree* with
     each other. High score + low confidence means indicators are mixed;
     treat those cautiously.
   - **Signal label** — Strong Buy / Buy / Neutral / Sell / Strong Sell,
     derived from score + confidence thresholds.
   You can filter/sort the results and tick stocks to send to Monitoring.

2. **Monitoring tab** — your shortlisted stocks, refreshed on demand, with
   a candlestick chart (EMA/Bollinger overlays) and a breakdown of exactly
   which indicators are pushing the score up or down.

## Setup (local)

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

Requires Python 3.10+.

## Deploying via GitHub + Streamlit Community Cloud

1. **Push to GitHub**
   ```bash
   git init
   git add .
   git commit -m "Initial commit: Nifty500 screener"
   git branch -M main
   git remote add origin https://github.com/<your-username>/<repo-name>.git
   git push -u origin main
   ```
   (`.gitignore` already excludes `__pycache__`, `venv/`, and the runtime
   `data/watchlist.json` — no need to commit those.)

2. **Deploy** at share.streamlit.io -> "New app" -> pick your repo/branch
   -> set main file to `app.py` -> Deploy. `requirements.txt` and
   `runtime.txt` (pins Python 3.11) are picked up automatically.

3. **Two things that behave differently on Streamlit Cloud vs. local:**
   - **Monitoring list doesn't persist across restarts.** Streamlit
     Cloud's free tier sleeps the app after inactivity, and the
     filesystem resets on wake/redeploy - so `data/watchlist.json` gets
     wiped. Use the **Download / Restore watchlist.json** buttons in the
     sidebar to back it up before you stop for the day and reload it next
     session.
   - **Live NSE fetch will likely fail** from Streamlit Cloud's shared IP
     ranges (NSE blocks a lot of cloud/datacenter traffic) - the app
     falls back to the bundled `data/nifty500_fallback.csv` automatically
     and shows a warning banner when this happens. That's expected, not
     a bug.

4. **Yahoo Finance rate limits**: shared cloud IPs get rate-limited more
   aggressively than a home connection. The app caches downloaded price
   data for a few minutes (`st.cache_data`) so repeated Streamlit reruns
   (every click reruns the script) don't re-hit Yahoo - but if a full
   500-stock scan starts failing, drop "Max stocks to scan" and/or the
   batch size in the sidebar.

## Notes on data

- The Nifty500 constituent list is fetched live from NSE's public archive
  (`nsearchives.nseindia.com`). NSE sometimes blocks scripted requests —
  if that happens, the app falls back to a bundled ~230-symbol list of
  major Nifty500 names (`data/nifty500_fallback.csv`). You can also paste
  your own custom symbol list in the sidebar.
- Scanning all 500 stocks takes a few minutes because of Yahoo's rate
  limits — use "Nifty50 subset (fast test)" or lower "Max stocks to scan"
  while you're tuning settings, then run the full scan when you're ready.
- Intraday intervals have limited history on Yahoo Finance: 5m data only
  goes back ~5-7 days, 15m/1h a bit further. Daily data goes back years.

## Tuning the scoring

Every indicator's weight is adjustable from the sidebar. The underlying
formulas live in `scoring.py` with comments explaining the logic — e.g.
RSI is scored as an overbought/oversold reversal signal, ADX/DI as trend
strength+direction (dampened when ADX < 20, i.e. no clear trend), MACD
histogram as momentum, etc. Tweak `scoring.py` directly if you want to
change how a specific indicator is interpreted.

## Files

- `app.py` — Streamlit UI (Screener + Monitoring tabs)
- `indicators.py` — RSI, MACD, ADX/DI, Bollinger, EMA, volume ratio
- `candlestick_patterns.py` — engulfing, hammer, shooting star, morning/
  evening star, marubozu, doji (no TA-Lib dependency)
- `scoring.py` — combines indicators into Trade Score + Confidence
- `data_fetch.py` — NSE symbol list + yfinance batch downloader
- `data/nifty500_fallback.csv` — fallback symbol list
- `data/watchlist.json` — your saved Monitoring list (created at runtime)
