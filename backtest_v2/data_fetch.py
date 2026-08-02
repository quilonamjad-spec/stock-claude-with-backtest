"""
data_fetch.py
-------------
Two jobs:

1. get_nifty500_list() - fetch the current Nifty500 constituent list from
   NSE's public archive CSV. NSE blocks requests without a browser-like
   User-Agent, so we set one. If the live fetch fails (NSE rate-limits /
   blocks a lot of cloud IPs), we fall back to a bundled CSV
   (data/nifty500_fallback.csv) so the app still works -- just flagged as
   possibly stale.

2. fetch_ohlcv() / fetch_batch() - pull OHLCV candles from Yahoo Finance
   via yfinance, in batches (Yahoo/yfinance will choke or rate-limit if
   you hit it with 500 individual single-ticker calls).

Note: Yahoo Finance data for NSE symbols is typically end-of-day accurate
but intraday candles can lag real-time by a few minutes and are not a
substitute for your broker/exchange terminal for actual execution.
"""

import io
import os
import time
import pandas as pd
import requests
import yfinance as yf

NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/csv,application/csv,*/*",
}

NIFTY500_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
FALLBACK_CSV = os.path.join(os.path.dirname(__file__), "data", "nifty500_fallback.csv")


def get_nifty500_list(timeout: int = 10):
    """
    Returns (df, source_label) where df has columns:
        Company Name, Industry, Symbol, Series, ISIN Code
    source_label is "live" or "fallback" so the UI can warn the user.
    """
    try:
        session = requests.Session()
        # NSE sometimes wants a warm-up hit to the homepage to set cookies
        session.get("https://www.nseindia.com", headers=NSE_HEADERS, timeout=timeout)
        resp = session.get(NIFTY500_URL, headers=NSE_HEADERS, timeout=timeout)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text))
        if "Symbol" in df.columns and len(df) > 100:
            return df, "live"
    except Exception:
        pass

    # Fallback to bundled (possibly stale) list
    if os.path.exists(FALLBACK_CSV):
        df = pd.read_csv(FALLBACK_CSV)
        return df, "fallback"

    raise RuntimeError(
        "Could not fetch the Nifty500 list live from NSE, and no fallback "
        "CSV was found at data/nifty500_fallback.csv."
    )


def to_yf_symbol(nse_symbol: str) -> str:
    """NSE symbols need a .NS suffix for Yahoo Finance."""
    return f"{nse_symbol.strip()}.NS"


def fetch_batch(symbols, interval="15m", period="5d", pause=1.0, batch_size=50):
    """
    Download OHLCV for a list of NSE symbols (without .NS suffix) in
    batches using yfinance's multi-ticker download (much faster and far
    less likely to be rate-limited than looping single downloads).

    interval: one of yfinance's supported intervals, e.g. "5m","15m","1h","1d"
    period: how much history to pull. yfinance limits intraday history:
        1m -> 7d max, 5m/15m/30m/1h -> 60d max, 1d -> years

    Returns dict: {symbol: DataFrame} for symbols that returned data.
    """
    results = {}
    yf_symbols = [to_yf_symbol(s) for s in symbols]

    for i in range(0, len(yf_symbols), batch_size):
        batch = yf_symbols[i:i + batch_size]
        try:
            data = yf.download(
                tickers=" ".join(batch),
                interval=interval,
                period=period,
                group_by="ticker",
                threads=True,
                progress=False,
                auto_adjust=False,
            )
        except Exception as e:
            print(f"Batch download failed for batch starting at {i}: {e}")
            continue

        for yf_sym, orig_sym in zip(batch, symbols[i:i + batch_size]):
            try:
                if len(batch) == 1:
                    df = data
                else:
                    df = data[yf_sym]
                df = df.dropna(how="all")
                if not df.empty:
                    results[orig_sym] = df
            except Exception:
                continue

        if i + batch_size < len(yf_symbols):
            time.sleep(pause)  # be polite between batches

    return results


def fetch_single(symbol, interval="15m", period="5d"):
    """Fetch OHLCV for a single NSE symbol (without .NS suffix)."""
    yf_sym = to_yf_symbol(symbol)
    df = yf.Ticker(yf_sym).history(period=period, interval=interval, auto_adjust=False)
    return df.dropna(how="all")


def fetch_index(index_symbol="^NSEI", interval="5m", period="5d"):
    """
    Fetch OHLCV for a market index (default: Nifty 50, ^NSEI). Indexes use
    their raw Yahoo ticker directly -- NOT run through to_yf_symbol()/the
    .NS suffix, which is only correct for individual NSE equity symbols.
    """
    df = yf.Ticker(index_symbol).history(period=period, interval=interval, auto_adjust=False)
    return df.dropna(how="all")
