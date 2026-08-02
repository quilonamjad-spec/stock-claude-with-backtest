"""
scoring.py
----------
Converts raw indicator values (already computed on the DataFrame by
indicators.py) into:

  - per-indicator "signal" in [-1, +1]  (-1 max bearish, +1 max bullish)
  - a combined Trade Score in [0, 100]   (50 = neutral)
  - a Confidence in [0, 100]             (how much the active indicators agree)
  - a human-readable Signal label

Every scoring function is intentionally simple and documented so you can
retune thresholds/weights to match your own trading style. None of this
is a "prediction" -- it's a rules-based summary of textbook technical
signals, meant to help you triage/shortlist, not to replace judgement.
"""

import numpy as np
from candlestick_patterns import detect_patterns

DEFAULT_WEIGHTS = {
    "RSI": 1.0,
    "MACD": 1.2,
    "ADX": 1.2,
    "BOLLINGER": 0.7,
    "VOLUME": 0.6,
    "EMA_TREND": 1.0,
    "CANDLESTICK": 0.8,
    "EXTENSION": 1.0,
    "VWAP": 1.0,
}


def signal_rsi(row):
    rsi = row.get("RSI", 50)
    if rsi <= 30:
        return (30 - rsi) / 30  # 0..1 bullish (oversold)
    if rsi >= 70:
        return -(rsi - 70) / 30  # 0..-1 bearish (overbought)
    # mild mean-reversion tilt inside the neutral zone
    return (50 - rsi) / 50 * 0.25


def signal_macd(row):
    hist = row.get("MACD_HIST", 0)
    macd = row.get("MACD", 0)
    signal_line = row.get("MACD_SIGNAL", 0)
    # normalize histogram roughly by price scale-free proxy: use sign + magnitude vs macd/signal spread
    denom = max(abs(macd), abs(signal_line), 1e-6)
    val = np.clip(hist / denom, -1, 1)
    return float(val)


def signal_adx(row):
    adx = row.get("ADX", 0)
    plus_di = row.get("PLUS_DI", 0)
    minus_di = row.get("MINUS_DI", 0)
    direction = 1 if plus_di >= minus_di else -1
    strength = min(adx / 50.0, 1.0)
    # Dampen signal when ADX < 20 (choppy / no clear trend)
    if adx < 20:
        strength *= 0.3
    return direction * strength


def signal_bollinger(row):
    pos = row.get("BB_POSITION", 0.5)
    # mild momentum-following interpretation: above mid-band = bullish lean
    return float((pos - 0.5) * 2 * 0.6)


def signal_volume(row, prev_close):
    ratio = row.get("VOL_RATIO", 1.0)
    close = row.get("Close", 0)
    if ratio <= 1.2 or prev_close is None or prev_close == 0:
        return 0.0
    day_return = (close - prev_close) / prev_close
    direction = 1 if day_return > 0 else (-1 if day_return < 0 else 0)
    conviction = min(ratio - 1, 1.0)
    return direction * conviction


def signal_ema_trend(row):
    close = row.get("Close", 0)
    ema20 = row.get("EMA20", close)
    ema50 = row.get("EMA50", close)
    if close > ema20 > ema50:
        return 0.8
    if close < ema20 < ema50:
        return -0.8
    if close > ema20:
        return 0.3
    if close < ema20:
        return -0.3
    return 0.0


def signal_extension(row):
    """
    Mean-reversion / exhaustion check, added after noticing Strong Buy
    signals kept firing right at local tops. Every OTHER component above
    (MACD, ADX, BOLLINGER, VOLUME, EMA_TREND) rewards "further in the
    trend's favor = more bullish/bearish" with essentially no ceiling --
    none of them ask "...but is this move too stretched to keep going?"
    RSI is the only one that fades at extremes, and it's frequently
    outvoted by the other five all agreeing "strong move."

    This measures how far price has stretched from its own 20-period mean,
    in ATR units -- i.e. volatility-normalized distance, not RSI's
    gain/loss ratio, so it's an independent read rather than a duplicate
    of RSI:

        extension = (Close - EMA20) / ATR

    Deliberately the OPPOSITE lean from the trend-following components:
    the further price is stretched above its mean, the MORE this fades
    toward bearish (possible exhaustion / mean-reversion risk), not more
    bullish. Symmetric on the downside (oversold bounce risk for shorts).

    Continuous ramp, no flat "neutral zone" -- per user preference to keep
    this as a single tunable component rather than adding a separate
    toggle for a dead-zone threshold. A mild 1-ATR stretch already gets a
    small fade (~0.25); it's not a hard 0 until some cutoff. Fully faded
    (magnitude 1) by 4 ATR away from the mean.
    """
    close = row.get("Close", 0)
    ema20 = row.get("EMA20", close)
    atr = row.get("ATR", 0)
    if atr is None or np.isnan(atr) or atr <= 0:
        return 0.0

    extension = (close - ema20) / atr
    magnitude = float(np.clip(abs(extension) / 4.0, 0, 1))
    sign = -1 if extension > 0 else 1  # stretched above mean -> fade bearish; stretched below -> fade bullish
    return sign * magnitude


