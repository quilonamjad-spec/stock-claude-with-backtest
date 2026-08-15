# NSE 500 Bull/Bear Scanner (Streamlit)

Two-stage scanner + ranker for the NSE 500 universe, built on free Yahoo
Finance 5-minute data via `yfinance`.

## File structure

`app.py` is UI only — all logic lives in focused modules it imports from:

| File | Contains |
|---|---|
| `config.py` | Shared constants: timezone helper (`now_ist`), sector-index definitions, default ranking weights |
| `data.py` | Everything that talks to Yahoo/NSE: universe list, sector constituent mapping, batched OHLCV fetches |
| `indicators.py` | Indicator math (VWAP, RSI, ATR, CMF, EMA...) + Bull/Bear condition evaluation. No Streamlit/network dependency — independently testable |
| `gate_rank.py` | The quality gate (exhaustion filter) and conviction-strength ranking |
| `scan_pipeline.py` | Ties `data.py` + `indicators.py` into the Stage 1 scan; shared by the live scan and the Stage 5 backtest |
| `simulate.py` | Transaction cost estimate + the walk-forward trade replay engine (with trailing stop-loss) |
| `trade_panel.py` | Stage 3 — Kite order placement (currently parked, see below) |
| `kite_client.py` | Kite Connect auth/order/margin wrapper |

If you're only changing one thing — e.g. adding an indicator, tweaking the
cost model, adjusting how Yahoo data is fetched — you should only need to
touch the one relevant module, not scroll through the whole app.

## Setup

```bash
pip install -r requirements.txt
streamlit run app.py
```

Push all the `.py` files, `requirements.txt`, and this README to your
GitHub repo, then deploy on Streamlit Community Cloud (or anywhere else)
pointing at `app.py`. All the module files need to sit alongside `app.py`
in the same directory — they're plain local imports, not a package.

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

**Stage 2 — Quality gate, then rank**
Ranking purely by "how far/how much" a stock moved tends to surface
*exhausted* names — the ones furthest from VWAP or with the biggest volume
spike are often already late in the move, not early in it. To fix that,
Stage 2 first runs a quality gate before scoring anything:

| Check | Rejects |
|---|---|
| **Trend** | EMA21 slope has flattened (not still trending in the trade's direction over the last 5 bars) |
| **Structure** | Price stretched more than N ATRs from EMA9 (over-extended), or an unbroken run of >N same-direction bars (blow-off) |
| **Momentum** | RSI(14) outside a healthy band — e.g. above ~78 on a Bull setup is the classic overbought/exhaustion reading, not strength |

Only stocks that clear all three go on to be ranked. All three thresholds
are adjustable in the sidebar, and the gate can be disabled entirely if you
want the raw magnitude ranking back. Rejected stocks aren't discarded —
they're shown in an expandable panel with the reason(s) they failed, so you
can sanity-check the thresholds.

Stage 1 is pass/fail only — it doesn't tell you which passing stock is
*strongest*. Stage 2 scores each Stage-1 result across all four standard
factor families (trend, momentum, volatility, volume), converting every
metric to "higher = stronger" regardless of Bull/Bear:

| Factor family | Metric | Meaning |
|---|---|---|
| Trend | EMA trend separation | % gap between EMA9 and EMA21 |
| Trend | VWAP distance | % how far price is from VWAP |
| Momentum | Breakout distance | % price has cleared the 10-bar high/low by |
| Volatility | ATR expansion | ATR(14) ÷ its own 20-bar average (>1 = volatility expanding) |
| Volume | Volume surge | current volume ÷ 20-period average volume |
| Volume | Money flow (CMF) | magnitude of the Chaikin Money Flow reading |

Each metric is min-max normalized to 0–1 across the current result set (so
a 5x volume spike doesn't automatically dominate just because it's a bigger
raw number than a 2% VWAP distance), then combined into a weighted
**Rank Score** using the sidebar sliders (defaults: 20% Trend/EMA, 15% VWAP,
20% Breakout, 15% ATR expansion, 20% Volume, 10% Money flow). Bull and Bear
stocks are ranked separately since they're different trades, not competing
on the same scale.

Volatility expansion is included deliberately, not just for completeness:
a breakout on *expanding* ATR tends to have more room to run than one on
flat/contracting volatility, where the move is more likely to stall.

## Sector-index alignment

Every Stage 1 result now shows three extra columns: **Index** (which
sectoral index the stock belongs to — e.g. TATAMOTORS → NIFTY AUTO),
**Index % Chg** (that index's own % move from the day's open, evaluated at
the same as-of time as the stock), and **Aligned** (✅ if the stock and its
index are moving the same direction, ❌ if not).

Mapping comes from NSE's own sectoral constituent lists (Bank, Private
Bank, PSU Bank, Auto, IT, Pharma, Healthcare, FMCG, Consumer Durables,
Metal, Chemicals, Media, Realty, Oil & Gas, Energy, Financial Services); a
stock not found in any of them falls back to NIFTY 50 as the comparison
index. Index prices come from Yahoo Finance sectoral index tickers.

