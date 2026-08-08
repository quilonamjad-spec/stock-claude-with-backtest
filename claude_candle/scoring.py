"""
scoring.py
Combines detected candlestick patterns with trend, volume, momentum,
and support/resistance context into one composite score on a 0-100 scale:

    0   = strongest bearish conviction
    50  = neutral / no edge either way
    100 = strongest bullish conviction

Plus a human-readable explanation of exactly how each number was reached.
"""
from dataclasses import dataclass, field
from typing import List
import pandas as pd

from patterns import detect_patterns, PatternHit

NEUTRAL = 50.0
MAX_DEVIATION = 50.0  # score is clamped to NEUTRAL +/- MAX_DEVIATION -> [0, 100]


@dataclass
class ScoreResult:
    ticker: str
    date: pd.Timestamp
    score: float          # 0-100, 50 = neutral
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


def _momentum_bonus(bias: str, rsi: float, macd_hist: float, base_weight: float) -> float:
    """Scaled to the pattern's own base_weight so a weak pattern (e.g. spinning top, weight 8)
    can't get boosted almost as high as a strong one (e.g. three soldiers, weight 24) just
    because RSI happens to be extreme."""
    bonus = 0.0
    if pd.isna(rsi):
        rsi = 50
    if bias == "bullish":
        if rsi < 30:
            bonus += base_weight * 0.25    # oversold supports a bullish reversal
        if macd_hist is not None and not pd.isna(macd_hist) and macd_hist > 0:
            bonus += base_weight * 0.15
    elif bias == "bearish":
        if rsi > 70:
            bonus += base_weight * 0.25    # overbought supports a bearish reversal
        if macd_hist is not None and not pd.isna(macd_hist) and macd_hist < 0:
            bonus += base_weight * 0.15
    return bonus


def _support_resistance_bonus(bias: str, close: float, support: float, resistance: float, base_weight: float) -> float:
    if pd.isna(support) or pd.isna(resistance) or resistance == support:
        return 0.0
    band = resistance - support
    if bias == "bullish":
        dist_to_support = (close - support) / band
        if dist_to_support < 0.15:
            return base_weight * 0.20
    elif bias == "bearish":
        dist_to_resistance = (resistance - close) / band
        if dist_to_resistance < 0.15:
            return base_weight * 0.20
    return 0.0


def _plain_english_context(hit_bias: str, trend: str) -> str:
    """A short, unambiguous sentence explaining *why* trend context pushed the score
    the way it did — added because 'trend[uptrend]' next to a bearish score reads as
    contradictory unless it's spelled out that reversal patterns are scored strongest
    exactly when they go AGAINST the prior trend."""
    if hit_bias == "bullish" and trend == "downtrend":
        return "  → Reversal-up signal after a decline: classic 'potential bottom' setup, scored strongly bullish."
    if hit_bias == "bullish" and trend == "uptrend":
        return "  → Bullish pattern continuing an existing uptrend: supportive, but a weaker signal than a bottom reversal."
    if hit_bias == "bearish" and trend == "uptrend":
        return "  → Reversal-down signal after a rally: classic 'potential top' setup — this is WHY it scores as a strong sell despite the prior trend being up."
    if hit_bias == "bearish" and trend == "downtrend":
        return "  → Bearish pattern continuing an existing downtrend: supportive, but a weaker signal than a top reversal."
    return "  → No clear prior trend to react against, so this pattern is scored on a weaker base."


def _verdict(score: float) -> str:
    if score >= 80:
        return "Strong Buy"
    if score >= 60:
        return "Buy"
    if score > 40:
        return "Neutral"
    if score > 20:
        return "Sell"
    return "Strong Sell"


def score_at(df: pd.DataFrame, ticker: str, i: int) -> ScoreResult:
    """Score the candle at integer position i, on a 0-100 scale (50 = neutral).
    df must already have indicators. Only uses data up to and including i — never looks ahead."""
    hits = detect_patterns(df, i)
    row = df.iloc[i]
    trend = row.get("trend", "unknown")
    rsi = row.get("rsi", 50)
    macd_hist = row.get("macd_hist", 0)
    vol_ratio = row.get("vol_ratio", 1.0)
    support = row.get("support_20", float("nan"))
    resistance = row.get("resistance_20", float("nan"))
    close = row.get("close", 0.0)

    deviation = 0.0  # signed distance from neutral (50): positive = bullish, negative = bearish
    reasons = []

    if not hits:
        reasons.append("No recognizable candlestick pattern on this candle — score stays at neutral (50).")

    for hit in hits:
        tmult = _trend_multiplier(hit.bias, trend)
        vmult = _volume_multiplier(vol_ratio)
        mbonus = _momentum_bonus(hit.bias, rsi, macd_hist, hit.base_weight)
        srbonus = _support_resistance_bonus(hit.bias, close, support, resistance, hit.base_weight)

        raw = hit.base_weight * tmult * vmult + mbonus + srbonus
        signed = raw if hit.bias == "bullish" else (-raw if hit.bias == "bearish" else 0)
        deviation += signed

        direction = "bullish" if hit.bias == "bullish" else ("bearish" if hit.bias == "bearish" else "neutral")
        reasons.append(
            f"{hit.name} ({direction}, {hit.strength}): base {hit.base_weight:.0f} "
            f"× trend[{trend}] x{tmult:.2f} × volume x{vmult:.2f} "
            f"+ momentum {mbonus:.0f} + S/R {srbonus:.0f} → {signed:+.1f} pts "
            f"{'toward 100 (bullish)' if signed > 0 else 'toward 0 (bearish)' if signed < 0 else ''}"
        )
        if hit.bias in ("bullish", "bearish"):
            reasons.append(_plain_english_context(hit.bias, trend))

    deviation = max(-MAX_DEVIATION, min(MAX_DEVIATION, deviation))
    score = round(NEUTRAL + deviation, 1)
    verdict = _verdict(score)

    if hits:
        reasons.append(f"Combined deviation from neutral: {deviation:+.1f} → final score {score}/100 ({verdict}).")

    return ScoreResult(
        ticker=ticker,
        date=df.index[i],
        score=score,
        verdict=verdict,
        patterns=hits,
        reasons=reasons,
        close=close,
        trend=trend,
        rsi=round(float(rsi), 1) if not pd.isna(rsi) else 50.0,
        vol_ratio=round(float(vol_ratio), 2) if not pd.isna(vol_ratio) else 1.0,
    )


def score_latest(df: pd.DataFrame, ticker: str) -> ScoreResult:
    """Score the most recent (last) candle in df. Convenience wrapper around score_at."""
    return score_at(df, ticker, len(df) - 1)
