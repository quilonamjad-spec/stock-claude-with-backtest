"""
data_fetcher.py
Pulls OHLCV data from Yahoo Finance via yfinance.
"""
import pandas as pd
import yfinance as yf
import streamlit as st


@st.cache_data(ttl=900, show_spinner=False)  # cache 15 min so a watchlist scan doesn't re-hit the API constantly
def fetch_ohlcv(ticker: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
    df = yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=True)
    if df is None or df.empty:
        return pd.DataFrame()

    # yfinance sometimes returns a MultiIndex column set for single tickers
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]

    df.columns = [str(c).lower() for c in df.columns]
    keep = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
    df = df[keep].dropna()
    return df
