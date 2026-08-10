"""
patterns.py
Detects the 20 candlestick patterns from the reference chart:

BULLISH: Hammer, Inverted Hammer, Dragonfly Doji, Bullish Spinning Top,
         Bullish Engulfing, Tweezer Bottom,
         Morning Doji Star, Three White Soldiers, Morning Star, Rising Three

NEUTRAL: Doji

BEARISH: Hanging Man, Shooting Star, Gravestone Doji, Bearish Spinning Top,
         Bearish Engulfing, Tweezer Top,
         Evening Doji Star, Three Black Crows, Evening Star, Falling Three

Each detector looks only at CLOSED candles up to index i (no lookahead).
Thresholds are conservative rules of thumb, not exact textbook definitions —
tune the constants below if you want stricter/looser matching.
"""
from dataclasses import dataclass
from typing import List

# ---- tunable thresholds ----
DOJI_BODY_PCT = 0.10          # body/range below this = doji-like
SMALL_BODY_PCT = 0.30         # body/range below this = "small body"
LONG_WICK_MULT = 2.0          # wick must be >= 2x body to count as "long"
TWEEZER_TOL = 0.15            # % of ATR-like tolerance for matching highs/lows
ENGULF_MIN_RATIO = 1.0        # engulfing body must be >= prior body


@dataclass
class PatternHit:
    name: str
    bias: str          # "bullish" | "bearish" | "neutral"
    strength: str       # "single" | "double" | "triple"
    base_weight: float  # 0-25, used by scoring.py


def _metrics(row) -> dict:
    o, h, l, c = row["open"], row["high"], row["low"], row["close"]
    rng = (h - l) if (h - l) != 0 else 1e-9
    body = abs(c - o)
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    return dict(
        open=o, high=h, low=l, close=c,
        body=body, range=rng,
        upper_wick=upper_wick, lower_wick=lower_wick,
        body_pct=body / rng,
        is_bull=c > o, is_bear=c < o,
    )


