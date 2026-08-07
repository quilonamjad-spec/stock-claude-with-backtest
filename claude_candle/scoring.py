"""
scoring.py
Combines detected candlestick patterns with trend, volume, momentum,
and support/resistance context into one composite score from -100 (strong
sell) to +100 (strong buy), plus a human-readable explanation of why.
"""
from dataclasses import dataclass, field
from typing import List
import pandas as pd

from patterns import detect_patterns, PatternHit


@dataclass
class ScoreResult:
    ticker: str
    date: pd.Timestamp
    score: float
    verdict: str
    patterns: List[PatternHit] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    close: float = 0.0
    trend: str = "unknown"
    rsi: float = 50.0
    vol_ratio: float = 1.0


def _trend_multiplier(bias: str, trend: str) -> float:
    if bias == "bullish":
        if trend == "downtrend":
            return 1.4   # reversal pattern aligned with "buy the dip" logic
        if trend == "uptrend":
            return 0.9   # bullish pattern mid-uptrend = weaker reversal signal, still fine
        return 0.7
    if bias == "bearish":
        if trend == "uptrend":
            return 1.4
        if trend == "downtrend":
            return 0.9
        return 0.7
    return 1.0


def _volume_multiplier(vol_ratio: float) -> float:
    if pd.isna(vol_ratio):
        return 1.0
    if vol_ratio >= 1.5:
        return 1.3
    if vol_ratio >= 1.0:
        return 1.1
    if vol_ratio >= 0.7:
        return 0.9
    return 0.75


def _momentum_bonus(bias: str, rsi: float, macd_hist: float) -> float:
    bonus = 0.0
    if pd.isna(rsi):
        rsi = 50
    if bias == "bullish":
        if rsi < 30:
            bonus += 8       # oversold supports a bullish reversal
        if macd_hist is not None and not pd.isna(macd_hist) and macd_hist > 0:
            bonus += 5
    elif bias == "bearish":
        if rsi > 70:
            bonus += 8       # overbought supports a bearish reversal
        if macd_hist is not None and not pd.isna(macd_hist) and macd_hist < 0:
            bonus += 5
    return bonus


def _support_resistance_bonus(bias: str, close: float, support: float, resistance: float) -> float:
    if pd.isna(support) or pd.isna(resistance) or resistance == support:
        return 0.0
    band = resistance - support
    if bias == "bullish":
        dist_to_support = (close - support) / band
        if dist_to_support < 0.15:
            return 6.0
    elif bias == "bearish":
        dist_to_resistance = (resistance - close) / band
        if dist_to_resistance < 0.15:
            return 6.0
    return 0.0


def score_latest(df: pd.DataFrame, ticker: str) -> ScoreResult:
    """Score the most recent (last) candle in df. df must already have indicators."""
    i = len(df) - 1
    hits = detect_patterns(df, i)
    row = df.iloc[i]
    trend = row.get("trend", "unknown")
    rsi = row.get("rsi", 50)
    macd_hist = row.get("macd_hist", 0)
    vol_ratio = row.get("vol_ratio", 1.0)
    support = row.get("support_20", float("nan"))
    resistance = row.get("resistance_20", float("nan"))
    close = row.get("close", 0.0)

    total_score = 0.0
    reasons = []

    if not hits:
        reasons.append("No recognizable candlestick pattern on the latest candle.")
    for hit in hits:
        tmult = _trend_multiplier(hit.bias, trend)
        vmult = _volume_multiplier(vol_ratio)
        mbonus = _momentum_bonus(hit.bias, rsi, macd_hist)
        srbonus = _support_resistance_bonus(hit.bias, close, support, resistance)

        raw = hit.base_weight * tmult * vmult + mbonus + srbonus
        signed = raw if hit.bias == "bullish" else (-raw if hit.bias == "bearish" else 0)
        total_score += signed

        reasons.append(
            f"{hit.name} ({hit.bias}, {hit.strength}): base {hit.base_weight:.0f} "
            f"× trend[{trend}] x{tmult:.2f} × volume x{vmult:.2f} "
            f"+ momentum {mbonus:.0f} + S/R {srbonus:.0f} → {signed:+.1f}"
        )

    total_score = max(-100, min(100, total_score))

    if total_score >= 40:
        verdict = "Strong Buy"
    elif total_score >= 15:
        verdict = "Buy"
    elif total_score > -15:
        verdict = "Neutral"
    elif total_score > -40:
        verdict = "Sell"
    else:
        verdict = "Strong Sell"

    return ScoreResult(
        ticker=ticker,
        date=df.index[i],
        score=round(total_score, 1),
        verdict=verdict,
        patterns=hits,
        reasons=reasons,
        close=close,
        trend=trend,
        rsi=round(float(rsi), 1) if not pd.isna(rsi) else 50.0,
        vol_ratio=round(float(vol_ratio), 2) if not pd.isna(vol_ratio) else 1.0,
    )
