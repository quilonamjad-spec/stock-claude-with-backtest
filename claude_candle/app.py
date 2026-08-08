"""
app.py
Streamlit candlestick pattern scanner & scorer.

Run locally:
    streamlit run app.py

Deploy free on Streamlit Community Cloud by pushing this repo to GitHub
and pointing streamlit.io/cloud at app.py (see README.md).
"""
import json
import os
import time
from datetime import datetime, date, time as dtime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from data_fetcher import fetch_ohlcv_batch, trim_to_period
from indicators import add_all_indicators, smooth_session_edges
from scoring import score_at, DEFAULT_WEIGHTS

st.set_page_config(page_title="Candlestick Pattern Screener", layout="wide")

st.title("📊 Candlestick Pattern Screener")
st.caption(
    "Scores stocks by combining candlestick pattern recognition with trend, "
    "volume, momentum, and support/resistance context. Educational tool only — not financial advice."
)

INTRADAY_INTERVALS = {"1h", "30m", "15m", "5m"}

NSE_DEFAULTS = "RELIANCE, TCS, INFY, HDFCBANK, ICICIBANK"
US_DEFAULTS = "AAPL, MSFT, NVDA, TSLA, AMZN"
NIFTY500_PATH = os.path.join(os.path.dirname(__file__), "nifty500.json")


@st.cache_data
def load_nifty500() -> list:
    with open(NIFTY500_PATH) as f:
        return json.load(f)["tickers"]


def normalize_ticker(raw: str, market: str) -> str:
    t = raw.strip().upper()
    if market == "India (NSE)" and not t.endswith((".NS", ".BO")):
        t += ".NS"
    elif market == "India (BSE)" and not t.endswith((".NS", ".BO")):
        t += ".BO"
    return t


