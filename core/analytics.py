"""
Performance analytics, metrics, and behavioural pattern detection.
"""

from typing import Dict, List
import pandas as pd
import numpy as np

from config import STRATEGY_NAMES


# ── Overall metrics ───────────────────────────────────────────────────────────

def compute_overall_metrics(positions: pd.DataFrame) -> Dict:
    closed = _closed(positions)
    if closed.empty:
        return _empty_metrics()

    total   = len(closed)
    wins    = closed[closed["net_pnl"] > 0]
    losses  = closed[closed["net_pnl"] <= 0]
    n_wins  = len(wins)
    n_loss  = len(losses)
    win_rate = n_wins / total

    avg_win  = float(wins["net_pnl"].mean()) if n_wins else 0.0
    avg_loss = float(losses["net_pnl"].abs().mean()) if n_loss else 0.0

    win_sum  = float(wins["net_pnl"].sum())
    loss_sum = float(losses["net_pnl"].abs().sum())
    profit_factor = (win_sum / loss_sum) if loss_sum else float("inf")

    expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)

    sorted_cl  = closed.sort_values("entry_datetime")
    cumulative = sorted_cl["net_pnl"].cumsum()
    peak       = cumulative.cummax()
    max_dd     = float((cumulative - peak).min())

    total_charges = float(closed["total_charges"].sum()) if "total_charges" in closed else 0.0
    total_net     = float(closed["net_pnl"].sum())
    total_gross   = float(closed["gross_pnl"].sum()) if "gross_pnl" in closed else 0.0

    avg_hold_hours = None
    if "entry_datetime" in closed and "exit_datetime" in closed:
        closed2 = closed.copy()
        closed2["entry_dt"] = pd.to_datetime(closed2["entry_datetime"], errors="coerce")
        closed2["exit_dt"]  = pd.to_datetime(closed2["exit_datetime"],  errors="coerce")
        valid = closed2.dropna(subset=["entry_dt", "exit_dt"])
        if not valid.empty:
            durations = (valid["exit_dt"] - valid["entry_dt"]).dt.total_seconds() / 3600
            avg_hold_hours = round(float(durations.mean()), 1)

    return {
        "total_trades":    int(total),
        "winners":         int(n_wins),
        "losers":          int(n_loss),
        "win_rate":        round(win_rate * 100, 1),
        "profit_factor":   round(profit_factor, 2) if profit_factor != float("inf") else 999.0,
        "expectancy":      round(expectancy, 2),
        "avg_win":         round(avg_win, 2),
        "avg_loss":        round(avg_loss, 2),
        "largest_win":     round(float(closed["net_pnl"].max()), 2),
        "largest_loss":    round(float(closed["net_pnl"].min()), 2),
        "total_net_pnl":   round(total_net, 2),
        "total_gross_pnl": round(total_gross, 2),
        "max_drawdown":    round(max_dd, 2),
        "total_charges":   round(total_charges, 2),
        "avg_hold_hours":  avg_hold_hours,
    }


def _empty_metrics() -> Dict:
    return {
        k: 0 for k in [
            "total_trades", "winners", "losers", "win_rate", "profit_factor",
            "expectancy", "avg_win", "avg_loss", "largest_win", "largest_loss",
            "total_net_pnl", "total_gross_pnl", "max_drawdown", "total_charges",
            "avg_hold_hours",
        ]
    }


# ── Strategy breakdown ────────────────────────────────────────────────────────

def by_strategy(positions: pd.DataFrame) -> pd.DataFrame:
    closed = _closed(positions)
    if closed.empty:
        return pd.DataFrame()

    rows = []
    for label, grp in closed.groupby("strategy_label"):
        n      = len(grp)
        n_wins = int((grp["net_pnl"] > 0).sum())
        w_sum  = float(grp.loc[grp["net_pnl"] > 0, "net_pnl"].sum())
        l_sum  = float(grp.loc[grp["net_pnl"] <= 0, "net_pnl"].abs().sum())
        pf     = round(w_sum / l_sum, 2) if l_sum else 999.0
        rows.append({
            "Strategy":      label,
            "Trades":        int(n),
            "Wins":          n_wins,
            "Win Rate (%)":  round(n_wins / n * 100, 1),
            "Avg P&L (₹)":  round(float(grp["net_pnl"].mean()), 0),
            "Total P&L (₹)": round(float(grp["net_pnl"].sum()), 0),
            "Profit Factor": pf,
        })
    return pd.DataFrame(rows).sort_values("Total P&L (₹)", ascending=False).reset_index(drop=True)


# ── Day-of-week breakdown ─────────────────────────────────────────────────────

def by_day_of_week(positions: pd.DataFrame) -> pd.DataFrame:
    closed = _closed(positions)
    if closed.empty:
        return pd.DataFrame()

    closed = closed.copy()
    closed["entry_dt"]  = pd.to_datetime(closed["entry_datetime"], errors="coerce")
    closed["day_name"]  = closed["entry_dt"].dt.day_name()

    rows = []
    for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]:
        grp = closed[closed["day_name"] == day]
        n   = len(grp)
        rows.append({
            "Day":           day,
            "Trades":        n,
            "Win Rate (%)":  round(float((grp["net_pnl"] > 0).sum()) / n * 100, 1) if n else 0.0,
            "Total P&L (₹)": round(float(grp["net_pnl"].sum()), 0) if n else 0.0,
            "Avg P&L (₹)":  round(float(grp["net_pnl"].mean()), 0) if n else 0.0,
        })
    return pd.DataFrame(rows)


