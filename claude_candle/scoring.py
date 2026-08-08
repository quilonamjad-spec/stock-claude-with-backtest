"""
scoring.py
Composite score on a 0-100 scale (50 = neutral, 100 = max bullish, 0 = max
bearish), built from THREE INDEPENDENT, WEIGHTED components:

    Candle Pattern  — pattern shape + trend alignment + volume + S/R proximity
    RSI             — overbought/oversold momentum
    MACD            — trend-momentum via the MACD histogram (ATR-normalized)

Each component always produces its own 0-100 sub-score regardless of what
the others say — this is the key fix over the old design, where RSI/MACD
only mattered if a candlestick pattern happened to fire on that exact
candle. Now every candle gets a real, continuously-varying score.

The three components are blended by user-adjustable weights that sum to
100 (defaults: Candle 40 / RSI 30 / MACD 30) — see DEFAULT_WEIGHTS.
"""
import math
from dataclasses import dataclass, field
from typing import List, Optional, Dict
import pandas as pd

from patterns import detect_patterns, PatternHit

NEUTRAL = 50.0
MAX_CANDLE_DEVIATION = 50.0  # candle component is clamped to NEUTRAL +/- this -> [0, 100]

DEFAULT_WEIGHTS: Dict[str, float] = {"candle": 40, "rsi": 30, "macd": 30}


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
    component_scores: Dict[str, float] = field(default_factory=dict)  # {"candle":.., "rsi":.., "macd":..}


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


def _support_resistance_bonus(bias: str, close: float, support: float, resistance: float, base_weight: float) -> float:
    if pd.isna(support) or pd.isna(resistance) or resistance == support:
        return 0.0
    band = resistance - support
    if bias == "bullish":
        if (close - support) / band < 0.15:
            return base_weight * 0.20
    elif bias == "bearish":
        if (resistance - close) / band < 0.15:
            return base_weight * 0.20
    return 0.0


def _plain_english_context(hit_bias: str, trend: str) -> str:
    """A short, unambiguous sentence explaining *why* trend context pushed the score
    the way it did — reversal patterns score strongest exactly when they go AGAINST
    the prior trend, which otherwise reads as contradictory."""
    if hit_bias == "bullish" and trend == "downtrend":
        return "    → Reversal-up signal after a decline: classic 'potential bottom' setup, scored strongly bullish."
    if hit_bias == "bullish" and trend == "uptrend":
        return "    → Bullish pattern continuing an existing uptrend: supportive, but weaker than a bottom reversal."
    if hit_bias == "bearish" and trend == "uptrend":
        return "    → Reversal-down signal after a rally: classic 'potential top' setup — scored strongly bearish."
    if hit_bias == "bearish" and trend == "downtrend":
        return "    → Bearish pattern continuing an existing downtrend: supportive, but weaker than a top reversal."
    return "    → No clear prior trend to react against, so this pattern is scored on a weaker base."


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


# =========================================================
# COMPONENT 1: Candle Pattern (shape + trend + volume + S/R)
# =========================================================
def _candle_component(df: pd.DataFrame, i: int, trend: float, vol_ratio: float,
                       support: float, resistance: float, close: float):
    hits = detect_patterns(df, i)
    reasons = []

    if not hits:
        reasons.append("No candlestick pattern detected on this candle — candle component stays neutral (50).")
        return NEUTRAL, reasons, hits

    deviation = 0.0
    for hit in hits:
        tmult = _trend_multiplier(hit.bias, trend)
        vmult = _volume_multiplier(vol_ratio)
        srbonus = _support_resistance_bonus(hit.bias, close, support, resistance, hit.base_weight)
        raw = hit.base_weight * tmult * vmult + srbonus
        signed = raw if hit.bias == "bullish" else (-raw if hit.bias == "bearish" else 0)
        deviation += signed

        direction = "bullish" if hit.bias == "bullish" else ("bearish" if hit.bias == "bearish" else "neutral")
        reasons.append(
            f"{hit.name} ({direction}, {hit.strength}): base {hit.base_weight:.0f} "
            f"× trend[{trend}] x{tmult:.2f} × volume x{vmult:.2f} + S/R {srbonus:.1f} → {signed:+.1f} pts"
        )
        if hit.bias in ("bullish", "bearish"):
            reasons.append(_plain_english_context(hit.bias, trend))

    deviation = max(-MAX_CANDLE_DEVIATION, min(MAX_CANDLE_DEVIATION, deviation))
    score = NEUTRAL + deviation
    return score, reasons, hits


