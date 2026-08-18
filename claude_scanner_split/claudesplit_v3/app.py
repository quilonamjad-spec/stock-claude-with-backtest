"""
NSE 500 Bull/Bear Scanner + Ranking
------------------------------------
Stage 1 (Scan): pulls 5-min OHLCV data from Yahoo Finance for the NSE 500
universe, evaluates the bull/bear condition set as of a chosen date & time,
and shows every stock that passed either side.

Stage 2 (Rank): takes the Stage-1 results and scores/sorts them by
conviction strength (how strongly each condition was cleared), using
user-adjustable weights.

This file is UI only — the actual logic lives in:
  config.py         shared constants, timezone helper, sector-index config
  data.py            Yahoo/NSE fetching (universe, sector maps, OHLCV batches)
  indicators.py       indicator math + Bull/Bear condition evaluation
  gate_rank.py         quality gate (exhaustion filter) + conviction ranking
  scan_pipeline.py      ties data.py + indicators.py into the Stage 1 scan
  simulate.py           trade replay engine + transaction cost estimate
  trade_panel.py        Stage 3 Kite order placement (currently parked)
  kite_client.py         Kite Connect auth/order wrapper

Run locally:
    pip install -r requirements.txt
    streamlit run app.py
"""

from datetime import timedelta, time as dtime

import pandas as pd
import streamlit as st
import yfinance as yf

from config import IST_TZ, now_ist, DEFAULT_WEIGHTS
from data import get_nse500_symbols, fetch_batch
from gate_rank import quality_gate, rank_results
from scan_pipeline import run_scan_pipeline
from simulate import estimate_roundtrip_cost_pct, simulate_trade
from trade_journal import load_journal, record_trade, delete_last_trade, summary_stats

