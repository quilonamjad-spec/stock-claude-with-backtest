"""
app.py
------
Nifty500 Day-Trading Screener & Monitor (Streamlit)

Two-stage workflow:
  1. SCREENER tab  - scan the full Nifty500 (or a subset), rank every
     stock by Trade Score / Confidence, and tick the ones you like.
  2. MONITORING tab - your shortlisted stocks get a faster-refreshing,
     more detailed view (candlestick chart + indicator breakdown).

Run with:  streamlit run app.py

DISCLAIMER: This is a rules-based technical screening tool, not investment
advice. It summarizes textbook indicator signals into a score - it does
not predict price movement. Yahoo Finance data can lag real markets by a
few minutes. Always validate against your broker/exchange terminal before
placing trades, and size positions according to your own risk management.
"""

import datetime as dt
import json
import os
import time

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from backtest import (
    entry_price_at_cutoff,
    period_for_cutoff_date,
    score_asof,
    simulate_trade,
    split_at_cutoff,
)
from data_fetch import get_nifty500_list, fetch_batch, fetch_single, fetch_index
from indicators import compute_all_indicators
from market_context import (
    alignment_label,
    day_change_pct,
    day_change_pct_asof,
    index_display_name,
    index_for_industry,
)
from scoring import score_symbol, score_trend, trend_summary, DEFAULT_WEIGHTS

st.set_page_config(page_title="Nifty500 Trade Scanner", layout="wide")

WATCHLIST_FILE = os.path.join(os.path.dirname(__file__), "data", "watchlist.json")

INDICATOR_LABELS = {
    "RSI": "RSI (14) - overbought/oversold",
    "MACD": "MACD - momentum/crossover",
    "ADX": "ADX/DI - trend strength & direction",
    "BOLLINGER": "Bollinger Bands - position in range",
    "VOLUME": "Volume - conviction on move",
    "EMA_TREND": "EMA 20/50 - trend alignment",
    "EXTENSION": "Extension - fades overbought/oversold stretch (ATR)",
    "VWAP": "VWAP - price vs session volume-weighted average",
    "CANDLESTICK": "Candlestick patterns",
}

INTERVAL_PERIOD_MAP = {
    "5m": "5d",
    "15m": "1mo",
    "1h": "3mo",
    "1d": "1y",
}


# ---------------------------------------------------------------- helpers

def load_watchlist():
    if os.path.exists(WATCHLIST_FILE):
        try:
            with open(WATCHLIST_FILE) as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_watchlist(symbols):
    os.makedirs(os.path.dirname(WATCHLIST_FILE), exist_ok=True)
    with open(WATCHLIST_FILE, "w") as f:
        json.dump(sorted(set(symbols)), f, indent=2)


@st.cache_data(ttl=3600, show_spinner=False)
def cached_nifty500_list():
    df, source = get_nifty500_list()
    return df, source


@st.cache_data(ttl=180, show_spinner=False)
def cached_fetch_batch(symbols_tuple, interval, period, batch_size):
    # Cache repeated identical calls for a few minutes so Streamlit reruns
    # (every widget click reruns the whole script) don't re-hit Yahoo.
    return fetch_batch(list(symbols_tuple), interval=interval, period=period, batch_size=batch_size)


@st.cache_data(ttl=60, show_spinner=False)
def cached_fetch_single(symbol, interval, period):
    return fetch_single(symbol, interval=interval, period=period)


@st.cache_data(ttl=180, show_spinner=False)
def cached_fetch_index(index_symbol, interval, period):
    return fetch_index(index_symbol=index_symbol, interval=interval, period=period)



def filter_chart_range(df, choice, interval):
    """
    Trim the DataFrame just for display purposes. Indicators (EMA/BB/etc.)
    should already be computed on the FULL history before calling this -
    this only shortens what's shown on the x-axis.
    """
    if df.empty or choice == "All downloaded":
        return df

    if interval == "1d":
        n_map = {"Last 30 candles": 30, "Last 60 candles": 60, "Last 120 candles": 120}
        n = n_map.get(choice, 60)
        return df.tail(n)

    # Intraday: filter by calendar day
    day_map = {"Today only": 1, "Last 2 days": 2, "Last 3 days": 3, "Last 5 days": 5}
    n_days = day_map.get(choice, 1)
    dates = pd.Series(df.index.date, index=df.index)
    unique_dates = sorted(dates.unique())
    keep_dates = set(unique_dates[-n_days:])
    return df[dates.isin(keep_dates)]