# =========================================================
# COMPONENT 2: RSI
# =========================================================
def _rsi_component(rsi: float):
    if pd.isna(rsi):
        rsi = 50.0
    score = max(0.0, min(100.0, 100.0 - rsi))  # low RSI (oversold) -> high score (bullish); high RSI -> low score
    reason = (
        f"RSI = {rsi:.1f} → component score {score:.1f}/100 "
        f"(oversold <30 leans bullish, overbought >70 leans bearish)"
    )
    return score, reason


# =========================================================
# COMPONENT 3: MACD (histogram, normalized by ATR so it's comparable across stocks)
# =========================================================
def _macd_component(macd_hist: float, atr: float):
    if pd.isna(macd_hist):
        macd_hist = 0.0
    if pd.isna(atr) or atr == 0:
        atr = 1.0
    normalized = macd_hist / (0.5 * atr)
    score = 50.0 + 50.0 * math.tanh(normalized)
    reason = (
        f"MACD histogram = {macd_hist:.3f} (ATR-normalized {normalized:+.2f}) → "
        f"component score {score:.1f}/100 (positive/rising histogram leans bullish)"
    )
    return score, reason


def score_at(df: pd.DataFrame, ticker: str, i: int, weights: Optional[Dict[str, float]] = None) -> ScoreResult:
    """Score the candle at integer position i, on a 0-100 scale (50 = neutral).
    df must already have indicators. Only uses data up to and including i — never looks ahead.

    weights: optional dict like {"candle": 40, "rsi": 30, "macd": 30} — any positive
    numbers, they're normalized to sum to 100 automatically. Defaults to DEFAULT_WEIGHTS.
    """
    weights = dict(weights) if weights else dict(DEFAULT_WEIGHTS)
    total_w = sum(max(0.0, w) for w in weights.values()) or 1.0
    w_candle = max(0.0, weights.get("candle", 0)) / total_w * 100
    w_rsi = max(0.0, weights.get("rsi", 0)) / total_w * 100
    w_macd = max(0.0, weights.get("macd", 0)) / total_w * 100

    row = df.iloc[i]
    trend = row.get("trend", "unknown")
    rsi = row.get("rsi", 50)
    macd_hist = row.get("macd_hist", 0)
    atr = row.get("atr", float("nan"))
    vol_ratio = row.get("vol_ratio", 1.0)
    support = row.get("support_20", float("nan"))
    resistance = row.get("resistance_20", float("nan"))
    close = row.get("close", 0.0)

    candle_score, candle_reasons, hits = _candle_component(df, i, trend, vol_ratio, support, resistance, close)
    rsi_score, rsi_reason = _rsi_component(rsi)
    macd_score, macd_reason = _macd_component(macd_hist, atr)

    final = (w_candle * candle_score + w_rsi * rsi_score + w_macd * macd_score) / 100.0
    final = round(max(0.0, min(100.0, final)), 1)
    verdict = _verdict(final)

    reasons = [f"── Candle Pattern component (weight {w_candle:.0f}%) ──"]
    reasons += candle_reasons
    reasons.append(f"Candle component: {candle_score:.1f}/100 → contributes {w_candle / 100 * candle_score:+.1f} pts")

    reasons.append(f"── RSI component (weight {w_rsi:.0f}%) ──")
    reasons.append(rsi_reason)
    reasons.append(f"RSI component contributes {w_rsi / 100 * rsi_score:+.1f} pts")

    reasons.append(f"── MACD component (weight {w_macd:.0f}%) ──")
    reasons.append(macd_reason)
    reasons.append(f"MACD component contributes {w_macd / 100 * macd_score:+.1f} pts")

    reasons.append(f"── Final blended score: {final}/100 → {verdict} ──")

    return ScoreResult(
        ticker=ticker,
        date=df.index[i],
        score=final,
        verdict=verdict,
        patterns=hits,
        reasons=reasons,
        close=close,
        trend=trend,
        rsi=round(float(rsi), 1) if not pd.isna(rsi) else 50.0,
        vol_ratio=round(float(vol_ratio), 2) if not pd.isna(vol_ratio) else 1.0,
        component_scores={"candle": round(candle_score, 1), "rsi": round(rsi_score, 1), "macd": round(macd_score, 1)},
    )


def score_latest(df: pd.DataFrame, ticker: str, weights: Optional[Dict[str, float]] = None) -> ScoreResult:
    """Score the most recent (last) candle in df. Convenience wrapper around score_at."""
    return score_at(df, ticker, len(df) - 1, weights=weights)