st.set_page_config(page_title="NSE 500 Bull/Bear Scanner", layout="wide")


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

    st.number_input(
        "💰 Investment per trade (₹)",
        min_value=100.0, value=float(st.session_state.get("investment_per_trade", 10000.0)),
        step=500.0, key="investment_per_trade",
        help="Set once here — Stage 4 and Stage 5 default to this same amount "
             "(you can still override it there). Stage 2 itself has no stoploss/"
             "target attached yet, so it can't show real profit/loss — just what "
             "taking all the picks below would cost in capital.",
    )
    rdf = st.session_state.ranked_df
    n_bull = int((rdf["Phase"] == "Bull").sum())
    n_bear = int((rdf["Phase"] == "Bear").sum())
    inv = st.session_state.investment_per_trade
    cap_col1, cap_col2, cap_col3 = st.columns(3)
    cap_col1.metric("Bull picks", n_bull, f"₹{n_bull * inv:,.0f} capital")
    cap_col2.metric("Bear picks", n_bear, f"₹{n_bear * inv:,.0f} capital")
    cap_col3.metric("Total capital required", f"₹{(n_bull + n_bear) * inv:,.0f}")
    st.caption(
        "For actual profit/loss in ₹, run these through Stage 4 (live) or "
        "Stage 5 (historical) — both use this same investment amount by default."
    )

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
        "💰 Investment per trade (₹)",
        min_value=100.0, value=float(st.session_state.get("investment_per_trade", 10000.0)),
        step=500.0, key="sim_trade_value",
        help="Defaults to the amount set in Stage 2, if you set one there. Used "
             "both for the transaction-cost estimate and to show ₹ profit/loss "
             "alongside the % figures.",
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
        sr = st.session_state.simulation_results.copy()
        sr["₹ P/L"] = (sr["Net P/L %"] / 100 * sim_trade_value).round(2)
        display_cols = [
            "Symbol", "Direction", "Entry Price", "Outcome", "Hit Time", "Hit Price",
            "P/L %", "Net P/L %", "₹ P/L", "Best seen (MFE %)", "Worst seen (MAE %)",
            "Current SL", "Trail Armed",
        ]
        st.dataframe(sr[display_cols], use_container_width=True)
        st.caption(
            f"Net P/L% assumes ~{estimate_roundtrip_cost_pct(sim_trade_value):.3f}% "
            f"round-trip transaction cost per trade at ₹{sim_trade_value:,.0f} investment per trade."
        )

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("🎯 Target/Trail hit", int(sr["Outcome"].isin(["Target Hit", "Trailing Stop Hit"]).sum()))
        m2.metric("🔴 Stoploss hit", int((sr["Outcome"] == "Stoploss Hit").sum()))
        m3.metric("➖ No hit (open)", int((sr["Outcome"] == "No Hit (EOD)").sum()))
        m4.metric("Avg Net P/L %", f"{sr['Net P/L %'].mean():.2f}%")

        n_trades = len(sr)
        m5, m6, m7 = st.columns(3)
        m5.metric("Capital deployed", f"₹{n_trades * sim_trade_value:,.0f}")
        m6.metric("Total ₹ P/L", f"₹{sr['₹ P/L'].sum():,.2f}")
        m7.metric("Avg ₹ P/L / trade", f"₹{sr['₹ P/L'].mean():,.2f}")
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
    "💰 Investment per trade (₹)",
    min_value=100.0, value=float(st.session_state.get("investment_per_trade", 10000.0)),
    step=500.0, key="bt_trade_value",
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
        # Explicit per-phase filter + head + concat instead of
        # groupby(...).apply(lambda g: g.head(n)) — some pandas versions
        # silently drop the grouping column ("Phase") from the apply result,
        # which then breaks pick["Phase"] below. This form is version-safe.
        picks = pd.concat(
            [day_ranked[day_ranked["Phase"] == p].head(bt_top_n) for p in day_ranked["Phase"].unique()],
            ignore_index=True,
        ) if not day_ranked.empty else day_ranked

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
        bt_df["₹ P/L"] = (bt_df["Net P/L %"] / 100 * bt_trade_value).round(2)
        bt_df["Cumulative Net P/L %"] = bt_df["Net P/L %"].cumsum()
        bt_df["Cumulative ₹ P/L"] = bt_df["₹ P/L"].cumsum()
        st.session_state.backtest_results = bt_df
        st.session_state.backtest_trade_value = bt_trade_value
    else:
        st.session_state.backtest_results = pd.DataFrame()
        st.warning(
            "No trades generated across the tested range — try more days, disabling "
            "the quality gate, or turning off 'Faster' mode for a wider universe."
        )

