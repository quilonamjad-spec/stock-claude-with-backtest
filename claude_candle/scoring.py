"""
scoring.py
Composite score on a 0-100 scale (50 = neutral, 100 = max bullish, 0 = max
bearish), built from SIX INDEPENDENT, WEIGHTED components spanning the four
main technical-analysis categories:

    Category    Component        What it measures
    ---------   ---------------   -----------------------------------------
    Pattern     Candle Pattern    Shape + trend alignment + volume + S/R
    Momentum    RSI               Overbought/oversold
    Momentum    MACD              Histogram direction/strength (ATR-normalized)
    Trend       Moving Averages   Price vs EMA20 + EMA20-vs-EMA50 cross
    Volatility  Bollinger Bands   %B position (mean-reversion framing)
    Volume      VWAP              Price vs volume-weighted average price

Each component always produces its own 0-100 sub-score regardless of what
the others say — no component's contribution depends on any other firing.
That's the key property that keeps scores continuously informative instead
of sitting at neutral whenever, say, no candlestick pattern happens to be
present on a given candle.

The components are blended by user-adjustable weights that sum to 100 (see
DEFAULT_WEIGHTS) — any subset of components can be dialed up or down freely.
"""
import math
from dataclasses import dataclass, field
from typing import List, Optional, Dict
import pandas as pd

from patterns import detect_patterns, PatternHit

NEUTRAL = 50.0
MAX_CANDLE_DEVIATION = 50.0  # candle component is clamped to NEUTRAL +/- this -> [0, 100]

# Balanced default: Pattern recognition gets the single biggest share since it's the
# most specific signal; the two Momentum components together (30%) get similar total
# weight to before; Trend, Volatility, and Volume round out the picture.
DEFAULT_WEIGHTS: Dict[str, float] = {
    "candle": 25,
    "rsi": 15,
    "macd": 15,
    "trend": 20,
    "volatility": 10,
    "volume": 15,
}

