"""
Strategy clustering and detection.
Groups individual fills into meaningful option strategies and tracks lifecycle.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pandas as pd

from config import CLUSTER_WINDOW_MINUTES, STRATEGY_NAMES


# ── Public API ────────────────────────────────────────────────────────────────

def cluster_and_classify(raw_trades: pd.DataFrame) -> List[Dict]:
    """
    Cluster raw trade fills into strategy positions.
    Returns list of position dicts ready for DB insertion.
    """
    if raw_trades.empty:
        return []

    df = raw_trades.copy()
    df["trade_dt"] = pd.to_datetime(df["trade_datetime"])
    df = df.sort_values("trade_dt").reset_index(drop=True)

    all_positions: List[Dict] = []

    for underlying, group in df.groupby("underlying"):
        positions = _process_underlying(group.copy(), str(underlying))
        all_positions.extend(positions)

    return all_positions


# ── Per-underlying processing ─────────────────────────────────────────────────

def _process_underlying(group: pd.DataFrame, underlying: str) -> List[Dict]:
    """
    For one underlying, assign each trade to either:
    - A new cluster being built (entry legs)
    - An existing open position (exit / adjustment leg)
    """
    open_positions: Dict[str, Dict] = {}   # position_id → pos dict
    finalized: List[Dict] = []

    # Active cluster accumulator
    cluster_trades: List[pd.Series] = []
    cluster_last_dt: Optional[datetime] = None

    def flush_cluster():
        if not cluster_trades:
            return
        cluster_df = pd.DataFrame(cluster_trades)
        pos = _new_position(cluster_df, underlying)
        open_positions[pos["position_id"]] = pos
        cluster_trades.clear()

    for _, trade in group.sort_values("trade_dt").iterrows():
        # 1. Check if this trade closes/adjusts an open position
        matched_pos_id = _find_match(trade, open_positions)

        if matched_pos_id:
            # Flush any active cluster first (it belongs to a different entry)
            flush_cluster()
            pos = open_positions[matched_pos_id]
            _apply_closing_trade(pos, trade)
            if _is_fully_closed(pos):
                finalized.append(pos)
                del open_positions[matched_pos_id]
            continue

        # 2. Try adding to active cluster
        if cluster_last_dt is not None:
            gap_minutes = (trade["trade_dt"] - cluster_last_dt).total_seconds() / 60
            if gap_minutes > CLUSTER_WINDOW_MINUTES:
                flush_cluster()

        cluster_trades.append(trade)
        cluster_last_dt = trade["trade_dt"]

    # Flush remaining cluster
    flush_cluster()

    # Any still-open positions are not yet closed
    for pos in open_positions.values():
        finalized.append(pos)

    return finalized


# ── Match a trade to an existing open position ────────────────────────────────

def _find_match(trade: pd.Series, open_positions: Dict[str, Dict]) -> Optional[str]:
    """Return position_id if this trade is an exit/adjustment of an open leg."""
    if not open_positions:
        return None

    trade_expiry = trade.get("expiry")
    trade_strike = float(trade.get("strike") or 0)
    trade_type   = trade.get("option_type")
    trade_bs     = trade["buy_sell"]

    for pos_id, pos in open_positions.items():
        if pos["underlying"] != trade["underlying"]:
            continue
        for ol in pos.get("open_legs", []):
            same_expiry = (ol["expiry"] == trade_expiry)
            same_strike = abs(float(ol.get("strike") or 0) - trade_strike) < 0.01
            same_type   = (ol["option_type"] == trade_type)
            opposite_bs = (ol["buy_sell"] != trade_bs)
            if same_expiry and same_strike and same_type and opposite_bs:
                return pos_id

    return None


def _apply_closing_trade(pos: Dict, trade: pd.Series) -> None:
    """Add a closing/adjustment leg to an existing position."""
    trade_expiry = trade.get("expiry")
    trade_strike = float(trade.get("strike") or 0)
    trade_type   = trade.get("option_type")

    # Determine role: EXIT if it closes an existing leg, else ADJUSTMENT
    is_exit = False
    for ol in pos.get("open_legs", []):
        if (ol["expiry"] == trade_expiry and
                abs(float(ol.get("strike") or 0) - trade_strike) < 0.01 and
                ol["option_type"] == trade_type and
                ol["buy_sell"] != trade["buy_sell"]):
            is_exit = True
            pos["open_legs"].remove(ol)
            break

    role = "EXIT" if is_exit else "ADJUSTMENT"
    if role == "ADJUSTMENT":
        pos["n_adjustments"] = pos.get("n_adjustments", 0) + 1
        # Track the new leg as open
        pos.setdefault("open_legs", []).append({
            "expiry":      trade.get("expiry"),
            "strike":      trade.get("strike"),
            "option_type": trade.get("option_type"),
            "buy_sell":    trade["buy_sell"],
            "quantity":    int(trade["quantity"]),
        })

    pos.setdefault("all_legs", []).append({
        "id":          trade["id"],
        "role":        role,
        "expiry":      trade.get("expiry"),
        "strike":      trade.get("strike"),
        "option_type": trade.get("option_type"),
        "buy_sell":    trade["buy_sell"],
        "quantity":    int(trade["quantity"]),
        "price":       float(trade["price"]),
        "trade_dt":    trade["trade_dt"],
    })
    pos["n_legs"] = len(pos.get("all_legs", []))

    if not pos.get("open_legs"):
        pos["status"] = "CLOSED"
        pos["exit_datetime"] = trade["trade_dt"].strftime("%Y-%m-%d %H:%M:%S")


def _is_fully_closed(pos: Dict) -> bool:
    return pos.get("status") == "CLOSED"


# ── Create a new position from a cluster ─────────────────────────────────────

def _new_position(cluster: pd.DataFrame, underlying: str) -> Dict:
    cluster = cluster.sort_values("trade_dt").reset_index(drop=True)
    strategy_type, confidence, conf_note = detect_strategy(cluster)

    entry_dt   = cluster.iloc[0]["trade_dt"]
    entry_str  = entry_dt.strftime("%Y-%m-%d %H:%M:%S")
    uid        = str(uuid.uuid4())[:8]
    position_id = f"{underlying}_{entry_dt.strftime('%Y%m%d')}_{strategy_type}_{uid}"

    open_legs = []
    all_legs  = []
    for _, t in cluster.iterrows():
        leg = {
            "id":          t["id"],
            "role":        "ENTRY",
            "expiry":      t.get("expiry"),
            "strike":      t.get("strike"),
            "option_type": t.get("option_type"),
            "buy_sell":    t["buy_sell"],
            "quantity":    int(t["quantity"]),
            "price":       float(t["price"]),
            "trade_dt":    t["trade_dt"],
        }
        all_legs.append(leg)
        open_legs.append({
            "expiry":      t.get("expiry"),
            "strike":      t.get("strike"),
            "option_type": t.get("option_type"),
            "buy_sell":    t["buy_sell"],
            "quantity":    int(t["quantity"]),
        })

    return {
        "position_id":     position_id,
        "underlying":      underlying,
        "strategy_type":   strategy_type,
        "strategy_label":  STRATEGY_NAMES.get(strategy_type, strategy_type),
        "confidence":      confidence,
        "confidence_note": conf_note,
        "status":          "OPEN",
        "entry_datetime":  entry_str,
        "exit_datetime":   None,
        "n_legs":          len(all_legs),
        "n_adjustments":   0,
        "gross_pnl":       None,
        "net_pnl":         None,
        "total_charges":   None,
        "max_capital":     None,
        "leg_structure":   _build_leg_structure(cluster),
        "all_legs":        all_legs,
        "open_legs":       open_legs,
    }


# ── Strategy detection ────────────────────────────────────────────────────────

def detect_strategy(legs: pd.DataFrame) -> Tuple[str, float, str]:
    """
    Identify strategy type from entry legs.
    Returns (strategy_type, confidence 0–1, explanation).
    """
    if legs.empty:
        return ("CUSTOM", 0.0, "Empty cluster")

    legs = legs.copy()
    legs["direction"] = legs["buy_sell"].map({"B": 1, "S": -1})

    # Filter to option rows only
    option_legs = legs[legs["option_type"].isin(["CE", "PE"])]
    if option_legs.empty:
        return ("CUSTOM", 0.4, "No option legs detected")

    n  = len(option_legs)
    calls = option_legs[option_legs["option_type"] == "CE"]
    puts  = option_legs[option_legs["option_type"] == "PE"]
    nc, np_ = len(calls), len(puts)

    n_expiries = option_legs["expiry"].nunique()

    if n == 1:
        return _one_leg(option_legs.iloc[0])
    if n == 2:
        return _two_legs(option_legs, calls, puts, nc, np_, n_expiries)
    if n == 3:
        return _three_legs(option_legs, calls, puts, nc, np_)
    if n == 4:
        return _four_legs(option_legs, calls, puts, nc, np_, n_expiries)

    return ("CUSTOM", 0.5, f"{n}-leg structure — classified as custom")


def _one_leg(leg: pd.Series) -> Tuple[str, float, str]:
    d = int(leg["direction"])
    t = str(leg["option_type"])
    if t == "CE":
        return ("LONG_CALL", 1.0, "Single long call") if d == 1 else ("SHORT_CALL", 1.0, "Single short call")
    return ("LONG_PUT", 1.0, "Single long put") if d == 1 else ("SHORT_PUT", 1.0, "Single short put")


def _two_legs(legs, calls, puts, nc, np_, n_expiries) -> Tuple[str, float, str]:
    l1, l2 = legs.iloc[0], legs.iloc[1]

    # Same option type (CC or PP)
    if nc == 2 or np_ == 2:
        same_type_legs = calls if nc == 2 else puts
        same_type_legs = same_type_legs.sort_values("strike")
        same_exp = (l1["expiry"] == l2["expiry"])
        same_str = abs(float(l1.get("strike") or 0) - float(l2.get("strike") or 0)) < 0.01

        if same_exp and not same_str:
            q1 = int(l1["quantity"]); q2 = int(l2["quantity"])
            ratio = max(q1, q2) / min(q1, q2) if min(q1, q2) else 1.0
            if abs(ratio - 1.0) > 0.15:
                return ("RATIO_SPREAD", 0.88, f"Ratio {ratio:.1f}:1 — same expiry, different strikes")

            lower_dir = int(same_type_legs.iloc[0]["direction"])
            if nc == 2:
                return ("BULL_CALL_SPREAD", 0.92, "Long lower CE + short higher CE") \
                    if lower_dir == 1 else ("BEAR_CALL_SPREAD", 0.92, "Short lower CE + long higher CE")
            else:
                return ("BEAR_PUT_SPREAD", 0.92, "Long higher PE + short lower PE") \
                    if lower_dir == 1 else ("BULL_PUT_SPREAD", 0.92, "Short higher PE + long lower PE")

        if not same_exp and same_str:
            q1 = int(l1["quantity"]); q2 = int(l2["quantity"])
            ratio = max(q1, q2) / min(q1, q2) if min(q1, q2) else 1.0
            if abs(ratio - 1.0) > 0.15:
                return ("RATIO_CALENDAR", 0.88, f"Ratio calendar {ratio:.1f}:1")
            return ("CALENDAR_SPREAD", 0.92, "Same strike, different expiries")

        if not same_exp and not same_str:
            return ("DIAGONAL_SPREAD", 0.85, "Different strikes and different expiries")

    # One CE + one PE
    if nc == 1 and np_ == 1:
        c = calls.iloc[0]; p = puts.iloc[0]
        same_exp = (c["expiry"] == p["expiry"])
        same_str = abs(float(c.get("strike") or 0) - float(p.get("strike") or 0)) < 0.01
        same_dir = (int(c["direction"]) == int(p["direction"]))

        if same_dir and same_exp:
            if same_str:
                lbl = "Long straddle" if int(c["direction"]) == 1 else "Short straddle"
                return ("STRADDLE", 0.96, lbl)
            else:
                lbl = "Long strangle" if int(c["direction"]) == 1 else "Short strangle"
                return ("STRANGLE", 0.96, lbl)

    return ("CUSTOM", 0.50, "2-leg structure not matched")


def _three_legs(legs, calls, puts, nc, np_) -> Tuple[str, float, str]:
    if nc == 2 and np_ == 1:
        short_calls = calls[calls["direction"] == -1]
        long_calls  = calls[calls["direction"] == 1]
        short_puts  = puts[puts["direction"] == -1]
        if len(short_calls) == 1 and len(long_calls) == 1 and len(short_puts) == 1:
            cs = float(short_calls.iloc[0].get("strike") or 0)
            cl = float(long_calls.iloc[0].get("strike") or 0)
            if cl > cs:  # long call above short call (capped upside risk)
                return ("JADE_LIZARD", 0.78, "Short put + short call spread")
    return ("CUSTOM", 0.52, f"3-leg structure ({nc} CE, {np_} PE)")


def _four_legs(legs, calls, puts, nc, np_, n_expiries) -> Tuple[str, float, str]:
    if nc == 2 and np_ == 2 and n_expiries == 1:
        cs = calls[calls["direction"] == -1]
        cl = calls[calls["direction"] == 1]
        ps = puts[puts["direction"] == -1]
        pl = puts[puts["direction"] == 1]

        if len(cs) == 1 and len(cl) == 1 and len(ps) == 1 and len(pl) == 1:
            cs_k = float(cs.iloc[0].get("strike") or 0)
            cl_k = float(cl.iloc[0].get("strike") or 0)
            ps_k = float(ps.iloc[0].get("strike") or 0)
            pl_k = float(pl.iloc[0].get("strike") or 0)

            # Classic Iron Condor: short inner, long outer
            if cs_k < cl_k and ps_k > pl_k:
                if abs(cs_k - ps_k) < 0.01:
                    return ("IRON_FLY", 0.95, "Short ATM straddle + long OTM wings (Iron Fly)")
                return ("IRON_CONDOR", 0.95, "Short inner strikes + long outer wings")

    # All-same-type butterfly
    if (nc == 4 and np_ == 0) or (nc == 0 and np_ == 4):
        return ("BUTTERFLY", 0.80, "4-leg butterfly")

    return ("CUSTOM", 0.50, f"4-leg structure ({nc} CE, {np_} PE)")


# ── Display helpers ───────────────────────────────────────────────────────────

def _build_leg_structure(cluster: pd.DataFrame) -> List[Dict]:
    structure = []
    for _, t in cluster.sort_values("trade_dt").iterrows():
        direction = "+" if t["buy_sell"] == "B" else "-"
        qty = int(t["quantity"])
        strike = int(t.get("strike") or 0)
        expiry = str(t.get("expiry") or "")
        opt    = str(t.get("option_type") or "")
        underlying = str(t.get("underlying") or "")
        structure.append({
            "display":     f"{direction}{qty} {underlying} {expiry} {strike} {opt}",
            "direction":   direction,
            "quantity":    qty,
            "strike":      t.get("strike"),
            "expiry":      expiry,
            "option_type": opt,
            "buy_sell":    t["buy_sell"],
            "price":       float(t["price"]),
        })
    return structure
