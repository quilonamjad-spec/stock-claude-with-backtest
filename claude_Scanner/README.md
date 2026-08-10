# NSE 500 Bull/Bear Scanner (Streamlit)

Two-stage scanner + ranker for the NSE 500 universe, built on free Yahoo
Finance 5-minute data via `yfinance`.

## Setup

```bash
pip install -r requirements.txt
streamlit run app.py
```

Push `app.py`, `requirements.txt`, and this README to your GitHub repo, then
deploy on Streamlit Community Cloud (or anywhere else) pointing at `app.py`.

Optional: NSE's official constituent list is fetched live from
`archives.nseindia.com`. If that request ever fails (NSE blocks a lot of
non-browser traffic intermittently), drop a `nifty500_fallback.csv` file
(with a `Symbol` column) in the repo root as a backup source.

## How it works

**Stage 1 — Scan**
For every NSE 500 stock, pulls 5-minute OHLCV history up to the date/time
you select in the sidebar (default: now), computes VWAP, 20-period volume
SMA, 9/21 EMA, 10-bar high/low, and 20-period Chaikin Money Flow, then
checks the bull and bear condition sets:

- **Bull**: Close > VWAP, Volume > SMA(Vol,20), EMA9 > EMA21, Close > High(10), CMF20 > 0
- **Bear**: Close < VWAP, Volume > SMA(Vol,20), EMA9 < EMA21, Close < Low(10), CMF20 < 0

Every stock that clears one full set shows up with its Phase, % change from
the day's open, LTP, and the bar timestamp it was evaluated on.

Because Yahoo only retains ~60 days of 5-minute candles, the "as-of"
date/time picker works for anything within roughly the last two months —
good enough to revisit "what did the scan show 2-3 days ago at 10:15 AM."

**Stage 2 — Rank**
Stage 1 is pass/fail only — it doesn't tell you which passing stock is
*strongest*. Stage 2 scores each Stage-1 result on five conviction metrics
(all converted to "higher = stronger", regardless of Bull/Bear):

| Metric | Meaning |
|---|---|
| VWAP distance | % how far price is from VWAP |
| Volume surge | current volume ÷ 20-period average volume |
| EMA trend separation | % gap between EMA9 and EMA21 |
| Breakout distance | % price has cleared the 10-bar high/low by |
| Money flow (CMF) | magnitude of the Chaikin Money Flow reading |

Each metric is min-max normalized to 0–1 across the current result set (so
a 5x volume spike doesn't automatically dominate just because it's a bigger
raw number than a 2% VWAP distance), then combined into a weighted
**Rank Score** using the sidebar sliders (defaults: 25% VWAP, 25% Volume,
20% Trend, 20% Breakout, 10% Money flow). Bull and Bear stocks are ranked
separately since they're different trades, not competing on the same scale.

Adjust the weights to match your style — e.g. push Volume and Breakout
higher if you trade pure momentum breakouts, or push VWAP/Money flow higher
if you care more about institutional-style accumulation than raw breakout
distance.

## Notes / limits

- Test mode limits the scan to the first 60 symbols — turn it off for a
  full 500-stock run (expect it to take a few minutes given Yahoo's
  batching/rate limits).
- Yahoo's 5-minute data can lag or gap for illiquid names — if a stock is
  missing from results, it likely didn't have enough clean bars for the
  20/21-period indicators as of your selected time.
- This is a research/screening tool, not trade advice — always confirm
  signals on your charting platform before acting.
