"""
data.py
-------
Everything that talks to Yahoo Finance or NSE's archive: the NSE 500
universe list, sector-index constituent mapping, batched OHLCV fetches,
and sector-index price changes. This is the one file that matters most
if/when Yahoo gets swapped for Kite's data feed later — that change
should only need to touch this file.
"""

import io

import pandas as pd
import requests
import streamlit as st
import yfinance as yf

from config import (
    NSE500_URL, LOCAL_FALLBACK, IST_TZ, SECTOR_INDICES, SECTOR_PRIORITY,
    DEFAULT_SECTOR, DEFAULT_INDEX_YAHOO,
)


# --------------------------------------------------------------------------
# UNIVERSE
# --------------------------------------------------------------------------
@st.cache_data(ttl=60 * 60 * 24)
def get_nse500_symbols() -> list[str]:
    """Fetch the current NSE 500 constituent list. Falls back to a local
    CSV (columns must include 'Symbol') if NSE's archive is unreachable."""
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(NSE500_URL, headers=headers, timeout=10)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text))
        symbols = df["Symbol"].astype(str).str.strip().tolist()
    except Exception:
        try:
            df = pd.read_csv(LOCAL_FALLBACK)
            symbols = df["Symbol"].astype(str).str.strip().tolist()
        except Exception:
            st.error(
                "Could not fetch the NSE 500 list from NSE's archive, and no "
                f"local fallback ('{LOCAL_FALLBACK}') was found. Add one to "
                "the repo root with a 'Symbol' column."
            )
            return []
    return [f"{s}.NS" for s in symbols]


# --------------------------------------------------------------------------
# SECTOR MAPPING & INDEX PRICES
# --------------------------------------------------------------------------
@st.cache_data(ttl=60 * 60 * 24)
def get_symbol_sector_map() -> dict:
    """symbol -> sector key (e.g. 'RELIANCE' -> 'ENERGY'). Stocks not found
    in any sectoral list fall back to NIFTY 50 as the comparison index —
    sectoral lists that fail to fetch are just skipped, not fatal."""
    mapping: dict = {}
    headers = {"User-Agent": "Mozilla/5.0"}
    for sector in SECTOR_PRIORITY:
        try:
            resp = requests.get(SECTOR_INDICES[sector]["csv"], headers=headers, timeout=10)
            resp.raise_for_status()
            df = pd.read_csv(io.StringIO(resp.text))
            for sym in df["Symbol"].astype(str).str.strip():
                mapping.setdefault(sym, sector)
        except Exception:
            continue
    return mapping


@st.cache_data(ttl=60 * 10, show_spinner=False)
def fetch_index_batch(period: str, interval: str) -> pd.DataFrame:
    tickers = [cfg["yahoo"] for cfg in SECTOR_INDICES.values()] + [DEFAULT_INDEX_YAHOO]
    return yf.download(
        tickers=tickers, period=period, interval=interval,
        group_by="ticker", threads=True, progress=False, auto_adjust=False,
    )


def compute_index_pct_changes(as_of, lookback_days: int) -> dict:
    """Returns {sector_key: pct_change_from_day_open} for every sector index
    plus 'NIFTY50', evaluated at the same as_of timestamp as the stock scan."""
    yahoo_to_sector = {cfg["yahoo"]: name for name, cfg in SECTOR_INDICES.items()}
    yahoo_to_sector[DEFAULT_INDEX_YAHOO] = DEFAULT_SECTOR
    tickers = list(yahoo_to_sector.keys())

    changes = {}
    try:
        batch = fetch_index_batch(period=f"{lookback_days}d", interval="5m")
    except Exception:
        return changes

    for tkr in tickers:
        try:
            idf = batch[tkr].dropna(how="all") if len(tickers) > 1 else batch
            if idf.empty:
                continue
            if idf.index.tz is None:
                idf.index = idf.index.tz_localize(IST_TZ)
            else:
                idf.index = idf.index.tz_convert(IST_TZ)
            data = idf[idf.index <= as_of]
            if data.empty:
                continue
            last = data.iloc[-1]
            same_day = data[data.index.date == last.name.date()]
            day_open = same_day["Open"].iloc[0]
            pct = round((last["Close"] - day_open) / day_open * 100, 2)
            changes[yahoo_to_sector[tkr]] = pct
        except Exception:
            continue
    return changes


# --------------------------------------------------------------------------
# STOCK OHLCV FETCH (batched, cached per session)
# --------------------------------------------------------------------------
@st.cache_data(ttl=60 * 10, show_spinner=False)
def fetch_batch(tickers: tuple, period: str, interval: str) -> pd.DataFrame:
    """Batch download via yfinance. Returns a MultiIndex-columned DataFrame
    (level 0 = ticker) so we only hit Yahoo once per batch instead of once
    per stock."""
    return yf.download(
        tickers=list(tickers),
        period=period,
        interval=interval,
        group_by="ticker",
        threads=True,
        progress=False,
        auto_adjust=False,
    )


def chunk(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i : i + size]


def fetch_symbol_5m_since(symbol_ns: str, start_time, lookback_days: int = 10) -> pd.DataFrame:
    """Single-ticker fetch, IST-normalized, trimmed to bars at/after
    start_time. Used by the trade simulation / backtest engine, which
    needs one specific symbol's bars rather than a whole-universe batch."""
    df = yf.download(symbol_ns, period=f"{lookback_days}d", interval="5m", progress=False, auto_adjust=False)
    if df.empty:
        return df
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    if df.index.tz is None:
        df.index = df.index.tz_localize(IST_TZ)
    else:
        df.index = df.index.tz_convert(IST_TZ)
    return df[df.index >= start_time]
