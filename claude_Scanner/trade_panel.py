"""
trade_panel.py
--------------
Stage 3 — Kite trade panel, extracted into its own module so it can be
toggled off in app.py (ENABLE_STAGE_3 = False) without deleting the code.
Re-enable by flipping that flag once you are ready to test this again.
"""

import pandas as pd
import streamlit as st

from kite_client import KiteSession, KITECONNECT_AVAILABLE


def render_trade_panel():
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
        return
    
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
