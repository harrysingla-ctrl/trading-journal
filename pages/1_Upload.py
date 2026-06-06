"""
Upload & parse Zerodha contract note PDFs.
"""

import streamlit as st
import pandas as pd

from db.database import (
    init_db, insert_contract_note, insert_raw_trades,
    mark_contract_note_processed, get_contract_notes,
    contract_note_exists, insert_position, insert_position_legs,
    mark_trades_assigned, get_setting, set_setting,
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

with st.sidebar:
    st.page_link("app.py",                   label="🏠 Home",          use_container_width=True)
    st.page_link("pages/1_Upload.py",        label="📤 Upload",        use_container_width=True)
    st.page_link("pages/2_Trade_Journal.py", label="📒 Trade Journal", use_container_width=True)
    st.page_link("pages/3_Dashboard.py",     label="📊 Dashboard",     use_container_width=True)
    st.page_link("pages/4_AI_Coach.py",      label="🤖 AI Coach",      use_container_width=True)
    st.divider()

# ── Sidebar: PDF password settings ───────────────────────────────────────────
with st.sidebar:
    st.subheader("⚙️ PDF Password")
    st.caption(
        "Zerodha contract notes are password-protected by default.  \n"
        "Common passwords: **PAN number** (e.g. ABCDE1234F) or **date of birth** (DDMMYYYY)."
    )

    saved_pw = get_setting("pdf_password", "")

    pdf_password = st.text_input(
        "Password",
        value=saved_pw,
        type="password",
        placeholder="e.g. ABCDE1234F",
        help="Password is stored locally in your database. Never sent anywhere.",
    )

    c1, c2 = st.columns(2)
    with c1:
        if st.button("💾 Save", use_container_width=True):
            set_setting("pdf_password", pdf_password)
            st.success("Saved!")
            st.rerun()
    with c2:
        if st.button("🗑 Clear", use_container_width=True):
            set_setting("pdf_password", "")
            pdf_password = ""
            st.success("Cleared!")
            st.rerun()

    st.divider()
    if saved_pw:
        st.success("✅ Password configured")
    else:
        st.warning("⚠️ No password set")

    st.caption(
        "Password is saved in your local SQLite database and never transmitted."
    )

# ── Main content ──────────────────────────────────────────────────────────────
st.title("📤 Upload Contract Notes")
st.caption(
    "Upload Zerodha contract note PDFs. "
    "Individual fills are extracted from **Annexure A** and "
    "automatically clustered into strategies."
)

# ── File uploader ─────────────────────────────────────────────────────────────
uploaded_files = st.file_uploader(
    "Select Zerodha contract note PDF(s)",
    type=["pdf"],
    accept_multiple_files=True,
    help="Supports the standard Zerodha contract note format (NSE + BSE F&O).",
)

if uploaded_files:
    for uploaded_file in uploaded_files:
        st.divider()
        st.subheader(f"📄 {uploaded_file.name}")

        with st.spinner("Parsing PDF…"):
            result = parse_contract_note(
                uploaded_file.read(),
                uploaded_file.name,
                password=saved_pw,
            )

        # ── Password error ────────────────────────────────────────────────
        if result.get("needs_password"):
            st.error(
                "🔒 **PDF is password-protected** and the current password is "
                "incorrect or not set.  \n\n"
                "Set the correct password in the **sidebar** on the left, then re-upload.  \n"
                "For Zerodha, this is usually your **PAN number** (e.g. ABCDE1234F) "
                "or **date of birth** (DDMMYYYY)."
            )
            continue

        trade_date = result["trade_date"]
        client_id  = result["client_id"]
        trades     = result["trades"]
        warnings   = result["parse_warnings"]

        # ── Parser warnings ───────────────────────────────────────────────
        if warnings:
            with st.expander(f"⚠️ {len(warnings)} parser warning(s)", expanded=False):
                for w in warnings:
                    st.warning(w)

        if not trades:
            st.error(
                "No option trades found. "
                "Ensure this is a Zerodha F&O contract note containing Annexure A."
            )
            continue

        trades_df = pd.DataFrame(trades)

        # ── Summary metrics ───────────────────────────────────────────────
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Fills extracted", len(trades))
        col2.metric("Trade date",      trade_date or "—")
        col3.metric("Client ID",       client_id  or "—")

        n_underlying = trades_df["underlying"].nunique() if not trades_df.empty else 0
        col4.metric("Underlyings",     n_underlying)

        # ── Preview ───────────────────────────────────────────────────────
        with st.expander("Preview extracted fills", expanded=True):
            show_cols = [c for c in
                ["trade_datetime", "underlying", "expiry", "strike",
                 "option_type", "buy_sell", "quantity", "price", "exchange"]
                if c in trades_df.columns]
            st.dataframe(
                trades_df[show_cols].sort_values("trade_datetime"),
                use_container_width=True,
                hide_index=True,
            )

            # Quick sanity check
            buy_val  = float((trades_df[trades_df["buy_sell"]=="B"]["price"]
                              * trades_df[trades_df["buy_sell"]=="B"]["quantity"]).sum())
            sell_val = float((trades_df[trades_df["buy_sell"]=="S"]["price"]
                              * trades_df[trades_df["buy_sell"]=="S"]["quantity"]).sum())
            sc1, sc2 = st.columns(2)
            sc1.caption(f"Total buy value: ₹{buy_val:,.0f}")
            sc2.caption(f"Total sell value: ₹{sell_val:,.0f}")

        # ── Duplicate guard ───────────────────────────────────────────────
        if contract_note_exists(uploaded_file.name, trade_date):
            st.warning(
                f"⚠️ A contract note for **{trade_date}** with this filename already "
                "exists. Skipping to prevent duplicates."
            )
            continue

        # ── Import button ─────────────────────────────────────────────────
        if st.button(
            f"💾 Import {len(trades)} fills from {trade_date}",
            key=f"import_{uploaded_file.name}",
            type="primary",
        ):
            with st.spinner("Saving fills and clustering strategies…"):

                note_id   = insert_contract_note(uploaded_file.name, trade_date, client_id)
                trade_ids = insert_raw_trades(trades, note_id)
                trades_df["id"] = trade_ids

                positions = cluster_and_classify(trades_df)

                for pos in positions:
                    pos_trade_ids = [lg["id"] for lg in pos.get("all_legs", [])]
                    pos_trades    = trades_df[trades_df["id"].isin(pos_trade_ids)]

                    pnl = calculate_position_pnl(pos_trades)
                    pos["gross_pnl"]     = pnl["gross_pnl"]
                    pos["net_pnl"]       = pnl["net_pnl"]
                    pos["total_charges"] = pnl["total_charges"]
                    pos["max_capital"]   = estimate_max_capital(pos_trades)

                    inferred = position_status_from_trades(pos_trades)
                    if pos.get("status") != "CLOSED":
                        pos["status"] = inferred

                    insert_position(pos)
                    insert_position_legs([
                        {
                            "position_id":  pos["position_id"],
                            "raw_trade_id": lg["id"],
                            "leg_role":     lg["role"],
                            "sequence_no":  seq,
                        }
                        for seq, lg in enumerate(pos.get("all_legs", []))
                    ])
                    mark_trades_assigned(pos_trade_ids)

                mark_contract_note_processed(note_id)

            n_closed = sum(1 for p in positions if p.get("status") == "CLOSED")
            n_open   = len(positions) - n_closed
            st.success(
                f"✅ Imported **{len(trades)}** fills into "
                f"**{len(positions)}** positions — "
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
