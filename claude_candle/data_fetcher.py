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
                return df[keep].dropna()
            last_err = "No data returned (empty response)."
        except Exception as e:
            last_err = str(e)

        # exponential backoff with jitter before retrying — rate limit errors need real wait time
        if attempt < max_retries - 1:
            time.sleep((2 ** attempt) + random.uniform(0.5, 1.5))

    raise RuntimeError(f"Failed to fetch {ticker} after {max_retries} attempts: {last_err}")
