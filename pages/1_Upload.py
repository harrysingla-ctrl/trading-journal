"""
Upload & parse Zerodha contract note PDFs.
"""

import streamlit as st
import pandas as pd

from db.database import (
    init_db, insert_contract_note, insert_raw_trades,
    mark_contract_note_processed, get_contract_notes,
    contract_note_exists, insert_position, insert_position_legs,
    mark_trades_assigned,
)
from core.parser import parse_contract_note
from core.clustering import cluster_and_classify
from core.pnl import (
    calculate_position_pnl, estimate_max_capital,
    position_status_from_trades,
)

st.set_page_config(
    page_title="Upload | Options Journal",
    page_icon="📤",
    layout="wide",
)
init_db()

st.title("📤 Upload Contract Notes")
st.caption(
    "Upload one or more Zerodha contract note PDFs. "
    "Strategies are automatically reconstructed from your fills."
)

# ── File upload ───────────────────────────────────────────────────────────────
uploaded_files = st.file_uploader(
    "Select Zerodha contract note PDF(s)",
    type=["pdf"],
    accept_multiple_files=True,
    help="Supports the standard Zerodha contract note format (NSE + BSE options).",
)

if uploaded_files:
    for uploaded_file in uploaded_files:
        st.divider()
        st.subheader(f"📄 {uploaded_file.name}")

        with st.spinner("Parsing PDF…"):
            result = parse_contract_note(uploaded_file.read(), uploaded_file.name)

        trade_date = result["trade_date"]
        client_id  = result["client_id"]
        trades     = result["trades"]
        warnings   = result["parse_warnings"]

        if warnings:
            with st.expander(f"⚠️ {len(warnings)} parser warning(s)", expanded=False):
                for w in warnings:
                    st.warning(w)

        if not trades:
            st.error(
                "No option trades could be extracted. "
                "Please check this is a Zerodha F&O contract note."
            )
            continue

        trades_df = pd.DataFrame(trades)

        col_info1, col_info2, col_info3 = st.columns(3)
        col_info1.metric("Fills extracted", len(trades))
        col_info2.metric("Trade date", trade_date)
        col_info3.metric("Client ID", client_id or "—")

        with st.expander("Preview extracted fills", expanded=True):
            show_cols = [
                c for c in
                ["trade_datetime", "underlying", "expiry", "strike",
                 "option_type", "buy_sell", "quantity", "price"]
                if c in trades_df.columns
            ]
            st.dataframe(trades_df[show_cols], use_container_width=True, hide_index=True)

        # Duplicate guard
        if contract_note_exists(uploaded_file.name, trade_date):
            st.warning(
                f"⚠️ A contract note for **{trade_date}** with this filename already exists. "
                "Skipping to prevent duplicate imports."
            )
            continue

        if st.button(
            f"💾 Import {len(trades)} fills from {trade_date}",
            key=f"import_{uploaded_file.name}",
        ):
            with st.spinner("Saving fills and clustering strategies…"):

                # 1. Save contract note record
                note_id = insert_contract_note(
                    uploaded_file.name, trade_date, client_id
                )

                # 2. Save raw trades → get auto-generated IDs
                trade_ids = insert_raw_trades(trades, note_id)
                trades_df["id"] = trade_ids

                # 3. Cluster fills into strategy positions
                positions = cluster_and_classify(trades_df)

                # 4. Calculate P&L for each position and persist
                for pos in positions:
                    pos_trade_ids = [lg["id"] for lg in pos.get("all_legs", [])]
                    pos_trades    = trades_df[trades_df["id"].isin(pos_trade_ids)]

                    pnl = calculate_position_pnl(pos_trades)
                    pos["gross_pnl"]    = pnl["gross_pnl"]
                    pos["net_pnl"]      = pnl["net_pnl"]
                    pos["total_charges"] = pnl["total_charges"]
                    pos["max_capital"]  = estimate_max_capital(pos_trades)

                    # Determine status from trade balance
                    inferred_status = position_status_from_trades(pos_trades)
                    if pos.get("status") != "CLOSED":
                        pos["status"] = inferred_status

                    insert_position(pos)

                    leg_records = [
                        {
                            "position_id":  pos["position_id"],
                            "raw_trade_id": lg["id"],
                            "leg_role":     lg["role"],
                            "sequence_no":  seq,
                        }
                        for seq, lg in enumerate(pos.get("all_legs", []))
                    ]
                    insert_position_legs(leg_records)
                    mark_trades_assigned(pos_trade_ids)

                mark_contract_note_processed(note_id)

            n_closed = sum(1 for p in positions if p.get("status") == "CLOSED")
            n_open   = len(positions) - n_closed
            st.success(
                f"✅ Imported **{len(trades)}** fills across **{len(positions)}** positions — "
                f"**{n_closed}** closed, **{n_open}** open."
            )
            st.balloons()

# ── Upload history ────────────────────────────────────────────────────────────
st.divider()
st.subheader("📁 Import History")
notes_df = get_contract_notes()
if notes_df.empty:
    st.info("No contract notes imported yet.")
else:
    notes_df["processed"] = notes_df["processed"].map({0: "⏳ Pending", 1: "✅ Done"})
    st.dataframe(
        notes_df[["filename", "trade_date", "client_id", "upload_datetime", "processed"]],
        use_container_width=True,
        hide_index=True,
    )
