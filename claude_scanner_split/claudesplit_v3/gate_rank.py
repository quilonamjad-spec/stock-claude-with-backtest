"""
gate_rank.py
------------
Two independent stages that run on Stage-1 scan output: the quality gate
(rejects exhausted/parabolic setups before they'd otherwise rank highly)
and the conviction-strength ranking. No Streamlit or network dependency.
"""

import numpy as np
import pandas as pd


# --------------------------------------------------------------------------
# QUALITY GATE — trend / structure / momentum checks that reject exhaustion
# --------------------------------------------------------------------------
def quality_gate(df: pd.DataFrame, params: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Splits Stage-1 results into (passed, rejected) based on trend,
    structure, and momentum checks. Bull and Bear are mirrored."""
    d = df.copy()
    is_bull = d["Phase"] == "Bull"

    # Trend check: EMA21 must still be sloping in the trade's direction —
    # rejects trends that already flattened out.
    trend_ok = np.where(is_bull, d["EMA21_slope"] > 0, d["EMA21_slope"] < 0)

    # Structure check: reject stocks stretched too far from EMA9 (in ATR
    # units) and reject unbroken same-direction runs — both are blow-off /
    # exhaustion signatures rather than healthy continuation.
    ext_ok = np.where(
        is_bull,
        d["Extension_ATR"] <= params["max_extension_atr"],
        d["Extension_ATR"] >= -params["max_extension_atr"],
    )
    run_ok = d["Consecutive_bars"].abs() <= params["max_consecutive_bars"]
    structure_ok = ext_ok & run_ok

    # Momentum check: RSI must be in a healthy band — positive/negative
    # enough to confirm momentum, but not already at an overbought/oversold
    # extreme (the classic exhaustion reading).
    momentum_ok = np.where(
        is_bull,
        (d["RSI14"] >= params["rsi_bull_min"]) & (d["RSI14"] <= params["rsi_bull_max"]),
        (d["RSI14"] >= 100 - params["rsi_bull_max"]) & (d["RSI14"] <= 100 - params["rsi_bull_min"]),
    )

    d["Trend OK"] = trend_ok
    d["Structure OK"] = structure_ok
    d["Momentum OK"] = momentum_ok
    passed_mask = trend_ok & structure_ok & momentum_ok

    return d[passed_mask].copy(), d[~passed_mask].copy()


# --------------------------------------------------------------------------
# RANKING
# --------------------------------------------------------------------------
def rank_results(df: pd.DataFrame, weights: dict) -> pd.DataFrame:
    d = df.copy()
    strength_cols = list(weights.keys())

    # Min-max normalize each strength metric to 0-1 across the current
    # filtered set, so raw scale differences (e.g. a 0-1 CMF vs a
    # 5x volume ratio) don't distort the weighting.
    for col in strength_cols:
        lo, hi = d[col].min(), d[col].max()
        if pd.isna(lo) or pd.isna(hi) or hi == lo:
            d[col + "_norm"] = 0.5
        else:
            d[col + "_norm"] = (d[col] - lo) / (hi - lo)

    total_w = sum(weights.values()) or 1.0
    d["Rank Score"] = sum(
        d[col + "_norm"] * (w / total_w) for col, w in weights.items()
    )
    d["Rank Score"] = (d["Rank Score"] * 100).round(1)
    d = d.sort_values(["Phase", "Rank Score"], ascending=[True, False])
    d.insert(0, "Rank", d.groupby("Phase")["Rank Score"].rank(ascending=False, method="first").astype(int))
    return d
