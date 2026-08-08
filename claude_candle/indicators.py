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


def smooth_session_edges(df: pd.DataFrame, n: int = 3) -> pd.DataFrame:
    """Intraday-only preprocessing targeted at the day transition, not every day in the
    lookback history: the CLOSE of the most recent complete trading day (its last n
    candles) and the OPEN of the current/latest day (its first n candles) are each
    aggregated into a single representative candle — that's the noisy overnight-gap zone
    (closing-settlement flurry into opening-auction volatility) that throws off pattern
    detection right at the start of a new session. Every earlier day is left completely
    untouched (it's only present for indicator warm-up, so there's no need to alter it).

    No information is discarded — open/high/low/close/volume all roll up correctly within
    each aggregated candle, it's just grouped.
    """
    if df.empty or len(df) <= n:
        return df

    work = df.copy()
    naive_idx = work.index.tz_localize(None) if work.index.tz is not None else work.index
    work["_date"] = naive_idx.date
    unique_dates = sorted(work["_date"].unique())
    last_date = unique_dates[-1]
    prev_date = unique_dates[-2] if len(unique_dates) >= 2 else None

    def _agg(chunk: pd.DataFrame, ts) -> pd.DataFrame:
        return pd.DataFrame({
            "open": [chunk["open"].iloc[0]],
            "high": [chunk["high"].max()],
            "low": [chunk["low"].min()],
            "close": [chunk["close"].iloc[-1]],
            "volume": [chunk["volume"].sum()],
        }, index=[ts])

    out_frames = []
    for d in unique_dates:
        day_df = work[work["_date"] == d].drop(columns="_date")

        if d == last_date and len(day_df) > n:
            # current/latest day (may still be in progress) — smooth only the open;
            # its close hasn't happened yet, so there's nothing to average there
            first_chunk = day_df.iloc[:n]
            first_agg = _agg(first_chunk, first_chunk.index[0])
            rest = day_df.iloc[n:]
            out_frames.append(pd.concat([first_agg, rest]))

        elif d == prev_date and len(day_df) > n:
            # the trading day immediately before "today" — smooth only its close,
            # since that's the half of the transition that pairs with today's open
            rest = day_df.iloc[:-n]
            last_chunk = day_df.iloc[-n:]
            last_agg = _agg(last_chunk, last_chunk.index[-1])
            out_frames.append(pd.concat([rest, last_agg]))

        else:
            # earlier history — left raw, only used for indicator warm-up
            out_frames.append(day_df)

    result = pd.concat(out_frames).sort_index()
    return result[["open", "high", "low", "close", "volume"]]
