"""
NSE 500 Bull/Bear Scanner + Ranking
------------------------------------
Stage 1 (Scan): pulls 5-min OHLCV data from Yahoo Finance for the NSE 500
universe, evaluates the bull/bear condition set as of a chosen date & time,
and shows every stock that passed either side.

Stage 2 (Rank): takes the Stage-1 results and scores/sorts them by
conviction strength (how strongly each condition was cleared), using
user-adjustable weights.

Run locally:
    pip install -r requirements.txt
    streamlit run app.py
"""

import io
from datetime import datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
import streamlit as st
import yfinance as yf

# --------------------------------------------------------------------------
# CONFIG
# --------------------------------------------------------------------------
st.set_page_config(page_title="NSE 500 Bull/Bear Scanner", layout="wide")

NSE500_URL = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
LOCAL_FALLBACK = "nifty500_fallback.csv"  # optional: ship a cached copy in your repo
IST_TZ = "Asia/Kolkata"
IST = ZoneInfo(IST_TZ)


def now_ist() -> datetime:
    """Server-timezone-proof 'now'. Never use bare datetime.now() for
    anything market-related — it returns the HOST's local clock (often
    UTC on cloud servers), not IST, and silently produces a wrong as_of."""
    return datetime.now(IST)

DEFAULT_WEIGHTS = {
    "Trend_strength": 0.20,       # trend      -> EMA9 vs EMA21 separation
    "Breakout_strength": 0.20,    # momentum   -> distance past 10-bar high/low
    "Volatility_strength": 0.15,  # volatility -> ATR(14) expansion vs its own avg
    "Volume_strength": 0.20,      # volume     -> volume vs 20-period avg
    "MoneyFlow_strength": 0.10,   # volume     -> CMF magnitude
    "VWAP_strength": 0.15,        # trend      -> distance from VWAP
}


# --------------------------------------------------------------------------
# UNIVERSE
# --------------------------------------------------------------------------
@st.cache_data(ttl=60 * 60 * 24)
def get_nse500_symbols() -> list[str]:
    """Fetch the current NSE 500 constituent list. Falls back to a local
    CSV (columns must include 'Symbol') if NSE's archive is unreachable."""
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(NSE500_URL, headers=headers, timeout=10)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text))
        symbols = df["Symbol"].astype(str).str.strip().tolist()
    except Exception:
        try:
            df = pd.read_csv(LOCAL_FALLBACK)
            symbols = df["Symbol"].astype(str).str.strip().tolist()
        except Exception:
            st.error(
                "Could not fetch the NSE 500 list from NSE's archive, and no "
                f"local fallback ('{LOCAL_FALLBACK}') was found. Add one to "
                "the repo root with a 'Symbol' column."
            )
            return []
    return [f"{s}.NS" for s in symbols]


# --------------------------------------------------------------------------
# SECTOR INDICES (for the stock-vs-sector alignment column)
# --------------------------------------------------------------------------
# Verified Yahoo Finance tickers as of Aug 2026 — a few gotchas worth noting:
#   - NIFTY FIN SERVICE uses a plain .NS ticker, NOT the ^CNX-style prefix
#     like most others (^CNXFIN is a DIFFERENT, narrower index — the 25/50
#     variant — don't swap it in).
#   - NIFTY BANK (^NSEBANK, 12 large banks) is NOT the same universe as
#     NIFTY PRIVATE BANK or NIFTY PSU BANK — a stock like BANDHANBNK sits
#     in Private Bank, not the main Bank index, so both are listed
#     separately below rather than folded into BANK.
#   - CHEMICALS' Yahoo ticker (NIFTY_CHEMICALS.NS) follows the same
#     "NIFTY_<NAME>.NS" pattern as Healthcare/Oil&Gas/Consumer Durables but
#     wasn't directly confirmed on a live quote page — if it turns out to
#     be wrong, that one sector just won't populate (fails gracefully,
#     falls back to NIFTY 50) rather than breaking anything.
SECTOR_INDICES = {
    "BANK":         {"csv": "https://archives.nseindia.com/content/indices/ind_niftybanklist.csv",            "yahoo": "^NSEBANK",             "label": "NIFTY BANK"},
    "PVT_BANK":     {"csv": "https://archives.nseindia.com/content/indices/ind_niftyprivatebanklist.csv",     "yahoo": "NIFTY_PVT_BANK.NS",     "label": "NIFTY PRIVATE BANK"},
    "PSU_BANK":     {"csv": "https://archives.nseindia.com/content/indices/ind_niftypsubanklist.csv",         "yahoo": "^CNXPSUBANK",           "label": "NIFTY PSU BANK"},
    "AUTO":         {"csv": "https://archives.nseindia.com/content/indices/ind_niftyautolist.csv",            "yahoo": "^CNXAUTO",              "label": "NIFTY AUTO"},
    "IT":           {"csv": "https://archives.nseindia.com/content/indices/ind_niftyitlist.csv",              "yahoo": "^CNXIT",                "label": "NIFTY IT"},
    "PHARMA":       {"csv": "https://archives.nseindia.com/content/indices/ind_niftypharmalist.csv",          "yahoo": "^CNXPHARMA",            "label": "NIFTY PHARMA"},
    "HEALTHCARE":   {"csv": "https://archives.nseindia.com/content/indices/ind_niftyhealthcarelist.csv",      "yahoo": "NIFTY_HEALTHCARE.NS",   "label": "NIFTY HEALTHCARE"},
    "FMCG":         {"csv": "https://archives.nseindia.com/content/indices/ind_niftyfmcglist.csv",            "yahoo": "^CNXFMCG",              "label": "NIFTY FMCG"},
    "CONSR_DURBL":  {"csv": "https://archives.nseindia.com/content/indices/ind_niftyconsumerdurableslist.csv","yahoo": "NIFTY_CONSR_DURBL.NS",  "label": "NIFTY CONSUMER DURABLES"},
    "METAL":        {"csv": "https://archives.nseindia.com/content/indices/ind_niftymetallist.csv",           "yahoo": "^CNXMETAL",             "label": "NIFTY METAL"},
    "CHEMICALS":    {"csv": "https://archives.nseindia.com/content/indices/ind_niftychemicalslist.csv",       "yahoo": "NIFTY_CHEMICALS.NS",    "label": "NIFTY CHEMICALS"},
    "MEDIA":        {"csv": "https://archives.nseindia.com/content/indices/ind_niftymedialist.csv",           "yahoo": "^CNXMEDIA",             "label": "NIFTY MEDIA"},
    "REALTY":       {"csv": "https://archives.nseindia.com/content/indices/ind_niftyrealtylist.csv",          "yahoo": "^CNXREALTY",            "label": "NIFTY REALTY"},
    "OIL_GAS":      {"csv": "https://archives.nseindia.com/content/indices/ind_niftyoilgaslist.csv",          "yahoo": "NIFTY_OIL_AND_GAS.NS",  "label": "NIFTY OIL AND GAS"},
    "ENERGY":       {"csv": "https://archives.nseindia.com/content/indices/ind_niftyenergylist.csv",          "yahoo": "^CNXENERGY",            "label": "NIFTY ENERGY"},
    "FIN_SERVICE":  {"csv": "https://archives.nseindia.com/content/indices/ind_niftyfinservicelist.csv",      "yahoo": "NIFTY_FIN_SERVICE.NS",  "label": "NIFTY FIN SERVICE"},
}
DEFAULT_SECTOR = "NIFTY50"
DEFAULT_INDEX_YAHOO = "^NSEI"
DEFAULT_INDEX_LABEL = "NIFTY 50"

