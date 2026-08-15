"""
simulate.py
-----------
The trade simulation / backtest engine: transaction-cost estimation and
the walk-forward replay (with trailing stop-loss) used by both Stage 4's
live monitoring and Stage 5's historical backtest.
"""

from data import fetch_symbol_5m_since


# --------------------------------------------------------------------------
# TRANSACTION COST ESTIMATE (Zerodha intraday equity, as of Aug 2026)
# --------------------------------------------------------------------------
def estimate_roundtrip_cost_pct(trade_value: float) -> float:
    """Round-trip (buy+sell) cost as a % of trade value, for a Zerodha
    intraday equity trade. Brokerage: lower of Rs 20 or 0.03% per executed
    order. STT: 0.025% on the sell side. Exchange txn charges: ~0.00297%
    both legs. SEBI: Rs 10/crore both legs. Stamp duty: 0.003% buy side.
    GST: 18% on (brokerage + SEBI + exchange charges).

    This is a planning estimate, not your exact contract note — rates are
    government/exchange-set and can change; treat Kite's own P&L as the
    source of truth and this as a fast approximation for backtesting.
    """
    if trade_value <= 0:
        return 0.0
    brokerage = min(20.0, trade_value * 0.0003) * 2  # buy + sell legs
    stt = trade_value * 0.00025
    exchange_txn = trade_value * 0.0000297 * 2
    sebi = trade_value * 0.000001 * 2
    stamp_duty = trade_value * 0.00003
    gst = 0.18 * (brokerage + sebi + exchange_txn)
    total_cost = brokerage + stt + exchange_txn + sebi + stamp_duty + gst
    return round(total_cost / trade_value * 100, 4)


# --------------------------------------------------------------------------
# TRADE SIMULATION ENGINE
# --------------------------------------------------------------------------
def simulate_trade(
    symbol_ns: str, direction: str, entry_price: float, entry_time,
    sl_pct: float, target_pct: float, trailing_enabled: bool, trailing_pct: float,
    lookback_days: int = 10, same_day_only: bool = True, trade_value: float = 10000.0,
) -> dict:
    """Walk-forward replay of 5-min candles from entry_time to now (or to
    that day's close, if same_day_only). Same-candle ambiguity (both SL and
    target crossed in one bar) resolves as stoploss-hit-first — the
    conservative read, since we only have OHLC, not tick data.

    Trailing logic: once the initial target is reached, the position isn't
    closed — instead the stop-loss arms and ratchets behind the best price
    seen since (peak for Long, trough for Short), only ever tightening,
    never loosening. Exit happens when that trailing line is finally hit.

    trade_value is the assumed rupee turnover per leg, used only to estimate
    round-trip transaction costs (see estimate_roundtrip_cost_pct) so every
    result reports both gross and net P/L.
    """
    cost_pct = estimate_roundtrip_cost_pct(trade_value)

    def finalize(res: dict) -> dict:
        res["Net P/L %"] = round(res["P/L %"] - cost_pct, 3)
        return res

    is_long = direction == "Long"
    result = {
        "Symbol": symbol_ns.replace(".NS", ""),
        "Direction": direction,
        "Entry Price": round(entry_price, 2),
        "Outcome": "No Hit (EOD)",
        "Hit Time": None,
        "Hit Price": None,
        "P/L %": 0.0,
        "Best seen (MFE %)": 0.0,
        "Worst seen (MAE %)": 0.0,
        "Current SL": round(entry_price * (1 - sl_pct / 100) if is_long else entry_price * (1 + sl_pct / 100), 2),
        "Trail Armed": False,
    }

    df = fetch_symbol_5m_since(symbol_ns, entry_time, lookback_days)
    if same_day_only and not df.empty:
        df = df[df.index.date == entry_time.date()]
    if df.empty:
        result["Hit Time"] = "no data since entry" if not same_day_only else "no more bars today"
        return finalize(result)

    sl = entry_price * (1 - sl_pct / 100) if is_long else entry_price * (1 + sl_pct / 100)
    target = entry_price * (1 + target_pct / 100) if is_long else entry_price * (1 - target_pct / 100)
    extreme = entry_price
    mfe = 0.0
    mae = 0.0
    armed = False

    for ts, row in df.iterrows():
        high, low = row["High"], row["Low"]

        fav = (high - entry_price) / entry_price * 100 if is_long else (entry_price - low) / entry_price * 100
        adv = (low - entry_price) / entry_price * 100 if is_long else (entry_price - high) / entry_price * 100
        mfe = max(mfe, fav)
        mae = min(mae, adv)

        sl_hit = (low <= sl) if is_long else (high >= sl)
        if sl_hit:
            pl = (sl - entry_price) / entry_price * 100 if is_long else (entry_price - sl) / entry_price * 100
            result.update({
                "Outcome": "Trailing Stop Hit" if armed else "Stoploss Hit",
                "Hit Time": ts.strftime("%Y-%m-%d %H:%M"),
                "Hit Price": round(sl, 2),
                "P/L %": round(pl, 3),
                "Best seen (MFE %)": round(mfe, 3),
                "Worst seen (MAE %)": round(mae, 3),
                "Current SL": round(sl, 2),
                "Trail Armed": armed,
            })
            return finalize(result)

        if not armed:
            target_hit = (high >= target) if is_long else (low <= target)
            if target_hit:
                if trailing_enabled:
                    armed = True
                    extreme = high if is_long else low
                    sl = extreme * (1 - trailing_pct / 100) if is_long else extreme * (1 + trailing_pct / 100)
                else:
                    result.update({
                        "Outcome": "Target Hit",
                        "Hit Time": ts.strftime("%Y-%m-%d %H:%M"),
                        "Hit Price": round(target, 2),
                        "P/L %": round(target_pct, 3),
                        "Best seen (MFE %)": round(mfe, 3),
                        "Worst seen (MAE %)": round(mae, 3),
                        "Current SL": round(sl, 2),
                        "Trail Armed": False,
                    })
                    return finalize(result)
        else:
            if is_long:
                extreme = max(extreme, high)
                sl = max(sl, extreme * (1 - trailing_pct / 100))
            else:
                extreme = min(extreme, low)
                sl = min(sl, extreme * (1 + trailing_pct / 100))

    last_close = df["Close"].iloc[-1]
    pl = (last_close - entry_price) / entry_price * 100 if is_long else (entry_price - last_close) / entry_price * 100
    result.update({
        "Hit Time": df.index[-1].strftime("%Y-%m-%d %H:%M"),
        "Hit Price": round(float(last_close), 2),
        "P/L %": round(float(pl), 3),
        "Best seen (MFE %)": round(mfe, 3),
        "Worst seen (MAE %)": round(mae, 3),
        "Current SL": round(sl, 2),
        "Trail Armed": armed,
    })
    return finalize(result)
