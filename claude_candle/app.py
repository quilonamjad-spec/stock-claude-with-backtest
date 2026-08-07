"""
app.py
Streamlit candlestick pattern scanner & scorer.

Run locally:
    streamlit run app.py

Deploy free on Streamlit Community Cloud by pushing this repo to GitHub
and pointing streamlit.io/cloud at app.py (see README.md).
"""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from data_fetcher import fetch_ohlcv
from indicators import add_all_indicators
from scoring import score_latest

st.set_page_config(page_title="Candlestick Pattern Screener", layout="wide")

st.title("📊 Candlestick Pattern Screener")
st.caption(
    "Scores stocks by combining candlestick pattern recognition with trend, "
    "volume, momentum, and support/resistance context. Educational tool only — not financial advice."
)

# ---------------- Sidebar controls ----------------
with st.sidebar:
    st.header("Watchlist")
    default_watchlist = "AAPL, MSFT, NVDA, TSLA, AMZN"
    tickers_raw = st.text_area("Tickers (comma separated)", value=default_watchlist, height=80)
    period = st.selectbox("History period", ["3mo", "6mo", "1y", "2y"], index=1)
    interval = st.selectbox("Candle interval", ["1d", "1wk"], index=0)
    min_score_filter = st.slider("Only show |score| ≥", 0, 100, 0)
    run_scan = st.button("🔍 Scan Watchlist", type="primary")

tickers = [t.strip().upper() for t in tickers_raw.split(",") if t.strip()]

if "scan_results" not in st.session_state:
    st.session_state.scan_results = {}

# ---------------- Run scan ----------------
if run_scan:
    st.session_state.scan_results = {}
    progress = st.progress(0.0, text="Scanning...")
    for idx, t in enumerate(tickers):
        try:
            df = fetch_ohlcv(t, period=period, interval=interval)
            if df.empty or len(df) < 30:
                st.session_state.scan_results[t] = {"error": "Not enough data returned."}
            else:
                df_ind = add_all_indicators(df)
                result = score_latest(df_ind, t)
                st.session_state.scan_results[t] = {"df": df_ind, "result": result}
        except Exception as e:
            st.session_state.scan_results[t] = {"error": str(e)}
        progress.progress((idx + 1) / max(len(tickers), 1), text=f"Scanning {t}...")
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
        table_df = table_df[table_df["Score"].abs() >= min_score_filter]
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
    st.dataframe(
        table_df.style.applymap(color_verdict, subset=["Verdict"]),
        use_container_width=True, hide_index=True,
    )

    # ---------------- Detail view ----------------
    st.subheader("Detail View")
    valid_tickers = [t for t in results if "error" not in results[t]]
    if valid_tickers:
        selected = st.selectbox("Select a ticker to inspect", valid_tickers)
        data = results[selected]
        df_ind, r = data["df"], data["result"]

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Score", r.score, r.verdict)
        col2.metric("Close", f"${r.close:.2f}")
        col3.metric("Trend", r.trend)
        col4.metric("RSI", r.rsi)

        # Candlestick chart with moving averages
        fig = go.Figure(data=[go.Candlestick(
            x=df_ind.index, open=df_ind["open"], high=df_ind["high"],
            low=df_ind["low"], close=df_ind["close"], name=selected,
        )])
        if "ema_20" in df_ind:
            fig.add_trace(go.Scatter(x=df_ind.index, y=df_ind["ema_20"], name="EMA 20", line=dict(width=1)))
        if "ema_50" in df_ind:
            fig.add_trace(go.Scatter(x=df_ind.index, y=df_ind["ema_50"], name="EMA 50", line=dict(width=1)))
        fig.update_layout(height=500, xaxis_rangeslider_visible=False,
                           margin=dict(l=10, r=10, t=30, b=10))
        st.plotly_chart(fig, use_container_width=True)

        # RSI subplot
        if "rsi" in df_ind:
            rsi_fig = go.Figure()
            rsi_fig.add_trace(go.Scatter(x=df_ind.index, y=df_ind["rsi"], name="RSI", line=dict(color="#7a5cf0")))
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
