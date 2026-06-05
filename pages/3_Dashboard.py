"""
Performance Dashboard — analytics and charts.
"""

import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd

from db.database import init_db, get_all_positions
from core.analytics import (
    compute_overall_metrics,
    equity_curve,
    by_strategy,
    by_day_of_week,
    monthly_pnl,
    detect_behavioral_patterns,
)
from config import COLORS

st.set_page_config(
    page_title="Dashboard | Options Journal",
    page_icon="📊",
    layout="wide",
)
init_db()

st.title("📊 Performance Dashboard")

positions = get_all_positions()
if positions.empty:
    st.info("No data yet. Upload contract notes first.")
    st.stop()

closed_count = int((positions["status"] == "CLOSED").sum())
if closed_count == 0:
    st.warning("No closed positions yet — import more contract notes to see analytics.")
    st.stop()

metrics = compute_overall_metrics(positions)

# ── KPI row ───────────────────────────────────────────────────────────────────
k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("Closed Trades",  metrics["total_trades"])
k2.metric("Win Rate",       f"{metrics['win_rate']:.1f}%")
k3.metric("Profit Factor",  f"{metrics['profit_factor']:.2f}")

pnl = metrics["total_net_pnl"]
k4.metric("Net P&L", f"₹{pnl:,.0f}", delta_color="normal" if pnl >= 0 else "inverse")

dd = metrics["max_drawdown"]
k5.metric("Max Drawdown", f"₹{dd:,.0f}", delta_color="inverse" if dd < 0 else "normal")
k6.metric("Total Charges", f"₹{metrics['total_charges']:,.0f}")

st.divider()

# ── Equity curve ──────────────────────────────────────────────────────────────
eq_df = equity_curve(positions)
if not eq_df.empty:
    st.subheader("📈 Equity Curve")
    fig_eq = go.Figure()
    fig_eq.add_trace(go.Scatter(
        x=eq_df["date"],
        y=eq_df["cumulative_pnl"],
        mode="lines",
        line=dict(color=COLORS["primary"], width=2.5),
        fill="tozeroy",
        fillcolor="rgba(0,212,170,0.08)",
        name="Cumulative Net P&L",
        hovertemplate="₹%{y:,.0f}<extra></extra>",
    ))
    fig_eq.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.4)
    fig_eq.update_layout(
        height=320, margin=dict(t=10, b=10),
        yaxis_title="Cumulative Net P&L (₹)",
        hovermode="x unified",
    )
    st.plotly_chart(fig_eq, use_container_width=True)

# ── Monthly P&L + Day of Week ─────────────────────────────────────────────────
col_m, col_d = st.columns(2)

with col_m:
    st.subheader("📅 Monthly P&L")
    monthly_df = monthly_pnl(positions)
    if not monthly_df.empty:
        bar_colors = [
            COLORS["profit"] if v >= 0 else COLORS["loss"]
            for v in monthly_df["net_pnl"]
        ]
        fig_m = go.Figure(go.Bar(
            x=monthly_df["month"],
            y=monthly_df["net_pnl"],
            marker_color=bar_colors,
            text=[f"₹{v:,.0f}" for v in monthly_df["net_pnl"]],
            textposition="outside",
            hovertemplate="%{x}: ₹%{y:,.0f}<extra></extra>",
        ))
        fig_m.update_layout(
            height=300, margin=dict(t=10, b=10),
            xaxis_tickangle=-45, yaxis_title="Net P&L (₹)",
        )
        st.plotly_chart(fig_m, use_container_width=True)
    else:
        st.info("Not enough data.")

with col_d:
    st.subheader("📆 Day-of-Week Performance")
    dow_df = by_day_of_week(positions)
    if not dow_df.empty and dow_df["Trades"].sum() > 0:
        bar_colors = [
            COLORS["profit"] if v >= 0 else COLORS["loss"]
            for v in dow_df["Total P&L (₹)"]
        ]
        fig_d = go.Figure(go.Bar(
            x=dow_df["Day"],
            y=dow_df["Total P&L (₹)"],
            marker_color=bar_colors,
            text=[f"₹{v:,.0f}" for v in dow_df["Total P&L (₹)"]],
            textposition="outside",
            hovertemplate="%{x}: ₹%{y:,.0f}<extra></extra>",
        ))
        fig_d.update_layout(
            height=300, margin=dict(t=10, b=10),
            yaxis_title="Net P&L (₹)",
        )
        st.plotly_chart(fig_d, use_container_width=True)
    else:
        st.info("Not enough data.")