def signal_vwap(row):
    """
    Above session VWAP = bullish (price has traded above the
    volume-weighted average since the open -- buyers have been paying up);
    below = bearish. Graduated by ATR distance from VWAP, same
    ATR-normalized style as EXTENSION, so a stock barely above/below VWAP
    scores near neutral while one meaningfully away scores near the full
    +-1.

    Deliberately NOT faded back toward neutral at large distances the way
    EXTENSION is -- VWAP is a directional/positioning signal, not an
    exhaustion signal. "Far above VWAP" is still read as bullish (just
    capped at +-1 by 2 ATR away), not walked back.

    Only meaningful on intraday data -- compute_vwap() resets VWAP each
    calendar day, so this is most useful at the 5m/15m/1h timeframes.
    """
    close = row.get("Close", 0)
    vwap = row.get("VWAP", close)
    atr = row.get("ATR", 0)
    if atr is None or np.isnan(atr) or atr <= 0:
        return 0.0

    distance = (close - vwap) / atr
    return float(np.clip(distance / 2.0, -1, 1))


def signal_candlestick(df):
    result = detect_patterns(df, lookback_index=-1)
    return result["signal"], result["patterns"]


def _extension_atr(row):
    """Raw (Close - EMA20) / ATR value, for display -- see signal_extension for the scored version."""
    close = row.get("Close", 0)
    ema20 = row.get("EMA20", close)
    atr = row.get("ATR", 0)
    if not atr or atr <= 0:
        return None
    return round(float((close - ema20) / atr), 2)


def score_symbol(df, active_indicators: dict, weights: dict = None):
    """
    df: OHLCV dataframe with indicator columns already computed
        (see indicators.compute_all_indicators).
    active_indicators: dict of {"RSI": True, "MACD": True, ...} toggles.
    weights: optional override of DEFAULT_WEIGHTS.

    Returns dict with trade_score, confidence, signal_label, breakdown.
    """
    weights = weights or DEFAULT_WEIGHTS
    if df is None or len(df) < 5:
        return {
            "trade_score": 50.0,
            "confidence": 0.0,
            "signal_label": "No Data",
            "breakdown": {},
        }

    row = df.iloc[-1]
    prev_close = df.iloc[-2]["Close"] if len(df) > 1 else None

    signals = {}
    patterns_found = []

    if active_indicators.get("RSI", True):
        signals["RSI"] = signal_rsi(row)
    if active_indicators.get("MACD", True):
        signals["MACD"] = signal_macd(row)
    if active_indicators.get("ADX", True):
        signals["ADX"] = signal_adx(row)
    if active_indicators.get("BOLLINGER", True):
        signals["BOLLINGER"] = signal_bollinger(row)
    if active_indicators.get("VOLUME", True):
        signals["VOLUME"] = signal_volume(row, prev_close)
    if active_indicators.get("EMA_TREND", True):
        signals["EMA_TREND"] = signal_ema_trend(row)
    if active_indicators.get("EXTENSION", True):
        signals["EXTENSION"] = signal_extension(row)
    if active_indicators.get("VWAP", True):
        signals["VWAP"] = signal_vwap(row)
    if active_indicators.get("CANDLESTICK", True):
        cs_signal, patterns_found = signal_candlestick(df)
        signals["CANDLESTICK"] = cs_signal

    if not signals:
        return {
            "trade_score": 50.0,
            "confidence": 0.0,
            "signal_label": "No Indicators Selected",
            "breakdown": {},
        }

    # Weighted average signal -> Trade Score
    total_weight = sum(weights.get(k, 1.0) for k in signals)
    weighted_sum = sum(signals[k] * weights.get(k, 1.0) for k in signals)
    combined = weighted_sum / total_weight if total_weight else 0.0
    combined = float(np.clip(combined, -1, 1))
    trade_score = round(50 + 50 * combined, 1)

    # Confidence: agreement ratio among active signals * average strength of agreeing ones
    vals = list(signals.values())
    majority_sign = 1 if combined >= 0 else -1
    agreeing = [v for v in vals if (v >= 0) == (majority_sign >= 0)]
    agreement_ratio = len(agreeing) / len(vals) if vals else 0
    avg_strength = float(np.mean([abs(v) for v in agreeing])) if agreeing else 0.0
    confidence = round(100 * agreement_ratio * (0.4 + 0.6 * avg_strength), 1)
    confidence = float(np.clip(confidence, 0, 100))

    # Signal label
    if trade_score >= 70 and confidence >= 55:
        label = "Strong Buy"
    elif trade_score >= 58:
        label = "Buy"
    elif trade_score <= 30 and confidence >= 55:
        label = "Strong Sell"
    elif trade_score <= 42:
        label = "Sell"
    else:
        label = "Neutral"

    return {
        "trade_score": trade_score,
        "confidence": confidence,
        "signal_label": label,
        "breakdown": signals,
        "patterns": patterns_found,
        "rsi": round(float(row.get("RSI", 0)), 1),
        "adx": round(float(row.get("ADX", 0)), 1),
        "macd_hist": round(float(row.get("MACD_HIST", 0)), 3),
        "vol_ratio": round(float(row.get("VOL_RATIO", 1)), 2),
        "close": round(float(row.get("Close", 0)), 2),
        "extension_atr": _extension_atr(row),
        "vwap": round(float(row.get("VWAP", row.get("Close", 0))), 2),
    }
