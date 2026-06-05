"""
AI Coach — rule-based insights + optional Claude API coaching.
"""

import streamlit as st
import pandas as pd

from db.database import init_db, get_all_positions
from core.analytics import compute_overall_metrics
from core.ai_coach import (
    get_daily_review,
    get_strategy_recommendations,
    summarise_mistakes,
)

st.set_page_config(
    page_title="AI Coach | Options Journal",
    page_icon="🤖",
    layout="wide",
)
init_db()

st.title("🤖 AI Coach")
st.caption(
    "Rule-based insights run instantly. "
    "Add your Anthropic API key for deeper, Claude-powered coaching."
)

# ── API key (sidebar) ─────────────────────────────────────────────────────────
with st.sidebar:
    st.subheader("🔑 Claude API Key")
    api_key = st.text_input(
        "Anthropic API Key",
        type="password",
        placeholder="sk-ant-…",
        help="Optional. Get a key at console.anthropic.com. Never stored on disk.",
    )
    if api_key:
        st.success("API key set ✅")
        st.caption("Claude will be used for deeper analysis.")
    else:
        st.info("Running rule-based mode.  \nAdd key to unlock AI insights.")

    st.divider()
    st.caption("Your data is never sent to Anthropic without an API key.")

# ── Load data ─────────────────────────────────────────────────────────────────
positions = get_all_positions()
if positions.empty:
    st.info("No data yet. Upload contract notes first.")
    st.stop()

metrics = compute_overall_metrics(positions)

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_daily, tab_strategy, tab_mistakes = st.tabs(
    ["📅 Daily Review", "🎯 Strategy Recommendations", "🔍 Mistake Analysis"]
)

# ─────────────────────────────────────────────────────────────────────────────
# TAB 1: Daily Review
# ─────────────────────────────────────────────────────────────────────────────
with tab_daily:
    st.subheader("Daily Review")

    positions["entry_dt"] = pd.to_datetime(positions["entry_datetime"], errors="coerce")
    available_dates = sorted(
        positions["entry_dt"].dt.date.dropna().unique(), reverse=True
    )

    if not available_dates:
        st.info("No entry dates found.")
    else:
        selected_date = st.selectbox(
            "Select trading day",
            available_dates,
            format_func=lambda d: d.strftime("%A, %d %b %Y"),
        )

        day_positions = positions[
            positions["entry_dt"].dt.date == selected_date
        ].copy()

        with st.spinner("Generating review…"):
            review = get_daily_review(
                day_positions, metrics, api_key if api_key else None
            )

        # ── Day KPIs ──────────────────────────────────────────────────────────
        d1, d2, d3, d4 = st.columns(4)
        d1.metric("Positions",  review.get("n_trades", 0))
        d2.metric("Wins",       review.get("n_wins", 0))
        d3.metric("Losses",     review.get("n_losses", 0))
        pnl = review.get("daily_pnl", 0)
        d4.metric(
            "Day P&L",
            f"₹{pnl:+,.0f}",
            delta_color="normal" if pnl >= 0 else "inverse",
        )

        # ── Best / worst ──────────────────────────────────────────────────────
        if review.get("best_trade"):
            st.success(
                f"✅ **Best:** {review['best_trade']} "
                f"→ ₹{review.get('best_pnl', 0):+,.0f}"
            )
        if review.get("worst_trade"):
            st.error(
                f"❌ **Worst:** {review['worst_trade']} "
                f"→ ₹{review.get('worst_pnl', 0):+,.0f}"
            )

        # ── Rule-based insights ───────────────────────────────────────────────
        st.markdown("**Insights:**")
        for insight in review.get("insights", []):
            t    = insight.get("type", "info")
            text = insight.get("text", "")
            if t == "positive":
                st.success(text)
            elif t == "warning":
                st.warning(text)
            else:
                st.info(text)

        # ── Claude insights ───────────────────────────────────────────────────
        if api_key and "claude_insights" in review:
            st.divider()
            st.markdown("**Claude's Coaching:**")
            st.markdown(review["claude_insights"])
        elif not api_key:
            st.caption("💡 Add API key to get Claude's personalised coaching for this day.")

# ─────────────────────────────────────────────────────────────────────────────
# TAB 2: Strategy Recommendations
# ─────────────────────────────────────────────────────────────────────────────
with tab_strategy:
    st.subheader("Strategy Recommendations")

    with st.spinner("Analysing strategy performance…"):
        recs = get_strategy_recommendations(
            positions, api_key if api_key else None
        )

    st.markdown(recs)

    if not api_key:
        st.caption("💡 Add API key for Claude's personalised strategy recommendations.")

# ─────────────────────────────────────────────────────────────────────────────
# TAB 3: Mistake Analysis
# ─────────────────────────────────────────────────────────────────────────────
with tab_mistakes:
    st.subheader("Mistake & Behavioural Analysis")

    with st.spinner("Scanning for patterns…"):
        analysis = summarise_mistakes(positions, api_key if api_key else None)

    st.markdown(analysis)

    if not api_key:
        st.caption("💡 Add API key for Claude's prioritised action plan.")

# ─────────────────────────────────────────────────────────────────────────────
st.divider()
st.caption(
    "Rule-based insights run entirely locally. "
    "Claude API calls are made only when an API key is provided and never cached. "
    "Your trade data is sent only to Anthropic's API and is subject to their privacy policy."
)
