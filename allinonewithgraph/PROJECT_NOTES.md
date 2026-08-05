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

## VWAP display column changed to VWAP % — same session, small follow-up
User found the raw VWAP price column confusing to eyeball (it's in the
stock's own price units, so it "goes into thousands" for higher-priced
names, unlike every other column which is normalized). Swapped the
**displayed** column from raw VWAP price to **VWAP %**
(`(Close - VWAP) / VWAP * 100`, e.g. `+0.8` = 0.8% above VWAP) — the raw
price is still computed and returned internally (`result["vwap"]`) in
case it's needed later, just not shown in the table per "price we can
calculate if required." New helper `_vwap_pct()` in `scoring.py`, unit
tested (above/below VWAP, zero-VWAP guard).

## REVERTED: Extension smoothing + VWAP default — same session, based on live testing
After trying the smoothed Extension + VWAP together, the user found the
combination faded out far too many setups — too few Short candidates were
surviving both filters at once. Reverted:

- `signal_extension()` back to the **dead-zone version**: flat 0 below
  1.5 ATR of stretch, linear fade to ±1 by 4 ATR. (The continuous-ramp
  version is still in git history / earlier in this file if it's worth
  revisiting — just not what's live now.)
- **VWAP now defaults OFF** (`DEFAULT_INDICATOR_ACTIVE = {"VWAP": False}`
  in `app_v1.py`) rather than being removed — one click to re-enable in
  the sidebar, no code changes needed if the user wants to test it again.
  The NaN-guard fix from the smoothing work was kept in both functions
  (that was a real correctness bug independent of the smoothing style).

**Important nuance the user flagged**: the problem showed up with Extension
+ VWAP **combined**, not necessarily either one alone. If/when this gets
revisited, worth specifically re-testing the combination (both active
together) rather than just re-testing each in isolation — that
interaction is the actual open question, not either indicator by itself.

## New: 📱 Mobile tab — added same session
Fourth tab, purpose-built for checking the tool while away from the desk
during market hours -- not a scaled-down version of everything, a
genuinely different narrow workflow. Explicit design decisions from the
user, in order:

1. **Scan phase**: a "📡 Scan Now" button (not silent/automatic) + a
   2-option **Nifty50 / Nifty500** radio (no other universe options, no
   sidebar indicator tuning exposed here -- but it DOES reuse whatever
   `active_indicators`/`weights` are currently set in the sidebar, since
   that's shared global state across every tab already, not something
   unique to Mobile).
2. Returns **up to 10 cards**: a **guaranteed top-5 Buy-aligned +
   top-5 Sell-aligned** split (not a straight top-10 by score) so the
   user always sees both sides even on a one-directional market day. Only
   🟢-Aligned candidates are shown at all (reuses the sector-alignment
   work from earlier this session) -- if fewer than 5 qualify on either
   side, it just shows what's there and says so, rather than backfilling
   with non-aligned picks.
3. Each card: Symbol, Signal (🟢/🔴 + label), Trade Score, Chg %, Sector,
   LTP, plus a checkbox. **"▶ Start Monitoring Selected"** moves ticked
   symbols into the Monitoring section below.
4. **Monitoring phase**: Direction **auto-fills from the Signal**
   (Buy/Strong Buy -> Long, Sell/Strong Sell -> Short) but stays editable
   per-card via a small selectbox -- explicit user choice, not fully
   locked like a hard rule. Shared Stoploss %/Target % number inputs
   (defaults 0.5/1.0, same convention as Backtest). **Manual "🔄 Refresh"
   only** -- no auto-polling, to keep data usage/battery light on mobile,
   consistent with the same reasoning already applied to VWAP/live data
   elsewhere.

**Key implementation trick -- reuses `backtest.py` almost as-is instead of
writing new simulation logic:**
- When a card is added to Monitoring, `EntryPrice` = that symbol's LTP at
  scan time, `EntryTime` = the last candle timestamp from the scan's own
  fetch (a real `pd.Timestamp`, not just "now").
- On every Refresh, re-fetches that symbol's data fresh, then calls the
  EXACT SAME `split_at_cutoff(fresh_df, entry_time.date(), entry_time.time())`
  and `simulate_trade(df_after, entry_price, direction, sl_pct, target_pct)`
  that the Backtest tab uses -- just fed live/today's data instead of a
  past date. `split_at_cutoff` doesn't care whether the "cutoff" was 2
  hours ago on a live day or 2 weeks ago on a historical one; the logic is
  identical either way. This was the reasoning that shaped the design
  discussion before building it, and it held up in testing below.
- Monitor list persists across new scans (starting a fresh scan does NOT
  clear what's already being monitored) -- lets the user re-scan later in
  the day for new setups without losing track of earlier ones. Each
  monitored card has its own ✖ remove button, plus a "Clear all
  monitoring" reset.

**Tested before shipping:**
1. Top-5/top-5 selection logic in isolation, including the edge case of
   fewer than 5 qualifying candidates on one side (doesn't crash, doesn't
   backfill incorrectly, reports the shortfall).
2. **The core reuse trick end-to-end**: built a synthetic scan snapshot
   (entry captured mid-session, e.g. 11:00), then a synthetic "later
   refresh" fetch with more candles including a forced target-hit a few
   candles after the entry timestamp -- confirmed `split_at_cutoff`
   correctly returns only candles strictly after the captured entry time
   (not the whole day), and `simulate_trade` correctly detects the hit.
   This was the one part of the design most likely to have a subtle bug
   (Timestamp date/time extraction, off-by-one on "strictly after"), so
   worth having verified it explicitly rather than assuming it "should
   just work" because Backtest already worked.

**Not yet tested:** against real Yahoo data / the actual Streamlit UI
(card rendering, checkbox state across reruns, button/rerun timing for
the ✖ remove and Refresh flows) -- same standing caveat as every other
feature built this way in this project. Worth a real walkthrough on an
actual trading day before trusting it mid-session.

## Desktop Monitoring tab: added the same live stoploss/target check as Mobile
User feedback after using Mobile: the desktop Monitoring tab had never had
this at all — it only ever showed current Trade Score/Signal for saved
watchlist stocks, with no concept of "I entered at X, tell me if I hit my
stop or target." New **"🎯 Position Tracking"** section added directly
inside `tab_monitoring`, right after the main watchlist table.

**Same pattern as Mobile, deliberately NOT the same literal session-state
list** (kept separate: `desktop_position_list` / `desktop_position_results`
vs. Mobile's `mobile_monitor_list` / `mobile_monitor_results`) — the
trigger flows differ enough (desktop: tick rows in the existing watchlist
table; Mobile: tick scan-result cards) that sharing the exact list added
more complexity than it saved, and Streamlit widget keys can't be reused
across two rendered instances anyway. The part that IS genuinely shared
is the only part that actually matters for correctness: both call the
exact same `split_at_cutoff()` / `simulate_trade()` from `backtest.py`.

**Flow:**
1. A `Track` checkbox column (via `st.data_editor`) on top of the existing
   watchlist table -> "▶ Start/Update Tracking Selected" captures
   EntryPrice (current LTP) + EntryTime (last candle timestamp from
   `detail_data`, already being computed for the chart-inspector section)
   + Direction (auto from Signal, same Buy->Long/Sell->Short convention as
   Mobile).
2. Shared Stoploss %/Target % number inputs (0.5/1.0 defaults, same as
   everywhere else), a Direction column editable per-row via
   `SelectboxColumn` (same widget pattern as the Backtest tab), and a
   manual "🔄 Refresh tracked positions" button.
3. Refresh always fetches fresh **5-minute** data regardless of whatever
   chart interval is selected in the sidebar — stoploss/target tracking
   needs to be intraday to mean anything; noted in the caption that
   results are most meaningful if the sidebar interval is also intraday
   (if it's Daily, EntryTime would be a daily-bar timestamp, not a real
   moment in the trading day).
4. Results table: Status (🎯/🛑/🟡 still open/⚠️ no data), Last Price,
   P/L %, As of time.
5. Duplicate-tracking guard: re-ticking an already-tracked symbol and
   clicking Start Tracking again is a no-op, doesn't create a second entry.

**Tested before shipping** (isolated logic tests, no Streamlit): the
add-to-tracking flow with auto-direction-from-Signal, the duplicate-add
guard (0 added on a re-click), and direction-edit persistence writing
back into the underlying list correctly. The `split_at_cutoff`/
`simulate_trade` reuse itself was already verified end-to-end under the
Mobile tab section above — same functions, not re-tested a second time
here since the logic is identical, only the surrounding UI differs.

**Not yet tested:** the actual Streamlit UI (data_editor checkbox
interaction, button/rerun timing) — same standing caveat as Mobile and
everything else built this session.

## MAJOR RESTRUCTURE: consolidated to 2 tabs — Screener & Monitoring removed
User's call: "too many tabs" — Backtest's engine already did everything
Screener and Monitoring did (Screener = cutoff is "now"; Monitoring =
cutoff is "whenever I started tracking," with ongoing replay), so keeping
all three as separate tabs was redundant surface area, not three genuinely
different tools. **App is now 2 tabs: "🧠 All-in-One (Scan · Monitor ·
Backtest)" and "📱 Mobile"** (Mobile intentionally left untouched — it's a
different physical context, not redundant with this).

**What got removed entirely:** the Screener tab and the Monitoring tab
(including the "🎯 Position Tracking" section added earlier this session —
superseded, not ported, see below on why).

**What got migrated into the (renamed) Backtest tab:**
1. **Persistent watchlist**: Screener's "save selections" -> watchlist.json
   flow would otherwise have gone dark (sidebar backup/restore widgets
   would have nothing feeding them). Added a lightweight
   "💾 Save shortlist to watchlist.json" button next to the existing
   shortlist checkboxes — merges with whatever's already saved rather than
   overwriting. Not a full watchlist-management UI (no in-app remove/view
   list) — that felt like exactly the kind of extra surface area this
   restructure was trying to cut; the sidebar download/upload still covers
   backup/restore.
2. **Chart inspector**: candlestick chart + indicator-breakdown bar chart +
   detected patterns, for any symbol in the current shortlist. Ported
   from Monitoring's `focus_symbol` selector. Reuses the SAME
   `filter_chart_range()`/`make_candlestick_chart()` helpers, unchanged.
   One correctness fix made while porting: the first draft filtered the
   indicator dataframe with `df.index <= pd.Timestamp.combine(cutoff_date,
   cutoff_time)` — this throws `TypeError: Invalid comparison between
   dtype=datetime64[...,Asia/Kolkata] and Timestamp` because yfinance's
   intraday index is tz-aware and `Timestamp.combine()` produces a
   tz-naive one. **Confirmed the crash with a standalone repro before
   fixing** — fixed by reusing `split_at_cutoff()` (already tz-safe by
   design, compares `.date()`/`.time()` components rather than full
   Timestamps, per its own docstring) instead of writing a second,
   buggier version of the same logic inline. Re-tested the fixed path
   with tz-aware synthetic data afterward to confirm `score_asof` gets a
   valid pre-cutoff slice.

**What did NOT get ported, deliberately: Position Tracking.** This was
built into Monitoring earlier THIS SAME SESSION, but its entire job —
capture entry price/time, track stoploss/target, manual refresh — is now
just what the existing Trade Simulation section does once the cutoff date
is today. No duplicate feature needed.

**The "one small input" the user asked for — live-aware Trade Simulation
button:**
- The button now detects `bt_cutoff_date == dt.date.today()`.
- **Historical date** (unchanged behavior): button reads
  "🧪 Run Trade Simulation", reuses the already-fetched
  `bt_symbol_data_map` from the scan (that day is closed, data won't
  change, no need to re-fetch).
- **Today**: button relabels to "🔄 Refresh (live...)", and instead of
  reusing the scan snapshot, **re-fetches fresh data per symbol**
  (`cached_fetch_single`) before re-running `simulate_trade` — so new
  candles that arrived since the cutoff get picked up. Clicking it again
  later in the session re-checks against whatever's arrived since. A
  caption explains this distinction so it's not a silent behavior change
  depending on the date picked.
- This is the exact same `split_at_cutoff`/`simulate_trade` reuse pattern
  used for Mobile and (the now-removed) desktop Position Tracking — same
  functions, just triggered from one adaptive button instead of a
  separate one.

**Verification before shipping:**
- Full-file compile check after the restructure.
- Grepped for dangling references to every removed-tab-local variable
  (`mon_df`, `detail_data`, `nifty_df`, `symbol_col`, `watchlist`) across
  the whole file — none found; Backtest and Mobile were already
  self-contained (each does its own `cached_nifty500_list()` /
  `cached_fetch_batch()` calls rather than reaching into Screener's
  locals), confirmed via a variable-cross-reference sweep before deleting
  anything, not just assumed safe.
- Confirmed exactly 2 `with tab_*:` blocks remain post-edit (previously 4).
- The tz-aware chart-inspector bug above was caught and fixed with an
  actual repro, not just reasoned about.

**Not yet tested:** the real Streamlit UI end-to-end — particularly
whether the live "🔄 Refresh" button feels right in practice throughout a
trading session, and whether the "no watchlist management UI, just a save
button" tradeoff is the right level of feature-cutting or too far. Worth
the user's real-session feedback before iterating further here.

## New: Trend (last 5) + Trend Conviction — same session
User's insight: the tool only ever answers "what does this look like right
now," never "how did it get here" — a Strong Buy that's been building
(Buy -> Buy -> Strong Buy) is a different situation than one that just
flipped from Neutral a candle ago, even though both show the same current
Signal. Directly relevant to the earlier "Strong Buy at the peak" problem.

**Design, per explicit user preference for simplicity:**
- NOT folded into the Trade Score itself as a 9th weighted component —
  same pattern as Extension/Sector Alignment: surface it as an info
  column first, let the user judge, only formalize into scoring later if
  it proves out in their own testing.
- Two new columns: **Trend (last 5)** — a sequence string like
  `N -> B -> B -> SB -> SB` (abbreviated signal at each of the last 5
  candles) — and **Trend Conviction** (0-100) — a single number, since
  user said "keep it simpler" after an initial "Score Δ" proposal. Same
  agreement-ratio x average-strength SHAPE as the existing Confidence
  formula (deliberately reused, not reinvented): what fraction of the
  last 5 candles are in the same Buy/Sell bucket as the CURRENT signal,
  weighted by how strong (far from 50) those agreeing points were. High =
  consistently building; low = mixed/oscillating even if the current
  reading looks strong. Neutral current signal -> conviction is 0 (no
  direction to have conviction about).

**Key mechanism (worth remembering for future edits):** needs ZERO new
data or fetches. `indicators.py` already computes every indicator as a
full column across the whole fetched history, not just the last row — so
"what would this have scored 3 candles ago" is just re-running the same
(cheap) combination step against an earlier row, not re-fetching or
re-computing anything. Refactored `scoring.py`'s core into
`_score_dataframe()` (works off a truncated dataframe, "now" = the whole
thing unchanged) so `score_symbol()` (current) and the new `score_trend()`
(last N points) share exactly one implementation rather than two that
could drift apart. One real subtlety handled: candlestick pattern
detection needs a *window* of candles, not a bare row, so `score_trend()`
truncates the dataframe progressively (`df.iloc[:i+1]`) rather than just
extracting rows.

**A real integration bug was caught and fixed while wiring this in — not
just theoretical, actually reproduced:** the main scan loop calls
`split_at_cutoff(df, ...)` on RAW OHLCV (no indicator columns yet).
`score_asof()` (from `backtest.py`) computes indicators internally on its
own copy, scores it, and returns just the result dict -- discarding that
computed copy. The first version of this feature then called
`score_trend(df_before, ...)` on the ORIGINAL raw `df_before` right after
-- which has no indicator columns at all, so every `row.get("RSI", 0)`
etc. silently fell back to defaults, producing a bogus "N->N->N->N->N"
trend regardless of the real data. **Caught this specifically by testing
the full pipeline end-to-end** (score_asof's result vs. a direct
`score_symbol` call on the same data disagreed -- 61.7/Buy vs. 50.0/
Neutral -- which shouldn't be possible if they're the same underlying
data) rather than trusting the isolated unit tests on `score_trend()`
alone, which used properly-indicator-computed data and so didn't expose
the bug. Fixed by computing indicators ONCE per symbol
(`compute_all_indicators(df_before)`) and reusing that same `df_before_ind`
for both the current score and the trend, replacing the `score_asof()`
call at that site with the equivalent direct call. Re-verified
post-fix: the trend's own last point now provably matches the current
as-of-cutoff score exactly (asserted, not just eyeballed) on the same
synthetic data that exposed the original bug.

**Tested before shipping:** `_score_dataframe` regression (score_symbol
behaves identically post-refactor), the insufficient-history guard (empty
trend list, `trend_summary` handles it gracefully), a synthetic
"building momentum" case vs. a synthetic "just flipped this candle" case
confirming conviction is meaningfully lower for the latter even when both
land on a similar current signal, and the full pipeline integration test
above that caught and confirmed the fix for the real bug.

**Not yet tested:** against real Yahoo data / the actual Streamlit UI —
same standing caveat as everything else this session.

## Bug fix: chart/indicator breakdown not updating on live Refresh
User noticed: clicking the live "🔄 Refresh" button correctly updated the
stoploss/target simulation results, but the candlestick chart and
indicator breakdown for the same symbol stayed frozen at scan-time values.

**Root cause**: the chart inspector reads its data from
`st.session_state["bt_symbol_data_map"]`, which was only ever written
ONCE, at the original scan. The live Refresh button fetches fresh data
per symbol (`cached_fetch_single`) for the stoploss/target check, but
that fresh data lived only in a local loop variable -- never written back
into session state. So the simulation results (computed fresh every
click) and the chart (reading a permanently stale snapshot) silently
drifted apart the moment any time passed between the scan and a refresh.

**Fix**: when `bt_is_live`, each freshly-fetched `df_full` is now also
written back into the `bt_symbol_data_map` dict (`bt_symbol_data_map[sym]
= df_full`), plus an explicit `st.session_state["bt_symbol_data_map"] =
bt_symbol_data_map` write-back after the loop (belt-and-suspenders on top
of the fact that dict mutation in place already propagates via shared
reference -- made explicit rather than relying on that being obvious).
The chart inspector recomputes indicators fresh on every script rerun
(not cached), so once its source data is current, it just works on the
next render -- same rerun that the Refresh button itself triggers.

Verified with an isolated test simulating the session-state
read/mutate/write-back sequence, confirming a "chart inspector" read
after the fix sees the fresh data rather than the original stale value.

## V2 backlog (in rough priority order the user cared about)
1. ~~**VWAP**~~ — ✅ DONE (see "EXTENSION smoothed + VWAP added" section above).
2. **Visual/sound alerts** on signal changes (e.g. entering Strong Buy/Sell).
   Discussed options: passive row-highlighting, toast/banner notification,
   or browser-beep via embedded JS (needs care re: Streamlit's rerun model
   so it doesn't repeat-fire on every rerun).
3. ~~**Lightweight mobile view**~~ — ✅ DONE (see "📱 Mobile tab" section
   above). Turned out different from the original note below (Scan +
   card-based shortlist, not just Monitoring-only) based on further
   discussion with the user.
4. **Remaining ~14 candlestick patterns** (see list above).

## Tone/expectations to carry forward
User is thoughtful and testing rigorously before trusting the tool — has
already internalized that Trade Score/Confidence are rules-based summaries,
not predictions, and that backtesting/paper-trading is needed before relying
on it. No need to over-caveat every response; they get it. Keep responses
practical and specific rather than re-explaining disclaimers already absorbed.