# More specific sectors first, so a stock that appears in more than one list
# (e.g. a bank in both BANK and the broader FIN_SERVICE list) keeps its most
# specific mapping — first match wins below.
SECTOR_PRIORITY = [
    "BANK", "PVT_BANK", "PSU_BANK", "AUTO", "IT", "PHARMA", "HEALTHCARE",
    "FMCG", "CONSR_DURBL", "METAL", "CHEMICALS", "MEDIA", "REALTY",
    "OIL_GAS", "ENERGY", "FIN_SERVICE",
]


@st.cache_data(ttl=60 * 60 * 24)
def get_symbol_sector_map() -> dict:
    """symbol -> sector key (e.g. 'RELIANCE' -> 'ENERGY'). Stocks not found
    in any sectoral list fall back to NIFTY 50 as the comparison index —
    sectoral lists that fail to fetch are just skipped, not fatal."""
    mapping: dict = {}
    headers = {"User-Agent": "Mozilla/5.0"}
    for sector in SECTOR_PRIORITY:
        try:
            resp = requests.get(SECTOR_INDICES[sector]["csv"], headers=headers, timeout=10)
            resp.raise_for_status()
            df = pd.read_csv(io.StringIO(resp.text))
            for sym in df["Symbol"].astype(str).str.strip():
                mapping.setdefault(sym, sector)
        except Exception:
            continue
    return mapping


@st.cache_data(ttl=60 * 10, show_spinner=False)
def fetch_index_batch(period: str, interval: str) -> pd.DataFrame:
    tickers = [cfg["yahoo"] for cfg in SECTOR_INDICES.values()] + [DEFAULT_INDEX_YAHOO]
    return yf.download(
        tickers=tickers, period=period, interval=interval,
        group_by="ticker", threads=True, progress=False, auto_adjust=False,
    )


def compute_index_pct_changes(as_of, lookback_days: int) -> dict:
    """Returns {sector_key: pct_change_from_day_open} for every sector index
    plus 'NIFTY50', evaluated at the same as_of timestamp as the stock scan."""
    yahoo_to_sector = {cfg["yahoo"]: name for name, cfg in SECTOR_INDICES.items()}
    yahoo_to_sector[DEFAULT_INDEX_YAHOO] = DEFAULT_SECTOR
    tickers = list(yahoo_to_sector.keys())

    changes = {}
    try:
        batch = fetch_index_batch(period=f"{lookback_days}d", interval="5m")
    except Exception:
        return changes

    for tkr in tickers:
        try:
            idf = batch[tkr].dropna(how="all") if len(tickers) > 1 else batch
            if idf.empty:
                continue
            if idf.index.tz is None:
                idf.index = idf.index.tz_localize(IST_TZ)
            else:
                idf.index = idf.index.tz_convert(IST_TZ)
            data = idf[idf.index <= as_of]
            if data.empty:
                continue
            last = data.iloc[-1]
            same_day = data[data.index.date == last.name.date()]
            day_open = same_day["Open"].iloc[0]
            pct = round((last["Close"] - day_open) / day_open * 100, 2)
            changes[yahoo_to_sector[tkr]] = pct
        except Exception:
            continue
    return changes