A couple of things worth knowing:
- **NIFTY BANK ≠ Private Bank / PSU Bank.** NIFTY BANK is only the 12
  large-cap banks; a stock like BANDHANBNK sits in NIFTY PRIVATE BANK
  instead, which is why these are kept as separate sectors rather than
  merged.
- NIFTY Financial Services specifically uses a plain `NIFTY_FIN_SERVICE.NS`
  ticker rather than the `^CNX`-style prefix others use; `^CNXFIN` is a
  different, narrower index and would silently give you the wrong number.
- Chemicals' ticker (`NIFTY_CHEMICALS.NS`) follows the same naming pattern
  as the others but wasn't confirmed against a live quote page — if it
  turns out wrong, that one sector just won't populate and falls back to
  NIFTY 50, it won't break anything else.

A stock breaking out while its own sector is flat or red is a different
signal than one moving with full sector participation — this column
doesn't judge which is "better" (broad participation vs. relative
strength are both legitimate reads), it just gives you the read.

## Stage 4 — Trade Simulation / Monitoring

A separate section for tracking specific trades through the day — pick
stocks (from Stage 2's ranked results, or type a custom symbol), tag each
Long or Short, set stoploss/target %, and hit refresh anytime to see
whether either was hit since entry.

**How the replay works:** it walks 5-minute candles from your entry time
forward, checking each one in order. If a single candle's range crosses
both your stoploss and target/trail level, it resolves as **stoploss-hit-
first** — the conservative read, since 5-min OHLC can't tell us the actual
order price moved within that bar.

**Trailing stop-loss:** this is the dynamic behavior you asked for. Once
price reaches the initial target, the position isn't closed — the
stop-loss arms and starts trailing behind the best price seen since (the
peak for a Long, the trough for a Short), tightening every candle but
never loosening. The trade only closes when that trailing line is
eventually hit, so a big continued move keeps getting protected further
without capping the upside at the original 1% target. Trailing % is
independently adjustable from the initial stoploss %.

Every row also shows **Best seen (MFE %)** and **Worst seen (MAE %)** —
the best and worst the price moved relative to entry over the whole
window, regardless of what actually triggered the exit — useful for
judging whether your stoploss/target % are sized sensibly (e.g. if MAE is
consistently much smaller than your stoploss %, your stop is probably too
wide for how this setup actually behaves).

Refreshing re-fetches candles since each stock's own entry time, so this
doubles as live intraday monitoring, not just a one-shot backtest.

**Same-day only** is on by default — a trade entered on, say, the 12th
only checks SL/target/trailing through that day's close, even if you
refresh the app on a later day. Turn it off once you're deliberately
testing multi-day holds; leave it on for same-day intraday testing so a
stale open position from a past session doesn't silently keep "running"
into today's candles.

## Transaction-cost-aware P/L

Every simulated trade (Stage 4 and Stage 5) now shows both **P/L %**
(gross, just the price move) and **Net P/L %** (after an estimated
round-trip Zerodha intraday equity cost). The cost model covers
brokerage (lower of ₹20 or 0.03% per executed order), STT (0.025% sell
side), exchange transaction charges, SEBI charges, stamp duty, and GST —
see `estimate_roundtrip_cost_pct()` in `app.py`. It's a planning estimate
using an assumed trade value you set (not your real position size), not a
substitute for your actual Kite contract note — but it's what turns "this
setup made 1%" into "this setup made 1% minus the ~0.1% it actually costs
to trade it," which matters a lot at small trade sizes where costs are a
bigger fraction of the move.

## Stage 5 — Historical Backtest

Runs the full scan → quality gate → rank → simulate pipeline across many
past trading days in one go, instead of one real-time day at a time. This
is the fastest way to find out whether the current setup has a real edge
— days to weeks of manual real-time testing compressed into one run.

**Important: it uses your current sidebar settings.** The quality gate
thresholds and ranking weights set in the sidebar above apply directly to
the backtest — this is deliberate, so a backtest run tells you what your
*current* configuration would have picked, not some separate hardcoded
setup. Stoploss/target/trailing have their own sliders within Stage 5
(independent from Stage 4's, in case you want to test different values).

**How it works:** picks the last N weekday trading days (NSE holidays
just come back with no scan matches and get skipped automatically), runs
a scan at a fixed entry time on each one, applies the quality gate,
ranks, and takes the top N per phase (Bull/Bear) as that day's "trades."
Each one is walked forward with `simulate_trade` exactly like Stage 4,
same-day only.

**Output:** total trades, win rate, avg net P/L per trade, avg win vs.
avg loss, a cumulative equity curve, a by-day breakdown, and every
individual trade — downloadable as CSV.

**Performance note:** all days in one backtest run share the same Yahoo
data fetch window (same `period` parameter under the hood), so after the
first day's download, Streamlit's cache serves the rest of the days
almost instantly — the slow part is the first fetch, not each additional
day. "Faster" mode (first 60 stocks) is on by default; turn it off once
you're ready to test against the full NSE 500, at the cost of a slower
first run.

## Stage 3 — Trade Panel (Zerodha Kite)

**Currently parked, not deleted.** The code lives in `trade_panel.py` and
is skipped at runtime via `ENABLE_STAGE_3 = False` near the top of the
Stage 3 block in `app.py`. Flip that to `True` and it runs exactly as
before, once you're ready to pick this back up. Also means the app no
longer needs `kiteconnect` installed just to run the scanner/simulation —
it's only imported if the flag is on.

Manual, confirm-before-send order placement on top of the scan/rank
results (or a custom symbol typed in directly). Built in `kite_client.py`
+ the "Stage 3" section of `app.py`.

**Setup:**
1. `pip install kiteconnect`
2. Create a Kite Connect app at [developers.kite.trade](https://developers.kite.trade) — order placement is free (Personal API); live/historical market data needs the ₹500/month paid plan.
3. In the app: paste your API key + secret, click "Get login URL," log in through Kite, then paste the `request_token` from the redirect URL back in and hit Connect. The `access_token` is kept in memory only (session state) — never written to disk — and expires daily, so you'll repeat this each trading day.

**Flow:**
1. **Choose what to trade** — either pick a stock from your Stage-2 ranked results, or switch to "Custom symbol" and type any NSE trading symbol directly (useful for testing outside the scanner's picks).
2. **Size the position** — enter a margin budget (defaults to ₹100); this calls Kite's own margin calculator (`order_margins`) for that specific stock rather than assuming a flat leverage multiple, since MIS margin varies by stock under SEBI's peak-margin rules.
3. **Stop-loss & target** — % based, defaults to 0.5% SL / 1% target off the live LTP used for sizing.
4. **Place entry order** — defaults to **dry run**, which shows exactly what would be sent without touching the market. To go live, uncheck dry run and type `CONFIRM` — the button stays disabled until you do.
5. **Check order status** — after a live order, refresh to see the fill.

**Not built yet, and worth knowing before you rely on this:**
- Exit orders (SL-M + target LIMIT) aren't auto-placed after entry fills — that's the next piece to add, along with the background monitoring loop that cancels whichever leg didn't trigger.
- This all runs inside your Streamlit session — closing the tab stops everything. A persistent backend for order monitoring (so a trade survives you closing the browser) is a separate service, not yet built.
- Test with dry run and small real quantities before trusting it with meaningful capital.

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