# ---------------- Sidebar controls ----------------
with st.sidebar:
    st.header("Watchlist")
    watchlist_mode = st.radio(
        "Source", ["Individual tickers", "Full Nifty 500 (scan whole market)"], index=0,
    )

    if watchlist_mode.startswith("Full"):
        nifty500 = load_nifty500()
        st.caption(f"✅ {len(nifty500)} NSE tickers loaded from Nifty 500.")
        extra_raw = st.text_input("Add extra tickers too (optional, comma-separated)")
        market = "India (NSE)"
        tickers = list(nifty500)
        if extra_raw.strip():
            tickers += [normalize_ticker(t, market) for t in extra_raw.split(",") if t.strip()]
        # de-dupe while preserving order
        seen = set()
        tickers = [t for t in tickers if not (t in seen or seen.add(t))]
    else:
        market = st.selectbox("Market", ["India (NSE)", "US", "India (BSE)"], index=0)
        default_watchlist = NSE_DEFAULTS if market != "US" else US_DEFAULTS
        tickers_raw = st.text_area(
            "Tickers (comma separated)", value=default_watchlist, height=80,
            help="For NSE/BSE you can type the symbol plain (e.g. RELIANCE) — "
                 "the .NS / .BO suffix is added automatically.",
        )
        tickers = [normalize_ticker(t, market) for t in tickers_raw.split(",") if t.strip()]

    period = st.selectbox(
        "History period", ["1d", "5d", "1mo", "3mo", "6mo"], index=2,
        help="'1d' pulls just today's session — pair it with a short interval like 5m to watch today's candles form.",
    )
    interval = st.selectbox(
        "Candle interval", ["1d", "1h", "30m", "15m", "5m"], index=0,
        help="Intraday intervals (1h/30m/15m/5m) are only available for roughly the last 60 days from Yahoo. "
             "For '1d' period, pick an intraday interval (e.g. 5m) or you'll only get a single candle.",
    )
    min_score_filter = st.slider(
        "Only show conviction ≥ (distance from neutral 50)", 0, 50, 0,
        help="e.g. 20 hides anything scoring between 30 and 70 (i.e. weak/neutral signals).",
    )

    st.divider()
    st.header("Point-in-time analysis")
    st.caption("Score the candle as of a specific day (and time, for intraday) instead of the latest one.")
    use_as_of = st.checkbox("Analyze as of a specific date/time", value=False)
    as_of_date = st.date_input("Date", value=date.today()) if use_as_of else None
    as_of_time = None
    if use_as_of and interval != "1d":
        as_of_time = st.time_input("Time", value=dtime(15, 30))  # NSE close ~15:30 IST

    st.divider()
    st.header("Scoring weights")
    st.caption(
        "How much each component contributes to the final score. They always sum to "
        "100% — move one and the others rebalance proportionally, keeping their "
        "relative ratio to each other."
    )

    # component key -> (session_state key, slider label)
    COMPONENT_SLIDERS = [
        ("candle", "w_candle", "Candle Pattern"),
        ("rsi", "w_rsi", "RSI"),
        ("macd", "w_macd", "MACD"),
        ("trend", "w_trend", "Trend (Moving Averages)"),
        ("volatility", "w_volatility", "Volatility (Bollinger Bands)"),
        ("volume", "w_volume", "Volume (VWAP)"),
    ]
    WEIGHT_KEYS = [sk for _, sk, _ in COMPONENT_SLIDERS]

    if "w_candle" not in st.session_state:
        for comp_key, state_key, _ in COMPONENT_SLIDERS:
            st.session_state[state_key] = DEFAULT_WEIGHTS[comp_key]

    def _rebalance_weights(changed_key: str):
        other_keys = [k for k in WEIGHT_KEYS if k != changed_key]
        new_val = st.session_state[changed_key]
        remaining = 100 - new_val
        if remaining < 0:
            st.session_state[changed_key] = 100
            remaining = 0
        total_others_old = sum(st.session_state[k] for k in other_keys)
        if total_others_old <= 0:
            for k in other_keys:
                st.session_state[k] = remaining / len(other_keys)
        else:
            for k in other_keys:
                st.session_state[k] = round(remaining * st.session_state[k] / total_others_old)
        drift = 100 - sum(st.session_state[k] for k in WEIGHT_KEYS)
        if drift != 0:
            st.session_state[other_keys[0]] += drift

    for _, state_key, label in COMPONENT_SLIDERS:
        st.slider(f"{label} weight (%)", 0, 100, key=state_key,
                  on_change=_rebalance_weights, args=(state_key,))

    split_str = " · ".join(f"{label} **{st.session_state[sk]}%**" for _, sk, label in COMPONENT_SLIDERS)
    st.caption(f"Current split → {split_str}")

    if st.button("Reset to defaults"):
        for comp_key, state_key, _ in COMPONENT_SLIDERS:
            st.session_state[state_key] = DEFAULT_WEIGHTS[comp_key]
        st.rerun()

    weights = {comp_key: st.session_state[state_key] for comp_key, state_key, _ in COMPONENT_SLIDERS}

    run_scan = st.button("🔍 Scan Watchlist", type="primary")

if "scan_results" not in st.session_state:
    st.session_state.scan_results = {}

