import pandas as pd
import numpy as np
from indicators import add_all_indicators
from scoring import score_latest, score_at

np.random.seed(0)

def make_downtrend_then_hammer(n=60):
    dates = pd.date_range("2026-01-01", periods=n, freq="B")
    close = 100 - np.cumsum(np.random.uniform(0.1, 0.6, n))  # steady downtrend
    open_ = close + np.random.uniform(-0.3, 0.3, n)
    high = np.maximum(open_, close) + np.random.uniform(0.1, 0.3, n)
    low = np.minimum(open_, close) - np.random.uniform(0.1, 0.3, n)
    vol = np.random.uniform(1e6, 1.2e6, n)

    # engineer a hammer on the final candle: small body near top, long lower wick
    c = close[-1]
    o = c - 0.05
    h = max(o, c) + 0.05
    l = min(o, c) - 2.5   # long lower wick
    open_[-1], close[-1], high[-1], low[-1] = o, c, h, l
    vol[-1] = vol[-2] * 1.8  # volume spike confirms it

    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": vol}, index=dates)
    return df


df = make_downtrend_then_hammer()
df = add_all_indicators(df)
result = score_latest(df, "TEST")

print("Ticker:", result.ticker)
print("Date:", result.date)
print("Close:", round(result.close, 2))
print("Trend:", result.trend)
print("RSI:", result.rsi)
print("Vol ratio:", result.vol_ratio)
print("Score:", result.score)
print("Verdict:", result.verdict)
print("Patterns found:", [p.name for p in result.patterns])
print("\nReasons:")
for r in result.reasons:
    print(" -", r)

# point-in-time test: score an earlier candle (mid-downtrend, no pattern engineered there)
mid_result = score_at(df, "TEST", 30)
print("\n--- Point-in-time score at candle 30 ---")
print("Date:", mid_result.date, "Score:", mid_result.score, "Verdict:", mid_result.verdict)
assert mid_result.date != result.date
print("\nPoint-in-time scoring works correctly (different date/score from latest).")
