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

#from kite_client import KiteSession, KITECONNECT_AVAILABLE

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
        st.session_state.scan_as_of = as_of
        st.session_state.ranked_df = None
        st.success(f"Scan complete — {len(results)} stock(s) matched as of {as_of}.")
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
    display_cols = ["Symbol", "Phase", "% Change", "LTP", "Bar Time"]
    st.dataframe(
        st.session_state.scan_df[display_cols].reset_index(drop=True),
        use_container_width=True,
    )

if st.session_state.ranked_df is not None:
    st.subheader("Stage 2 — Ranked results (post quality-gate)")
    rank_cols = [
        "Rank", "Symbol", "Phase", "% Change", "LTP", "Rank Score",
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
# STAGE 3 — TRADE PANEL (Zerodha Kite)
# --------------------------------------------------------------------------
st.divider()
st.header("Stage 3 — Trade Panel (Zerodha Kite)")
st.caption(
    "Manual, confirm-before-send order placement. Defaults to dry-run — "
    "nothing reaches the market until you explicitly disable that and confirm."
)

if not KITECONNECT_AVAILABLE:
    st.error("`kiteconnect` isn't installed. Run `pip install kiteconnect` and restart the app.")
    st.stop()

if "kite_session" not in st.session_state:
    st.session_state.kite_session = None
if "kite_connected" not in st.session_state:
    st.session_state.kite_connected = False
if "sizing_result" not in st.session_state:
    st.session_state.sizing_result = None
if "entry_order" not in st.session_state:
    st.session_state.entry_order = None

# ---- Connect ----
with st.expander("Connect to Kite", expanded=not st.session_state.kite_connected):
    c1, c2 = st.columns(2)
    api_key = c1.text_input("API Key", type="password", key="kite_api_key")
    api_secret = c2.text_input("API Secret", type="password", key="kite_api_secret")

    if st.button("Get login URL") and api_key and api_secret:
        st.session_state.kite_session = KiteSession(api_key, api_secret)
        st.markdown(f"[Click here to log in to Kite]({st.session_state.kite_session.login_url()})")
        st.caption(
            "After logging in, Kite redirects to your app's registered redirect URL "
            "with a `request_token=...` query parameter — copy that token below."
        )

    request_token = st.text_input("request_token from the redirect URL")
    if st.button("Connect", type="primary"):
        if not st.session_state.kite_session:
            st.error("Get the login URL first.")
        elif not request_token:
            st.error("Paste the request_token first.")
        else:
            try:
                st.session_state.kite_session.generate_session(request_token)
                st.session_state.kite_connected = True
                st.success("Connected to Kite.")
            except Exception as e:
                st.error(f"Login failed: {e}")

# ---- Trade flow (only once connected) ----
if st.session_state.kite_connected and st.session_state.kite_session:
    ks: KiteSession = st.session_state.kite_session

    try:
        available_margin = ks.equity_available_margin()
        st.metric("Available equity margin", f"₹{available_margin:,.2f}")
    except Exception as e:
        st.warning(f"Couldn't fetch funds: {e}")

    st.subheader("1. Choose what to trade")
    source = st.radio(
        "Stock source",
        ["From ranked results", "Custom symbol"],
        horizontal=True,
    )

    tradingsymbol = None
    transaction_type = None  # "BUY" (Bull) or "SELL" (Bear)

    if source == "From ranked results":
        if st.session_state.ranked_df is None or st.session_state.ranked_df.empty:
            st.info("No ranked results yet — run Stage 1 and Stage 2 first, or switch to Custom symbol.")
        else:
            options = (
                st.session_state.ranked_df["Symbol"] + " — " + st.session_state.ranked_df["Phase"]
            ).tolist()
            choice = st.selectbox("Pick a ranked stock", options)
            if choice:
                sym, phase = choice.split(" — ")
                tradingsymbol = sym
                transaction_type = "BUY" if phase == "Bull" else "SELL"
    else:
        tradingsymbol = st.text_input("NSE trading symbol (e.g. RELIANCE, TCS)").strip().upper()
        direction = st.radio("Direction", ["Bull (BUY)", "Bear (SELL, requires short-selling permissions)"], horizontal=True)
        transaction_type = "BUY" if direction.startswith("Bull") else "SELL"

    if tradingsymbol:
        st.subheader("2. Size the position")
        budget_margin = st.number_input("Margin to risk (₹)", min_value=1.0, value=100.0, step=50.0)

        if st.button("Calculate size"):
            try:
                st.session_state.sizing_result = ks.size_for_budget(
                    exchange="NSE",
                    tradingsymbol=tradingsymbol,
                    transaction_type=transaction_type,
                    budget_margin=budget_margin,
                )
            except Exception as e:
                st.error(f"Sizing failed: {e}")
                st.session_state.sizing_result = None

        sr = st.session_state.sizing_result
        if sr:
            if sr.quantity < 1:
                st.warning(
                    f"₹{budget_margin:.0f} isn't enough margin for even 1 share of "
                    f"{tradingsymbol} (needs ~₹{sr.margin_per_share:.2f}/share)."
                )
            else:
                st.write(
                    f"**Quantity: {sr.quantity}** at LTP ₹{sr.ltp:.2f} "
                    f"— est. margin required ₹{sr.estimated_total_margin:.2f}"
                )

                st.subheader("3. Stop-loss & target")
                sl_pct = st.number_input("Stop-loss %", min_value=0.1, value=0.5, step=0.1)
                target_pct = st.number_input("Target %", min_value=0.1, value=1.0, step=0.1)
                if transaction_type == "BUY":
                    sl_price = round(sr.ltp * (1 - sl_pct / 100), 2)
                    target_price = round(sr.ltp * (1 + target_pct / 100), 2)
                else:
                    sl_price = round(sr.ltp * (1 + sl_pct / 100), 2)
                    target_price = round(sr.ltp * (1 - target_pct / 100), 2)
                st.write(f"Stop-loss: **₹{sl_price}** · Target: **₹{target_price}**")

                st.subheader("4. Place entry order")
                dry_run = st.checkbox("Dry run (recommended while testing)", value=True)
                confirmed = True
                if not dry_run:
                    confirm_text = st.text_input("Type CONFIRM to enable live order placement")
                    confirmed = confirm_text.strip() == "CONFIRM"
                    if not confirmed:
                        st.warning("Live orders are disabled until you type CONFIRM.")

                if st.button("Place Entry Order", disabled=not confirmed, type="primary"):
                    result = ks.place_order(
                        exchange="NSE",
                        tradingsymbol=tradingsymbol,
                        transaction_type=transaction_type,
                        quantity=sr.quantity,
                        product="MIS",
                        order_type="MARKET",
                        dry_run=dry_run,
                    )
                    st.session_state.entry_order = result
                    st.json(result)

if st.session_state.entry_order and not st.session_state.entry_order.get("dry_run", True):
    st.subheader("5. Check order status")
    order_id = st.session_state.entry_order.get("order_id")
    if order_id and st.button("Refresh status"):
        try:
            st.dataframe(pd.DataFrame(st.session_state.kite_session.order_status(order_id)))
        except Exception as e:
            st.error(f"Couldn't fetch order status: {e}")
    st.info(
        "SL/target exit orders aren't auto-placed yet — confirm the fill above, "
        "then we'll wire up the exit-order + monitoring loop next."
    )
