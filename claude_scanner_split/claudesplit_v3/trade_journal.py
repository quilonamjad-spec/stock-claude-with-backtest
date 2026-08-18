"""
trade_journal.py
-----------------
A persistent, informational trade log: you enter a symbol, direction,
entry time, exit time, and quantity (number of shares — real fills are
whole shares, not an exact rupee amount); this looks up the actual
traded prices at those two timestamps from Yahoo, and calculates P/L
(gross and net of the same transaction-cost model used everywhere else
in the app). Every trade is appended to trade_journal.csv so it survives
across app restarts — replacing the manual day-by-day table you were
keeping by hand.

This module does not feed into the scanner, gate, or ranking in any way
— it's a standalone record of what you actually did, for your own
review.
"""

import os

import pandas as pd

from data import get_price_at, fetch_symbol_5m_since
from simulate import estimate_roundtrip_cost_pct

JOURNAL_PATH = os.path.join(os.path.dirname(__file__), "trade_journal.csv")

COLUMNS = [
    "Date", "Symbol", "Direction", "Entry Time", "Exit Time",
    "Quantity", "Entry Price", "Exit Price", "Amount Invested",
    "Gross P/L %", "Net P/L %", "Net ₹ P/L",
    "Best seen (MFE %)", "Worst seen (MAE %)",
]


def load_journal() -> pd.DataFrame:
    """All trades logged so far, oldest first. Empty (but correctly
    columned) DataFrame if nothing has been logged yet."""
    if os.path.exists(JOURNAL_PATH):
        return pd.read_csv(JOURNAL_PATH)
    return pd.DataFrame(columns=COLUMNS)


def _excursion(symbol_ns: str, entry_time, entry_bar, exit_bar, entry_price: float, is_long: bool):
    """Best/worst % move (gross, unrealized) seen between entry and exit,
    from the real 5-min highs/lows in between — same MFE/MAE idea the
    historical backtest's equity chart uses for its candle wicks. Falls
    back to (0.0, 0.0) if the intermediate bars can't be fetched, so a
    lookup hiccup here never blocks logging the trade itself."""
    try:
        bars = fetch_symbol_5m_since(symbol_ns, entry_time)
        bars = bars[bars.index <= exit_bar]
        if bars.empty:
            return 0.0, 0.0
        if is_long:
            mfe = (bars["High"].max() - entry_price) / entry_price * 100
            mae = (bars["Low"].min() - entry_price) / entry_price * 100
        else:
            mfe = (entry_price - bars["Low"].min()) / entry_price * 100
            mae = (entry_price - bars["High"].max()) / entry_price * 100
        return float(mfe), float(mae)
    except Exception:
        return 0.0, 0.0


def record_trade(symbol: str, direction: str, entry_time, exit_time, quantity: float) -> dict:
    """Looks up real entry/exit prices from Yahoo at the given timestamps,
    computes P/L (from quantity — real fills are whole shares, not a
    fixed rupee amount), appends the trade to the CSV, and returns the
    row as a dict. Raises ValueError if either price can't be found (e.g.
    the timestamp is outside Yahoo's ~60-day 5-min window, or before
    market data exists for that symbol/day)."""
    symbol_ns = f"{symbol.upper().replace('.NS', '')}.NS"

    entry_price, entry_bar = get_price_at(symbol_ns, entry_time)
    if entry_price is None:
        raise ValueError(
            f"No 5-min bar found at/before {entry_time} for {symbol_ns}. "
            "Check the symbol and that the date is within the last ~60 days."
        )
    exit_price, exit_bar = get_price_at(symbol_ns, exit_time)
    if exit_price is None:
        raise ValueError(
            f"No 5-min bar found at/before {exit_time} for {symbol_ns}. "
            "Check the symbol and that the date is within the last ~60 days."
        )

    is_long = direction == "Long"
    amount_invested = quantity * entry_price
    gross_pct = (
        (exit_price - entry_price) / entry_price * 100 if is_long
        else (entry_price - exit_price) / entry_price * 100
    )
    cost_pct = estimate_roundtrip_cost_pct(amount_invested)
    net_pct = gross_pct - cost_pct
    net_rs = round(net_pct / 100 * amount_invested, 2)
    mfe_pct, mae_pct = _excursion(symbol_ns, entry_time, entry_bar, exit_bar, entry_price, is_long)

    row = {
        "Date": entry_bar.strftime("%Y-%m-%d"),
        "Symbol": symbol_ns.replace(".NS", ""),
        "Direction": direction,
        "Entry Time": entry_bar.strftime("%Y-%m-%d %H:%M"),
        "Exit Time": exit_bar.strftime("%Y-%m-%d %H:%M"),
        "Quantity": int(quantity),
        "Entry Price": round(entry_price, 2),
        "Exit Price": round(exit_price, 2),
        "Amount Invested": round(amount_invested, 2),
        "Gross P/L %": round(gross_pct, 3),
        "Net P/L %": round(net_pct, 3),
        "Net ₹ P/L": net_rs,
        "Best seen (MFE %)": round(mfe_pct, 3),
        "Worst seen (MAE %)": round(mae_pct, 3),
    }

    journal = load_journal()
    journal = pd.concat([journal, pd.DataFrame([row])], ignore_index=True)
    journal.to_csv(JOURNAL_PATH, index=False)
    return row


def delete_last_trade() -> bool:
    """Removes the most recently logged trade (e.g. to undo a typo'd
    entry). Returns False if the journal is already empty."""
    journal = load_journal()
    if journal.empty:
        return False
    journal = journal.iloc[:-1]
    journal.to_csv(JOURNAL_PATH, index=False)
    return True


def summary_stats(journal: pd.DataFrame) -> dict:
    """Cumulative stats matching the style of the 4-day manual table this
    replaces: trade count, win rate, and total net ₹."""
    if journal.empty:
        return {"trades": 0, "win_rate": None, "total_net_rs": 0.0}
    trades = len(journal)
    win_rate = (journal["Net P/L %"] > 0).mean() * 100
    total_net_rs = journal["Net ₹ P/L"].sum()
    return {"trades": trades, "win_rate": win_rate, "total_net_rs": round(total_net_rs, 2)}
