# Candlestick Pattern Screener

A Streamlit app that scans a watchlist of stocks, detects the 20 classic
candlestick patterns (hammer, engulfing, morning/evening star, three white
soldiers, etc.), and combines them with **trend, volume, momentum, and
support/resistance context** into a single -100 to +100 buy/sell score —
with a plain-English breakdown of why.

⚠️ **Educational tool only. Not financial advice.**

## Scoring: six independent, weighted components

The 0-100 score (50 = neutral) is now a balanced scorecard across all four
major technical-analysis categories, built from **six components that are
each always computed independently**, then blended by weights you control:

| Category | Component | What it measures | Default weight |
|---|---|---|---|
| Pattern | **Candle Pattern** | Detected shape × trend alignment × volume × S/R | 25% |
| Momentum | **RSI** | Overbought/oversold (`100 - RSI`) | 15% |
| Momentum | **MACD** | ATR-normalized histogram direction/strength | 15% |
| Trend | **Moving Averages** | Price vs EMA20 + EMA20-vs-EMA50 cross (ATR-normalized) | 20% |
| Volatility | **Bollinger Bands** | %B position, framed as mean-reversion | 10% |
| Volume | **VWAP** | Price vs volume-weighted average price (ATR-normalized) | 15% |

Each component is computed independently of the others — this is what keeps
scores continuously informative instead of sitting at neutral whenever, say,
no candlestick pattern happens to be present. A strongly bearish candle
pattern showing up mid-uptrend, with price above VWAP and MACD still rising,
correctly nets out closer to Neutral rather than reading as a Strong Sell —
the components pull against each other exactly the way a human analyst
weighing multiple signals would.

**Adjust the weights** in the sidebar — six sliders that always sum to
100%. Move one and the other five rebalance proportionally, keeping their
relative ratio to each other. Weight changes re-score instantly using
already-fetched data — no need to re-scan the watchlist.

The detail view shows all six component sub-scores alongside the blended
final score, and the results table includes a column per component too, so
you can see exactly which factor is driving (or dragging down) any result
at a glance.

## Scoring scale

Every candle gets a score from **0 to 100**:

- **50 = neutral** — no clear signal
- **100 = strongest bullish conviction**
- **0 = strongest bearish conviction**

| Score range | Verdict |
|---|---|
| 80–100 | Strong Buy |
| 60–79 | Buy |
| 41–59 | Neutral |
| 21–40 | Sell |
| 0–20 | Strong Sell |

Each detected pattern has a base weight (5 for a plain Doji up to 24 for
Three White Soldiers/Black Crows) that's then adjusted by trend alignment,
volume, momentum (RSI/MACD), and proximity to support/resistance — see
`scoring.py` for the exact formula, and the "Why this score" section in the
app for a line-by-line breakdown of any given score.

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

## Scanning the full Nifty 500

In the sidebar, switch **Source** to "Full Nifty 500 (scan whole market)" to
score all 500 constituents in one run (from the bundled `nifty500.json`).
You can also add extra tickers on top of the 500. Individual-ticker mode is
still there if you just want a quick watchlist — switch back anytime.

Under the hood, scanning 500 tickers uses **batched downloads**
(`fetch_ohlcv_batch` in `data_fetcher.py`) — ~40 tickers per API call instead
of one call per ticker — so a full-market scan takes roughly 15-30 seconds
instead of several minutes, and is far less likely to hit Yahoo's rate limit.

## Today's session / intraday candles

Set **History period** to `1d` and **Candle interval** to `5m` (or `15m`,
`30m`, `1h`) to watch today's candles as they form. Combine with
"Analyze as of a specific date/time" to freeze the analysis at a particular
moment during the session (e.g. "what did the 10:15 AM candle look like").

**Behind the scenes**, when you pick a short intraday window like `1d`/`5d`:

1. The app quietly fetches a full month of intraday history, not just today —
   this gives indicators like RSI(14) and SMA(20) enough prior candles to be
   valid *from the market open*, instead of sitting on NaN for the first
   ~15 candles of the day.
2. It smooths the day-transition boundary specifically: the **last 3 candles of
   the most recent complete trading day** (yesterday's close) and the
   **first 3 candles of today** are each aggregated into one representative
   candle — that's the noisy overnight-gap zone (closing-settlement flurry
   into opening-auction volatility). Every earlier day in the lookback
   history is left completely raw/untouched — it's only there to give
   indicators their warm-up, so there's no need to alter it.
3. Only then does it trim back down to just the window you asked for
   (today, or the last 5 days) for scoring and display.

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