def detect_patterns(df, i: int) -> List[PatternHit]:
    """Return all pattern hits found on candle i (uses candles i-2..i, trend at i)."""
    hits: List[PatternHit] = []
    if i < 0 or i >= len(df):
        return hits

    m0 = _metrics(df.iloc[i])
    trend = df["trend"].iloc[i] if "trend" in df.columns else "unknown"
    prior_downtrend = trend == "downtrend"
    prior_uptrend = trend == "uptrend"

    # =========================================================
    # SINGLE CANDLE PATTERNS
    # =========================================================
    small_body = m0["body_pct"] < SMALL_BODY_PCT
    tiny_body = m0["body_pct"] < DOJI_BODY_PCT
    long_lower = m0["lower_wick"] >= LONG_WICK_MULT * max(m0["body"], m0["range"] * 0.02)
    long_upper = m0["upper_wick"] >= LONG_WICK_MULT * max(m0["body"], m0["range"] * 0.02)
    tiny_upper = m0["upper_wick"] < m0["range"] * 0.10
    tiny_lower = m0["lower_wick"] < m0["range"] * 0.10

    # Doji family (very small body)
    if tiny_body:
        if long_lower and tiny_upper:
            hits.append(PatternHit("Dragonfly Doji", "bullish" if prior_downtrend else "neutral", "single", 15))
        elif long_upper and tiny_lower:
            hits.append(PatternHit("Gravestone Doji", "bearish" if prior_uptrend else "neutral", "single", 15))
        else:
            hits.append(PatternHit("Doji", "neutral", "single", 5))

    # Hammer / Hanging Man (small body near top, long lower wick, small upper wick)
    elif small_body and long_lower and m0["upper_wick"] < m0["body"] * 1.5:
        if prior_downtrend:
            hits.append(PatternHit("Hammer", "bullish", "single", 18))
        elif prior_uptrend:
            hits.append(PatternHit("Hanging Man", "bearish", "single", 18))

    # Inverted Hammer / Shooting Star (small body near bottom, long upper wick)
    elif small_body and long_upper and m0["lower_wick"] < m0["body"] * 1.5:
        if prior_downtrend:
            hits.append(PatternHit("Inverted Hammer", "bullish", "single", 14))
        elif prior_uptrend:
            hits.append(PatternHit("Shooting Star", "bearish", "single", 18))

    # Spinning tops (small body, roughly balanced wicks on both sides)
    elif small_body and m0["upper_wick"] > m0["body"] * 0.5 and m0["lower_wick"] > m0["body"] * 0.5:
        if m0["is_bull"]:
            hits.append(PatternHit("Bullish Spinning Top", "bullish", "single", 8))
        else:
            hits.append(PatternHit("Bearish Spinning Top", "bearish", "single", 8))

    # =========================================================
    # DOUBLE CANDLE PATTERNS
    # =========================================================
    if i >= 1:
        m1 = _metrics(df.iloc[i - 1])  # previous candle

        # Bullish / Bearish Engulfing
        if m1["is_bear"] and m0["is_bull"] and m0["open"] <= m1["close"] and m0["close"] >= m1["open"] \
                and m0["body"] >= m1["body"] * ENGULF_MIN_RATIO:
            hits.append(PatternHit("Bullish Engulfing", "bullish", "double", 20))

        if m1["is_bull"] and m0["is_bear"] and m0["open"] >= m1["close"] and m0["close"] <= m1["open"] \
                and m0["body"] >= m1["body"] * ENGULF_MIN_RATIO:
            hits.append(PatternHit("Bearish Engulfing", "bearish", "double", 20))

        # Tweezer Bottom / Top (matching lows or highs across 2 candles)
        tol = m0["range"] * TWEEZER_TOL + m1["range"] * TWEEZER_TOL
        if abs(m0["low"] - m1["low"]) <= tol and prior_downtrend and m1["is_bear"] and m0["is_bull"]:
            hits.append(PatternHit("Tweezer Bottom", "bullish", "double", 14))
        if abs(m0["high"] - m1["high"]) <= tol and prior_uptrend and m1["is_bull"] and m0["is_bear"]:
            hits.append(PatternHit("Tweezer Top", "bearish", "double", 14))

    # =========================================================
    # TRIPLE CANDLE PATTERNS
    # =========================================================
    if i >= 2:
        m1 = _metrics(df.iloc[i - 1])
        m2 = _metrics(df.iloc[i - 2])  # oldest of the three
        trend2 = df["trend"].iloc[i - 2] if "trend" in df.columns else "unknown"

        # Morning Star / Morning Doji Star: bearish, small-body gap-down star, bullish closing into candle1 body
        if m2["is_bear"] and m2["body_pct"] > SMALL_BODY_PCT \
                and m1["body_pct"] < SMALL_BODY_PCT and max(m1["open"], m1["close"]) < m2["close"] \
                and m0["is_bull"] and m0["close"] > (m2["open"] + m2["close"]) / 2 \
                and trend2 == "downtrend":
            name = "Morning Doji Star" if m1["body_pct"] < DOJI_BODY_PCT else "Morning Star"
            hits.append(PatternHit(name, "bullish", "triple", 22))

        # Evening Star / Evening Doji Star: bullish, small-body gap-up star, bearish closing into candle1 body
        if m2["is_bull"] and m2["body_pct"] > SMALL_BODY_PCT \
                and m1["body_pct"] < SMALL_BODY_PCT and min(m1["open"], m1["close"]) > m2["close"] \
                and m0["is_bear"] and m0["close"] < (m2["open"] + m2["close"]) / 2 \
                and trend2 == "uptrend":
            name = "Evening Doji Star" if m1["body_pct"] < DOJI_BODY_PCT else "Evening Star"
            hits.append(PatternHit(name, "bearish", "triple", 22))

        # Three White Soldiers: 3 consecutive bullish candles, each closing higher, small wicks
        if m2["is_bull"] and m1["is_bull"] and m0["is_bull"] \
                and m1["close"] > m2["close"] and m0["close"] > m1["close"] \
                and m1["open"] > m2["open"] and m0["open"] > m1["open"] \
                and m0["body_pct"] > 0.5 and m1["body_pct"] > 0.5 and m2["body_pct"] > 0.5:
            hits.append(PatternHit("Three White Soldiers", "bullish", "triple", 24))

        # Three Black Crows: 3 consecutive bearish candles, each closing lower
        if m2["is_bear"] and m1["is_bear"] and m0["is_bear"] \
                and m1["close"] < m2["close"] and m0["close"] < m1["close"] \
                and m1["open"] < m2["open"] and m0["open"] < m1["open"] \
                and m0["body_pct"] > 0.5 and m1["body_pct"] > 0.5 and m2["body_pct"] > 0.5:
            hits.append(PatternHit("Three Black Crows", "bearish", "triple", 24))

        # Rising Three Methods: long bullish, 3 small bearish inside its range (using i-3..i, need 5 candles)
        if i >= 4:
            m4 = _metrics(df.iloc[i - 4])  # long bullish candle
            mids = [_metrics(df.iloc[j]) for j in range(i - 3, i)]  # 3 small candles
            if m4["is_bull"] and m4["body_pct"] > 0.5 \
                    and all(mm["is_bear"] and mm["low"] > m4["low"] and mm["high"] < m4["high"] for mm in mids) \
                    and m0["is_bull"] and m0["close"] > m4["close"]:
                hits.append(PatternHit("Rising Three Methods", "bullish", "triple", 16))

            if m4["is_bear"] and m4["body_pct"] > 0.5 \
                    and all(mm["is_bull"] and mm["high"] < m4["high"] and mm["low"] > m4["low"] for mm in mids) \
                    and m0["is_bear"] and m0["close"] < m4["close"]:
                hits.append(PatternHit("Falling Three Methods", "bearish", "triple", 16))

    return hits
