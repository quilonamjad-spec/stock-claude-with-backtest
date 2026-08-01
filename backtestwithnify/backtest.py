"""
backtest.py
-----------
Post-market "cutoff replay" backtester.

Workflow this supports:
  1. Freeze the screener at a cutoff time (e.g. 09:30) using ONLY data known
     up to that point -> get a ranked list, same as the live Screener would
     have shown you at that exact moment intraday.
  2. Pick your shortlist (5-10 stocks) and manually tag each as Long/Short.
  3. Replay the REST of that trading day's 5-minute candles forward and see
     whether a stoploss% or target% would have hit first, or neither.

This is intentionally decoupled from the live Screener/Monitoring tabs -
it works off a single calendar day's intraday history, never touches the
watchlist file, and is meant to be run after market close for strategy
testing ("would this setup have worked?"), not as a live trading signal.

Ambiguity note: OHLC candles don't tell you the order in which price moved
within a candle. If both the stoploss and target price fall inside the same
candle's High-Low range, we can't know which was actually hit first from
this data. Per project convention, we resolve this conservatively: assume
the stoploss was hit first (worst case for the trader).
"""

import datetime as dt

import pandas as pd

from indicators import compute_all_indicators
from scoring import score_symbol


def split_at_cutoff(df: pd.DataFrame, cutoff_date: dt.date, cutoff_time: dt.time):
    """
    Split an intraday OHLCV DataFrame into (df_before, df_after) around a
    cutoff date+time.

    IMPORTANT asymmetry (deliberate, matches how indicators.py expects to
    be used - see PROJECT_NOTES.md "do NOT truncate before computing
    EMA50/Bollinger"):

      - df_before carries ALL history up to and including the cutoff
        candle, spanning PRIOR CALENDAR DAYS too. Indicators like EMA50
        (needs ~50 periods), Bollinger(20), RSI/ADX(14) are meaningless on
        just that morning's 2-3 candles since market open - they need
        real lookback. Scoring "as of cutoff" should reflect what the live
        Screener would genuinely have shown you at that moment, which
        includes prior-day history.
      - df_after is restricted to `cutoff_date` ONLY, strictly after
        cutoff_time. This is the forward replay window for the trade
        simulation - "check till end of day" means that one trading day,
        not into subsequent sessions.

    Comparing on .date()/.time() (rather than a combined tz-aware
    Timestamp) sidesteps timezone-localization mismatches between the
    yfinance index and a naive Streamlit date/time widget value.
    """
    if df.empty:
        return df.iloc[0:0], df.iloc[0:0]

    idx_dates = df.index.date
    idx_times = df.index.time

    before_mask = (idx_dates < cutoff_date) | ((idx_dates == cutoff_date) & (idx_times <= cutoff_time))
    after_mask = (idx_dates == cutoff_date) & (idx_times > cutoff_time)

    before = df[before_mask]
    after = df[after_mask]
    return before, after


def entry_price_at_cutoff(df_before: pd.DataFrame):
    """Entry price = close of the last candle at/before the cutoff."""
    if df_before.empty:
        return None
    return float(df_before["Close"].iloc[-1])


def score_asof(df_before: pd.DataFrame, active_indicators: dict, weights: dict):
    """
    Score a symbol using ONLY candles up to the cutoff - mirrors exactly
    what the live Screener tab would have shown at that moment intraday.
    Returns None if there isn't enough pre-cutoff history to compute
    indicators on.
    """
    if df_before.empty:
        return None
    df_ind = compute_all_indicators(df_before)
    return score_symbol(df_ind, active_indicators, weights)


def _pl_pct(entry_price, exit_price, direction):
    raw = (exit_price - entry_price) / entry_price * 100
    return raw if direction == "Long" else -raw


def _result(outcome, hit_time, hit_price, entry_price, direction, best_excursion, worst_excursion):
    return {
        "outcome": outcome,
        "hit_time": hit_time,
        "hit_price": round(hit_price, 2),
        "pl_pct": round(_pl_pct(entry_price, hit_price, direction), 3),
        "mfe_pct": round(_pl_pct(entry_price, best_excursion, direction), 3),
        "mae_pct": round(_pl_pct(entry_price, worst_excursion, direction), 3),
    }


def simulate_trade(df_after: pd.DataFrame, entry_price, direction: str,
                    stoploss_pct: float, target_pct: float):
    """
    Walk forward candle-by-candle through df_after (everything strictly
    after the cutoff, same day) and determine whether stoploss or target
    was hit first.

    direction: "Long" or "Short"
    stoploss_pct / target_pct: percent values, e.g. 0.5 and 1.0 (not fractions)

    Returns a dict:
        outcome    : "Target Hit" | "Stoploss Hit" | "No Hit (EOD)" | "No Data"
        hit_time   : timestamp of the hit (or last candle's timestamp for EOD)
        hit_price  : price at which the outcome was decided
        pl_pct     : realized P/L% at that point
        mfe_pct    : max favorable excursion seen over the whole window
        mae_pct    : max adverse excursion seen over the whole window
                     (mfe/mae are useful even on "No Hit" runs - e.g. a
                     trade might have gotten within 0.1% of target without
                     ever touching it)
    """
    if entry_price is None or df_after.empty:
        return {
            "outcome": "No Data", "hit_time": None, "hit_price": None,
            "pl_pct": None, "mfe_pct": None, "mae_pct": None,
        }

    direction = direction.strip().title()
    if direction not in ("Long", "Short"):
        raise ValueError("direction must be 'Long' or 'Short'")

    if direction == "Long":
        target_price = entry_price * (1 + target_pct / 100)
        stop_price = entry_price * (1 - stoploss_pct / 100)
    else:
        target_price = entry_price * (1 - target_pct / 100)
        stop_price = entry_price * (1 + stoploss_pct / 100)

    best_excursion = entry_price   # most favorable price reached so far
    worst_excursion = entry_price  # most adverse price reached so far

    for ts, row in df_after.iterrows():
        high, low = row["High"], row["Low"]

        if direction == "Long":
            best_excursion = max(best_excursion, high)
            worst_excursion = min(worst_excursion, low)
            hit_target = high >= target_price
            hit_stop = low <= stop_price
        else:
            best_excursion = min(best_excursion, low)
            worst_excursion = max(worst_excursion, high)
            hit_target = low <= target_price
            hit_stop = high >= stop_price

        if hit_stop:
            # Covers both "stoploss only" and the ambiguous same-candle
            # case - worst case (stoploss first) wins by convention.
            return _result("Stoploss Hit", ts, stop_price, entry_price, direction,
                            best_excursion, worst_excursion)
        if hit_target:
            return _result("Target Hit", ts, target_price, entry_price, direction,
                            best_excursion, worst_excursion)

    # Neither hit by end of day -> mark-to-market at the last available close.
    eod_price = float(df_after["Close"].iloc[-1])
    eod_time = df_after.index[-1]
    return _result("No Hit (EOD)", eod_time, eod_price, entry_price, direction,
                   best_excursion, worst_excursion)


def period_for_cutoff_date(cutoff_date: dt.date, today: dt.date = None) -> str:
    """
    yfinance intraday history has a lookback ceiling (5m/15m ~60d). Pick a
    period string with a small buffer over how far back cutoff_date is, so
    a single fetch covers it without over-requesting on Yahoo for recent
    dates.
    """
    today = today or dt.date.today()
    days_ago = max(0, (today - cutoff_date).days)
    return f"{min(60, days_ago + 5)}d"