# --------------------------------------------------------------------------
# DATA FETCH (batched, cached per session)
# --------------------------------------------------------------------------
@st.cache_data(ttl=60 * 10, show_spinner=False)
def fetch_batch(tickers: tuple, period: str, interval: str) -> pd.DataFrame:
    """Batch download via yfinance. Returns a MultiIndex-columned DataFrame
    (level 0 = ticker) so we only hit Yahoo once per batch instead of once
    per stock."""
    return yf.download(
        tickers=list(tickers),
        period=period,
        interval=interval,
        group_by="ticker",
        threads=True,
        progress=False,
        auto_adjust=False,
    )


def chunk(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i : i + size]


# --------------------------------------------------------------------------
# INDICATORS
# --------------------------------------------------------------------------
def compute_vwap(df: pd.DataFrame) -> pd.Series:
    d = df.copy()
    day = d.index.date
    typical = (d["High"] + d["Low"] + d["Close"]) / 3
    pv = typical * d["Volume"]
    cum_pv = pv.groupby(day).cumsum()
    cum_vol = d["Volume"].groupby(day).cumsum()
    return cum_pv / cum_vol.replace(0, np.nan)


def compute_cmf(df: pd.DataFrame, period: int = 20) -> pd.Series:
    rng = (df["High"] - df["Low"]).replace(0, np.nan)
    mfm = ((df["Close"] - df["Low"]) - (df["High"] - df["Close"])) / rng
    mfv = mfm * df["Volume"]
    return mfv.rolling(period).sum() / df["Volume"].rolling(period).sum()


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["Close"].shift(1)
    tr = pd.concat(
        [
            df["High"] - df["Low"],
            (df["High"] - prev_close).abs(),
            (df["Low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean()


def compute_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    delta = df["Close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def compute_consecutive_bars(df: pd.DataFrame) -> pd.Series:
    """Signed count of consecutive same-direction closes: +3 means 3 straight
    up-closes, -5 means 5 straight down-closes. Used to catch blow-off runs."""
    direction = np.sign(df["Close"].diff()).fillna(0)
    group = (direction != direction.shift()).cumsum()
    return direction * direction.groupby(group).cumcount().add(1)


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["VWAP"] = compute_vwap(d)
    d["SMA_Vol20"] = d["Volume"].rolling(20).mean()
    d["EMA9"] = d["Close"].ewm(span=9, adjust=False).mean()
    d["EMA21"] = d["Close"].ewm(span=21, adjust=False).mean()
    d["High10"] = d["High"].shift(1).rolling(10).max()
    d["Low10"] = d["Low"].shift(1).rolling(10).min()
    d["CMF20"] = compute_cmf(d, 20)
    d["ATR14"] = compute_atr(d, 14)
    d["ATR14_avg20"] = d["ATR14"].rolling(20).mean()
    d["RSI14"] = compute_rsi(d, 14)
    d["EMA21_slope"] = d["EMA21"] - d["EMA21"].shift(5)
    d["Extension_ATR"] = (d["Close"] - d["EMA9"]) / d["ATR14"]
    d["Consecutive_bars"] = compute_consecutive_bars(d)
    return d


def evaluate_row(row: pd.Series):
    bull = (
        row["Close"] > row["VWAP"]
        and row["Volume"] > row["SMA_Vol20"]
        and row["EMA9"] > row["EMA21"]
        and row["Close"] > row["High10"]
        and row["CMF20"] > 0
    )
    bear = (
        row["Close"] < row["VWAP"]
        and row["Volume"] > row["SMA_Vol20"]
        and row["EMA9"] < row["EMA21"]
        and row["Close"] < row["Low10"]
        and row["CMF20"] < 0
    )
    if bull:
        return "Bull"
    if bear:
        return "Bear"
    return None


def build_result(symbol: str, df: pd.DataFrame, as_of):
    """Evaluate conditions on the last bar at/ before `as_of`. Returns a
    dict of raw + strength metrics, or None if no condition set passed."""
    data = df[df.index <= as_of]
    if len(data) < 25:  # need enough bars for the 20/21-period indicators
        return None

    data = compute_indicators(data)
    last = data.iloc[-1]
    required = [
        "VWAP", "SMA_Vol20", "EMA9", "EMA21", "High10", "Low10", "CMF20",
        "ATR14", "ATR14_avg20", "RSI14", "EMA21_slope", "Extension_ATR",
    ]
    if last[required].isna().any():
        return None

    phase = evaluate_row(last)
    if phase is None:
        return None

    same_day = data[data.index.date == last.name.date()]
    day_open = same_day["Open"].iloc[0]
    pct_change = (last["Close"] - day_open) / day_open * 100

    vwap_strength = abs((last["Close"] - last["VWAP"]) / last["VWAP"] * 100)
    volume_strength = last["Volume"] / last["SMA_Vol20"] if last["SMA_Vol20"] else np.nan
    trend_strength = abs((last["EMA9"] - last["EMA21"]) / last["EMA21"] * 100)
    if phase == "Bull":
        breakout_strength = (last["Close"] - last["High10"]) / last["High10"] * 100
    else:
        breakout_strength = (last["Low10"] - last["Close"]) / last["Low10"] * 100
    moneyflow_strength = abs(last["CMF20"])
    # Volatility expansion: current ATR vs its own 20-bar average. >1 means
    # volatility is expanding (favorable for breakout follow-through).
    volatility_strength = (
        last["ATR14"] / last["ATR14_avg20"] if last["ATR14_avg20"] else np.nan
    )

    return {
        "Symbol": symbol.replace(".NS", ""),
        "Phase": phase,
        "% Change": round(pct_change, 2),
        "LTP": round(last["Close"], 2),
        "Bar Time": last.name.strftime("%Y-%m-%d %H:%M"),
        "Trend_strength": trend_strength,
        "Breakout_strength": max(breakout_strength, 0),
        "Volatility_strength": volatility_strength,
        "Volume_strength": volume_strength,
        "MoneyFlow_strength": moneyflow_strength,
        "VWAP_strength": vwap_strength,
        # Quality-gate inputs (not part of the rank score itself)
        "RSI14": round(last["RSI14"], 1),
        "EMA21_slope": last["EMA21_slope"],
        "Extension_ATR": last["Extension_ATR"],
        "Consecutive_bars": int(last["Consecutive_bars"]),
    }


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


# --------------------------------------------------------------------------
# SCAN PIPELINE (shared by Stage 1's live scan and Stage 5's backtest)
# --------------------------------------------------------------------------
def run_scan_pipeline(
    symbols: list, as_of, lookback_days: int, batch_size: int,
    progress_prefix: str = "", show_progress: bool = True,
) -> pd.DataFrame:
    """Batched fetch -> per-symbol condition evaluation -> sector-index
    alignment, for a given symbol universe and as_of timestamp. Returns an
    empty DataFrame if nothing matched. This is the exact same logic Stage 1
    uses, factored out so Stage 5's multi-day backtest can call it without
    duplicating the fetch/evaluate loop."""
    period = f"{lookback_days}d"
    results = []
    progress = st.progress(0.0, text=f"{progress_prefix}Downloading & scanning...") if show_progress else None
    total_batches = max(1, (len(symbols) + batch_size - 1) // batch_size)

    for i, group in enumerate(chunk(symbols, batch_size)):
        try:
            batch_df = fetch_batch(tuple(group), period=period, interval="5m")
        except Exception:
            if progress:
                progress.progress((i + 1) / total_batches)
            continue

        for sym in group:
            try:
                if len(group) == 1:
                    sdf = batch_df
                else:
                    if sym not in batch_df.columns.get_level_values(0):
                        continue
                    sdf = batch_df[sym].dropna(how="all")
                if sdf.empty:
                    continue
                if sdf.index.tz is None:
                    sdf.index = sdf.index.tz_localize(IST_TZ)
                else:
                    sdf.index = sdf.index.tz_convert(IST_TZ)
                res = build_result(sym, sdf, as_of)
                if res:
                    results.append(res)
            except Exception:
                continue

        if progress:
            progress.progress((i + 1) / total_batches, text=f"{progress_prefix}Scanned batch {i + 1}/{total_batches}")

    if progress:
        progress.empty()

    if not results:
        return pd.DataFrame()

    sector_map = get_symbol_sector_map()
    index_changes = compute_index_pct_changes(as_of, lookback_days)
    for r in results:
        sector = sector_map.get(r["Symbol"], DEFAULT_SECTOR)
        idx_label = SECTOR_INDICES.get(sector, {}).get("label", DEFAULT_INDEX_LABEL)
        idx_pct = index_changes.get(sector, index_changes.get(DEFAULT_SECTOR))
        r["Index"] = idx_label
        r["Index % Chg"] = idx_pct
        r["Aligned"] = (
            None if idx_pct is None
            else ("✅" if (r["% Change"] >= 0) == (idx_pct >= 0) else "❌")
        )

    return pd.DataFrame(results)


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
# UI
# --------------------------------------------------------------------------
st.title("NSE 500 Bull / Bear Scanner")
st.caption(
    "Stage 1 filters the NSE 500 universe on Yahoo Finance 5-minute data. "
    "Stage 2 ranks whatever Stage 1 returns by conviction strength."
)

with st.sidebar:
    st.header("Scan settings")

    if st.button("🔄 Use current date/time"):
        st.session_state["scan_date_input"] = now_ist().date()
        st.session_state["scan_time_input"] = now_ist().time().replace(second=0, microsecond=0)
        st.rerun()

    scan_date = st.date_input("Date", value=now_ist().date(), key="scan_date_input")
    scan_time = st.time_input(
        "Time (as-of)", value=now_ist().time().replace(second=0, microsecond=0), key="scan_time_input"
    )
    as_of = pd.Timestamp.combine(scan_date, scan_time).tz_localize(IST_TZ)
    st.caption(f"Evaluating as of: {as_of}")

    lookback_days = st.slider(
        "Lookback window (days of 5-min history to pull)",
        min_value=5, max_value=59, value=15,
        help="Yahoo only keeps 5-minute candles for ~60 days. Needs to be "
             "large enough to reach back to the selected date.",
    )

    test_mode = st.checkbox("Test mode (first 60 stocks only — faster)", value=True)
    batch_size = st.slider("Download batch size", 20, 100, 50)

    if st.button("🗑️ Clear data cache"):
        fetch_batch.clear()
        st.success("Cache cleared — next scan will pull fresh data from Yahoo.")

    st.divider()
    st.header("Quality gate")
    st.caption("Trend, structure & momentum checks — run before ranking to filter out exhausted/parabolic setups.")
    gate_enabled = st.checkbox("Enable quality gate", value=True)
    st.caption("Trend check uses EMA21's slope over the last 5 bars (fixed at scan time).")
    max_extension_atr = st.slider("Structure check: max extension from EMA9 (in ATRs)", 1.0, 5.0, 2.5, 0.25)
    max_consecutive_bars = st.slider("Structure check: max consecutive same-direction bars", 3, 12, 6)
    rsi_bull_min, rsi_bull_max = st.slider(
        "Momentum check: healthy RSI band (Bull; mirrored for Bear)",
        0, 100, (50, 78),
    )
    gate_params = {
        "max_extension_atr": max_extension_atr,
        "max_consecutive_bars": max_consecutive_bars,
        "rsi_bull_min": rsi_bull_min,
        "rsi_bull_max": rsi_bull_max,
    }

    st.divider()
    st.header("Ranking weights")
    st.caption("Used in Stage 2. Any relative scale works — they're normalized to sum to 1.")
    st.caption("Trend")
    w_trend = st.slider("EMA9/EMA21 separation", 0.0, 1.0, DEFAULT_WEIGHTS["Trend_strength"])
    w_vwap = st.slider("VWAP distance", 0.0, 1.0, DEFAULT_WEIGHTS["VWAP_strength"])
    st.caption("Momentum")
    w_break = st.slider("Breakout distance", 0.0, 1.0, DEFAULT_WEIGHTS["Breakout_strength"])
    st.caption("Volatility")
    w_atr = st.slider("ATR expansion", 0.0, 1.0, DEFAULT_WEIGHTS["Volatility_strength"])
    st.caption("Volume")
    w_vol = st.slider("Volume surge", 0.0, 1.0, DEFAULT_WEIGHTS["Volume_strength"])
    w_cmf = st.slider("Money flow (CMF)", 0.0, 1.0, DEFAULT_WEIGHTS["MoneyFlow_strength"])
    weights = {
        "Trend_strength": w_trend,
        "Breakout_strength": w_break,
        "Volatility_strength": w_atr,
        "Volume_strength": w_vol,
        "MoneyFlow_strength": w_cmf,
        "VWAP_strength": w_vwap,
    }

if "scan_df" not in st.session_state:
    st.session_state.scan_df = None
if "ranked_df" not in st.session_state:
    st.session_state.ranked_df = None
if "gate_rejected" not in st.session_state:
    st.session_state.gate_rejected = None
if "scan_as_of" not in st.session_state:
    st.session_state.scan_as_of = None

col1, col2 = st.columns([1, 1])
run_scan = col1.button("🔍 Run Scan", type="primary", use_container_width=True)
run_rank = col2.button("🏆 Rank Results", use_container_width=True, disabled=st.session_state.scan_df is None)

with st.expander("🔧 Data diagnostics — check what Yahoo actually has right now"):
    st.caption(
        "Bypasses the scan entirely: pulls the most recent 5-min bars for one "
        "symbol straight from Yahoo, so you can see whether today's data exists "
        "at the source before blaming the app logic."
    )
    diag_symbol = st.text_input("Symbol (no .NS needed)", value="RELIANCE", key="diag_symbol")
    if st.button("Check latest data"):
        try:
            raw = yf.download(f"{diag_symbol.upper()}.NS", period="2d", interval="5m", progress=False)
            if raw.empty:
                st.error("Yahoo returned no data at all for this symbol/period.")
            else:
                st.write(f"Query run at: {now_ist()} (IST)")
                st.write(f"Latest bar in the response: **{raw.index[-1]}**")
                st.dataframe(raw.tail(5))
        except Exception as e:
            st.error(f"Fetch failed: {e}")

# ---- STAGE 1 ----
if run_scan:
    symbols = get_nse500_symbols()
    if test_mode:
        symbols = symbols[:60]

    if not symbols:
        st.stop()

    scan_result_df = run_scan_pipeline(symbols, as_of, lookback_days, batch_size)

    if not scan_result_df.empty:
        st.session_state.scan_df = scan_result_df
        st.session_state.scan_as_of = as_of
        st.session_state.ranked_df = None
        st.success(f"Scan complete — {len(scan_result_df)} stock(s) matched as of {as_of}.")
    else:
        st.session_state.scan_df = pd.DataFrame()
        st.warning("No stocks matched either condition set for the selected date/time.")

# ---- STAGE 2 ----
if run_rank and st.session_state.scan_df is not None and not st.session_state.scan_df.empty:
    base_df = st.session_state.scan_df
    if gate_enabled:
        passed_df, rejected_df = quality_gate(base_df, gate_params)
        st.session_state.gate_rejected = rejected_df
        if passed_df.empty:
            st.warning(
                f"Quality gate rejected all {len(base_df)} Stage-1 result(s) "
                "(exhaustion/weak-trend filters). Loosen the thresholds in "
                "the sidebar, or disable the gate, and re-run ranking."
            )
            st.session_state.ranked_df = None
        else:
            st.session_state.ranked_df = rank_results(passed_df, weights)
            st.info(
                f"Quality gate: {len(passed_df)} of {len(base_df)} passed "
                f"(trend/structure/momentum) and were ranked."
            )
    else:
        st.session_state.gate_rejected = None
        st.session_state.ranked_df = rank_results(base_df, weights)

# ---- DISPLAY ----
if st.session_state.scan_df is not None and not st.session_state.scan_df.empty:
    st.subheader("Stage 1 — Scan results")
    scanned_at = st.session_state.get("scan_as_of")
    if scanned_at is not None:
        if scanned_at.date() == now_ist().date():
            st.caption(f"Scanned as of: {scanned_at} (today)")
        else:
            st.error(
                f"⚠️ This scan was run as of {scanned_at} — that's NOT today. "
                "Click '🔄 Use current date/time' in the sidebar and re-run the scan."
            )
    display_cols = ["Symbol", "Phase", "% Change", "LTP", "Index", "Index % Chg", "Aligned", "Bar Time"]
    st.dataframe(
        st.session_state.scan_df[display_cols].reset_index(drop=True),
        use_container_width=True,
    )

if st.session_state.ranked_df is not None:
    st.subheader("Stage 2 — Ranked results (post quality-gate)")
    rank_cols = [
        "Rank", "Symbol", "Phase", "% Change", "LTP", "Rank Score",
        "Index", "Index % Chg", "Aligned",
        "RSI14", "Extension_ATR", "Consecutive_bars", "Bar Time",
    ]
    bull_tab, bear_tab = st.tabs(["Bull phase", "Bear phase"])
    with bull_tab:
        b = st.session_state.ranked_df[st.session_state.ranked_df["Phase"] == "Bull"]
        st.dataframe(b[rank_cols].reset_index(drop=True), use_container_width=True)
    with bear_tab:
        s = st.session_state.ranked_df[st.session_state.ranked_df["Phase"] == "Bear"]
        st.dataframe(s[rank_cols].reset_index(drop=True), use_container_width=True)

    csv = st.session_state.ranked_df[rank_cols].to_csv(index=False).encode("utf-8")
    st.download_button("Download ranked results (CSV)", csv, "ranked_results.csv", "text/csv")

    if st.session_state.gate_rejected is not None and not st.session_state.gate_rejected.empty:
        with st.expander(f"See {len(st.session_state.gate_rejected)} stock(s) rejected by the quality gate"):
            reject_cols = [
                "Symbol", "Phase", "% Change", "LTP",
                "Trend OK", "Structure OK", "Momentum OK",
                "RSI14", "Extension_ATR", "Consecutive_bars",
            ]
            st.dataframe(
                st.session_state.gate_rejected[reject_cols].reset_index(drop=True),
                use_container_width=True,
            )

# --------------------------------------------------------------------------
# STAGE 3 — TRADE PANEL (Zerodha Kite) — parked for now, code lives in
# trade_panel.py. Flip ENABLE_STAGE_3 to True once you're ready to test it.
# --------------------------------------------------------------------------
ENABLE_STAGE_3 = False
if ENABLE_STAGE_3:
    from trade_panel import render_trade_panel
    render_trade_panel()


# --------------------------------------------------------------------------
# STAGE 4 — TRADE SIMULATION / MONITORING (with trailing stop-loss)
# --------------------------------------------------------------------------
def fetch_symbol_5m_since(symbol_ns: str, start_time, lookback_days: int = 10) -> pd.DataFrame:
    """Single-ticker fetch, IST-normalized, trimmed to bars at/after start_time."""
    df = yf.download(symbol_ns, period=f"{lookback_days}d", interval="5m", progress=False, auto_adjust=False)
    if df.empty:
        return df
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    if df.index.tz is None:
        df.index = df.index.tz_localize(IST_TZ)
    else:
        df.index = df.index.tz_convert(IST_TZ)
    return df[df.index >= start_time]


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


st.divider()
st.header("Stage 4 — Trade Simulation / Monitoring")
st.caption(
    "Pick stocks, tag Long/Short, set stoploss/target %, then refresh through the day "
    "to see if either was hit. Same-candle ambiguity resolves as stoploss-hit-first "
    "(worst case). Once the target is hit, the stoploss trails behind price instead "
    "of exiting — the position only closes when the trailing line is hit."
)

if "simulation_list" not in st.session_state:
    st.session_state.simulation_list = []
if "simulation_results" not in st.session_state:
    st.session_state.simulation_results = None

st.subheader("1. Build your watchlist")
add_col1, add_col2 = st.columns(2)

with add_col1:
    st.markdown("**From ranked results**")
    if st.session_state.ranked_df is not None and not st.session_state.ranked_df.empty:
        rdf = st.session_state.ranked_df
        options = (rdf["Symbol"] + " — " + rdf["Phase"]).tolist()
        picked = st.multiselect("Tick stocks to add", options, key="sim_pick_ranked")
        if st.button("Add ticked to simulation"):
            existing = {e["Symbol"] for e in st.session_state.simulation_list}
            for choice in picked:
                sym, phase = choice.split(" — ")
                if sym in existing:
                    continue
                row = rdf[rdf["Symbol"] == sym].iloc[0]
                st.session_state.simulation_list.append({
                    "Symbol": sym,
                    "Direction": "Long" if phase == "Bull" else "Short",
                    "Entry Price": float(row["LTP"]),
                    "Entry Time": st.session_state.scan_as_of,
                })
            st.rerun()
    else:
        st.caption("No ranked results yet — run Stage 1/2, or add a custom symbol instead.")

with add_col2:
    st.markdown("**Custom symbol**")
    custom_sym = st.text_input("NSE symbol", key="sim_custom_symbol").strip().upper()
    custom_dir = st.radio("Direction", ["Long", "Short"], horizontal=True, key="sim_custom_dir")
    if st.button("Add custom symbol"):
        if custom_sym:
            try:
                q = yf.download(f"{custom_sym}.NS", period="1d", interval="5m", progress=False)
                ltp = float(q["Close"].dropna().iloc[-1])
                existing = {e["Symbol"] for e in st.session_state.simulation_list}
                if custom_sym not in existing:
                    st.session_state.simulation_list.append({
                        "Symbol": custom_sym,
                        "Direction": custom_dir,
                        "Entry Price": ltp,
                        "Entry Time": now_ist(),
                    })
                st.rerun()
            except Exception as e:
                st.error(f"Couldn't fetch LTP for {custom_sym}: {e}")

if st.session_state.simulation_list:
    watch_df = pd.DataFrame(st.session_state.simulation_list)
    st.dataframe(watch_df, use_container_width=True)
    if st.button("Clear watchlist"):
        st.session_state.simulation_list = []
        st.session_state.simulation_results = None
        st.rerun()

    st.subheader("2. Set stoploss, target & trailing")
    c1, c2, c3, c4 = st.columns(4)
    sim_sl_pct = c1.slider("Stoploss %", 0.1, 5.0, 0.5, 0.1, key="sim_sl_pct")
    sim_target_pct = c2.slider("Target %", 0.1, 5.0, 1.0, 0.1, key="sim_target_pct")
    sim_trailing_enabled = c3.checkbox("Trailing SL after target", value=True, key="sim_trail_enable")
    sim_trailing_pct = c4.slider(
        "Trailing SL %", 0.1, 3.0, 0.3, 0.1, key="sim_trail_pct", disabled=not sim_trailing_enabled
    )
    sim_same_day_only = st.checkbox(
        "Same-day only (stop checking at that day's close)",
        value=True,
        key="sim_same_day_only",
        help="On: a trade entered on the 12th only checks SL/target through the "
             "12th's close, even if you refresh on a later day. Off: the replay "
             "carries forward into following days too — useful once you're "
             "testing multi-day holds, not for same-day intraday testing.",
    )
    sim_trade_value = st.number_input(
        "Assumed trade value per position (₹) — for cost estimate only",
        min_value=100.0, value=10000.0, step=500.0, key="sim_trade_value",
        help="Not your actual position size — just used to estimate Zerodha's "
             "round-trip transaction cost (brokerage, STT, exchange, GST, stamp "
             "duty) so Net P/L% reflects real costs, not just the raw price move.",
    )

    st.caption(
        "Cutoff is each stock's own entry time, so this doubles as live monitoring: "
        "every refresh re-fetches candles since entry and re-checks whether "
        "stoploss/target/trailing has been hit — click again any time through the session."
    )
    if st.button("🔄 Refresh (live — re-check against latest candles)", type="primary"):
        sim_results = [
            simulate_trade(
                symbol_ns=f"{entry['Symbol']}.NS",
                direction=entry["Direction"],
                entry_price=entry["Entry Price"],
                entry_time=entry["Entry Time"],
                sl_pct=sim_sl_pct,
                target_pct=sim_target_pct,
                trailing_enabled=sim_trailing_enabled,
                trailing_pct=sim_trailing_pct,
                same_day_only=sim_same_day_only,
                trade_value=sim_trade_value,
            )
            for entry in st.session_state.simulation_list
        ]
        st.session_state.simulation_results = pd.DataFrame(sim_results)

    if st.session_state.simulation_results is not None:
        st.subheader("Results")
        sr = st.session_state.simulation_results
        display_cols = [
            "Symbol", "Direction", "Entry Price", "Outcome", "Hit Time", "Hit Price",
            "P/L %", "Net P/L %", "Best seen (MFE %)", "Worst seen (MAE %)",
            "Current SL", "Trail Armed",
        ]
        st.dataframe(sr[display_cols], use_container_width=True)
        st.caption(
            f"Net P/L% assumes ~{estimate_roundtrip_cost_pct(sim_trade_value):.3f}% "
            f"round-trip transaction cost per trade at ₹{sim_trade_value:,.0f} trade value."
        )

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("🎯 Target/Trail hit", int(sr["Outcome"].isin(["Target Hit", "Trailing Stop Hit"]).sum()))
        m2.metric("🔴 Stoploss hit", int((sr["Outcome"] == "Stoploss Hit").sum()))
        m3.metric("➖ No hit (open)", int((sr["Outcome"] == "No Hit (EOD)").sum()))
        m4.metric("Avg Net P/L %", f"{sr['Net P/L %'].mean():.2f}%")
else:
    st.info("Add stocks above to start a simulation.")

# --------------------------------------------------------------------------
# STAGE 5 — HISTORICAL BACKTEST
# --------------------------------------------------------------------------
st.divider()
st.header("Stage 5 — Historical Backtest")
st.caption(
    "Replays scan → quality gate → rank → simulate across several past trading "
    "days in one run — using the SAME quality-gate and ranking-weight settings "
    "set in the sidebar above, so this tests what your current configuration "
    "would actually have picked, not a separate setup. Limited to roughly the "
    "last 60 days by Yahoo's 5-minute data window."
)

bt_c1, bt_c2, bt_c3 = st.columns(3)
bt_num_days = bt_c1.slider("Trading days to test", 3, 40, 10, key="bt_num_days")
bt_entry_time = bt_c2.time_input("Entry time each day", value=dtime(10, 0), key="bt_entry_time")
bt_fast_mode = bt_c3.checkbox("Faster (first 60 stocks only)", value=True, key="bt_fast_mode")

bt_c4, bt_c5, bt_c6 = st.columns(3)
bt_top_n = bt_c4.slider("Top N per phase per day", 1, 5, 2, key="bt_top_n")
bt_use_gate = bt_c5.checkbox("Apply quality gate", value=gate_enabled, key="bt_use_gate")
bt_trade_value = bt_c6.number_input(
    "Assumed trade value (₹)", min_value=100.0, value=10000.0, step=500.0, key="bt_trade_value"
)

bt_c7, bt_c8, bt_c9, bt_c10 = st.columns(4)
bt_sl_pct = bt_c7.slider("Stoploss %", 0.1, 5.0, 0.5, 0.1, key="bt_sl_pct")
bt_target_pct = bt_c8.slider("Target %", 0.1, 5.0, 1.0, 0.1, key="bt_target_pct")
bt_trailing_enabled = bt_c9.checkbox("Trailing SL after target", value=True, key="bt_trailing_enabled")
bt_trailing_pct = bt_c10.slider(
    "Trailing SL %", 0.1, 3.0, 0.3, 0.1, key="bt_trailing_pct", disabled=not bt_trailing_enabled
)

if "backtest_results" not in st.session_state:
    st.session_state.backtest_results = None

if st.button("▶️ Run historical backtest", type="primary"):
    candidate_days = []
    d = now_ist().date() - timedelta(days=1)  # start from yesterday — today may still be in progress
    while len(candidate_days) < bt_num_days:
        if d.weekday() < 5:  # Mon-Fri; NSE holidays just come back empty and get skipped below
            candidate_days.append(d)
        d -= timedelta(days=1)
    candidate_days.reverse()

    oldest_day = candidate_days[0]
    days_back = (now_ist().date() - oldest_day).days + 3  # buffer for indicator warm-up bars
    bt_lookback_days = min(59, max(days_back, 10))

    symbols = get_nse500_symbols()
    if bt_fast_mode:
        symbols = symbols[:60]

    all_trades = []
    per_day_note = []
    day_progress = st.progress(0.0, text="Running backtest...")

    for di, day in enumerate(candidate_days):
        day_as_of = pd.Timestamp.combine(day, bt_entry_time).tz_localize(IST_TZ)

        day_scan = run_scan_pipeline(
            symbols, day_as_of, bt_lookback_days, batch_size, show_progress=False,
        )
        if day_scan.empty:
            per_day_note.append((str(day), "no scan matches"))
            day_progress.progress((di + 1) / len(candidate_days), text=f"{day}: no matches")
            continue

        if bt_use_gate:
            passed, _ = quality_gate(day_scan, gate_params)
        else:
            passed = day_scan

        if passed.empty:
            per_day_note.append((str(day), "none passed gate"))
            day_progress.progress((di + 1) / len(candidate_days), text=f"{day}: none passed gate")
            continue

        day_ranked = rank_results(passed, weights)
        picks = day_ranked.groupby("Phase", group_keys=False).apply(lambda g: g.head(bt_top_n))

        for _, pick in picks.iterrows():
            direction = "Long" if pick["Phase"] == "Bull" else "Short"
            trade_res = simulate_trade(
                symbol_ns=f"{pick['Symbol']}.NS",
                direction=direction,
                entry_price=float(pick["LTP"]),
                entry_time=day_as_of,
                sl_pct=bt_sl_pct,
                target_pct=bt_target_pct,
                trailing_enabled=bt_trailing_enabled,
                trailing_pct=bt_trailing_pct,
                lookback_days=bt_lookback_days,
                same_day_only=True,
                trade_value=bt_trade_value,
            )
            trade_res["Date"] = str(day)
            trade_res["Rank Score"] = pick.get("Rank Score")
            all_trades.append(trade_res)

        day_progress.progress((di + 1) / len(candidate_days), text=f"{day}: {len(picks)} trade(s)")

    day_progress.empty()

    if all_trades:
        bt_df = pd.DataFrame(all_trades)
        bt_df["Cumulative Net P/L %"] = bt_df["Net P/L %"].cumsum()
        st.session_state.backtest_results = bt_df
    else:
        st.session_state.backtest_results = pd.DataFrame()
        st.warning(
            "No trades generated across the tested range — try more days, disabling "
            "the quality gate, or turning off 'Faster' mode for a wider universe."
        )

if st.session_state.backtest_results is not None and not st.session_state.backtest_results.empty:
    bt = st.session_state.backtest_results
    st.subheader("Backtest results")

    total_trades = len(bt)
    win_rate = (bt["Net P/L %"] > 0).mean() * 100
    avg_net = bt["Net P/L %"].mean()
    total_net = bt["Net P/L %"].sum()
    wins = bt.loc[bt["Net P/L %"] > 0, "Net P/L %"]
    losses = bt.loc[bt["Net P/L %"] <= 0, "Net P/L %"]

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Total trades", total_trades)
    k2.metric("Win rate", f"{win_rate:.0f}%")
    k3.metric("Avg net P/L / trade", f"{avg_net:.2f}%")
    k4.metric("Sum net P/L", f"{total_net:.2f}%")

    k5, k6 = st.columns(2)
    k5.metric("Avg win", f"{wins.mean():.2f}%" if len(wins) else "—")
    k6.metric("Avg loss", f"{losses.mean():.2f}%" if len(losses) else "—")

    st.line_chart(bt["Cumulative Net P/L %"])

    by_day = bt.groupby("Date")["Net P/L %"].sum().reset_index().rename(columns={"Net P/L %": "Day Net P/L %"})
    st.caption("By day")
    st.dataframe(by_day, use_container_width=True)

    st.caption("All trades")
    trade_cols = [
        "Date", "Symbol", "Direction", "Entry Price", "Outcome", "Hit Time",
        "P/L %", "Net P/L %", "Rank Score", "Best seen (MFE %)", "Worst seen (MAE %)",
    ]
    st.dataframe(bt[trade_cols], use_container_width=True)

    bt_csv = bt[trade_cols].to_csv(index=False).encode("utf-8")
    st.download_button("Download backtest trades (CSV)", bt_csv, "backtest_trades.csv", "text/csv")