def make_candlestick_chart(df, symbol):
    fig = go.Figure(data=[go.Candlestick(
        x=df.index, open=df["Open"], high=df["High"],
        low=df["Low"], close=df["Close"], name=symbol,
    )])
    if "EMA20" in df.columns:
        fig.add_trace(go.Scatter(x=df.index, y=df["EMA20"], name="EMA 20",
                                  line=dict(width=1, color="orange")))
    if "EMA50" in df.columns:
        fig.add_trace(go.Scatter(x=df.index, y=df["EMA50"], name="EMA 50",
                                  line=dict(width=1, color="blue")))
    if "BB_UPPER" in df.columns:
        fig.add_trace(go.Scatter(x=df.index, y=df["BB_UPPER"], name="BB Upper",
                                  line=dict(width=1, color="gray", dash="dot")))
        fig.add_trace(go.Scatter(x=df.index, y=df["BB_LOWER"], name="BB Lower",
                                  line=dict(width=1, color="gray", dash="dot")))
    fig.update_layout(height=450, margin=dict(l=10, r=10, t=30, b=10),
                       xaxis_rangeslider_visible=False,
                       template="plotly_dark")
    return fig


def signal_color(label):
    return {
        "Strong Buy": "#0f9d58",
        "Buy": "#66bb6a",
        "Neutral": "#9e9e9e",
        "Sell": "#ef9a9a",
        "Strong Sell": "#d32f2f",
        "No Data": "#616161",
        "No Indicators Selected": "#616161",
    }.get(label, "#9e9e9e")


# ---------------------------------------------------------------- sidebar

st.sidebar.title("⚙️ Scanner Settings")

interval = st.sidebar.selectbox(
    "Candle timeframe", list(INTERVAL_PERIOD_MAP.keys()), index=1,
    help="Drives all indicator calculations. 15m is a good default for day trading.",
)
period = INTERVAL_PERIOD_MAP[interval]

st.sidebar.markdown("**Indicators to include**")
active_indicators = {}
weights = {}
# VWAP defaults OFF: combined with Extension it was fading out too many
# Short candidates in testing. Both are being revisited together later -
# left toggleable (not removed) so it's a one-click re-test, not a rebuild.
DEFAULT_INDICATOR_ACTIVE = {"VWAP": False}
for key, label in INDICATOR_LABELS.items():
    col1, col2 = st.sidebar.columns([3, 2])
    with col1:
        active_indicators[key] = st.checkbox(
            label, value=DEFAULT_INDICATOR_ACTIVE.get(key, True), key=f"chk_{key}"
        )
    with col2:
        weights[key] = st.slider("wt", 0.0, 2.0, DEFAULT_WEIGHTS[key], 0.1,
                                  key=f"wt_{key}", label_visibility="collapsed")

st.sidebar.markdown("---")
universe_choice = st.sidebar.radio(
    "Universe to scan",
    ["Full Nifty500", "Nifty50 subset (fast test)", "Custom list"],
    index=0,
)
batch_size = st.sidebar.slider("Batch size (tickers per API call)", 20, 100, 50, 10,
                                help="Larger batches are faster but more likely to hit Yahoo rate limits.")
max_scan = st.sidebar.slider("Max stocks to scan this run", 20, 500, 500, 10,
                              help="Lower this while testing so a run doesn't take too long.")

st.sidebar.markdown("---")
st.sidebar.markdown("**Monitoring list backup**")
st.sidebar.caption(
    "On Streamlit Cloud the saved list resets when the app restarts/sleeps. "
    "Download it before you stop for the day, and re-upload next time."
)
_current_wl = load_watchlist()
st.sidebar.download_button(
    "⬇️ Download watchlist.json",
    data=json.dumps(_current_wl, indent=2),
    file_name="watchlist.json",
    mime="application/json",
    disabled=not _current_wl,
)
_uploaded_wl = st.sidebar.file_uploader("⬆️ Restore watchlist.json", type=["json"])
if _uploaded_wl is not None:
    try:
        restored = json.load(_uploaded_wl)
        if isinstance(restored, list):
            save_watchlist(restored)
            st.sidebar.success(f"Restored {len(restored)} symbols. Check the Monitoring tab.")
    except Exception:
        st.sidebar.error("Couldn't parse that file.")

st.sidebar.markdown("---")
st.sidebar.caption(

    "⚠️ Educational/research tool only, not investment advice. "
    "Yahoo Finance data may be delayed. Verify against your broker before trading."
)

# ---------------------------------------------------------------- header

st.title("📊 Nifty500 Day-Trading Screener")
st.caption(
    f"Timeframe: **{interval}** · Scoring blends selected indicators into a "
    f"0-100 Trade Score (50 = neutral) and a 0-100 Confidence (indicator agreement)."
)

tab_backtest, tab_mobile = st.tabs(
    ["🧠 All-in-One (Scan \u00b7 Monitor \u00b7 Backtest)", "\U0001F4F1 Mobile"]
)

# ---------------------------------------------------------------- BACKTEST

