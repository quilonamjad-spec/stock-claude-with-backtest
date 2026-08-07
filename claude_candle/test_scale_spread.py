"""Sanity check: different candle scenarios should land at different points
on the 0-100 scale, not all cluster at one spot."""
import numpy as np
import pandas as pd
from indicators import add_all_indicators
from scoring import score_latest

np.random.seed(1)


def flat_series(n, start=100, noise=0.15):
    dates = pd.date_range("2026-01-01", periods=n, freq="B")
    close = start + np.cumsum(np.random.uniform(-noise, noise, n))
    open_ = close + np.random.uniform(-0.1, 0.1, n)
    high = np.maximum(open_, close) + np.random.uniform(0.05, 0.15, n)
    low = np.minimum(open_, close) - np.random.uniform(0.05, 0.15, n)
    vol = np.full(n, 1_000_000.0)
    return dates, open_, high, low, close, vol


def scenario_no_pattern():
    dates, o, h, l, c, v = flat_series(60)
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": v}, index=dates)


def scenario_weak_doji_no_trend():
    df = scenario_no_pattern()
    i = -1
    c = df["close"].iloc[i]
    df.iloc[i, df.columns.get_loc("open")] = c
    df.iloc[i, df.columns.get_loc("high")] = c + 0.05
    df.iloc[i, df.columns.get_loc("low")] = c - 0.05
    return df


def scenario_strong_three_black_crows_uptrend():
    dates = pd.date_range("2026-01-01", periods=60, freq="B")
    close = 100 + np.cumsum(np.random.uniform(0.1, 0.5, 60))  # uptrend
    open_ = close - np.random.uniform(-0.2, 0.2, 60)
    high = np.maximum(open_, close) + 0.1
    low = np.minimum(open_, close) - 0.1
    vol = np.full(60, 1_000_000.0)

    # last 3 candles: three black crows, each closing lower, with a volume spike
    base = close[-4]
    for k, offset in enumerate([1, 2, 3]):
        o = base - (offset - 1) * 0.8
        c = base - offset * 1.0
        open_[-4 + offset], close[-4 + offset] = o, c
        high[-4 + offset] = max(o, c) + 0.05
        low[-4 + offset] = min(o, c) - 0.05
    vol[-1] = vol[-2] * 2.0

    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": vol}, index=dates)


scenarios = {
    "No pattern (flat/random)": scenario_no_pattern(),
    "Weak doji, no trend context": scenario_weak_doji_no_trend(),
    "Three Black Crows after uptrend + volume": scenario_strong_three_black_crows_uptrend(),
}

print(f"{'Scenario':45s} {'Score':>7s}  Verdict")
print("-" * 75)
for name, df in scenarios.items():
    df_ind = add_all_indicators(df)
    r = score_latest(df_ind, "TEST")
    print(f"{name:45s} {r.score:7.1f}  {r.verdict}")

print("\nExpectation: scores should spread out (not all ~50), with the crows")
print("scenario landing well below 50 (bearish) and the others near/at 50.")