COMPONENT_LABELS = {
    "candle": "Candle Pattern",
    "rsi": "RSI",
    "macd": "MACD",
    "trend": "Trend (MA)",
    "volatility": "Volatility (Bollinger)",
    "volume": "Volume (VWAP)",
}


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
    component_scores: Dict[str, float] = field(default_factory=dict)


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
# COMPONENT: Candle Pattern (shape + trend + volume + S/R)
# =========================================================
def _candle_component(df: pd.DataFrame, i: int, trend: str, vol_ratio: float,
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
# COMPONENT: RSI
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
# COMPONENT: MACD (histogram, normalized by ATR so it's comparable across stocks)
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


# =========================================================
# COMPONENT: Trend (Moving Averages) — price vs EMA20, plus EMA20-vs-EMA50 cross,
# both ATR-normalized so the same formula shape works across any stock's price scale
# =========================================================
def _trend_ma_component(close: float, ema20: float, ema50: float, atr: float):
    if pd.isna(ema20) or pd.isna(ema50):
        return NEUTRAL, "Not enough history yet for EMA20/EMA50 — trend component stays neutral (50)."
    if pd.isna(atr) or atr == 0:
        atr = 1.0

    price_vs_ema20 = math.tanh((close - ema20) / (0.5 * atr))
    ema20_vs_ema50 = math.tanh((ema20 - ema50) / (0.5 * atr))
    combined = 0.6 * price_vs_ema20 + 0.4 * ema20_vs_ema50
    score = max(0.0, min(100.0, 50.0 + 50.0 * combined))

    reason = (
        f"Close {'above' if close >= ema20 else 'below'} EMA20 ({ema20:.2f}); "
        f"EMA20 {'above' if ema20 >= ema50 else 'below'} EMA50 ({ema50:.2f}) → "
        f"trend component {score:.1f}/100"
    )
    return score, reason


# =========================================================
# COMPONENT: Volatility (Bollinger Bands) — %B framed as mean-reversion:
# near the upper band leans bearish (overextended), near the lower band leans bullish
# =========================================================
def _volatility_bb_component(close: float, bb_upper: float, bb_lower: float):
    if pd.isna(bb_upper) or pd.isna(bb_lower) or bb_upper == bb_lower:
        return NEUTRAL, "Not enough history yet for Bollinger Bands — volatility component stays neutral (50)."

    pct_b = (close - bb_lower) / (bb_upper - bb_lower)
    pct_b_clamped = max(0.0, min(1.0, pct_b))
    score = 100.0 - pct_b_clamped * 100.0

    reason = (
        f"%B = {pct_b:.2f} (0 = at lower band, 1 = at upper band) → "
        f"volatility component {score:.1f}/100 (near upper band leans bearish/overextended, "
        f"near lower band leans bullish/oversold)"
    )
    return score, reason


# =========================================================
# COMPONENT: Volume (VWAP) — price vs volume-weighted average price, ATR-normalized
# =========================================================
def _volume_vwap_component(close: float, vwap: float, atr: float):
    if pd.isna(vwap):
        return NEUTRAL, "VWAP not available yet — volume component stays neutral (50)."
    if pd.isna(atr) or atr == 0:
        atr = 1.0

    signal = math.tanh((close - vwap) / (0.5 * atr))
    score = max(0.0, min(100.0, 50.0 + 50.0 * signal))
    reason = (
        f"Close {'above' if close >= vwap else 'below'} VWAP ({vwap:.2f}) → "
        f"volume component {score:.1f}/100 (above VWAP leans bullish institutional flow)"
    )
    return score, reason


def score_at(df: pd.DataFrame, ticker: str, i: int, weights: Optional[Dict[str, float]] = None) -> ScoreResult:
    """Score the candle at integer position i, on a 0-100 scale (50 = neutral).
    df must already have indicators. Only uses data up to and including i — never looks ahead.

    weights: optional dict with any subset of {"candle","rsi","macd","trend","volatility",
    "volume"} — any positive numbers, normalized to sum to 100 automatically. Missing keys
    default to 0. Defaults to DEFAULT_WEIGHTS.
    """
    weights = dict(weights) if weights else dict(DEFAULT_WEIGHTS)
    total_w = sum(max(0.0, w) for w in weights.values()) or 1.0
    w = {k: max(0.0, weights.get(k, 0)) / total_w * 100 for k in COMPONENT_LABELS}

    row = df.iloc[i]
    trend = row.get("trend", "unknown")
    rsi = row.get("rsi", 50)
    macd_hist = row.get("macd_hist", 0)
    atr = row.get("atr", float("nan"))
    vol_ratio = row.get("vol_ratio", 1.0)
    support = row.get("support_20", float("nan"))
    resistance = row.get("resistance_20", float("nan"))
    close = row.get("close", 0.0)
    ema20 = row.get("ema_20", float("nan"))
    ema50 = row.get("ema_50", float("nan"))
    bb_upper = row.get("bb_upper", float("nan"))
    bb_lower = row.get("bb_lower", float("nan"))
    vwap = row.get("vwap", float("nan"))

    candle_score, candle_reasons, hits = _candle_component(df, i, trend, vol_ratio, support, resistance, close)
    rsi_score, rsi_reason = _rsi_component(rsi)
    macd_score, macd_reason = _macd_component(macd_hist, atr)
    trend_score, trend_reason = _trend_ma_component(close, ema20, ema50, atr)
    vol_bb_score, vol_bb_reason = _volatility_bb_component(close, bb_upper, bb_lower)
    vwap_score, vwap_reason = _volume_vwap_component(close, vwap, atr)

    component_scores = {
        "candle": candle_score, "rsi": rsi_score, "macd": macd_score,
        "trend": trend_score, "volatility": vol_bb_score, "volume": vwap_score,
    }

    final = sum(w[k] * component_scores[k] for k in COMPONENT_LABELS) / 100.0
    final = round(max(0.0, min(100.0, final)), 1)
    verdict = _verdict(final)

    reasons = [f"── {COMPONENT_LABELS['candle']} component (weight {w['candle']:.0f}%) ──"]
    reasons += candle_reasons
    reasons.append(f"Candle component: {candle_score:.1f}/100 → contributes {w['candle'] / 100 * candle_score:+.1f} pts")

    reasons.append(f"── {COMPONENT_LABELS['rsi']} component (weight {w['rsi']:.0f}%) ──")
    reasons.append(rsi_reason)
    reasons.append(f"RSI component contributes {w['rsi'] / 100 * rsi_score:+.1f} pts")

    reasons.append(f"── {COMPONENT_LABELS['macd']} component (weight {w['macd']:.0f}%) ──")
    reasons.append(macd_reason)
    reasons.append(f"MACD component contributes {w['macd'] / 100 * macd_score:+.1f} pts")

    reasons.append(f"── {COMPONENT_LABELS['trend']} component (weight {w['trend']:.0f}%) ──")
    reasons.append(trend_reason)
    reasons.append(f"Trend component contributes {w['trend'] / 100 * trend_score:+.1f} pts")

    reasons.append(f"── {COMPONENT_LABELS['volatility']} component (weight {w['volatility']:.0f}%) ──")
    reasons.append(vol_bb_reason)
    reasons.append(f"Volatility component contributes {w['volatility'] / 100 * vol_bb_score:+.1f} pts")

    reasons.append(f"── {COMPONENT_LABELS['volume']} component (weight {w['volume']:.0f}%) ──")
    reasons.append(vwap_reason)
    reasons.append(f"Volume component contributes {w['volume'] / 100 * vwap_score:+.1f} pts")

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
        component_scores={k: round(v, 1) for k, v in component_scores.items()},
    )


def score_latest(df: pd.DataFrame, ticker: str, weights: Optional[Dict[str, float]] = None) -> ScoreResult:
    """Score the most recent (last) candle in df. Convenience wrapper around score_at."""
    return score_at(df, ticker, len(df) - 1, weights=weights)
