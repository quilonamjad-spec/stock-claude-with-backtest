"""
indicators.py
-------------
Pure pandas/numpy implementations of the technical indicators used by the
scanner. No TA-Lib dependency (avoids the C-library install headache).

Every `compute_*` function takes an OHLCV DataFrame (columns: Open, High,
Low, Close, Volume) and returns the DataFrame with new indicator columns
appended.
"""

import numpy as np
import pandas as pd


def compute_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    delta = df["Close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    df["RSI"] = rsi.fillna(50)
    return df


def compute_macd(df: pd.DataFrame, fast=12, slow=26, signal=9) -> pd.DataFrame:
    ema_fast = df["Close"].ewm(span=fast, adjust=False).mean()
    ema_slow = df["Close"].ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line

    df["MACD"] = macd_line
    df["MACD_SIGNAL"] = signal_line
    df["MACD_HIST"] = hist
    return df


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    return atr


def compute_adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    high, low, close = df["High"], df["Low"], df["Close"]

    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    atr = compute_atr(df, period).replace(0, np.nan)

    plus_dm_s = pd.Series(plus_dm, index=df.index).ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    minus_dm_s = pd.Series(minus_dm, index=df.index).ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    plus_di = 100 * (plus_dm_s / atr)
    minus_di = 100 * (minus_dm_s / atr)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    df["PLUS_DI"] = plus_di.fillna(0)
    df["MINUS_DI"] = minus_di.fillna(0)
    df["ADX"] = adx.fillna(0)
    return df


def compute_bollinger(df: pd.DataFrame, period: int = 20, num_std: float = 2.0) -> pd.DataFrame:
    mid = df["Close"].rolling(period).mean()
    std = df["Close"].rolling(period).std()
    df["BB_MID"] = mid
    df["BB_UPPER"] = mid + num_std * std
    df["BB_LOWER"] = mid - num_std * std
    rng = (df["BB_UPPER"] - df["BB_LOWER"]).replace(0, np.nan)
    df["BB_POSITION"] = ((df["Close"] - df["BB_LOWER"]) / rng).clip(0, 1).fillna(0.5)
    return df


def compute_ema_trend(df: pd.DataFrame, fast=20, slow=50) -> pd.DataFrame:
    df[f"EMA{fast}"] = df["Close"].ewm(span=fast, adjust=False).mean()
    df[f"EMA{slow}"] = df["Close"].ewm(span=slow, adjust=False).mean()
    return df


def compute_volume_ratio(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    avg_vol = df["Volume"].rolling(period).mean().replace(0, np.nan)
    df["VOL_RATIO"] = (df["Volume"] / avg_vol).fillna(1.0)
    return df


def compute_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Run the full indicator stack on a copy of df and return it."""
    df = df.copy()
    df = compute_rsi(df)
    df = compute_macd(df)
    df["ATR"] = compute_atr(df, period=14)
    df = compute_adx(df)
    df = compute_bollinger(df)
    df = compute_ema_trend(df)
    df = compute_volume_ratio(df)
    return df
