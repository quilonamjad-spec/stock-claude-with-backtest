"""
indicators.py
-------------
Technical indicator math and the Bull/Bear condition evaluation. Pure
pandas/numpy — no Streamlit import, no network calls — so this can be
unit-tested with a plain OHLCV DataFrame without touching the rest of
the app.
"""

import numpy as np
import pandas as pd


def compute_vwap(df: pd.DataFrame) -> pd.Series:
    d = df.copy()
    day = d.index.date
    typical = (d["High"] + d["Low"] + d["Close"]) / 3
    pv = typical * d["Volume"]
    cum_pv = pv.groupby(day).cumsum()
    cum_vol = d["Volume"].groupby(day).cumsum()
    return cum_pv / cum_vol.replace(0, np.nan)


def compute_cmf(df: pd.DataFrame, period: int = 20) -> pd.Series:
    rng = (df["High"] - df["Low"]).replace(0, np.nan)
    mfm = ((df["Close"] - df["Low"]) - (df["High"] - df["Close"])) / rng
    mfv = mfm * df["Volume"]
    return mfv.rolling(period).sum() / df["Volume"].rolling(period).sum()


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["Close"].shift(1)
    tr = pd.concat(
        [
            df["High"] - df["Low"],
            (df["High"] - prev_close).abs(),
            (df["Low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean()


def compute_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    delta = df["Close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def compute_consecutive_bars(df: pd.DataFrame) -> pd.Series:
    """Signed count of consecutive same-direction closes: +3 means 3 straight
    up-closes, -5 means 5 straight down-closes. Used to catch blow-off runs."""
    direction = np.sign(df["Close"].diff()).fillna(0)
    group = (direction != direction.shift()).cumsum()
    return direction * direction.groupby(group).cumcount().add(1)


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["VWAP"] = compute_vwap(d)
    d["SMA_Vol20"] = d["Volume"].rolling(20).mean()
    d["EMA9"] = d["Close"].ewm(span=9, adjust=False).mean()
    d["EMA21"] = d["Close"].ewm(span=21, adjust=False).mean()
    d["High10"] = d["High"].shift(1).rolling(10).max()
    d["Low10"] = d["Low"].shift(1).rolling(10).min()
    d["CMF20"] = compute_cmf(d, 20)
    d["ATR14"] = compute_atr(d, 14)
    d["ATR14_avg20"] = d["ATR14"].rolling(20).mean()
    d["RSI14"] = compute_rsi(d, 14)
    d["EMA21_slope"] = d["EMA21"] - d["EMA21"].shift(5)
    d["Extension_ATR"] = (d["Close"] - d["EMA9"]) / d["ATR14"]
    d["Consecutive_bars"] = compute_consecutive_bars(d)
    return d


def evaluate_row(row: pd.Series):
    bull = (
        row["Close"] > row["VWAP"]
        and row["Volume"] > row["SMA_Vol20"]
        and row["EMA9"] > row["EMA21"]
        and row["Close"] > row["High10"]
        and row["CMF20"] > 0
    )
    bear = (
        row["Close"] < row["VWAP"]
        and row["Volume"] > row["SMA_Vol20"]
        and row["EMA9"] < row["EMA21"]
        and row["Close"] < row["Low10"]
        and row["CMF20"] < 0
    )
    if bull:
        return "Bull"
    if bear:
        return "Bear"
    return None


def build_result(symbol: str, df: pd.DataFrame, as_of):
    """Evaluate conditions on the last bar at/ before `as_of`. Returns a
    dict of raw + strength metrics, or None if no condition set passed."""
    data = df[df.index <= as_of]
    if len(data) < 25:  # need enough bars for the 20/21-period indicators
        return None

    data = compute_indicators(data)
    last = data.iloc[-1]
    required = [
        "VWAP", "SMA_Vol20", "EMA9", "EMA21", "High10", "Low10", "CMF20",
        "ATR14", "ATR14_avg20", "RSI14", "EMA21_slope", "Extension_ATR",
    ]
    if last[required].isna().any():
        return None

    phase = evaluate_row(last)
    if phase is None:
        return None

    same_day = data[data.index.date == last.name.date()]
    day_open = same_day["Open"].iloc[0]
    pct_change = (last["Close"] - day_open) / day_open * 100

    vwap_strength = abs((last["Close"] - last["VWAP"]) / last["VWAP"] * 100)
    volume_strength = last["Volume"] / last["SMA_Vol20"] if last["SMA_Vol20"] else np.nan
    trend_strength = abs((last["EMA9"] - last["EMA21"]) / last["EMA21"] * 100)
    if phase == "Bull":
        breakout_strength = (last["Close"] - last["High10"]) / last["High10"] * 100
    else:
        breakout_strength = (last["Low10"] - last["Close"]) / last["Low10"] * 100
    moneyflow_strength = abs(last["CMF20"])
    # Volatility expansion: current ATR vs its own 20-bar average. >1 means
    # volatility is expanding (favorable for breakout follow-through).
    volatility_strength = (
        last["ATR14"] / last["ATR14_avg20"] if last["ATR14_avg20"] else np.nan
    )

    return {
        "Symbol": symbol.replace(".NS", ""),
        "Phase": phase,
        "% Change": round(pct_change, 2),
        "LTP": round(last["Close"], 2),
        "Bar Time": last.name.strftime("%Y-%m-%d %H:%M"),
        "Trend_strength": trend_strength,
        "Breakout_strength": max(breakout_strength, 0),
        "Volatility_strength": volatility_strength,
        "Volume_strength": volume_strength,
        "MoneyFlow_strength": moneyflow_strength,
        "VWAP_strength": vwap_strength,
        # Quality-gate inputs (not part of the rank score itself)
        "RSI14": round(last["RSI14"], 1),
        "EMA21_slope": last["EMA21_slope"],
        "Extension_ATR": last["Extension_ATR"],
        "Consecutive_bars": int(last["Consecutive_bars"]),
    }