# ── Time series ───────────────────────────────────────────────────────────────

def equity_curve(positions: pd.DataFrame) -> pd.DataFrame:
    closed = _closed(positions)
    if closed.empty:
        return pd.DataFrame(columns=["date", "net_pnl", "cumulative_pnl"])

    closed = closed.copy()
    closed["date"] = pd.to_datetime(closed["entry_datetime"], errors="coerce")
    closed = closed.sort_values("date")
    closed["cumulative_pnl"] = closed["net_pnl"].cumsum()
    return closed[["date", "net_pnl", "cumulative_pnl"]].reset_index(drop=True)


def monthly_pnl(positions: pd.DataFrame) -> pd.DataFrame:
    closed = _closed(positions)
    if closed.empty:
        return pd.DataFrame()

    closed = closed.copy()
    closed["entry_dt"] = pd.to_datetime(closed["entry_datetime"], errors="coerce")
    closed["month"]    = closed["entry_dt"].dt.to_period("M").astype(str)

    monthly = closed.groupby("month").agg(
        trades  = ("net_pnl", "count"),
        net_pnl = ("net_pnl", "sum"),
        wins    = ("net_pnl", lambda x: int((x > 0).sum())),
    ).reset_index()
    monthly["win_rate"] = (monthly["wins"] / monthly["trades"] * 100).round(1)
    monthly["net_pnl"]  = monthly["net_pnl"].round(0)
    return monthly


# ── Behavioural pattern detection ─────────────────────────────────────────────

def detect_behavioral_patterns(positions: pd.DataFrame) -> List[Dict]:
    """Rule-based detection of trading mistakes and patterns."""
    closed = _closed(positions)
    warnings: List[Dict] = []
    if closed.empty:
        return warnings

    closed = closed.copy()
    closed["entry_dt"] = pd.to_datetime(closed["entry_datetime"], errors="coerce")
    closed["date"]     = closed["entry_dt"].dt.date

    # 1. Overtrading days
    trades_per_day = closed.groupby("date").size()
    heavy = trades_per_day[trades_per_day > 5]
    if len(heavy):
        warnings.append({
            "type": "warning",
            "title": "Overtrading Detected",
            "detail": (
                f"You traded more than 5 strategies on {len(heavy)} day(s). "
                "High trade frequency on a single day often reflects emotional decision-making."
            ),
        })

    # 2. Revenge trading (large loss followed quickly by a new entry)
    sorted_pos = closed.sort_values("entry_dt")
    for i in range(1, len(sorted_pos)):
        prev = sorted_pos.iloc[i - 1]
        curr = sorted_pos.iloc[i]
        pnl_prev = float(prev["net_pnl"])
        gap_mins = (curr["entry_dt"] - prev["entry_dt"]).total_seconds() / 60
        if pnl_prev < -3000 and gap_mins < 30:
            warnings.append({
                "type": "warning",
                "title": "Possible Revenge Trade",
                "detail": (
                    f"A loss of ₹{abs(pnl_prev):,.0f} on {prev['entry_dt'].date()} "
                    f"was followed by a new position within {gap_mins:.0f} minutes."
                ),
            })

    # 3. Consistently losing strategy
    strat_pnl = closed.groupby("strategy_label")["net_pnl"].sum()
    strat_cnt = closed.groupby("strategy_label").size()
    for strat, pnl in strat_pnl.items():
        if pnl < 0 and strat_cnt.get(strat, 0) >= 3:
            warnings.append({
                "type": "info",
                "title": f"Consistent Loss: {strat}",
                "detail": (
                    f"Your {strat} trades have a cumulative loss of ₹{abs(pnl):,.0f} "
                    f"over {strat_cnt[strat]} trades. Review your edge in this strategy."
                ),
            })

    # 4. High brokerage leakage
    if "total_charges" in closed:
        total_ch  = float(closed["total_charges"].fillna(0).sum())
        total_net = float(closed["net_pnl"].sum())
        gross_sum = total_net + total_ch
        if gross_sum > 0 and (total_ch / gross_sum) > 0.15:
            warnings.append({
                "type": "warning",
                "title": "High Brokerage Leakage",
                "detail": (
                    f"Total charges ₹{total_ch:,.0f} = "
                    f"{total_ch / gross_sum * 100:.1f}% of gross P&L. "
                    "Consider reducing trade frequency or leg count."
                ),
            })

    # 5. Excessive adjustments
    if "n_adjustments" in closed:
        high_adj = closed[closed["n_adjustments"] >= 3]
        if len(high_adj):
            avg_pnl = float(high_adj["net_pnl"].mean())
            warnings.append({
                "type": "info",
                "title": "Frequent Over-Adjustment",
                "detail": (
                    f"{len(high_adj)} position(s) had 3 or more adjustments. "
                    f"Average P&L on these: ₹{avg_pnl:,.0f}. "
                    "Excessive adjustments erode the premium collected."
                ),
            })

    return warnings


# ── Helpers ───────────────────────────────────────────────────────────────────

def _closed(positions: pd.DataFrame) -> pd.DataFrame:
    if positions.empty:
        return pd.DataFrame()
    mask = (positions["status"] == "CLOSED") & (positions["net_pnl"].notna())
    return positions[mask].copy()
