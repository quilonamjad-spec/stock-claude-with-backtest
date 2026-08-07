"""
app.py
Streamlit candlestick pattern scanner & scorer.

Run locally:
    streamlit run app.py

Deploy free on Streamlit Community Cloud by pushing this repo to GitHub
and pointing streamlit.io/cloud at app.py (see README.md).
"""
import time
from datetime import datetime, date, time as dtime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from data_fetcher import fetch_ohlcv
from indicators import add_all_indicators
from scoring import score_at

st.set_page_config(page_title="Candlestick Pattern Screener", layout="wide")

st.title("📊 Candlestick Pattern Screener")
st.caption(
    "Scores stocks by combining candlestick pattern recognition with trend, "
    "volume, momentum, and support/resistance context. Educational tool only — not financial advice."
)

NSE_DEFAULTS = "RELIANCE, TCS, INFY, HDFCBANK, ICICIBANK"
US_DEFAULTS = "AAPL, MSFT, NVDA, TSLA, AMZN"

# ---------------- Sidebar controls ----------------
with st.sidebar:
    st.header("Watchlist")
    market = st.selectbox("Market", ["India (NSE)", "US", "India (BSE)"], index=0)
    default_watchlist = NSE_DEFAULTS if market != "US" else US_DEFAULTS
    tickers_raw = st.text_area(
        "Tickers (comma separated)", value=default_watchlist, height=80,
        help="For NSE/BSE you can type the symbol plain (e.g. RELIANCE) — "
             "the .NS / .BO suffix is added automatically.",
    )
    period = st.selectbox("History period", ["1mo", "3mo", "6mo"], index=0)
    interval = st.selectbox(
        "Candle interval", ["1d", "1h", "30m", "15m"], index=0,
        help="Intraday intervals (1h/30m/15m) are only available for roughly the last 60 days from Yahoo, "
             "which fits within a 1mo/3mo window.",
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

    run_scan = st.button("🔍 Scan Watchlist", type="primary")


def normalize_ticker(raw: str, market: str) -> str:
    t = raw.strip().upper()
    if market == "India (NSE)" and not t.endswith((".NS", ".BO")):
        t += ".NS"
    elif market == "India (BSE)" and not t.endswith((".NS", ".BO")):
        t += ".BO"
    return t


tickers = [normalize_ticker(t, market) for t in tickers_raw.split(",") if t.strip()]

if "scan_results" not in st.session_state:
    st.session_state.scan_results = {}

# ---------------- Run scan ----------------
if run_scan:
    st.session_state.scan_results = {}
    as_of_dt = None
    if use_as_of:
        as_of_dt = datetime.combine(as_of_date, as_of_time or dtime(23, 59))

    progress = st.progress(0.0, text="Scanning...")
    for idx, t in enumerate(tickers):
        try:
            df = fetch_ohlcv(t, period=period, interval=interval)
            if df.empty or len(df) < 5:
                st.session_state.scan_results[t] = {"error": "Not enough data returned."}
            else:
                df_ind = add_all_indicators(df)

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

                result = score_at(df_ind, t, i)
                st.session_state.scan_results[t] = {"df": df_ind, "result": result, "i": i}
        except Exception as e:
            st.session_state.scan_results[t] = {"error": str(e)}

        progress.progress((idx + 1) / max(len(tickers), 1), text=f"Scanning {t}...")
        time.sleep(0.6)  # small gap between requests to avoid Yahoo rate-limiting on multi-ticker scans
    progress.empty()

# ---------------- Results table ----------------
results = st.session_state.scan_results

if results:
    rows = []
    for t, data in results.items():
        if "error" in data:
            rows.append({"Ticker": t, "Score": None, "Verdict": "Error", "Close": None,
                         "Trend": None, "RSI": None, "Vol Ratio": None, "Patterns": data["error"]})
        else:
            r = data["result"]
            pattern_names = ", ".join(p.name for p in r.patterns) if r.patterns else "—"
            rows.append({
                "Ticker": t, "Score": r.score, "Verdict": r.verdict, "Close": round(r.close, 2),
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
    valid_tickers = [t for t in results if "error" not in results[t]]
    if valid_tickers:
        selected = st.selectbox("Select a ticker to inspect", valid_tickers)
        data = results[selected]
        df_ind, r, analyzed_i = data["df"], data["result"], data["i"]
        currency = "₹" if selected.endswith((".NS", ".BO")) else "$"

        st.caption(f"Analyzed candle: **{r.date}** (candle {analyzed_i + 1} of {len(df_ind)} in the loaded window)")
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Score (0-100, 50=neutral)", f"{r.score}", r.verdict)
        col2.metric("Close", f"{currency}{r.close:.2f}")
        col3.metric("Trend", r.trend)
        col4.metric("RSI", r.rsi)
        st.progress(r.score / 100)

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
    st.info("Enter tickers in the sidebar and click **Scan Watchlist** to begin.")

st.divider()
st.caption(
    "⚠️ This tool is for educational purposes only and does not constitute financial advice. "
    "Candlestick patterns are probabilistic signals, not guarantees — always combine with your own "
    "research and risk management."
)
