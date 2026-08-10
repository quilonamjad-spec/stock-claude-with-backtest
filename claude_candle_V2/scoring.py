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
# most specific signal. Volatility (Bollinger) is weighted up from its original 10% to
# 20% — band-crossing behavior tends to be a LEADING exhaustion/reversal signal, unlike
# RSI/MACD/Trend/VWAP which are largely coincident or lagging (they mostly confirm a
# move after it's already played out). Trend/RSI reduced slightly to compensate.
DEFAULT_WEIGHTS: Dict[str, float] = {
    "candle": 25,
    "rsi": 15,
    "macd": 10,
    "trend": 15,
    "volatility": 20,
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
# both ATR-normalized so the same formula shape works across any stock's price scale.
# Divisor is 3x ATR (not 0.5x) deliberately: ATR is a single DAY's typical range, so a
# 0.5x-ATR divisor saturates the score to ~100 after barely one day of normal drift,
# which is far too eager — any real multi-day trend would pin this at the ceiling
# almost immediately. A wider divisor requires a genuinely sustained move to saturate,
# giving a much more graduated read of "how strong is this trend, really".
# =========================================================
def _trend_ma_component(close: float, ema20: float, ema50: float, atr: float):
    if pd.isna(ema20) or pd.isna(ema50):
        return NEUTRAL, "Not enough history yet for EMA20/EMA50 — trend component stays neutral (50)."
    if pd.isna(atr) or atr == 0:
        atr = 1.0

    price_vs_ema20 = math.tanh((close - ema20) / (3.0 * atr))
    ema20_vs_ema50 = math.tanh((ema20 - ema50) / (3.0 * atr))
    combined = 0.6 * price_vs_ema20 + 0.4 * ema20_vs_ema50
    score = max(0.0, min(100.0, 50.0 + 50.0 * combined))

    reason = (
        f"Close {'above' if close >= ema20 else 'below'} EMA20 ({ema20:.2f}); "
        f"EMA20 {'above' if ema20 >= ema50 else 'below'} EMA50 ({ema50:.2f}) → "
        f"trend component {score:.1f}/100"
    )
    return score, reason


# =========================================================
# COMPONENT: Volatility (Bollinger Bands) — %B position (mean-reversion framing) PLUS
# explicit band-cross detection. Sitting near a band is a weak, persistent signal (price
# can "walk the band" for days during a strong trend without reversing) — but a candle
# that closes back INSIDE the band after having breached it is a much sharper,
# time-specific reversal trigger, and a fresh breach is an early extension warning.
# This is deliberately weighted heavily, since band-crossing behavior tends to be a
# LEADING signal for exhaustion/reversal, unlike RSI/MACD/Trend/VWAP which are largely
# coincident or lagging (they only confirm a move after it's already played out).
# =========================================================
def _volatility_bb_component(df: pd.DataFrame, i: int, close: float, bb_upper: float, bb_lower: float):
    if pd.isna(bb_upper) or pd.isna(bb_lower) or bb_upper == bb_lower:
        return NEUTRAL, "Not enough history yet for Bollinger Bands — volatility component stays neutral (50)."

    pct_b = (close - bb_lower) / (bb_upper - bb_lower)  # can go <0 or >1 when price is outside the bands
    base_score = max(0.0, min(100.0, 50.0 - (pct_b - 0.5) * 100.0))

    cross_bonus = 0.0
    cross_note = ""
    if i >= 1:
        prev_close = df["close"].iloc[i - 1]
        prev_upper = df["bb_upper"].iloc[i - 1] if "bb_upper" in df.columns else float("nan")
        prev_lower = df["bb_lower"].iloc[i - 1] if "bb_lower" in df.columns else float("nan")
        if not pd.isna(prev_upper) and not pd.isna(prev_lower):
            was_above, was_below = prev_close > prev_upper, prev_close < prev_lower
            now_above, now_below = close > bb_upper, close < bb_lower

            if was_above and not now_above:
                cross_bonus = -25.0
                cross_note = " — closed back INSIDE the upper band after breaching it: classic reversal-DOWN confirmation."
            elif was_below and not now_below:
                cross_bonus = +25.0
                cross_note = " — closed back INSIDE the lower band after breaching it: classic reversal-UP confirmation (bounce)."
            elif now_above and not was_above:
                cross_bonus = -8.0
                cross_note = " — just breached the upper band: extended, early warning (may still 'walk the band' further)."
            elif now_below and not was_below:
                cross_bonus = +8.0
                cross_note = " — just breached the lower band: oversold extension, early warning (may still 'walk the band' further)."

    score = max(0.0, min(100.0, base_score + cross_bonus))
    reason = (
        f"%B = {pct_b:.2f} (0 = lower band, 1 = upper band, can exceed this range when price is "
        f"outside the bands) → base {base_score:.1f}/100{cross_note} → volatility component {score:.1f}/100"
    )
    return score, reason


# =========================================================
# COMPONENT: Volume (VWAP) — price vs volume-weighted average price, ATR-normalized.
# Same wider-divisor reasoning as the Trend component above (3x ATR, not 0.5x) — price
# drifting a fraction of a day's typical range away from VWAP is completely normal and
# shouldn't already read as maximally bullish/bearish.
# =========================================================
def _volume_vwap_component(close: float, vwap: float, atr: float):
    if pd.isna(vwap):
        return NEUTRAL, "VWAP not available yet — volume component stays neutral (50)."
    if pd.isna(atr) or atr == 0:
        atr = 1.0

    signal = math.tanh((close - vwap) / (3.0 * atr))
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
    vol_bb_score, vol_bb_reason = _volatility_bb_component(df, i, close, bb_upper, bb_lower)
    vwap_score, vwap_reason = _volume_vwap_component(close, vwap, atr)

    component_scores = {
        "candle": candle_score, "rsi": rsi_score, "macd": macd_score,
        "trend": trend_score, "volatility": vol_bb_score, "volume": vwap_score,
    }

    final_raw = sum(w[k] * component_scores[k] for k in COMPONENT_LABELS) / 100.0

    # VARIANCE-RESTORING STRETCH: averaging N partially-independent components
    # mathematically shrinks the result toward the center (the more components, the
    # tighter the shrinkage) — even genuine multi-component agreement then struggles to
    # reach a clear Buy/Sell reading, and everything piles up as "Neutral". This factor
    # exactly compensates for that shrinkage (1/sqrt(sum of normalized weights squared)),
    # so real consensus among components can reach the same conviction a single strong
    # indicator would show on its own — while genuine disagreement between components
    # still correctly nets out near neutral, since the stretch is applied AFTER blending,
    # not to any individual component.
    w_frac = {k: w[k] / 100.0 for k in COMPONENT_LABELS}
    stretch = 1.0 / math.sqrt(sum(v * v for v in w_frac.values())) if any(w_frac.values()) else 1.0
    final = 50.0 + stretch * (final_raw - 50.0)
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

    reasons.append(
        f"── Raw blend: {final_raw:.1f} → stretch x{stretch:.2f} (restores spread lost to "
        f"averaging {len(COMPONENT_LABELS)} components) → Final score: {final}/100 → {verdict} ──"
    )

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