# ---------------- Run scan (fetches + computes indicators only — NOT final scores,
# so that adjusting the weight sliders afterward re-scores instantly without a re-fetch) ----------------
if run_scan:
    st.session_state.scan_results = {}
    as_of_dt = None
    if use_as_of:
        as_of_dt = datetime.combine(as_of_date, as_of_time or dtime(23, 59))

    # For short intraday display windows (today / last 5 days), fetch extra history behind
    # the scenes so indicators (SMA20, RSI14, etc.) have enough warm-up data and so the
    # first candles of the day aren't sitting on NaN indicators. We trim back down to the
    # requested display window AFTER indicators are computed.
    needs_warmup = interval in INTRADAY_INTERVALS and period in ("1d", "5d")
    fetch_period = "1mo" if needs_warmup else period

    status = st.empty()
    status.info(f"Fetching {len(tickers)} ticker(s) in batches — this is much faster than one-by-one...")
    ohlcv_by_ticker = fetch_ohlcv_batch(tuple(tickers), period=fetch_period, interval=interval)
    status.empty()

    progress = st.progress(0.0, text="Preparing data...")
    for idx, t in enumerate(tickers):
        try:
            df = ohlcv_by_ticker.get(t, pd.DataFrame())
            if df.empty or len(df) < 5:
                st.session_state.scan_results[t] = {"error": "Not enough data returned."}
            else:
                if interval in INTRADAY_INTERVALS:
                    # smooth noisy opening/closing candles per trading day before scoring
                    df = smooth_session_edges(df, n=3)

                df_ind = add_all_indicators(df)
                if needs_warmup:
                    # now that indicators have their warm-up history, cut back to just
                    # the display window the user actually asked for (e.g. today only)
                    df_ind = trim_to_period(df_ind, period)

                if df_ind.empty:
                    st.session_state.scan_results[t] = {"error": "No candles left after trimming to the selected period."}
                    progress.progress((idx + 1) / max(len(tickers), 1))
                    continue

                # make index tz-naive for comparison (Yahoo returns tz-aware intraday timestamps)
                idx_naive = df_ind.index.tz_localize(None) if df_ind.index.tz is not None else df_ind.index

                if as_of_dt is not None:
                    eligible = idx_naive <= as_of_dt
                    if not eligible.any():
                        st.session_state.scan_results[t] = {
                            "error": f"No data at/before {as_of_dt} (earliest candle: {idx_naive[0]})."
                        }
                        progress.progress((idx + 1) / max(len(tickers), 1))
                        continue
                    i = eligible.nonzero()[0][-1]  # position of the last eligible candle
                else:
                    i = len(df_ind) - 1

                st.session_state.scan_results[t] = {"df": df_ind, "i": i}
        except Exception as e:
            st.session_state.scan_results[t] = {"error": str(e)}

        if idx % 25 == 0 or idx == len(tickers) - 1:
            progress.progress((idx + 1) / max(len(tickers), 1), text=f"Preparing data... {idx + 1}/{len(tickers)}")
    progress.empty()

# ---------------- Score with current weights (recomputed every render — cheap, so
# moving the weight sliders updates results live without needing to re-scan) ----------------
raw = st.session_state.scan_results
results = {}
for t, data in raw.items():
    if "error" in data:
        results[t] = data
    else:
        results[t] = {"df": data["df"], "i": data["i"], "result": score_at(data["df"], t, data["i"], weights=weights)}

