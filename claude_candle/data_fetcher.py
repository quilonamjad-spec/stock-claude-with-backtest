"""
data_fetcher.py
Pulls OHLCV data from Yahoo Finance via yfinance, with retry/backoff since
Yahoo aggressively rate-limits (YFRateLimitError) when scanning several
tickers back-to-back.
"""
import random
import time

import pandas as pd
import yfinance as yf
import streamlit as st


@st.cache_data(ttl=1800, show_spinner=False)  # cache 30 min — cuts down repeat hits during a scan
def fetch_ohlcv(ticker: str, period: str = "1mo", interval: str = "1d", max_retries: int = 4) -> pd.DataFrame:
    last_err = None
    for attempt in range(max_retries):
        try:
            df = yf.download(
                ticker, period=period, interval=interval,
                progress=False, auto_adjust=True, threads=False,
            )
            if df is not None and not df.empty:
                # yfinance sometimes returns a MultiIndex column set for single tickers
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = [c[0] for c in df.columns]
                df.columns = [str(c).lower() for c in df.columns]
                keep = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
                cleaned = df[keep].dropna()
                return trim_to_period(cleaned, period)
            last_err = "No data returned (empty response)."
        except Exception as e:
            last_err = str(e)

        # exponential backoff with jitter before retrying — rate limit errors need real wait time
        if attempt < max_retries - 1:
            time.sleep((2 ** attempt) + random.uniform(0.5, 1.5))

    raise RuntimeError(f"Failed to fetch {ticker} after {max_retries} attempts: {last_err}")


def _clean_single(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    df.columns = [str(c).lower() for c in df.columns]
    keep = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
    return df[keep].dropna()


# how many *trading days* each period should keep — used by trim_to_period below
_PERIOD_TRADING_DAYS = {"1d": 1, "5d": 5}


def trim_to_period(df: pd.DataFrame, period: str) -> pd.DataFrame:
    """Trims a DataFrame down to the most-recent N trading days that `period` implies
    (e.g. '1d' -> just the latest trading day, '5d' -> the last 5). No-op for periods
    like '1mo'/'3mo'. Used by the caller AFTER indicators have been computed on a longer
    fetch window, so short display periods don't sacrifice indicator warm-up."""
    n_days = _PERIOD_TRADING_DAYS.get(period)
    if n_days is None or df.empty:
        return df
    idx = df.index.tz_localize(None) if df.index.tz is not None else df.index
    unique_dates = sorted(set(idx.date))
    keep_dates = set(unique_dates[-n_days:])
    mask = [d in keep_dates for d in idx.date]
    return df[mask]


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_ohlcv_batch(
    tickers: tuple, period: str = "1mo", interval: str = "1d",
    chunk_size: int = 40, max_retries: int = 3,
) -> dict:
    """Fetch OHLCV for many tickers efficiently — a handful of batched calls instead of
    one call per ticker. Essential for scanning the full Nifty 500 without getting
    rate-limited. Returns {ticker: DataFrame}; a ticker with no data maps to an empty DataFrame.

    NOTE: this does NOT trim to a short display period internally — callers that want a
    short display window (e.g. today's 5m candles) should request a longer `period` here
    (so indicators have warm-up data + prior-session context) and call trim_to_period()
    themselves after computing indicators.
    """
    results: dict = {}
    tickers = list(tickers)

    for start in range(0, len(tickers), chunk_size):
        chunk = tickers[start:start + chunk_size]
        data = None
        last_err = None

        for attempt in range(max_retries):
            try:
                data = yf.download(
                    chunk, period=period, interval=interval, group_by="ticker",
                    threads=True, progress=False, auto_adjust=True,
                )
                break
            except Exception as e:
                last_err = str(e)
                if attempt < max_retries - 1:
                    time.sleep((2 ** attempt) + random.uniform(0.5, 1.5))

        if data is None:
            for t in chunk:
                results[t] = pd.DataFrame()
            continue

        for t in chunk:
            try:
                if len(chunk) == 1:
                    df_t = data  # yfinance doesn't add a ticker column level for a single-item list
                elif isinstance(data.columns, pd.MultiIndex) and t in data.columns.get_level_values(0):
                    df_t = data[t]
                else:
                    df_t = pd.DataFrame()
                results[t] = _clean_single(df_t) if not df_t.empty else pd.DataFrame()
            except Exception:
                results[t] = pd.DataFrame()

        if start + chunk_size < len(tickers):
            time.sleep(1.0)  # gap between chunks to stay under Yahoo's rate limit

    return results
