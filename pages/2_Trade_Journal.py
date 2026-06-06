"""
Trade Journal — view reconstructed strategies and their full lifecycle.
"""

import json
import streamlit as st
import pandas as pd

from db.database import init_db, get_all_positions, get_position_trades
from core.pnl import get_detailed_charge_breakdown

st.set_page_config(
    page_title="Trade Journal | Options Journal",
    page_icon="📒",
    layout="wide",
)
init_db()

st.title("📒 Trade Journal")
st.caption("Every strategy reconstructed from fills — with lifecycle, leg structure, and charge breakdown.")

positions = get_all_positions()
if positions.empty:
    st.info("No positions yet. Head to **Upload** to import contract notes.")
    st.stop()

# ── Filters ───────────────────────────────────────────────────────────────────
f1, f2, f3, f4 = st.columns(4)
with f1:
    status_filter = st.selectbox("Status", ["All", "OPEN", "CLOSED"])
with f2:
    underlying_opts = ["All"] + sorted(positions["underlying"].dropna().unique().tolist())
    underlying_filter = st.selectbox("Underlying", underlying_opts)
with f3:
    strategy_opts = ["All"] + sorted(
        positions["strategy_label"].dropna().unique().tolist()
    )
    strategy_filter = st.selectbox("Strategy", strategy_opts)
with f4:
    search_term = st.text_input("Search position ID", placeholder="e.g. NIFTY_2024…")

filtered = positions.copy()
if status_filter != "All":
    filtered = filtered[filtered["status"] == status_filter]
if underlying_filter != "All":
    filtered = filtered[filtered["underlying"] == underlying_filter]
if strategy_filter != "All":
    filtered = filtered[filtered["strategy_label"] == strategy_filter]
if search_term:
    filtered = filtered[
        filtered["position_id"].str.contains(search_term, case=False, na=False)
    ]

st.caption(f"Showing **{len(filtered)}** of **{len(positions)}** positions")

if filtered.empty:
    st.info("No positions match the current filters.")
    st.stop()

# ── Position cards ────────────────────────────────────────────────────────────
for _, row in filtered.iterrows():
    pnl         = row.get("net_pnl")
    pnl_str     = f"₹{pnl:+,.0f}" if pnl is not None else "Open"
    status_icon = "✅" if row["status"] == "CLOSED" else "🔵"
    pnl_icon    = "🟢" if (pnl or 0) > 0 else ("🔴" if (pnl or 0) < 0 else "⚪")
    conf        = row.get("confidence")
    conf_str    = f"{conf * 100:.0f}%" if conf else "?"

    header = (
        f"{status_icon} **{row['strategy_label']}** — "
        f"{row['underlying']} &nbsp;|&nbsp; "
        f"{str(row['entry_datetime'])[:16]} &nbsp;|&nbsp; "
        f"{pnl_icon} {pnl_str}"
    )

    with st.expander(header, expanded=False):

        # ── KPIs ──────────────────────────────────────────────────────────────
        m1, m2, m3, m4, m5, m6 = st.columns(6)
        m1.metric("Status",      row["status"])
        m2.metric("Net P&L",     pnl_str)
        m3.metric("Gross P&L",   f"₹{row.get('gross_pnl') or 0:,.0f}")
        m4.metric("Charges",     f"₹{row.get('total_charges') or 0:,.0f}")
        m5.metric("Legs",        int(row.get("n_legs") or 0))
        m6.metric("Adjustments", int(row.get("n_adjustments") or 0))

        # ── Leg structure ─────────────────────────────────────────────────────
        leg_structure_raw = row.get("leg_structure")
        if leg_structure_raw:
            try:
                legs = (
                    json.loads(leg_structure_raw)
                    if isinstance(leg_structure_raw, str)
                    else leg_structure_raw
                )
                if legs:
                    st.markdown("**Leg Structure (entry):**")
                    leg_col1, leg_col2 = st.columns([1, 3])
                    with leg_col1:
                        for leg in legs:
                            direction_color = "🟢" if leg.get("direction") == "+" else "🔴"
                            st.code(
                                f"{direction_color} {leg.get('display', '')}  @  ₹{leg.get('price', 0):.2f}",
                                language=None,
                            )
            except Exception:
                pass

        # ── Strategy confidence ───────────────────────────────────────────────
        if row.get("confidence_note"):
            st.caption(f"🧠 Detection: {row['confidence_note']} (confidence {conf_str})")

        # ── Trade lifecycle ───────────────────────────────────────────────────
        pos_trades = get_position_trades(row["position_id"])
        if not pos_trades.empty:
            st.markdown("**Trade Lifecycle:**")

            # Colour leg roles
            def style_role(val):
                colors = {
                    "ENTRY":      "background-color: #1e3a2f; color: #22c55e",
                    "EXIT":       "background-color: #3a1e1e; color: #ef4444",
                    "ADJUSTMENT": "background-color: #2d2a14; color: #f59e0b",
                }
                return colors.get(val, "")

            show_cols = [
                c for c in
                ["trade_datetime", "leg_role", "buy_sell", "quantity",
                 "price", "strike", "option_type", "expiry"]
                if c in pos_trades.columns
            ]
            styled = pos_trades[show_cols].style.map(style_role, subset=["leg_role"])
            st.dataframe(styled, use_container_width=True, hide_index=True)

            # ── Charge breakdown ──────────────────────────────────────────────
            with st.expander("💰 Charge Breakdown", expanded=False):
                ch = get_detailed_charge_breakdown(pos_trades)
                ch_df = pd.DataFrame([
                    {"Component": "Brokerage",       "Amount (₹)": ch["brokerage"]},
                    {"Component": "Exchange (ETC)",   "Amount (₹)": ch["etc"]},
                    {"Component": "SEBI",             "Amount (₹)": ch["sebi"]},
                    {"Component": "STT",              "Amount (₹)": ch["stt"]},
                    {"Component": "Stamp Duty",       "Amount (₹)": ch["stamp_duty"]},
                    {"Component": "GST",              "Amount (₹)": ch["gst"]},
                    {"Component": "─────────────────", "Amount (₹)": "────────"},
                    {"Component": "Total Charges",    "Amount (₹)": ch["total_charges"]},
                ])
                st.dataframe(ch_df, use_container_width=True, hide_index=True)

        # ── Max capital ───────────────────────────────────────────────────────
        if row.get("max_capital"):
            st.caption(f"💵 Max capital at risk (est.): ₹{row['max_capital']:,.0f}")