if results:
    rows = []
    for t, data in results.items():
        if "error" in data:
            rows.append({"Ticker": t, "Score": None, "Verdict": "Error", "Close": None,
                         "Candle": None, "RSI comp": None, "MACD comp": None,
                         "Trend comp": None, "Vol.ty comp": None, "Volume comp": None,
                         "Trend": None, "RSI": None, "Vol Ratio": None, "Patterns": data["error"]})
        else:
            r = data["result"]
            cs = r.component_scores
            pattern_names = ", ".join(p.name for p in r.patterns) if r.patterns else "—"
            rows.append({
                "Ticker": t, "Score": r.score, "Verdict": r.verdict, "Close": round(r.close, 2),
                "Candle": cs.get("candle"), "RSI comp": cs.get("rsi"), "MACD comp": cs.get("macd"),
                "Trend comp": cs.get("trend"), "Vol.ty comp": cs.get("volatility"), "Volume comp": cs.get("volume"),
                "Trend": r.trend, "RSI": r.rsi, "Vol Ratio": r.vol_ratio, "Patterns": pattern_names,
            })

    table_df = pd.DataFrame(rows)
    if min_score_filter > 0:
        table_df = table_df[(table_df["Score"] - 50).abs() >= min_score_filter]
    table_df = table_df.sort_values("Score", ascending=False, na_position="last")

    def color_verdict(val):
        colors = {
            "Strong Buy": "background-color:#1a7a3a;color:white",
            "Buy": "background-color:#3fae5a;color:white",
            "Neutral": "background-color:#d9c93f;color:black",
            "Sell": "background-color:#c0554a;color:white",
            "Strong Sell": "background-color:#8f2b21;color:white",
            "Error": "background-color:#555555;color:white",
        }
        return colors.get(val, "")

    st.subheader("Screening Results")
    styler = table_df.style
    # pandas >= 2.1 renamed Styler.applymap to .map; support both for compatibility
    styler = styler.map(color_verdict, subset=["Verdict"]) if hasattr(styler, "map") \
        else styler.applymap(color_verdict, subset=["Verdict"])
    st.dataframe(styler, use_container_width=True, hide_index=True)

    # ---------------- Detail view ----------------
    st.subheader("Detail View")
    # Only offer tickers that survived the conviction filter above (and have no error) —
    # keeps the dropdown in sync with whatever the table is currently showing.
    valid_tickers = [t for t in table_df["Ticker"] if "error" not in results[t]]
    if valid_tickers:
        selected = st.selectbox(
            f"Select a ticker to inspect ({len(valid_tickers)} match the current filter)",
            valid_tickers,
        )
        data = results[selected]
        df_ind, r, analyzed_i = data["df"], data["result"], data["i"]
        currency = "₹" if selected.endswith((".NS", ".BO")) else "$"

        st.caption(f"Analyzed candle: **{r.date}** (candle {analyzed_i + 1} of {len(df_ind)} in the loaded window)")
        st.caption(f"Data loaded: {len(df_ind)} candles from {df_ind.index[0]} to {df_ind.index[-1]}")
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Score (0-100, 50=neutral)", f"{r.score}", r.verdict)
        col2.metric("Close", f"{currency}{r.close:.2f}")
        col3.metric("Trend going in", r.trend, help="The trend BEFORE this candle — reversal patterns score strongest when they go against this.")
        col4.metric("RSI", r.rsi)
        st.progress(r.score / 100)

        with st.container(border=True):
            cc1, cc2, cc3 = st.columns(3)
            cc1.metric(f"Candle ({weights['candle']:.0f}%)", f"{r.component_scores.get('candle', 50)}/100")
            cc2.metric(f"RSI ({weights['rsi']:.0f}%)", f"{r.component_scores.get('rsi', 50)}/100")
            cc3.metric(f"MACD ({weights['macd']:.0f}%)", f"{r.component_scores.get('macd', 50)}/100")
            cc4, cc5, cc6 = st.columns(3)
            cc4.metric(f"Trend/MA ({weights['trend']:.0f}%)", f"{r.component_scores.get('trend', 50)}/100")
            cc5.metric(f"Volatility/BB ({weights['volatility']:.0f}%)", f"{r.component_scores.get('volatility', 50)}/100")
            cc6.metric(f"Volume/VWAP ({weights['volume']:.0f}%)", f"{r.component_scores.get('volume', 50)}/100")
            st.caption("Adjust the weight sliders in the sidebar to rebalance how much each component drives the final score — no need to re-scan.")

        # Candlestick chart with moving averages — only show data up to the analyzed candle
        # so the chart matches exactly what the score "saw" (no lookahead)
        chart_df = df_ind.iloc[: analyzed_i + 1]
        fig = go.Figure(data=[go.Candlestick(
            x=chart_df.index, open=chart_df["open"], high=chart_df["high"],
            low=chart_df["low"], close=chart_df["close"], name=selected,
        )])
        if "ema_20" in chart_df:
            fig.add_trace(go.Scatter(x=chart_df.index, y=chart_df["ema_20"], name="EMA 20", line=dict(width=1)))
        if "ema_50" in chart_df:
            fig.add_trace(go.Scatter(x=chart_df.index, y=chart_df["ema_50"], name="EMA 50", line=dict(width=1)))
        fig.add_vline(x=chart_df.index[-1], line_dash="dot", line_color="gray")
        fig.update_layout(height=500, xaxis_rangeslider_visible=False,
                           margin=dict(l=10, r=10, t=30, b=10))
        st.plotly_chart(fig, use_container_width=True)

        # RSI subplot
        if "rsi" in chart_df:
            rsi_fig = go.Figure()
            rsi_fig.add_trace(go.Scatter(x=chart_df.index, y=chart_df["rsi"], name="RSI", line=dict(color="#7a5cf0")))
            rsi_fig.add_hline(y=70, line_dash="dot", line_color="red")
            rsi_fig.add_hline(y=30, line_dash="dot", line_color="green")
            rsi_fig.update_layout(height=200, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(rsi_fig, use_container_width=True)

        st.markdown("**Why this score:**")
        for reason in r.reasons:
            st.write("• " + reason)
    else:
        st.warning("No tickers match the current conviction filter — lower the slider in the sidebar to see details.")
else:
    st.info("Enter tickers in the sidebar and click **Scan Watchlist** to begin.")

st.divider()
st.caption(
    "⚠️ This tool is for educational purposes only and does not constitute financial advice. "
    "Candlestick patterns are probabilistic signals, not guarantees — always combine with your own "
    "research and risk management."
)
