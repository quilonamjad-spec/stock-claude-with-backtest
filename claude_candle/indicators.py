"""
indicators.py
Adds the contextual technical indicators that make candlestick pattern
scoring reliable: trend (moving averages), momentum (RSI, MACD),
volatility (ATR, Bollinger Bands), volume, and support/resistance.
"""
import pandas as pd
import numpy as np


def add_moving_averages(df: pd.DataFrame, windows=(20, 50, 200)) -> pd.DataFrame:
    for w in windows:
        df[f"sma_{w}"] = df["close"].rolling(w).mean()
        df[f"ema_{w}"] = df["close"].ewm(span=w, adjust=False).mean()
    return df


def add_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))
    df["rsi"] = df["rsi"].fillna(50)
    return df


def add_macd(df: pd.DataFrame, fast=12, slow=26, signal=9) -> pd.DataFrame:
    ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
    ema_slow = df["close"].ewm(span=slow, adjust=False).mean()
    df["macd"] = ema_fast - ema_slow
    df["macd_signal"] = df["macd"].ewm(span=signal, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]
    return df


def add_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df["atr"] = tr.rolling(period).mean()
    return df


def add_bollinger(df: pd.DataFrame, period=20, num_std=2) -> pd.DataFrame:
    mid = df["close"].rolling(period).mean()
    std = df["close"].rolling(period).std()
    df["bb_mid"] = mid
    df["bb_upper"] = mid + num_std * std
    df["bb_lower"] = mid - num_std * std
    return df


def add_volume_avg(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    df["vol_avg_20"] = df["volume"].rolling(period).mean()
    df["vol_ratio"] = df["volume"] / df["vol_avg_20"]
    return df


def add_support_resistance(df: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    df["resistance_20"] = df["high"].rolling(window).max()
    df["support_20"] = df["low"].rolling(window).min()
    return df


def compute_trend(df: pd.DataFrame, short: int = 20, long: int = 50) -> pd.DataFrame:
    """Simple trend classification from EMA relationship: uptrend / downtrend / range."""
    if f"ema_{short}" not in df.columns or f"ema_{long}" not in df.columns:
        df = add_moving_averages(df, (short, long))
    s = df[f"ema_{short}"]
    l = df[f"ema_{long}"]
    trend = pd.Series("range", index=df.index)
    trend[s > l * 1.01] = "uptrend"
    trend[s < l * 0.99] = "downtrend"
    trend[s.isna() | l.isna()] = "unknown"
    df["trend"] = trend
    return df


def add_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Run the full indicator pipeline. Expects columns: open, high, low, close, volume."""
    df = df.copy()
    df = add_moving_averages(df)
    df = add_rsi(df)
    df = add_macd(df)
    df = add_atr(df)
    df = add_bollinger(df)
    df = add_volume_avg(df)
    df = add_support_resistance(df)
    df = compute_trend(df)
    return df
