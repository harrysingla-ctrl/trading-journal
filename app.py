"""
Options Trading Journal — Home Page
"""

import streamlit as st
from pathlib import Path

from config import APP_NAME, APP_ICON, VERSION, DB_PATH
from db.database import init_db, get_all_positions, get_contract_notes

st.set_page_config(
    page_title=APP_NAME,
    page_icon=APP_ICON,
    layout="wide",
    initial_sidebar_state="expanded",
)

init_db()

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(f"## {APP_ICON} {APP_NAME}")
    st.caption(f"v{VERSION}  ·  Zerodha  ·  NSE / BSE")
    st.divider()

    st.markdown("**Navigate**")
    st.page_link("app.py",                        label="🏠 Home",          use_container_width=True)
    st.page_link("pages/1_Upload.py",             label="📤 Upload",        use_container_width=True)
    st.page_link("pages/2_Trade_Journal.py",      label="📒 Trade Journal", use_container_width=True)
    st.page_link("pages/3_Dashboard.py",          label="📊 Dashboard",     use_container_width=True)
    st.page_link("pages/4_AI_Coach.py",           label="🤖 AI Coach",      use_container_width=True)

# ── Hero ──────────────────────────────────────────────────────────────────────
st.title(f"{APP_ICON} {APP_NAME}")
st.markdown(
    "**Professional journal for option sellers and multi-leg strategy traders.**  \n"
    "Upload Zerodha contract notes → auto-reconstruct strategies → track lifecycle → analyse performance."
)
st.divider()

# ── Quick stats ───────────────────────────────────────────────────────────────
positions = get_all_positions()
notes     = get_contract_notes()

closed = (
    positions[(positions["status"] == "CLOSED") & (positions["net_pnl"].notna())]
    if not positions.empty else positions
)

win_rate  = "—"
total_pnl = 0.0
if not closed.empty:
    n_wins    = int((closed["net_pnl"] > 0).sum())
    win_rate  = f"{n_wins / len(closed) * 100:.1f}%"
    total_pnl = float(closed["net_pnl"].sum())

k1, k2, k3, k4 = st.columns(4)
k1.metric("Contract Notes", len(notes))
k2.metric("Total Positions", len(positions))
k3.metric("Win Rate", win_rate)
k4.metric("Net P&L", f"₹{total_pnl:,.0f}",
          delta_color="normal" if total_pnl >= 0 else "inverse")

st.divider()

# ── Navigation tiles ──────────────────────────────────────────────────────────
c1, c2, c3, c4 = st.columns(4)

with c1:
    st.page_link("pages/1_Upload.py", label="📤 Upload", use_container_width=True)
    st.caption("Import Zerodha PDF contract notes. Duplicate dates are auto-skipped.")

with c2:
    st.page_link("pages/2_Trade_Journal.py", label="📒 Trade Journal", use_container_width=True)
    st.caption("View reconstructed strategies, leg structure, lifecycle & charges.")

with c3:
    st.page_link("pages/3_Dashboard.py", label="📊 Dashboard", use_container_width=True)
    st.caption("Equity curve, monthly P&L, strategy breakdown, drawdown analysis.")

with c4:
    st.page_link("pages/4_AI_Coach.py", label="🤖 AI Coach", use_container_width=True)
    st.caption("Rule-based insights + optional Claude API for deeper coaching.")

st.divider()

# ── Database management ───────────────────────────────────────────────────────
with st.expander("⚙️ Database Management", expanded=False):
    st.markdown(
        "**Local use:** the SQLite database is saved alongside this app.  \n"
        "**Streamlit Cloud:** the filesystem resets on redeploy — export your DB regularly "
        "and re-import after redeploy."
    )
    col_exp, col_imp = st.columns(2)

    with col_exp:
        if st.button("⬇️ Export Database"):
            if Path(DB_PATH).exists():
                from db.database import export_db_bytes
                st.download_button(
                    label="💾 Download trading_journal.db",
                    data=export_db_bytes(),
                    file_name="trading_journal.db",
                    mime="application/octet-stream",
                )
            else:
                st.warning("No database file found yet.")

    with col_imp:
        uploaded_db = st.file_uploader("⬆️ Import Database (.db)", type=["db"], key="db_import")
        if uploaded_db is not None:
            from db.database import import_db_bytes
            try:
                import_db_bytes(uploaded_db.read())
                st.success("✅ Database imported. Refresh the page.")
            except ValueError as exc:
                st.error(str(exc))
