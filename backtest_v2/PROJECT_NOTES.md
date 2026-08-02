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

## Index (Nifty 50) alignment context — added this session
New module `market_context.py` (pure logic, no Streamlit) + a new
`fetch_index()` in `data_fetch.py`. Surfaces "is the broader market moving
the same direction as this stock" — a top-down check the user described
doing manually before trusting a signal ("if index is up and my stock is
also positive, only trade if I get a Buy signal").

**Explicit user decisions this session:**
- Measured as **% change from the day's first candle Open to latest
  Close** (not an EMA-based measure — user picked this over the
  alternative options offered).
- **Info-only, not a hard filter.** Shows as an `st.metric` (Nifty 50
  change) above the results table + a per-stock "Chg %" column in both
  tabs. Nothing is hidden/filtered based on it — the user wants to judge
  alignment visually themselves, same as their manual process. If this
  ever changes, the hook point is straightforward: filter `result_df` /
  `bt_result_df` on `Chg % (today)` sign vs `index_chg` sign before
  building the display df.

**Implementation notes:**
- `fetch_index(index_symbol="^NSEI", ...)` deliberately does NOT go
  through `to_yf_symbol()` — that function appends `.NS`, which is only
  correct for individual NSE equities, not index tickers like `^NSEI`.
- `day_change_pct(df, target_date=None)` — for the live Screener tab, uses
  "today" (max date in the fetched df) implicitly.
- `day_change_pct_asof(df, cutoff_date, cutoff_time)` — for the Backtest
  tab, restricted to candles at/before the cutoff only (same
  don't-peek-ahead principle as `split_at_cutoff` in `backtest.py`).
- Index fetch is cached the same way as batch equity fetches
  (`cached_fetch_index`, `ttl=180`) so it doesn't add a second slow
  network call on every rerun.
- Unit-tested with synthetic data (full-day change, as-of-cutoff change,
  no-data-for-date case) before wiring into the UI.
- **Not yet tested against real Yahoo data / in the deployed app** — same
  caveat as the Backtest tab itself.

## Index alignment upgraded to SECTOR-SPECIFIC — added same session
Follow-up to the broad-Nifty50 version above. The user clarified they
wanted per-sector comparison, not just the broad index — e.g. TCS should
be checked against Nifty IT, not Nifty 50, since the broad index can be
flat while IT as a sector is moving hard either way.

**What changed:**
- `market_context.py` gained `SECTOR_INDEX_MAP`, `index_for_industry()`,
  `index_display_name()`, and `alignment_label()`.
- `SECTOR_INDEX_MAP` maps the NSE "Industry" label (exactly as it appears
  in the Nifty500 constituent CSV, e.g. `nifty500_fallback.csv`'s
  `Industry` column) to a sectoral index ticker. **Only mapped where a
  ticker was verified to actually exist on Yahoo Finance via web search**
  (as of Aug 2026): `^CNXIT` (IT), `^CNXPHARMA` (Healthcare — closest
  proxy, Nifty500 Healthcare is pharma-heavy), `^CNXAUTO`, `^CNXFMCG`,
  `^CNXMETAL`, `^CNXENERGY` (Oil Gas & Consumable Fuels + Energy),
  `^CNXREALTY`, `NIFTY_FIN_SERVICE.NS` (Financial Services — note the
  underscore/.NS ticker format, different convention from the `^CNX*`
  ones), `^CNXINFRA` (used as an approximate proxy for both Construction
  and Construction Materials — not an exact sector match, flagged as such
  in the code comment). Industries with **no confirmed dedicated Yahoo
  ticker** (Capital Goods, Consumer Durables, Power, Chemicals, Consumer
  Services, Telecommunication, Services, Textiles, Diversified) fall back
  to Nifty 50 rather than guessing a ticker that might not resolve.
- Both tabs now fetch each **unique** sector index only ONCE per scan
  (not once per stock) — a Nifty500 scan might only touch ~10 distinct
  sectors even with 500 symbols, so this avoids redundant Yahoo calls.
  `^NSEI` change is reused from the existing broad-index fetch rather than
  fetched twice.
- New columns in both Screener and Backtest results tables: **Sector**
  (friendly display name), **Sector Chg %**, and **Aligned**
  (🟢/🔴/⚪ emoji, next to Signal — matches the visual the user asked for:
  "a small column just besides signal saying it is aligned").
- `alignment_label(signal, stock_chg, sector_chg)`: True if Buy/Strong Buy
  AND both stock and sector are up; True if Sell/Strong Sell AND both are
  down; **None** (shown as ⚪, not 🔴) for Neutral signals or missing data
  — a Neutral signal isn't "misaligned," there's just nothing to check.
- Symbol→industry lookup always comes from `cached_nifty500_list()`
  regardless of which universe the user picked (Nifty50 subset / Full
  Nifty500 / Custom list in Backtest) — a custom-pasted symbol might still
  be a real Nifty500 constituent, so this isn't skipped for that path.
  Symbols genuinely outside the list (or with a missing/NaN Industry
  value) fall back to Nifty 50, same as unmapped industries.
- Still **info-only, not a filter** — same explicit user choice as the
  broad-index version. Nothing is hidden based on alignment.
- Unit-tested (`index_for_industry`, `index_display_name`,
  `alignment_label`) plus an isolated end-to-end simulation of the full
  row-building/aggregation logic (unique-sector-fetch-once, per-symbol
  lookup, alignment computation) with synthetic data before wiring into
  the UI — all passing. **Not yet tested against real Yahoo data** for
  the sector tickers specifically (the tickers themselves were verified
  to exist via web search, but not fetched live in this environment,
  which has no network access for testing).

## Related idea raised, still NOT built: per-stock trend-vs-MA gate
(A trader friend's suggestion — different axis from the above: compares
the stock against its OWN moving average rather than an index/sector, and
would be a hard gate rather than an info column. Still just discussed, not
built — see full context earlier in this file if picked back up.)

## New indicator: EXTENSION (mean-reversion / exhaustion fade) — added same session
Direct response to a real problem the user spotted using the tool: **Strong
Buy signals kept firing right at local tops**, right before a downtrend.

**Root cause, confirmed by reading `scoring.py`:** 5 of the original 7
components (MACD, ADX, BOLLINGER, VOLUME, EMA_TREND) are pure
momentum-following with no ceiling — "further extended in the trend's
favor" always scores MORE bullish, never less. Only RSI faded at extremes,
and it was frequently outvoted by the other five all agreeing "strong
move" (weight 1.0 out of ~6.4 total) — structurally, that combination is
most likely to hit max score exactly when a move is most stretched, i.e.
most likely to reverse.

**Fix:** new 8th component in `scoring.py`, `signal_extension()`:
- `extension = (Close - EMA20) / ATR` — how many ATRs price has stretched
  from its own 20-period mean. ATR-normalized (not RSI's gain/loss ratio),
  so it's an independent check, not a duplicate of RSI.
- Deliberately the OPPOSITE lean from the other momentum components: the
  further price is stretched, the MORE this fades toward the opposite
  direction (further above mean -> bearish fade; further below -> bullish
  fade, i.e. oversold-bounce risk for shorts). Neutral inside ±1.5 ATR,
  fully faded by ±4.0 ATR (linear ramp between).
- `indicators.py`: `compute_all_indicators` now also stores `ATR` as its
  own column (previously computed internally by `compute_adx` but
  discarded, not exposed).
- Added to `DEFAULT_WEIGHTS` (weight 1.0, same as RSI/EMA_TREND) and to
  the sidebar's per-indicator toggle+weight UI (`INDICATOR_LABELS`) — same
  as every other component, fully optional/retunable, not hardcoded in.
- `extension_atr` (the raw, unscored ATR-distance value) is surfaced as a
  new **Extension (ATR)** column in both Screener and Backtest results
  tables, so the user can see the raw number driving the fade, not just
  the net effect on Trade Score.

**Tested before shipping:**
1. Unit tests on `signal_extension()` directly: neutral case (0), inside
   the no-fade zone (0), fully stretched up (-1.0), fully stretched down
   (+1.0), a partial/halfway fade (-0.5), and the `ATR<=0` guard (0).
2. **Integration test**: built a synthetic 80-candle series — steady
   uptrend, then one sharp blow-off spike far from EMA20 with a volume
   surge on the final candle (the exact "exhausted, about to reverse"
   shape the user described). Scored it WITH vs WITHOUT Extension active:
   **75.5 (Strong Buy) without Extension -> 65.4 (Buy, no longer
   "Strong") with it** — confirms the fix actually changes the outcome on
   the failure case it was built for, not just that the code runs.

**Not yet tested:** against real historical data via the Backtest tab —
next natural step is for the user to re-run some of the same dates/stocks
that previously showed "Strong Buy near a peak" and see whether Extension
active would have downgraded them, plus check it isn't overcorrecting
(fading genuinely strong trending moves too early). Weight is retunable
live in the sidebar if it needs dialing up/down.

## EXTENSION smoothed + VWAP added — same session, follow-up to the above
User feedback after the Extension work: don't keep adding new toggles for
every refinement (toggle sprawl), but the overextension fade specifically
should be a smooth continuous tilt rather than a flat dead-zone + ramp.
VWAP, on the other hand, is a genuinely new signal and *should* get its
own toggle.

**`signal_extension()` changed (same "EXTENSION" toggle, no new toggle
added):**
- Old: flat 0 below 1.5 ATR, then linear ramp to ±1 by 4 ATR (a dead
  zone before any penalty kicked in).
- New: continuous ramp from 0, `magnitude = clip(|extension|/4, 0, 1)` —
  e.g. a mild 1-ATR stretch now gets a small ~-0.25 tilt immediately,
  rather than nothing until crossing a threshold. Still fully faded by
  4 ATR either direction.
- Also fixed a latent NaN-guard bug while touching this: the old
  `if not atr or atr <= 0` check doesn't actually catch `atr == NaN`
  (NaN is truthy in Python, and `NaN <= 0` is False) — replaced with an
  explicit `np.isnan(atr)` check. Same fix applied to the new
  `signal_vwap()` below. In practice ATR is virtually always valid by
  the last row given the lookback windows already in use, but worth
  having the guard actually work.

**New: `VWAP` indicator, own toggle, own weight (default 1.0):**
- `indicators.py`: `compute_vwap()` — cumulative (typical price × volume)
  / cumulative volume, **resetting at each calendar day boundary**
  (grouped by `df.index.date`). Verified this specifically with a
  two-day synthetic test (day 1 prices ~100, day 2 prices ~200) to
  confirm day 2's VWAP isn't dragged down by day 1's history — this is
  the most likely place a subtle bug would hide, so worth flagging it was
  actually tested, not just assumed. Only meaningful on intraday
  timeframes (5m/15m/1h) — noted in both the docstring and the sidebar
  label; on Daily bars it's not a meaningful concept and should stay
  toggled off.
- `scoring.py`: `signal_vwap()` — same ATR-normalized-distance style as
  `signal_extension()` (`(Close - VWAP) / ATR`), full ±1 by 2 ATR away.
  Deliberately **NOT faded back toward neutral at large distances** the
  way Extension is — VWAP is read as a directional/positioning signal
  (institutions have been paying up/selling down vs. the session
  average), not an exhaustion signal, so "far above VWAP" stays bullish
  rather than getting walked back.
- Added to `DEFAULT_WEIGHTS`, `INDICATOR_LABELS` (sidebar toggle+weight,
  same pattern as every other component), and the raw `vwap` value is
  surfaced as a new **VWAP** column in both Screener and Backtest results
  tables.
- This closes out the **#1 item on the original V2 backlog** — VWAP had
  been on the list since the very first session.

**Also discussed, NOT done:** graduating `signal_ema_trend()` (still the
one true discrete-step-function component — only 4 possible outputs
regardless of magnitude). Flagged as a related issue but the user's
feedback in this exchange was specifically scoped to Extension's dead
zone and VWAP; didn't get an explicit go-ahead to touch EMA_TREND itself,
so left as-is. Worth revisiting if the "Strong Buy at the top" pattern
keeps showing up in backtests even with Extension active — EMA_TREND
handing out full marks (0.8) to a barely-there crossover would be the
next-most-likely remaining contributor.

**Tested before shipping:** VWAP daily-reset (two-day synthetic series),
`signal_extension`'s new continuous ramp (0, 1-ATR tilt, NaN-ATR guard),
`signal_vwap` (above/below/at/mild-distance, all uncapped-until-2-ATR as
designed), a `score_symbol` integration check that toggling VWAP on/off
actually moves the Trade Score, and **re-ran the original blow-off-spike
test** from the Extension section above to confirm the smoothing didn't
undo the original fix (still 69.5/Buy with Extension vs. 78.8/Strong Buy
without, on the same synthetic spike). **Not yet tested against real
Yahoo data** — same standing caveat as everything else in the Backtest
tab and its dependents.

## V2 backlog (in rough priority order the user cared about)
1. ~~**VWAP**~~ — ✅ DONE (see "EXTENSION smoothed + VWAP added" section above).
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