with tab_backtest:
    st.subheader("🧠 All-in-One: Scan · Monitor · Backtest")
    st.caption(
        "One workflow for all three: set the cutoff to **today, right now** to use "
        "this as a live Screener; set it to a past date to Backtest a setup after the "
        "close; either way, shortlist your picks, tag Long/Short, and track them "
        "against a stoploss/target -- with a live-aware Refresh when the cutoff is "
        "today (see the Trade Simulation section below). Yahoo 5-minute history only "
        "goes back ~60 days."
    )

    bt_col1, bt_col2, bt_col3 = st.columns(3)
    with bt_col1:
        bt_date = st.date_input("Trading day to test", value=dt.date.today(), key="bt_date_input")
    with bt_col2:
        bt_time = st.time_input("Cutoff time", value=dt.time(9, 30), key="bt_time_input")
    with bt_col3:
        bt_universe = st.radio(
            "Universe", ["Nifty50 subset (fast test)", "Full Nifty500", "Custom list"],
            index=0, key="bt_universe",
        )

    if bt_universe == "Custom list":
        bt_custom_text = st.text_area(
            "Enter NSE symbols, comma or newline separated (no .NS suffix needed)",
            "RELIANCE, TCS, HDFCBANK, INFY, ICICIBANK",
            key="bt_custom",
        )
        raw = [s.strip().upper().removesuffix(".NS") for s in bt_custom_text.replace("\n", ",").split(",") if s.strip()]
        seen = set()
        bt_symbols = [s for s in raw if not (s in seen or seen.add(s))]
    else:
        try:
            bt_nifty_df, _bt_src = cached_nifty500_list()
            bt_all_symbols = bt_nifty_df["Symbol"].dropna().unique().tolist()
        except Exception as e:
            st.error(f"Could not load symbol list: {e}")
            bt_all_symbols = []
        bt_symbols = bt_all_symbols[:50] if bt_universe == "Nifty50 subset (fast test)" else bt_all_symbols

    bt_max_scan = st.slider("Max stocks to scan", 10, 500, 50, 10, key="bt_max_scan")
    bt_symbols = bt_symbols[:bt_max_scan]

    st.write(f"Will scan **{len(bt_symbols)}** symbols for **{bt_date}** as of **{bt_time.strftime('%H:%M')}**.")

    if st.button("▶ Run Screener As-Of Cutoff", type="primary") and bt_symbols:
        bt_period = period_for_cutoff_date(bt_date)
        progress = st.progress(0.0, text="Downloading 5-minute data...")
        bt_data_map = {}
        n_batches = max(1, (len(bt_symbols) + batch_size - 1) // batch_size)
        for b in range(n_batches):
            chunk = bt_symbols[b * batch_size:(b + 1) * batch_size]
            chunk_data = cached_fetch_batch(tuple(chunk), "5m", bt_period, len(chunk))
            bt_data_map.update(chunk_data)
            progress.progress((b + 1) / n_batches, text=f"Downloaded batch {b + 1}/{n_batches}")

        progress.progress(1.0, text="Scoring as of cutoff...")
        try:
            bt_index_df = cached_fetch_index("^NSEI", "5m", bt_period)
            bt_index_chg = day_change_pct_asof(bt_index_df, bt_date, bt_time)
        except Exception:
            bt_index_chg = None

        # Map each scanned symbol to its sector index (falls back to Nifty
        # 50 for symbols with no known/mapped industry), then fetch each
        # UNIQUE sector index only once rather than per-stock. Industry
        # lookup is independent of the universe choice above (custom-list
        # symbols may still be Nifty500 constituents).
        try:
            bt_industry_df, _ = cached_nifty500_list()
            bt_symbol_to_industry = dict(zip(bt_industry_df["Symbol"], bt_industry_df.get("Industry", pd.Series(dtype=str))))
        except Exception:
            bt_symbol_to_industry = {}
        bt_symbol_to_sector_index = {
            sym: index_for_industry(bt_symbol_to_industry.get(sym)) for sym in bt_data_map.keys()
        }
        bt_unique_sector_indices = set(bt_symbol_to_sector_index.values())
        bt_sector_chg_map = {}
        for sec_idx in bt_unique_sector_indices:
            try:
                if sec_idx == "^NSEI":
                    bt_sector_chg_map[sec_idx] = bt_index_chg  # reuse, already fetched above
                else:
                    bt_sec_df = cached_fetch_index(sec_idx, "5m", bt_period)
                    bt_sector_chg_map[sec_idx] = day_change_pct_asof(bt_sec_df, bt_date, bt_time)
            except Exception:
                bt_sector_chg_map[sec_idx] = None

        bt_rows = []
        bt_symbol_data_map = {}  # full-day df per symbol, reused in the simulation step
        for sym, df in bt_data_map.items():
            try:
                df_before, df_after = split_at_cutoff(df, bt_date, bt_time)
                if df_before.empty:
                    continue
                # Compute indicators ONCE and reuse for both the current score and the
                # trend -- score_asof() used to do this internally then discard its own
                # df_ind, which meant a separate score_trend(df_before, ...) call right
                # after it would silently operate on raw OHLCV with no indicator columns
                # at all (row.get("RSI", 0) etc all falling back to defaults -> bogus
                # "Neutral" trend regardless of the real data). Caught this with an
                # end-to-end test before it shipped, not just the isolated unit tests.
                df_before_ind = compute_all_indicators(df_before)
                result = score_symbol(df_before_ind, active_indicators, weights)
                if result is None or result["signal_label"] == "No Data":
                    continue
                entry_price = entry_price_at_cutoff(df_before)
                if entry_price is None:
                    continue
                bt_stock_chg = day_change_pct_asof(df, bt_date, bt_time)
                bt_sector_idx = bt_symbol_to_sector_index.get(sym, "^NSEI")
                bt_sector_chg = bt_sector_chg_map.get(bt_sector_idx)
                bt_aligned = alignment_label(result["signal_label"], bt_stock_chg, bt_sector_chg)
                bt_aligned_display = {True: "🟢 Aligned", False: "🔴 Not Aligned"}.get(bt_aligned, "⚪ -")
                bt_trend = score_trend(df_before_ind, active_indicators, weights, lookback=5)
                bt_trend_seq, bt_trend_conviction = trend_summary(bt_trend)
                bt_rows.append({
                    "Symbol": sym,
                    "Entry Price (at cutoff)": round(entry_price, 2),
                    "Chg % (as of cutoff)": bt_stock_chg,
                    "Trade Score": result["trade_score"],
                    "Confidence": result["confidence"],
                    "Signal": result["signal_label"],
                    "Trend (last 5)": bt_trend_seq,
                    "Trend Conviction": bt_trend_conviction,
                    "Sector": index_display_name(bt_sector_idx),
                    "Sector Chg %": bt_sector_chg,
                    "Aligned": bt_aligned_display,
                    "RSI": result.get("rsi"),
                    "ADX": result.get("adx"),
                    "Extension (ATR)": result.get("extension_atr"),
                    "VWAP %": result.get("vwap_pct"),
                    "Patterns": ", ".join(result.get("patterns", [])) or "-",
                    "Candles after cutoff": len(df_after),
                })
                bt_symbol_data_map[sym] = df
            except Exception:
                continue
        progress.empty()

        if not bt_rows:
            st.error(
                "No usable data for that date/cutoff. Check: the date has to be a trading "
                "day within the last ~60 days, and needs enough prior-day history before it "
                "for indicators like EMA50 to be valid (very early listings/IPOs may fail)."
            )
        else:
            bt_result_df = pd.DataFrame(bt_rows).sort_values("Trade Score", ascending=False).reset_index(drop=True)
            st.session_state["bt_result_df"] = bt_result_df
            st.session_state["bt_symbol_data_map"] = bt_symbol_data_map
            st.session_state["bt_cutoff_date"] = bt_date
            st.session_state["bt_cutoff_time"] = bt_time
            st.session_state["bt_index_chg"] = bt_index_chg
            # a fresh screener run invalidates any prior simulation
            st.session_state.pop("bt_sim_df", None)

    if "bt_result_df" in st.session_state:
        bt_result_df = st.session_state["bt_result_df"]
        st.markdown(
            f"**As-of-cutoff screener results** — "
            f"{st.session_state['bt_cutoff_date']} @ {st.session_state['bt_cutoff_time'].strftime('%H:%M')} "
            f"({len(bt_result_df)} scored)"
        )
        st.caption(
            "**Trend (last 5)** shows the Signal at each of the last 5 candles, e.g. "
            "`N -> B -> B -> SB -> SB` = has been building; `S -> S -> S -> S -> B` = "
            "just flipped this candle -- same current Signal, very different situation. "
            "**Trend Conviction** turns that into one 0-100 number: how much the recent "
            "history actually agrees with and supports the CURRENT signal. A high "
            "Trade Score with LOW conviction is exactly the case worth a second look."
        )

        bt_index_chg = st.session_state.get("bt_index_chg")
        if bt_index_chg is not None:
            st.metric("Nifty 50 (as of cutoff)", f"{bt_index_chg:+.2f}%", delta=f"{bt_index_chg:+.2f}%")
            st.caption(
                "The **Aligned** column does the sector-specific version of this check "
                "automatically -- e.g. TCS is compared against Nifty IT, not the broad "
                "index. 🟢 = signal agrees with both the stock's own move AND its sector "
                "as of the cutoff; 🔴 = it doesn't; ⚪ = Neutral signal or no sector match. "
                "Info only, not a filter -- **Sector**, **Sector Chg %** and "
                "**Chg % (as of cutoff)** are shown so you can judge it yourself."
            )
        else:
            st.caption("Nifty 50 index context unavailable for this cutoff (Yahoo fetch issue) - filters below still work as normal.")

        bt_colf1, bt_colf2, bt_colf3 = st.columns(3)
        with bt_colf1:
            bt_signal_filter = st.multiselect(
                "Filter by signal", ["Strong Buy", "Buy", "Neutral", "Sell", "Strong Sell"],
                default=["Strong Buy", "Buy", "Strong Sell", "Sell"],
                key="bt_signal_filter",
            )
        with bt_colf2:
            bt_min_confidence = st.slider("Minimum confidence", 0, 100, 0, key="bt_min_confidence")
        with bt_colf3:
            bt_sort_by = st.selectbox("Sort by", ["Trade Score", "Confidence"], index=0, key="bt_sort_by")

        bt_filtered_df = bt_result_df[
            bt_result_df["Signal"].isin(bt_signal_filter) & (bt_result_df["Confidence"] >= bt_min_confidence)
        ].sort_values(bt_sort_by, ascending=False).reset_index(drop=True)

        st.markdown(f"**{len(bt_filtered_df)}** stocks match your filters (of {len(bt_result_df)} scored).")

        bt_display_df = bt_filtered_df.copy()
        bt_display_df.insert(0, "Simulate", False)
        bt_edited = st.data_editor(
            bt_display_df,
            column_config={
                "Simulate": st.column_config.CheckboxColumn("Add to Simulation"),
                "Trade Score": st.column_config.ProgressColumn("Trade Score", min_value=0, max_value=100, format="%.0f"),
                "Confidence": st.column_config.ProgressColumn("Confidence", min_value=0, max_value=100, format="%.0f"),
                "Trend Conviction": st.column_config.ProgressColumn("Trend Conviction", min_value=0, max_value=100, format="%.0f"),
            },
            disabled=[c for c in bt_display_df.columns if c != "Simulate"],
            hide_index=True,
            use_container_width=True,
            height=400,
            key="bt_screener_editor",
        )
        bt_shortlist = bt_edited.loc[bt_edited["Simulate"], "Symbol"].tolist()

        if bt_shortlist and st.button("💾 Save shortlist to watchlist.json"):
            merged = set(load_watchlist()) | set(bt_shortlist)
            save_watchlist(list(merged))
            st.success(f"Saved ({len(merged)} symbols total). Downloadable from the sidebar backup.")

        st.markdown("---")
        st.markdown("### Trade Simulation")
        st.caption(
            "Tick 5-10 stocks above (Add to Simulation), tag each Long/Short, set your "
            "stoploss/target % below, then run the replay. Same-candle ambiguity is "
            "resolved as stoploss-hit-first (worst case)."
        )

        bt_col_sl, bt_col_tg = st.columns(2)
        with bt_col_sl:
            bt_stoploss_pct = st.slider("Stoploss %", 0.1, 5.0, 0.5, 0.1, key="bt_sl_pct")
        with bt_col_tg:
            bt_target_pct = st.slider("Target %", 0.1, 5.0, 1.0, 0.1, key="bt_tg_pct")

        if not bt_shortlist:
            st.info("Tick stocks above in the **Add to Simulation** column to build your shortlist.")
        else:
            bt_direction_defaults = st.session_state.get("bt_directions", {})
            bt_direction_rows = [
                {
                    "Symbol": sym,
                    "Entry Price": bt_result_df.loc[bt_result_df["Symbol"] == sym, "Entry Price (at cutoff)"].iloc[0],
                    "Direction": bt_direction_defaults.get(sym, "Long"),
                }
                for sym in bt_shortlist
            ]
            bt_direction_df = pd.DataFrame(bt_direction_rows)
            bt_edited_dir = st.data_editor(
                bt_direction_df,
                column_config={
                    "Direction": st.column_config.SelectboxColumn("Direction", options=["Long", "Short"]),
                },
                disabled=["Symbol", "Entry Price"],
                hide_index=True,
                use_container_width=True,
                key="bt_direction_editor",
            )
            st.session_state["bt_directions"] = dict(zip(bt_edited_dir["Symbol"], bt_edited_dir["Direction"]))

            bt_cutoff_date = st.session_state["bt_cutoff_date"]
            bt_cutoff_time = st.session_state["bt_cutoff_time"]
            bt_is_live = bt_cutoff_date == dt.date.today()
            bt_sim_button_label = "🔄 Refresh (live -- re-checks against just-arrived candles)" if bt_is_live else "🧪 Run Trade Simulation"

            if bt_is_live:
                st.caption(
                    "Cutoff is today, so this doubles as live monitoring: every click "
                    "re-fetches the latest candles since your cutoff and re-checks "
                    "whether stoploss/target has been hit since then -- click again "
                    "any time through the session."
                )

            if st.button(bt_sim_button_label, type="primary"):
                bt_symbol_data_map = st.session_state["bt_symbol_data_map"]
                bt_sim_rows = []
                for _, r in bt_edited_dir.iterrows():
                    sym = r["Symbol"]
                    if bt_is_live:
                        # Re-fetch fresh so newly-arrived candles since the cutoff are
                        # included -- the original scan's snapshot would otherwise be
                        # stale for anything but a closed historical day.
                        try:
                            df_full = cached_fetch_single(sym, "5m", "5d")
                        except Exception:
                            df_full = bt_symbol_data_map.get(sym)
                    else:
                        df_full = bt_symbol_data_map.get(sym)
                    if df_full is None:
                        continue
                    _, df_after = split_at_cutoff(df_full, bt_cutoff_date, bt_cutoff_time)
                    sim = simulate_trade(df_after, r["Entry Price"], r["Direction"], bt_stoploss_pct, bt_target_pct)
                    bt_sim_rows.append({
                        "Symbol": sym,
                        "Direction": r["Direction"],
                        "Entry Price": round(r["Entry Price"], 2),
                        "Outcome": sim["outcome"],
                        "Hit Time": sim["hit_time"].strftime("%H:%M") if sim["hit_time"] is not None else "-",
                        "Hit Price": sim["hit_price"],
                        "P/L %": sim["pl_pct"],
                        "Best seen (MFE %)": sim["mfe_pct"],
                        "Worst seen (MAE %)": sim["mae_pct"],
                    })
                st.session_state["bt_sim_df"] = pd.DataFrame(bt_sim_rows)

        if "bt_sim_df" in st.session_state and not st.session_state["bt_sim_df"].empty:
            bt_sim_df = st.session_state["bt_sim_df"]
            st.markdown("#### Results")

            outcome_emoji = {
                "Target Hit": "🎯 Target Hit",
                "Stoploss Hit": "🛑 Stoploss Hit",
                "No Hit (EOD)": "➖ No Hit (EOD)",
                "No Data": "⚠️ No Data",
            }
            bt_sim_display = bt_sim_df.copy()
            bt_sim_display["Outcome"] = bt_sim_display["Outcome"].map(lambda v: outcome_emoji.get(v, v))
            st.dataframe(bt_sim_display, hide_index=True, use_container_width=True)

            n_target = int((bt_sim_df["Outcome"] == "Target Hit").sum())
            n_stop = int((bt_sim_df["Outcome"] == "Stoploss Hit").sum())
            n_nohit = int((bt_sim_df["Outcome"] == "No Hit (EOD)").sum())
            bt_m1, bt_m2, bt_m3 = st.columns(3)
            bt_m1.metric("🎯 Target hit", n_target)
            bt_m2.metric("🛑 Stoploss hit", n_stop)
            bt_m3.metric("➖ No hit by EOD", n_nohit)

        if bt_shortlist:
            st.markdown("---")
            st.markdown("### 🔍 Inspect a stock's chart")
            bt_symbol_data_map = st.session_state.get("bt_symbol_data_map", {})
            bt_inspect_options = [s for s in bt_shortlist if s in bt_symbol_data_map]
            if not bt_inspect_options:
                st.caption("Run the scan above first to make charts available for your shortlist.")
            else:
                bt_focus_symbol = st.selectbox("Symbol", bt_inspect_options, key="bt_focus_symbol")
                if bt_focus_symbol:
                    bt_focus_df_ind = compute_all_indicators(bt_symbol_data_map[bt_focus_symbol])
                    bt_focus_df_before, _ = split_at_cutoff(
                        bt_focus_df_ind, st.session_state["bt_cutoff_date"], st.session_state["bt_cutoff_time"]
                    )
                    bt_focus_result = score_asof(bt_focus_df_before, active_indicators, weights)
                    if bt_focus_result:
                        fc1, fc2, fc3 = st.columns(3)
                        fc1.metric("Trade Score", f"{bt_focus_result['trade_score']:.0f} / 100")
                        fc2.metric("Confidence", f"{bt_focus_result['confidence']:.0f} / 100")
                        fc3.metric("Signal", bt_focus_result["signal_label"])

                    bt_chart_range_options = ["Today only", "Last 2 days", "Last 3 days", "Last 5 days", "All downloaded"]
                    bt_chart_range = st.radio("Chart range", bt_chart_range_options, index=0, horizontal=True, key="bt_chart_range")
                    bt_chart_df = filter_chart_range(bt_focus_df_ind, bt_chart_range, "5m")
                    st.plotly_chart(make_candlestick_chart(bt_chart_df, bt_focus_symbol), use_container_width=True)

                    if bt_focus_result and bt_focus_result.get("breakdown"):
                        st.markdown("**Indicator breakdown** (signal contribution, -1 bearish to +1 bullish)")
                        bt_breakdown_df = pd.DataFrame([
                            {"Indicator": k, "Signal": round(v, 2)}
                            for k, v in bt_focus_result["breakdown"].items()
                        ])
                        st.bar_chart(bt_breakdown_df.set_index("Indicator"))
                        if bt_focus_result.get("patterns"):
                            st.markdown(f"**Candlestick patterns detected:** {', '.join(bt_focus_result['patterns'])}")
    else:
        st.info("Set a date/cutoff/universe above and click **Run Screener As-Of Cutoff** to begin.")

# ------------------------------------------------------------------ MOBILE

with tab_mobile:
    st.subheader("📱 Mobile")
    st.caption(
        "Lightweight, on-the-go mode: Scan gives you 10 cards (top-5 Buy-aligned + "
        "top-5 Sell-aligned), tap the ones you want to track, then Monitor checks "
        "them against your stoploss/target with a manual refresh -- no charts, no "
        "sidebar tuning, minimal data use."
    )

    mob_universe = st.radio("Universe", ["Nifty50", "Nifty500"], index=0, horizontal=True, key="mobile_universe")

    if st.button("📡 Scan Now", type="primary"):
        try:
            mob_nifty_df, _ = cached_nifty500_list()
            mob_all_symbols = mob_nifty_df["Symbol"].dropna().unique().tolist()
        except Exception as e:
            st.error(f"Could not load symbol list: {e}")
            mob_all_symbols = []
        mob_symbols = mob_all_symbols[:50] if mob_universe == "Nifty50" else mob_all_symbols

        with st.spinner(f"Scanning {len(mob_symbols)} symbols..."):
            mob_data_map = {}
            n_batches = max(1, (len(mob_symbols) + batch_size - 1) // batch_size)
            for b in range(n_batches):
                chunk = mob_symbols[b * batch_size:(b + 1) * batch_size]
                mob_data_map.update(cached_fetch_batch(tuple(chunk), "5m", "5d", len(chunk)))

            try:
                mob_index_chg = day_change_pct(cached_fetch_index("^NSEI", "5m", "5d"))
            except Exception:
                mob_index_chg = None

            try:
                mob_symbol_to_industry = dict(zip(mob_nifty_df["Symbol"], mob_nifty_df.get("Industry", pd.Series(dtype=str))))
            except Exception:
                mob_symbol_to_industry = {}
            mob_symbol_to_sector = {sym: index_for_industry(mob_symbol_to_industry.get(sym)) for sym in mob_data_map}
            mob_sector_chg_map = {}
            for sec_idx in set(mob_symbol_to_sector.values()):
                if sec_idx == "^NSEI":
                    mob_sector_chg_map[sec_idx] = mob_index_chg
                else:
                    try:
                        mob_sector_chg_map[sec_idx] = day_change_pct(cached_fetch_index(sec_idx, "5m", "5d"))
                    except Exception:
                        mob_sector_chg_map[sec_idx] = None

            mob_rows = []
            for sym, df in mob_data_map.items():
                try:
                    df_ind = compute_all_indicators(df)
                    result = score_symbol(df_ind, active_indicators, weights)
                    stock_chg = day_change_pct(df)
                    sector_idx = mob_symbol_to_sector.get(sym, "^NSEI")
                    sector_chg = mob_sector_chg_map.get(sector_idx)
                    aligned = alignment_label(result["signal_label"], stock_chg, sector_chg)
                    if not aligned:  # only keep 🟢-aligned candidates -- None/False both drop out
                        continue
                    mob_rows.append({
                        "Symbol": sym,
                        "Signal": result["signal_label"],
                        "Trade Score": result["trade_score"],
                        "Chg %": stock_chg,
                        "Sector": index_display_name(sector_idx),
                        "LTP": result.get("close"),
                        "EntryTime": df.index[-1],
                    })
                except Exception:
                    continue

        mob_buys = sorted(
            [r for r in mob_rows if r["Signal"] in ("Buy", "Strong Buy")],
            key=lambda r: r["Trade Score"], reverse=True,
        )[:5]
        mob_sells = sorted(
            [r for r in mob_rows if r["Signal"] in ("Sell", "Strong Sell")],
            key=lambda r: r["Trade Score"],
        )[:5]
        st.session_state["mobile_cards"] = mob_buys + mob_sells
        st.session_state["mobile_scan_time"] = dt.datetime.now().strftime("%H:%M:%S")
        if len(mob_buys) < 5 or len(mob_sells) < 5:
            st.caption(
                f"Found {len(mob_buys)} Buy-aligned and {len(mob_sells)} Sell-aligned candidates "
                f"(fewer than 5 on one side today -- showing what's there)."
            )

    if "mobile_cards" in st.session_state:
        mob_cards = st.session_state["mobile_cards"]
        st.markdown(f"**{len(mob_cards)} candidates** as of {st.session_state.get('mobile_scan_time', '-')}")

        if not mob_cards:
            st.info("No Aligned Buy/Sell candidates found this scan. Try again in a bit, or switch to Nifty500 for more coverage.")

        for card in mob_cards:
            sym = card["Symbol"]
            is_buy = card["Signal"] in ("Buy", "Strong Buy")
            with st.container(border=True):
                c1, c2 = st.columns([2, 1])
                with c1:
                    st.markdown(f"**{sym}**  {'🟢' if is_buy else '🔴'} {card['Signal']}")
                    st.caption(f"{card['Sector']} · Chg {card['Chg %']:+.2f}%" if card["Chg %"] is not None else card["Sector"])
                with c2:
                    st.metric("Score", card["Trade Score"], label_visibility="collapsed")
                st.caption(f"LTP ₹{card['LTP']}")
                st.checkbox("Add to Monitor", key=f"mobile_pick_{sym}")

        if mob_cards and st.button("▶ Start Monitoring Selected", type="primary"):
            monitor_list = st.session_state.get("mobile_monitor_list", [])
            existing_symbols = {m["Symbol"] for m in monitor_list}
            added = 0
            for card in mob_cards:
                sym = card["Symbol"]
                if st.session_state.get(f"mobile_pick_{sym}") and sym not in existing_symbols:
                    monitor_list.append({
                        "Symbol": sym,
                        "EntryPrice": card["LTP"],
                        "EntryTime": card["EntryTime"],
                        "Direction": "Long" if card["Signal"] in ("Buy", "Strong Buy") else "Short",
                    })
                    added += 1
            st.session_state["mobile_monitor_list"] = monitor_list
            if added:
                st.success(f"Added {added} to Monitoring below.")
            else:
                st.warning("Tick at least one card above first.")

    st.markdown("---")
    st.markdown("### Monitoring")

    mob_monitor_list = st.session_state.get("mobile_monitor_list", [])
    if not mob_monitor_list:
        st.caption("Nothing being monitored yet -- scan above and add a few cards.")
    else:
        mc1, mc2, mc3 = st.columns(3)
        with mc1:
            mob_sl_pct = st.number_input("Stoploss %", 0.1, 5.0, 0.5, 0.1, key="mobile_sl_pct")
        with mc2:
            mob_target_pct = st.number_input("Target %", 0.1, 5.0, 1.0, 0.1, key="mobile_target_pct")
        with mc3:
            st.write("")
            st.write("")
            mob_refresh = st.button("🔄 Refresh", type="primary")

        mob_remove = None
        for i, m in enumerate(mob_monitor_list):
            sym = m["Symbol"]
            with st.container(border=True):
                h1, h2 = st.columns([3, 1])
                with h1:
                    st.markdown(f"**{sym}**")
                with h2:
                    if st.button("✖", key=f"mobile_remove_{sym}"):
                        mob_remove = i
                m["Direction"] = st.selectbox(
                    "Direction", ["Long", "Short"],
                    index=0 if m["Direction"] == "Long" else 1,
                    key=f"mobile_dir_{sym}", label_visibility="collapsed",
                )
                st.caption(f"Entry ₹{m['EntryPrice']} at {m['EntryTime'].strftime('%H:%M')}")

                result = st.session_state.get("mobile_monitor_results", {}).get(sym)
                if result is None:
                    st.caption("Not refreshed yet.")
                else:
                    outcome_emoji = {
                        "Target Hit": "🎯 Target Hit", "Stoploss Hit": "🛑 Stoploss Hit",
                        "No Hit (EOD)": "🟡 Still open", "No Data": "⚠️ No data yet",
                    }
                    st.markdown(f"**{outcome_emoji.get(result['outcome'], result['outcome'])}**")
                    if result["pl_pct"] is not None:
                        st.caption(f"P/L so far: {result['pl_pct']:+.2f}%  ·  last ₹{result['hit_price']} at {result['hit_time'].strftime('%H:%M') if result['hit_time'] is not None else '-'}")

        if mob_remove is not None:
            mob_monitor_list.pop(mob_remove)
            st.session_state["mobile_monitor_list"] = mob_monitor_list
            st.rerun()

        if mob_refresh:
            with st.spinner("Refreshing..."):
                mob_results = {}
                for m in mob_monitor_list:
                    sym = m["Symbol"]
                    try:
                        fresh_df = cached_fetch_single(sym, "5m", "5d")
                        entry_time = m["EntryTime"]
                        _, df_after = split_at_cutoff(fresh_df, entry_time.date(), entry_time.time())
                        mob_results[sym] = simulate_trade(
                            df_after, m["EntryPrice"], m["Direction"], mob_sl_pct, mob_target_pct
                        )
                    except Exception:
                        mob_results[sym] = {"outcome": "No Data", "hit_time": None, "hit_price": None, "pl_pct": None}
                st.session_state["mobile_monitor_results"] = mob_results
            st.rerun()

        if st.button("Clear all monitoring"):
            st.session_state["mobile_monitor_list"] = []
            st.session_state["mobile_monitor_results"] = {}
            st.rerun()