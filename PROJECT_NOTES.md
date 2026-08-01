# Nifty500 Day-Trading Screener — Project Notes (for V2 handoff)

## What this is
A Streamlit app for scanning the Nifty500 universe using Yahoo Finance data,
scoring stocks by technical indicators, and shortlisting candidates to a
faster-refreshing Monitoring watchlist. Built for personal day-trading use,
deployed via GitHub + Streamlit Community Cloud.

## Project structure (as originally delivered)
```
nifty_trade_scanner/
├── app.py                      # Streamlit UI: Screener tab + Monitoring tab
├── indicators.py                # RSI, MACD, ADX/DI, Bollinger, EMA20/50, volume ratio
├── candlestick_patterns.py      # Pattern detection (see coverage below)
├── scoring.py                   # Combines indicators into Trade Score + Confidence
├── data_fetch.py                # NSE symbol list fetch + yfinance batch downloader
├── data/nifty500_fallback.csv   # ~230 symbol fallback list (used if NSE blocks fetch)
├── requirements.txt
├── runtime.txt                  # pins python-3.11 for Streamlit Cloud
├── .streamlit/config.toml       # dark theme, headless
├── .gitignore
└── README.md
```
**Important**: this note describes the app as I last edited it. If the user
uploads a zip, treat the actual files as ground truth — they may have made
local edits or tested changes I don't know about. Diff against this note,
don't assume it's still accurate.

## Core design decisions (the "why")

**No TA-Lib dependency.** All indicators and candlestick patterns are
implemented in pure pandas/numpy specifically to avoid the TA-Lib C-library
install pain on Windows. Keep it this way unless the user asks otherwise.

**Scoring logic** (in `scoring.py`):
- Each indicator produces a signal in **[-1, +1]** (bearish to bullish).
- Weighted average of active signals → rescaled to **Trade Score (0-100)**,
  50 = neutral, via `50 + 50 * combined_signal`.
- **Confidence (0-100)** = agreement_ratio × avg_strength_of_agreeing_signals.
  This is a *separate* axis from Trade Score — it measures how much the
  active indicators agree with each other, not how strong the direction is.
  A high score + low confidence means one or two indicators are dominating
  while others disagree.
- Signal label buckets: Strong Buy (score≥70 & conf≥55), Buy (≥58),
  Neutral, Sell (≤42), Strong Sell (≤30 & conf≥55).
- Per-indicator logic specifics: RSI treated as overbought/oversold reversal
  (not trend-following); ADX signal is dampened ×0.3 when ADX<20 (no clear
  trend = less reliable); Volume only contributes when ratio>1.2x average.
- Default weights: MACD/ADX = 1.2 (highest), RSI/EMA_TREND = 1.0,
  CANDLESTICK = 0.8, BOLLINGER = 0.7, VOLUME = 0.6.

**Candlestick pattern coverage — deliberately partial.** Currently detects:
Bullish/Bearish Engulfing, Hammer, Shooting Star, Morning Star, Evening Star,
Doji, Marubozu (bullish/bearish). **NOT yet implemented** (queued for V2):
Inverted Hammer, Dragonfly Doji, Bullish/Bearish Spinning Top, Tweezer
Top/Bottom, Morning/Evening Doji Star, Three White Soldiers, Three Black
Crows, Rising/Falling Three Methods, Hanging Man, Gravestone Doji.
(User provided a reference chart from Warrior Trading listing these.)

**Two-stage workflow was a deliberate user request**: Screener (scan
everything, coarse) → tick stocks → Monitoring (few stocks, detailed,
faster refresh). Not a single unified view — keep this separation in V2.

**Universe options**: Full Nifty500 / Nifty50 subset (fast test) / Custom
list (paste your own tickers — this is confirmed working well by the user,
their preferred way to test with a small band of stocks). Custom list
strips `.NS` suffix automatically and dedupes.

**Chart range control**: Monitoring detail view defaults to "Today only"
for intraday timeframes (was previously showing full downloaded history,
which the user found too noisy). Indicators are computed on full history
first, then the display window is trimmed — do NOT truncate before
computing EMA50/Bollinger or they'll be inaccurate.

## Deployment context
User deploys via **GitHub → Streamlit Community Cloud** (share.streamlit.io),
not local-only. This matters because:
- Filesystem is ephemeral — `data/watchlist.json` resets on app
  sleep/redeploy. Added sidebar Download/Restore buttons as a workaround.
- NSE live list fetch will likely get blocked from Streamlit Cloud's shared
  IPs — falls back to bundled CSV automatically (expected, not a bug).
- Yahoo Finance rate-limits harder from cloud IPs — added `st.cache_data`
  caching (TTL 60-180s) around fetch calls so Streamlit's rerun-on-every-click
  behavior doesn't hammer Yahoo repeatedly.