if st.session_state.backtest_results is not None and not st.session_state.backtest_results.empty:
    bt = st.session_state.backtest_results.reset_index(drop=True)
    inv = st.session_state.get("backtest_trade_value", bt_trade_value)
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

    k5, k6, k7, k8 = st.columns(4)
    k5.metric("Avg win", f"{wins.mean():.2f}%" if len(wins) else "—")
    k6.metric("Avg loss", f"{losses.mean():.2f}%" if len(losses) else "—")
    k7.metric("Capital deployed", f"₹{total_trades * inv:,.0f}", help=f"{total_trades} trades × ₹{inv:,.0f} each")
    k8.metric("Total ₹ P/L", f"₹{bt['₹ P/L'].sum():,.2f}")

    st.markdown(f"**Equity curve — ₹{inv:,.0f} invested per trade**")
    st.caption(
        "Each candle is one trade: body = open→close cumulative ₹ P/L (net of "
        "costs), wick = the best/worst unrealized excursion during that trade "
        "(gross, from MFE/MAE) — so a long wick past the body shows a trade "
        "that moved further in your favor (or against you) than what was "
        "actually realized. The line traces the running total on top."
    )

    open_vals = bt["Cumulative ₹ P/L"].shift(1).fillna(0.0)
    close_vals = bt["Cumulative ₹ P/L"]
    high_raw = open_vals + (bt["Best seen (MFE %)"] / 100 * inv)
    low_raw = open_vals + (bt["Worst seen (MAE %)"] / 100 * inv)
    high_vals = pd.concat([high_raw, close_vals], axis=1).max(axis=1)
    low_vals = pd.concat([low_raw, close_vals], axis=1).min(axis=1)
    x_labels = bt["Date"] + " " + bt["Symbol"]

    try:
        import plotly.graph_objects as go

        fig = go.Figure()
        fig.add_trace(go.Candlestick(
            x=x_labels, open=open_vals, high=high_vals, low=low_vals, close=close_vals,
            increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
            name="Per-trade ₹ P/L",
        ))
        fig.add_trace(go.Scatter(
            x=x_labels, y=close_vals, mode="lines+markers", name="Cumulative ₹ P/L",
            line=dict(color="#42a5f5", width=2), marker=dict(size=4),
        ))
        fig.add_hline(y=0, line_dash="dot", line_color="gray", annotation_text="Break-even")
        fig.update_layout(
            template="plotly_dark", height=450, xaxis_rangeslider_visible=False,
            xaxis_title=None, yaxis_title="Cumulative ₹ P/L",
            margin=dict(l=10, r=10, t=10, b=10),
        )
        st.plotly_chart(fig, use_container_width=True)
    except ImportError:
        st.warning("Install `plotly` (`pip install plotly`) for the candlestick equity chart. Showing a plain line instead.")
        st.line_chart(bt["Cumulative ₹ P/L"])

    by_day = bt.groupby("Date").agg(
        **{"Trades": ("Symbol", "count"), "Day Net P/L %": ("Net P/L %", "sum"), "Day ₹ P/L": ("₹ P/L", "sum")}
    ).reset_index()
    st.caption("By day")
    st.dataframe(by_day, use_container_width=True)

    st.caption("All trades")
    trade_cols = [
        "Date", "Symbol", "Direction", "Entry Price", "Outcome", "Hit Time",
        "P/L %", "Net P/L %", "₹ P/L", "Rank Score", "Best seen (MFE %)", "Worst seen (MAE %)",
    ]
    st.dataframe(bt[trade_cols], use_container_width=True)

    bt_csv = bt[trade_cols].to_csv(index=False).encode("utf-8")
    st.download_button("Download backtest trades (CSV)", bt_csv, "backtest_trades.csv", "text/csv")

# --------------------------------------------------------------------------
# TRADE JOURNAL — real trades you actually took, logged manually.
# Informational only: does not feed the scan, gate, or ranking.
# --------------------------------------------------------------------------
st.divider()
st.header("📓 Trade Journal")
st.caption(
    "Log a real trade you took — symbol, entry/exit time, quantity (shares). "
    "Entry and exit prices are looked up automatically from Yahoo's 5-min "
    "bars at those timestamps; P/L is calculated net of the same "
    "transaction-cost model used in the backtest. Purely a record for your "
    "own review — it doesn't feed back into the scan, gate, or ranking."
)

with st.form("journal_entry_form"):
    j1, j2, j3 = st.columns(3)
    j_symbol = j1.text_input("Symbol (no .NS needed)", key="j_symbol")
    j_direction = j2.selectbox("Direction", ["Long", "Short"], key="j_direction")
    j_qty = j3.number_input("Quantity (shares)", min_value=1, value=1, step=1, key="j_qty")

    j4, j5 = st.columns(2)
    with j4:
        j_entry_date = st.date_input("Entry date", value=now_ist().date(), key="j_entry_date")
        j_entry_time = st.time_input("Entry time", value=now_ist().time().replace(second=0, microsecond=0), key="j_entry_time")
    with j5:
        j_exit_date = st.date_input("Exit date", value=now_ist().date(), key="j_exit_date")
        j_exit_time = st.time_input("Exit time", value=now_ist().time().replace(second=0, microsecond=0), key="j_exit_time")

    submitted = st.form_submit_button("➕ Log trade", type="primary")

