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

DEFAULT_WEIGHTS = {
    "VWAP_strength": 0.25,
    "Volume_strength": 0.25,
    "Trend_strength": 0.20,
    "Breakout_strength": 0.20,
    "MoneyFlow_strength": 0.10,
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


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["VWAP"] = compute_vwap(d)
    d["SMA_Vol20"] = d["Volume"].rolling(20).mean()
    d["EMA9"] = d["Close"].ewm(span=9, adjust=False).mean()
    d["EMA21"] = d["Close"].ewm(span=21, adjust=False).mean()
    d["High10"] = d["High"].shift(1).rolling(10).max()
    d["Low10"] = d["Low"].shift(1).rolling(10).min()
    d["CMF20"] = compute_cmf(d, 20)
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
    if last[["VWAP", "SMA_Vol20", "EMA9", "EMA21", "High10", "Low10", "CMF20"]].isna().any():
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

    return {
        "Symbol": symbol.replace(".NS", ""),
        "Phase": phase,
        "% Change": round(pct_change, 2),
        "LTP": round(last["Close"], 2),
        "Bar Time": last.name.strftime("%Y-%m-%d %H:%M"),
        "VWAP_strength": vwap_strength,
        "Volume_strength": volume_strength,
        "Trend_strength": trend_strength,
        "Breakout_strength": max(breakout_strength, 0),
        "MoneyFlow_strength": moneyflow_strength,
    }


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
# UI
# --------------------------------------------------------------------------
st.title("NSE 500 Bull / Bear Scanner")
st.caption(
    "Stage 1 filters the NSE 500 universe on Yahoo Finance 5-minute data. "
    "Stage 2 ranks whatever Stage 1 returns by conviction strength."
)

with st.sidebar:
    st.header("Scan settings")

    scan_date = st.date_input("Date", value=datetime.now().date())
    scan_time = st.time_input("Time (as-of)", value=datetime.now().time().replace(second=0, microsecond=0))
    as_of = pd.Timestamp.combine(scan_date, scan_time).tz_localize(IST_TZ)

    lookback_days = st.slider(
        "Lookback window (days of 5-min history to pull)",
        min_value=5, max_value=59, value=15,
        help="Yahoo only keeps 5-minute candles for ~60 days. Needs to be "
             "large enough to reach back to the selected date.",
    )

    test_mode = st.checkbox("Test mode (first 60 stocks only — faster)", value=True)
    batch_size = st.slider("Download batch size", 20, 100, 50)

    st.divider()
    st.header("Ranking weights")
    st.caption("Used in Stage 2. Any relative scale works — they're normalized to sum to 1.")
    w_vwap = st.slider("VWAP distance", 0.0, 1.0, DEFAULT_WEIGHTS["VWAP_strength"])
    w_vol = st.slider("Volume surge", 0.0, 1.0, DEFAULT_WEIGHTS["Volume_strength"])
    w_trend = st.slider("EMA trend separation", 0.0, 1.0, DEFAULT_WEIGHTS["Trend_strength"])
    w_break = st.slider("Breakout distance", 0.0, 1.0, DEFAULT_WEIGHTS["Breakout_strength"])
    w_cmf = st.slider("Money flow (CMF)", 0.0, 1.0, DEFAULT_WEIGHTS["MoneyFlow_strength"])
    weights = {
        "VWAP_strength": w_vwap,
        "Volume_strength": w_vol,
        "Trend_strength": w_trend,
        "Breakout_strength": w_break,
        "MoneyFlow_strength": w_cmf,
    }

if "scan_df" not in st.session_state:
    st.session_state.scan_df = None
if "ranked_df" not in st.session_state:
    st.session_state.ranked_df = None

col1, col2 = st.columns([1, 1])
run_scan = col1.button("🔍 Run Scan", type="primary", use_container_width=True)
run_rank = col2.button("🏆 Rank Results", use_container_width=True, disabled=st.session_state.scan_df is None)

# ---- STAGE 1 ----
if run_scan:
    symbols = get_nse500_symbols()
    if test_mode:
        symbols = symbols[:60]

    if not symbols:
        st.stop()

    period = f"{lookback_days}d"
    results = []
    progress = st.progress(0.0, text="Downloading & scanning...")
    total_batches = (len(symbols) + batch_size - 1) // batch_size

    for i, group in enumerate(chunk(symbols, batch_size)):
        try:
            batch_df = fetch_batch(tuple(group), period=period, interval="5m")
        except Exception as e:
            st.warning(f"Batch download failed ({e}); skipping this batch.")
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

        progress.progress((i + 1) / total_batches, text=f"Scanned batch {i + 1}/{total_batches}")

    progress.empty()

    if results:
        st.session_state.scan_df = pd.DataFrame(results)
        st.session_state.ranked_df = None
        st.success(f"Scan complete — {len(results)} stock(s) matched as of {as_of}.")
    else:
        st.session_state.scan_df = pd.DataFrame()
        st.warning("No stocks matched either condition set for the selected date/time.")

# ---- STAGE 2 ----
if run_rank and st.session_state.scan_df is not None and not st.session_state.scan_df.empty:
    st.session_state.ranked_df = rank_results(st.session_state.scan_df, weights)

# ---- DISPLAY ----
if st.session_state.scan_df is not None and not st.session_state.scan_df.empty:
    st.subheader("Stage 1 — Scan results")
    display_cols = ["Symbol", "Phase", "% Change", "LTP", "Bar Time"]
    st.dataframe(
        st.session_state.scan_df[display_cols].reset_index(drop=True),
        use_container_width=True,
    )

if st.session_state.ranked_df is not None:
    st.subheader("Stage 2 — Ranked results")
    rank_cols = ["Rank", "Symbol", "Phase", "% Change", "LTP", "Rank Score", "Bar Time"]
    bull_tab, bear_tab = st.tabs(["Bull phase", "Bear phase"])
    with bull_tab:
        b = st.session_state.ranked_df[st.session_state.ranked_df["Phase"] == "Bull"]
        st.dataframe(b[rank_cols].reset_index(drop=True), use_container_width=True)
    with bear_tab:
        s = st.session_state.ranked_df[st.session_state.ranked_df["Phase"] == "Bear"]
        st.dataframe(s[rank_cols].reset_index(drop=True), use_container_width=True)

    csv = st.session_state.ranked_df[rank_cols].to_csv(index=False).encode("utf-8")
    st.download_button("Download ranked results (CSV)", csv, "ranked_results.csv", "text/csv")
