"""
candlestick_patterns.py
------------------------
Lightweight, dependency-free candlestick pattern detector.

Deliberately avoids TA-Lib (a C library that's a pain to install on
Windows) and instead implements the handful of patterns that matter most
for intraday/day-trading decisions, using plain OHLC math.

Each detector looks at the most recent candle (and sometimes the one or two
before it) and returns a signal in the range [-1, +1]:
    +1  => strongly bullish pattern
     0  => neutral / indecision
    -1  => strongly bearish pattern

detect_patterns() aggregates all of them and returns:
    {
        "patterns": ["Bullish Engulfing", "Hammer", ...],   # names detected
        "signal": float in [-1, 1],                          # averaged signal
    }
"""

import pandas as pd
import numpy as np


def _body(o, c):
    return abs(c - o)


def _range(h, l):
    return max(h - l, 1e-9)


def _is_bullish(o, c):
    return c > o


def _upper_wick(o, h, c):
    return h - max(o, c)


def _lower_wick(o, l, c):
    return min(o, c) - l


def detect_doji(df, i, body_ratio=0.1):
    o, h, l, c = df.iloc[i][["Open", "High", "Low", "Close"]]
    rng = _range(h, l)
    if _body(o, c) / rng <= body_ratio:
        return "Doji", 0.0
    return None, 0.0


def detect_hammer(df, i, lower_wick_mult=2.0, upper_wick_max=0.3):
    o, h, l, c = df.iloc[i][["Open", "High", "Low", "Close"]]
    rng = _range(h, l)
    body = _body(o, c)
    lower = _lower_wick(o, l, c)
    upper = _upper_wick(o, h, c)
    if body / rng < 0.35 and lower >= lower_wick_mult * body and upper <= upper_wick_max * rng:
        # Bullish if it appears after a down move
        if i >= 3 and df.iloc[i - 3:i]["Close"].is_monotonic_decreasing:
            return "Hammer", 0.6
        return "Hammer", 0.4
    return None, 0.0


def detect_shooting_star(df, i, upper_wick_mult=2.0, lower_wick_max=0.3):
    o, h, l, c = df.iloc[i][["Open", "High", "Low", "Close"]]
    rng = _range(h, l)
    body = _body(o, c)
    lower = _lower_wick(o, l, c)
    upper = _upper_wick(o, h, c)
    if body / rng < 0.35 and upper >= upper_wick_mult * body and lower <= lower_wick_max * rng:
        if i >= 3 and df.iloc[i - 3:i]["Close"].is_monotonic_increasing:
            return "Shooting Star", -0.6
        return "Shooting Star", -0.4
    return None, 0.0


def detect_engulfing(df, i):
    if i < 1:
        return None, 0.0
    o1, c1 = df.iloc[i - 1][["Open", "Close"]]
    o2, c2 = df.iloc[i][["Open", "Close"]]
    prev_bear = c1 < o1
    curr_bull = c2 > o2
    prev_bull = c1 > o1
    curr_bear = c2 < o2

    if prev_bear and curr_bull and o2 <= c1 and c2 >= o1:
        return "Bullish Engulfing", 0.7
    if prev_bull and curr_bear and o2 >= c1 and c2 <= o1:
        return "Bearish Engulfing", -0.7
    return None, 0.0


def detect_morning_evening_star(df, i):
    if i < 2:
        return None, 0.0
    o1, c1 = df.iloc[i - 2][["Open", "Close"]]
    o2, h2, l2, c2 = df.iloc[i - 1][["Open", "High", "Low", "Close"]]
    o3, c3 = df.iloc[i][["Open", "Close"]]

    body1 = _body(o1, c1)
    body2 = _body(o2, c2)
    body3 = _body(o3, c3)

    # Morning star: big bearish candle, small-body candle, big bullish candle closing above midpoint of candle 1
    if c1 < o1 and body2 < body1 * 0.5 and c3 > o3 and c3 > (o1 + c1) / 2:
        return "Morning Star", 0.75
    # Evening star: big bullish candle, small-body candle, big bearish candle closing below midpoint of candle 1
    if c1 > o1 and body2 < body1 * 0.5 and c3 < o3 and c3 < (o1 + c1) / 2:
        return "Evening Star", -0.75
    return None, 0.0


def detect_marubozu(df, i, wick_max=0.05):
    o, h, l, c = df.iloc[i][["Open", "High", "Low", "Close"]]
    rng = _range(h, l)
    upper = _upper_wick(o, h, c)
    lower = _lower_wick(o, l, c)
    if upper / rng <= wick_max and lower / rng <= wick_max:
        if c > o:
            return "Bullish Marubozu", 0.5
        else:
            return "Bearish Marubozu", -0.5
    return None, 0.0


PATTERN_FUNCS = [
    detect_engulfing,
    detect_morning_evening_star,
    detect_hammer,
    detect_shooting_star,
    detect_marubozu,
    detect_doji,
]


def detect_patterns(df: pd.DataFrame, lookback_index: int = -1):
    """
    Run all pattern detectors against the candle at `lookback_index`
    (default: most recent closed candle, i.e. -1 / last row).

    Returns dict: {"patterns": [...], "signal": float}
    """
    if df is None or len(df) < 4:
        return {"patterns": [], "signal": 0.0}

    i = lookback_index if lookback_index >= 0 else len(df) + lookback_index

    names = []
    values = []
    for fn in PATTERN_FUNCS:
        try:
            name, val = fn(df, i)
        except Exception:
            name, val = None, 0.0
        if name:
            names.append(name)
            values.append(val)

    signal = float(np.mean(values)) if values else 0.0
    return {"patterns": names, "signal": signal}
