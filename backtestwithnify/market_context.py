"""
market_context.py
------------------
Small, pure helpers for "how is the broader market doing" context, used by
both the live Screener and the Backtest tab.

Rationale (from a real trading habit the user described): before trusting
a Buy/Sell signal on an individual stock, check whether the index itself
is moving the same direction, and whether the stock's own day is green/red
in line with it. E.g. "only take a Buy signal if Nifty50 is up today AND
the stock itself is up today" -- a basic top-down alignment check that
filters out stocks moving opposite the broader tape.

Deliberately kept as an INFO layer, not a hard filter (explicit user
choice): these values are surfaced as a metric + a column for the user to
judge visually, never used to hide rows.

"Up/down" is measured as % change from the day's first candle Open to the
latest available Close within that same day (also the user's explicit
choice over an EMA-based measure).
"""

import datetime as dt

import pandas as pd


def day_change_pct(df: pd.DataFrame, target_date: dt.date = None):
    """
    % change from `target_date`'s first candle Open to that day's LAST
    available candle Close within df. If target_date is None, uses the
    most recent calendar date present in df (i.e. "today so far" for a
    live intraday fetch).

    Returns None if there's no data for that date or the open is zero.
    """
    if df is None or df.empty:
        return None

    idx_dates = df.index.date
    if target_date is None:
        target_date = idx_dates.max()

    day_rows = df[idx_dates == target_date]
    if day_rows.empty:
        return None

    day_open = float(day_rows["Open"].iloc[0])
    last_close = float(day_rows["Close"].iloc[-1])
    if day_open == 0:
        return None

    return round((last_close - day_open) / day_open * 100, 2)


def day_change_pct_asof(df: pd.DataFrame, cutoff_date: dt.date, cutoff_time: dt.time):
    """
    Same idea as day_change_pct, but restricted to candles at/before
    cutoff_time on cutoff_date -- i.e. "how was the index/stock doing AT
    the cutoff moment", for the Backtest tab. Deliberately does NOT peek
    at candles after the cutoff (same as the rest of the backtest logic).

    Returns None if there's no data at/before the cutoff on that date.
    """
    if df is None or df.empty:
        return None

    idx_dates = df.index.date
    idx_times = df.index.time
    day_rows = df[(idx_dates == cutoff_date) & (idx_times <= cutoff_time)]
    if day_rows.empty:
        return None

    day_open = float(day_rows["Open"].iloc[0])
    price_at_cutoff = float(day_rows["Close"].iloc[-1])
    if day_open == 0:
        return None

    return round((price_at_cutoff - day_open) / day_open * 100, 2)