## Testing status (as of last conversation)
- User tested a 50-stock custom-list scan on desktop — **worked well**,
  scores/confidence looked sensible.
- Tested on **mobile**: analysis and monitoring worked, but sidebar
  navigation (toggles/sliders in the collapsed drawer) was clunky. Desktop
  experience rated as very good. This confirms the plan to build a
  lightweight mobile-only Monitoring view for V2 (no sidebar tuning, no
  full scan — just check shortlisted stocks + score + signal on the go).
- Chart range fix (today-only default) confirmed working well via screenshot.

## Backtest tab — added this session (cutoff replay / stoploss-target simulator)
New third tab, `🧪 Backtest`, plus a new pure-logic module `backtest.py`
(no Streamlit imports — same convention as `indicators.py`/`scoring.py`, so
it stays independently testable). Meant for **post-market strategy
testing**, not live trading: "would this setup have worked?"

**Workflow the user wanted:**
1. Give a cutoff time (e.g. 09:30) on a given trading day → run the
   screener using *only* data known up to that point → get a ranked list.
2. Manually shortlist ~5-10 stocks, tag each Long/Short (user chose manual
   tagging over auto-inferring direction from the Signal label).
3. Replay the *rest* of that day's 5-minute candles forward and check
   whether stoploss% or target% would have hit first, or neither by EOD.
   Stoploss%/target% are sliders (toggles) so the user can quickly re-run
   with different values to see what would've worked better.

**Key implementation details / gotchas for future edits:**
- `split_at_cutoff(df, cutoff_date, cutoff_time)` is **deliberately
  asymmetric**: `df_before` carries ALL history up to and including the
  cutoff candle (spanning prior calendar days), while `df_after` is
  restricted to that single calendar day only, strictly after cutoff_time.
  This was a bug I caught before shipping — if `df_before` were restricted
  to same-day-only, a 09:30 cutoff would only have 2-3 candles since market
  open, nowhere near enough for EMA50/Bollinger(20)/RSI(14)/ADX(14) to be
  valid (same "don't truncate before computing indicators" rule as the
  Monitoring chart-range fix above, just applied here too).
- Split compares on `.date()`/`.time()` rather than tz-aware Timestamps —
  sidesteps tz-localization mismatches between yfinance's index and naive
  Streamlit date/time widget values.
- 5-minute interval chosen (over 1-minute) per user's tradeoff: less
  precise but Yahoo gives ~60 days of history vs ~7 days for 1-minute.
- Same-candle ambiguity (both stoploss and target fall inside one candle's
  High/Low range — OHLC can't tell you which happened first) resolves as
  **stoploss-hit-first, worst case**, per explicit user choice.
- `simulate_trade()` also returns MFE%/MAE% (max favorable/adverse
  excursion) even on "No Hit (EOD)" outcomes — e.g. a trade might have
  gotten within 0.1% of target without ever touching it, useful signal for
  tuning the stoploss/target % values.
- Direction is 100% manual (user's choice, not auto-inferred from Signal)
  — a `st.data_editor` with a Long/Short selectbox column per shortlisted
  symbol.
- `period_for_cutoff_date()` picks a small-buffer yfinance period string
  based on how far back the cutoff date is, capped at 60d (5m interval max).
- Full-day OHLCV per symbol is cached in `st.session_state["bt_symbol_data_map"]`
  after the screener run so the simulation step doesn't need to re-fetch.
- Logic was unit-tested with synthetic data (target-only, stoploss-only,
  ambiguous same-candle, Long, Short, no-hit/EOD, empty-data cases) — all
  passing before this was wired into the UI. Not yet tested against real
  Yahoo data / in the deployed app.

## V2 backlog (in rough priority order the user cared about)
1. **VWAP** — user specifically wants this, called it "very good for sure."
   Note: VWAP is session-based (resets each trading day), not a rolling
   indicator like EMA — needs per-day cumulative calculation logic, unlike
   everything else currently in `indicators.py`. Add to scoring the same
   way EMA trend works (price above/below VWAP = bullish/bearish lean).
2. **Visual/sound alerts** on signal changes (e.g. entering Strong Buy/Sell).
   Discussed options: passive row-highlighting, toast/banner notification,
   or browser-beep via embedded JS (needs care re: Streamlit's rerun model
   so it doesn't repeat-fire on every rerun).
3. **Lightweight mobile view** — Monitoring-only, no sidebar tuning, no full
   scan. Separate page or mode toggle.
4. **Remaining ~14 candlestick patterns** (see list above).

## Tone/expectations to carry forward
User is thoughtful and testing rigorously before trusting the tool — has
already internalized that Trade Score/Confidence are rules-based summaries,
not predictions, and that backtesting/paper-trading is needed before relying
on it. No need to over-caveat every response; they get it. Keep responses
practical and specific rather than re-explaining disclaimers already absorbed.