# ── Strategy P&L ──────────────────────────────────────────────────────────────
st.subheader("🎯 Strategy Performance")
strat_df = by_strategy(positions)
if not strat_df.empty:
    chart_col, table_col = st.columns([3, 2])

    with chart_col:
        fig_s = px.bar(
            strat_df,
            x="Strategy",
            y="Total P&L (₹)",
            color="Total P&L (₹)",
            color_continuous_scale=[
                [0, COLORS["loss"]], [0.5, COLORS["neutral"]], [1, COLORS["profit"]]
            ],
            text="Total P&L (₹)",
        )
        fig_s.update_traces(
            texttemplate="₹%{text:,.0f}",
            textposition="outside",
        )
        fig_s.update_layout(
            height=350, margin=dict(t=10, b=10),
            showlegend=False, xaxis_tickangle=-25,
        )
        st.plotly_chart(fig_s, use_container_width=True)

    with table_col:
        st.dataframe(strat_df, use_container_width=True, hide_index=True)

# ── Win rate by strategy ──────────────────────────────────────────────────────
if not strat_df.empty and len(strat_df) > 1:
    st.subheader("🏆 Win Rate by Strategy")
    wr_colors = [
        COLORS["profit"] if v >= 50 else COLORS["loss"]
        for v in strat_df["Win Rate (%)"]
    ]
    fig_wr = go.Figure(go.Bar(
        x=strat_df["Strategy"],
        y=strat_df["Win Rate (%)"],
        marker_color=wr_colors,
        text=[f"{v:.0f}%" for v in strat_df["Win Rate (%)"]],
        textposition="outside",
        hovertemplate="%{x}: %{y:.1f}%<extra></extra>",
    ))
    fig_wr.add_hline(
        y=50, line_dash="dash", line_color="gray", opacity=0.5,
        annotation_text="50% breakeven", annotation_position="right",
    )
    fig_wr.update_layout(
        height=280, margin=dict(t=10, b=10),
        yaxis=dict(title="Win Rate (%)", range=[0, 110]),
        xaxis_tickangle=-25,
    )
    st.plotly_chart(fig_wr, use_container_width=True)

# ── Drawdown ──────────────────────────────────────────────────────────────────
if not eq_df.empty and len(eq_df) > 2:
    st.subheader("📉 Drawdown")
    peak     = eq_df["cumulative_pnl"].cummax()
    drawdown = eq_df["cumulative_pnl"] - peak
    fig_dd = go.Figure(go.Scatter(
        x=eq_df["date"],
        y=drawdown,
        mode="lines",
        fill="tozeroy",
        line=dict(color=COLORS["loss"], width=1.5),
        fillcolor="rgba(239,68,68,0.12)",
        hovertemplate="₹%{y:,.0f}<extra></extra>",
    ))
    fig_dd.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.3)
    fig_dd.update_layout(
        height=220, margin=dict(t=10, b=10),
        yaxis_title="Drawdown (₹)",
    )
    st.plotly_chart(fig_dd, use_container_width=True)

# ── Summary stats table ───────────────────────────────────────────────────────
st.subheader("📋 Key Statistics")
stats = [
    ("Total Closed Trades",  metrics["total_trades"]),
    ("Winners",              metrics["winners"]),
    ("Losers",               metrics["losers"]),
    ("Win Rate",             f"{metrics['win_rate']:.1f}%"),
    ("Profit Factor",        f"{metrics['profit_factor']:.2f}"),
    ("Expectancy",           f"₹{metrics['expectancy']:,.0f}"),
    ("Avg Win",              f"₹{metrics['avg_win']:,.0f}"),
    ("Avg Loss",             f"₹{metrics['avg_loss']:,.0f}"),
    ("Largest Win",          f"₹{metrics['largest_win']:,.0f}"),
    ("Largest Loss",         f"₹{metrics['largest_loss']:,.0f}"),
    ("Total Net P&L",        f"₹{metrics['total_net_pnl']:,.0f}"),
    ("Max Drawdown",         f"₹{metrics['max_drawdown']:,.0f}"),
    ("Total Charges Paid",   f"₹{metrics['total_charges']:,.0f}"),
    ("Avg Holding (hrs)",    str(metrics.get("avg_hold_hours") or "—")),
]
stats_df = pd.DataFrame(stats, columns=["Metric", "Value"])
st.dataframe(stats_df, use_container_width=True, hide_index=True)

# ── Behavioural warnings ──────────────────────────────────────────────────────
st.divider()
st.subheader("🔍 Behavioural Pattern Detection")
warnings = detect_behavioral_patterns(positions)
if warnings:
    for w in warnings:
        if w["type"] == "warning":
            st.warning(f"**{w['title']}** — {w['detail']}")
        else:
            st.info(f"**{w['title']}** — {w['detail']}")
else:
    st.success("✅ No significant behavioural patterns detected.")