if submitted:
    if not j_symbol.strip():
        st.error("Enter a symbol.")
    else:
        entry_ts = pd.Timestamp.combine(j_entry_date, j_entry_time).tz_localize(IST_TZ)
        exit_ts = pd.Timestamp.combine(j_exit_date, j_exit_time).tz_localize(IST_TZ)
        if exit_ts <= entry_ts:
            st.error("Exit time must be after entry time.")
        else:
            try:
                row = record_trade(j_symbol, j_direction, entry_ts, exit_ts, j_qty)
                st.success(
                    f"Logged {row['Symbol']} ({row['Direction']}, {row['Quantity']} sh): "
                    f"entry ₹{row['Entry Price']} → exit ₹{row['Exit Price']}, "
                    f"net {row['Net P/L %']}% (₹{row['Net ₹ P/L']})"
                )
            except ValueError as e:
                st.error(str(e))

journal_df = load_journal()
if not journal_df.empty:
    stats = summary_stats(journal_df)
    jc1, jc2, jc3 = st.columns(3)
    jc1.metric("Trades logged", stats["trades"])
    jc2.metric("Win rate", f"{stats['win_rate']:.0f}%" if stats["win_rate"] is not None else "—")
    jc3.metric("Total net ₹ P/L", f"₹{stats['total_net_rs']:,.2f}")

    st.dataframe(journal_df.iloc[::-1], use_container_width=True)  # most recent first

    jd1, jd2 = st.columns(2)
    journal_csv = journal_df.to_csv(index=False).encode("utf-8")
    jd1.download_button("Download journal (CSV)", journal_csv, "trade_journal.csv", "text/csv")
    if jd2.button("🗑️ Delete last entry"):
        if delete_last_trade():
            st.rerun()

    # ---- Equity curve, same candlestick style as the historical backtest:
    # body = cumulative ₹ P/L per trade (open→close), wick = best/worst
    # unrealized excursion (MFE/MAE) during that trade, at each trade's own
    # quantity — unlike the backtest, real journal trades aren't all the
    # same size, so the wick math uses each row's own Amount Invested
    # rather than one fixed figure.
    st.markdown("**Equity curve**")
    st.caption(
        "Each candle is one logged trade, in the order you entered them: "
        "body = cumulative ₹ P/L (net of costs), wick = the best/worst "
        "unrealized move seen during that trade (gross, from real 5-min "
        "highs/lows) — a wick past the body means the trade moved further "
        "in your favor (or against you) than what you actually realized. "
        "The line traces the running total."
    )

    j_bt = journal_df.copy()
    j_bt["Cumulative ₹ P/L"] = j_bt["Net ₹ P/L"].cumsum()
    j_open = j_bt["Cumulative ₹ P/L"].shift(1).fillna(0.0)
    j_close = j_bt["Cumulative ₹ P/L"]
    j_high_raw = j_open + (j_bt["Best seen (MFE %)"] / 100 * j_bt["Amount Invested"])
    j_low_raw = j_open + (j_bt["Worst seen (MAE %)"] / 100 * j_bt["Amount Invested"])
    j_high = pd.concat([j_high_raw, j_close], axis=1).max(axis=1)
    j_low = pd.concat([j_low_raw, j_close], axis=1).min(axis=1)
    j_labels = j_bt["Date"].astype(str) + " " + j_bt["Symbol"]

    try:
        import plotly.graph_objects as go

        jfig = go.Figure()
        jfig.add_trace(go.Candlestick(
            x=j_labels, open=j_open, high=j_high, low=j_low, close=j_close,
            increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
            name="Per-trade ₹ P/L",
        ))
        jfig.add_trace(go.Scatter(
            x=j_labels, y=j_close, mode="lines+markers", name="Cumulative ₹ P/L",
            line=dict(color="#42a5f5", width=2), marker=dict(size=4),
        ))
        jfig.add_hline(y=0, line_dash="dot", line_color="gray", annotation_text="Break-even")
        jfig.update_layout(
            template="plotly_dark", height=450, xaxis_rangeslider_visible=False,
            xaxis_title=None, yaxis_title="Cumulative ₹ P/L",
            margin=dict(l=10, r=10, t=10, b=10),
        )
        st.plotly_chart(jfig, use_container_width=True)
    except ImportError:
        st.warning("Install `plotly` (`pip install plotly`) for the candlestick equity chart. Showing a plain line instead.")
        st.line_chart(j_close)
else:
    st.caption("No trades logged yet.")
