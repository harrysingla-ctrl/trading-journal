"""
AI coaching module.
Rule-based insights always run; Claude API is optional.
"""

import json
from typing import Dict, List, Optional
import pandas as pd

from config import CLAUDE_MODEL


# ── Daily review ──────────────────────────────────────────────────────────────

def get_daily_review(
    day_positions: pd.DataFrame,
    overall_metrics: Dict,
    api_key: Optional[str] = None,
) -> Dict:
    review = _rule_daily_review(day_positions, overall_metrics)
    if api_key and not day_positions.empty:
        review["claude_insights"] = _claude_daily_review(
            day_positions, overall_metrics, api_key
        )
    return review


def _rule_daily_review(positions: pd.DataFrame, metrics: Dict) -> Dict:
    if positions.empty:
        return {
            "daily_pnl": 0, "n_trades": 0, "n_wins": 0, "n_losses": 0,
            "win_rate": 0, "best_trade": None, "best_pnl": None,
            "worst_trade": None, "worst_pnl": None,
            "insights": [{"type": "info", "text": "No trades found for this day."}],
        }

    closed    = positions[positions["status"] == "CLOSED"]
    open_pos  = positions[positions["status"] == "OPEN"]
    daily_pnl = float(closed["net_pnl"].sum()) if not closed.empty else 0.0
    n_trades  = len(positions)
    n_wins    = int((closed["net_pnl"] > 0).sum()) if not closed.empty else 0
    n_losses  = len(closed) - n_wins
    day_wr    = (n_wins / len(closed) * 100) if len(closed) else 0.0

    best_trade  = None
    best_pnl    = None
    worst_trade = None
    worst_pnl   = None
    if not closed.empty:
        best_idx = closed["net_pnl"].idxmax()
        worst_idx = closed["net_pnl"].idxmin()
        best_trade  = closed.loc[best_idx, "strategy_label"]
        best_pnl    = round(float(closed.loc[best_idx, "net_pnl"]), 2)
        worst_trade = closed.loc[worst_idx, "strategy_label"]
        worst_pnl   = round(float(closed.loc[worst_idx, "net_pnl"]), 2)

    insights: List[Dict] = []
    avg_wr = float(metrics.get("win_rate", 50))

    if day_wr > avg_wr + 10:
        insights.append({
            "type": "positive",
            "text": f"Great day — win rate {day_wr:.0f}% vs your average {avg_wr:.0f}%.",
        })
    elif len(closed) > 0 and day_wr < avg_wr - 15:
        insights.append({
            "type": "warning",
            "text": f"Below-average day — win rate {day_wr:.0f}% vs your average {avg_wr:.0f}%.",
        })

    if n_trades > 6:
        insights.append({
            "type": "warning",
            "text": f"High trade count: {n_trades} positions today. Watch for overtrading.",
        })

    if len(open_pos) > 0:
        insights.append({
            "type": "info",
            "text": f"{len(open_pos)} position(s) still open heading into the next session.",
        })

    if daily_pnl > 0 and daily_pnl > float(metrics.get("avg_win", 0)) * 1.5:
        insights.append({
            "type": "positive",
            "text": f"Exceptional day — P&L of ₹{daily_pnl:,.0f} is well above your average win.",
        })

    if not insights:
        insights.append({"type": "info", "text": "Routine trading day — no specific patterns detected."})

    return {
        "daily_pnl":   round(daily_pnl, 2),
        "n_trades":    n_trades,
        "n_wins":      n_wins,
        "n_losses":    n_losses,
        "win_rate":    round(day_wr, 1),
        "best_trade":  best_trade,
        "best_pnl":    best_pnl,
        "worst_trade": worst_trade,
        "worst_pnl":   worst_pnl,
        "insights":    insights,
    }


def _claude_daily_review(
    positions: pd.DataFrame,
    metrics: Dict,
    api_key: str,
) -> str:
    try:
        import anthropic

        closed = positions[positions["status"] == "CLOSED"]
        trade_summary = []
        for _, row in closed.iterrows():
            trade_summary.append({
                "strategy":   str(row.get("strategy_label", "?")),
                "underlying": str(row.get("underlying", "")),
                "entry_time": str(row.get("entry_datetime", ""))[:16],
                "n_legs":     int(row.get("n_legs", 0)),
                "n_adj":      int(row.get("n_adjustments", 0)),
                "net_pnl":    round(float(row.get("net_pnl") or 0), 0),
            })

        prompt = f"""You are an expert options trading coach reviewing a trader's day on NSE/BSE.

Today's closed positions:
{json.dumps(trade_summary, indent=2)}

Trader's historical averages:
- Win Rate: {metrics.get('win_rate', 0):.1f}%
- Profit Factor: {metrics.get('profit_factor', 0):.2f}
- Avg Win: ₹{metrics.get('avg_win', 0):,.0f}
- Avg Loss: ₹{metrics.get('avg_loss', 0):,.0f}
- Expectancy: ₹{metrics.get('expectancy', 0):,.0f}

Provide a concise coaching review with:
1. What worked today (1-2 sentences)
2. What to improve (1-2 sentences)
3. One specific, actionable recommendation for tomorrow

Be direct and specific. Focus on decision quality, not just outcomes. Use ₹ for amounts."""

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text

    except ImportError:
        return "anthropic package not installed. Run: pip install anthropic"
    except Exception as exc:
        return f"Claude API error: {exc}"


# ── Strategy recommendations ──────────────────────────────────────────────────

def get_strategy_recommendations(
    positions: pd.DataFrame,
    api_key: Optional[str] = None,
) -> str:
    from core.analytics import by_strategy
    strat_df = by_strategy(positions)

    if strat_df.empty:
        return "Not enough closed trades yet for strategy recommendations."

    if not api_key:
        lines = ["**Strategy Performance (rule-based summary):**\n"]
        for _, row in strat_df.iterrows():
            icon = "✅" if row["Total P&L (₹)"] > 0 else "❌"
            lines.append(
                f"{icon} **{row['Strategy']}** — "
                f"{row['Trades']} trades | "
                f"{row['Win Rate (%)']:.0f}% win rate | "
                f"P&L: ₹{row['Total P&L (₹)']:,.0f} | "
                f"PF: {row['Profit Factor']}"
            )
        return "\n".join(lines)

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        prompt = f"""You are an expert options trading coach for an Indian NSE/BSE trader.

Strategy performance breakdown:
{strat_df.to_string(index=False)}

Provide 3-4 specific, actionable recommendations to improve overall performance.
Focus on: which strategies to emphasise, which to reduce or stop, any sizing adjustments.
Be concise — 2 sentences per recommendation. Use ₹ for currency."""

        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text

    except Exception as exc:
        return f"Claude API error: {exc}"


# ── Mistake summary ───────────────────────────────────────────────────────────

def summarise_mistakes(positions: pd.DataFrame, api_key: Optional[str] = None) -> str:
    from core.analytics import detect_behavioral_patterns
    patterns = detect_behavioral_patterns(positions)
    if not patterns:
        return "✅ No significant behavioural patterns detected."

    bullet_list = "\n".join(
        f"- **{p['title']}**: {p['detail']}" for p in patterns
    )

    if not api_key:
        return bullet_list

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        prompt = f"""You are a trading coach. The following behavioural patterns were detected:

{bullet_list}

Summarise these issues and provide one prioritised action plan (max 3 steps) to address them.
Be direct and concise. Use ₹ for currency."""

        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text
    except Exception as exc:
        return f"{bullet_list}\n\nClaude API error: {exc}"
