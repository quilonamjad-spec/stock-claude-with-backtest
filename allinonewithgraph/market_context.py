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


# ---------------------------------------------------------- sector indices

NIFTY_50_INDEX = "^NSEI"

# Maps the NSE "Industry" label (exactly as it appears in the Nifty500
# constituent CSV -- ind_nifty500list.csv / data/nifty500_fallback.csv) to
# the closest official Nifty sectoral index, using ONLY tickers confirmed
# to exist on Yahoo Finance (verified Aug 2026). Industries without a
# confirmed dedicated sectoral index fall back to Nifty 50 rather than
# guessing a ticker that might not resolve.
SECTOR_INDEX_MAP = {
    "Information Technology": "^CNXIT",
    "Healthcare": "^CNXPHARMA",                  # closest available proxy - Nifty500 Healthcare is pharma-heavy
    "Automobile and Auto Components": "^CNXAUTO",
    "Fast Moving Consumer Goods": "^CNXFMCG",
    "Metals & Mining": "^CNXMETAL",
    "Oil Gas & Consumable Fuels": "^CNXENERGY",
    "Energy": "^CNXENERGY",
    "Realty": "^CNXREALTY",
    "Financial Services": "NIFTY_FIN_SERVICE.NS",
    "Construction": "^CNXINFRA",                 # approximate proxy, not an exact sector match
    "Construction Materials": "^CNXINFRA",        # approximate proxy, not an exact sector match
    # Deliberately NOT mapped (no confirmed dedicated Yahoo-tradable
    # sectoral index as of this writing) -- falls back to Nifty 50:
    #   Capital Goods, Consumer Durables, Power, Chemicals,
    #   Consumer Services, Telecommunication, Services, Textiles,
    #   Diversified
}

INDEX_DISPLAY_NAME = {
    "^NSEI": "Nifty 50",
    "^CNXIT": "Nifty IT",
    "^CNXPHARMA": "Nifty Pharma",
    "^CNXAUTO": "Nifty Auto",
    "^CNXFMCG": "Nifty FMCG",
    "^CNXMETAL": "Nifty Metal",
    "^CNXENERGY": "Nifty Energy",
    "^CNXREALTY": "Nifty Realty",
    "NIFTY_FIN_SERVICE.NS": "Nifty Fin Service",
    "^CNXINFRA": "Nifty Infra",
}


def index_for_industry(industry) -> str:
    """
    Best-effort map from an NSE 'Industry' label to a sectoral index
    ticker. Falls back to Nifty 50 for unmapped industries, missing data,
    or symbols with no known industry (e.g. a custom-pasted symbol not in
    the Nifty500 list), rather than guessing.
    """
    if not industry or (isinstance(industry, float) and pd.isna(industry)):
        return NIFTY_50_INDEX
    return SECTOR_INDEX_MAP.get(str(industry).strip(), NIFTY_50_INDEX)


def index_display_name(index_ticker: str) -> str:
    return INDEX_DISPLAY_NAME.get(index_ticker, index_ticker)


def alignment_label(signal_label: str, stock_chg, ref_chg):
    """
    The user's manual top-down check, formalized: "index/sector is up AND
    the stock itself is up -> trust a Buy signal; both down -> trust a
    Sell signal." Mirrors what they used to eyeball by hand.

    Returns True (aligned), False (not aligned), or None (Neutral signal,
    or missing data -- caller should show this as a neutral dash, not a
    red flag).
    """
    if stock_chg is None or ref_chg is None:
        return None
    if signal_label in ("Buy", "Strong Buy"):
        return stock_chg > 0 and ref_chg > 0
    if signal_label in ("Sell", "Strong Sell"):
        return stock_chg < 0 and ref_chg < 0
    return None
