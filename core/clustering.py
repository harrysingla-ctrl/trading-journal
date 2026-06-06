"""
Strategy clustering and classification — v2.

Key changes over v1:
  • Net-position based detection: fills are aggregated per contract before
    classifying, so multiple partial fills of same contract work correctly.
  • Dynamic re-classification: every exit and adjustment triggers a fresh
    strategy detection on the remaining open legs.
    e.g. Strangle → close one leg → correctly becomes Short Call / Short Put.
  • 20+ strategy patterns including asymmetric, ratio, butterfly, condor,
    broken-wing, back-spread, jade lizard, risk reversal.
  • Partial close support: reduces open qty instead of removing the leg.
"""

from __future__ import annotations

import uuid
from typing import Dict, List, Optional, Tuple

import pandas as pd

from config import CLUSTER_WINDOW_MINUTES, STRATEGY_NAMES


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def cluster_and_classify(raw_trades: pd.DataFrame) -> List[Dict]:
    """Cluster raw fills into strategy positions. Returns list of position dicts."""
    if raw_trades.empty:
        return []

    df = raw_trades.copy()
    df["trade_dt"] = pd.to_datetime(df["trade_datetime"])
    df = df.sort_values("trade_dt").reset_index(drop=True)

    all_positions: List[Dict] = []
    for underlying, group in df.groupby("underlying"):
        all_positions.extend(_process_underlying(group.copy(), str(underlying)))
    return all_positions


def detect_strategy(legs: pd.DataFrame) -> Tuple[str, float, str]:
    """
    Public helper — detect strategy from a DataFrame of entry legs.
    Used by the upload page to show detected strategy before saving.
    """
    open_legs = [
        {
            "expiry":      t.get("expiry"),
            "strike":      t.get("strike"),
            "option_type": t.get("option_type"),
            "buy_sell":    t["buy_sell"],
            "quantity":    int(t["quantity"]),
        }
        for _, t in legs.iterrows()
    ]
    return _classify_open_legs(open_legs)


# ─────────────────────────────────────────────────────────────────────────────
# Per-underlying processing
# ─────────────────────────────────────────────────────────────────────────────

def _process_underlying(group: pd.DataFrame, underlying: str) -> List[Dict]:
    open_positions: Dict[str, Dict] = {}
    finalized:      List[Dict]      = []

    cluster_trades: List[pd.Series] = []
    cluster_last_dt = None

    def flush_cluster() -> None:
        if not cluster_trades:
            return
        pos = _new_position(pd.DataFrame(cluster_trades), underlying)
        open_positions[pos["position_id"]] = pos
        cluster_trades.clear()

    for _, trade in group.sort_values("trade_dt").iterrows():
        matched_id = _find_match(trade, open_positions)

        if matched_id:
            flush_cluster()
            _apply_trade_to_position(open_positions[matched_id], trade)
            if _is_closed(open_positions[matched_id]):
                finalized.append(open_positions.pop(matched_id))
            continue

        if cluster_last_dt is not None:
            gap_min = (trade["trade_dt"] - cluster_last_dt).total_seconds() / 60
            if gap_min > CLUSTER_WINDOW_MINUTES:
                flush_cluster()

        cluster_trades.append(trade)
        cluster_last_dt = trade["trade_dt"]

    flush_cluster()
    finalized.extend(open_positions.values())
    return finalized


# ─────────────────────────────────────────────────────────────────────────────
# Position matching
# ─────────────────────────────────────────────────────────────────────────────

def _find_match(trade: pd.Series, open_positions: Dict[str, Dict]) -> Optional[str]:
    """Return the position_id this trade closes or adjusts, else None."""
    t_exp    = trade.get("expiry")
    t_strike = float(trade.get("strike") or 0)
    t_type   = trade.get("option_type")
    t_bs     = trade["buy_sell"]

    for pos_id, pos in open_positions.items():
        if pos["underlying"] != trade["underlying"]:
            continue
        for ol in pos.get("open_legs", []):
            if (ol["expiry"] == t_exp
                    and abs(float(ol.get("strike") or 0) - t_strike) < 0.01
                    and ol["option_type"] == t_type
                    and ol["buy_sell"] != t_bs):
                return pos_id
    return None


def _apply_trade_to_position(pos: Dict, trade: pd.Series) -> None:
    """
    Apply an exit or adjustment trade to a position.
    After applying, re-classify the remaining open legs.
    """
    t_exp    = trade.get("expiry")
    t_strike = float(trade.get("strike") or 0)
    t_type   = trade.get("option_type")
    t_qty    = int(trade["quantity"])
    t_bs     = trade["buy_sell"]

    is_exit = False
    open_legs = pos.get("open_legs", [])

    for i, ol in enumerate(open_legs):
        if (ol["expiry"] == t_exp
                and abs(float(ol.get("strike") or 0) - t_strike) < 0.01
                and ol["option_type"] == t_type
                and ol["buy_sell"] != t_bs):

            is_exit  = True
            ol_qty   = int(ol["quantity"])

            if t_qty >= ol_qty:
                open_legs.pop(i)
                # Reversed position — add remaining qty in new direction
                if t_qty > ol_qty:
                    open_legs.append({
                        "expiry": t_exp, "strike": t_strike,
                        "option_type": t_type, "buy_sell": t_bs,
                        "quantity": t_qty - ol_qty,
                    })
            else:
                # Partial close — reduce qty
                open_legs[i] = {**ol, "quantity": ol_qty - t_qty}
            break

    role = "EXIT" if is_exit else "ADJUSTMENT"
    if role == "ADJUSTMENT":
        pos["n_adjustments"] = pos.get("n_adjustments", 0) + 1
        open_legs.append({
            "expiry": t_exp, "strike": t_strike,
            "option_type": t_type, "buy_sell": t_bs, "quantity": t_qty,
        })

    pos.setdefault("all_legs", []).append({
        "id":          trade["id"],
        "role":        role,
        "expiry":      t_exp,
        "strike":      t_strike,
        "option_type": t_type,
        "buy_sell":    t_bs,
        "quantity":    t_qty,
        "price":       float(trade["price"]),
        "trade_dt":    trade["trade_dt"],
    })
    pos["n_legs"] = len(pos["all_legs"])

    # ── Re-classify on every change ───────────────────────────────────────
    if open_legs:
        new_type, new_conf, new_note = _classify_open_legs(open_legs)
        pos["strategy_type"]    = new_type
        pos["strategy_label"]   = STRATEGY_NAMES.get(new_type, new_type)
        pos["confidence"]       = new_conf
        pos["confidence_note"]  = f"[After {role.lower()}] {new_note}"
    else:
        pos["status"]        = "CLOSED"
        pos["exit_datetime"] = trade["trade_dt"].strftime("%Y-%m-%d %H:%M:%S")


def _is_closed(pos: Dict) -> bool:
    return pos.get("status") == "CLOSED"


# ─────────────────────────────────────────────────────────────────────────────
# New position creation
# ─────────────────────────────────────────────────────────────────────────────

def _new_position(cluster: pd.DataFrame, underlying: str) -> Dict:
    cluster = cluster.sort_values("trade_dt").reset_index(drop=True)

    open_legs: List[Dict] = []
    all_legs:  List[Dict] = []

    for _, t in cluster.iterrows():
        open_legs.append({
            "expiry":      t.get("expiry"),
            "strike":      t.get("strike"),
            "option_type": t.get("option_type"),
            "buy_sell":    t["buy_sell"],
            "quantity":    int(t["quantity"]),
        })
        all_legs.append({
            "id":          t["id"],
            "role":        "ENTRY",
            "expiry":      t.get("expiry"),
            "strike":      t.get("strike"),
            "option_type": t.get("option_type"),
            "buy_sell":    t["buy_sell"],
            "quantity":    int(t["quantity"]),
            "price":       float(t["price"]),
            "trade_dt":    t["trade_dt"],
        })

    strategy_type, confidence, conf_note = _classify_open_legs(open_legs)
    entry_dt    = cluster.iloc[0]["trade_dt"]
    position_id = f"{underlying}_{entry_dt.strftime('%Y%m%d')}_{strategy_type}_{str(uuid.uuid4())[:8]}"

    return {
        "position_id":     position_id,
        "underlying":      underlying,
        "strategy_type":   strategy_type,
        "strategy_label":  STRATEGY_NAMES.get(strategy_type, strategy_type),
        "confidence":      confidence,
        "confidence_note": conf_note,
        "status":          "OPEN",
        "entry_datetime":  entry_dt.strftime("%Y-%m-%d %H:%M:%S"),
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


# ─────────────────────────────────────────────────────────────────────────────
# Net-position based classification (called at entry + after every change)
# ─────────────────────────────────────────────────────────────────────────────

def _classify_open_legs(open_legs: List[Dict]) -> Tuple[str, float, str]:
    """
    Aggregate open_legs to net positions per contract, then classify.
    This handles multiple fills of the same contract and partial exits.
    """
    if not open_legs:
        return ("CUSTOM", 0.0, "No open legs")

    nets: Dict[tuple, int] = {}
    for ol in open_legs:
        key  = (str(ol.get("expiry") or ""), float(ol.get("strike") or 0), str(ol.get("option_type") or ""))
        sign = 1 if ol["buy_sell"] == "B" else -1
        nets[key] = nets.get(key, 0) + sign * int(ol.get("quantity") or 0)

    active = {k: v for k, v in nets.items() if v != 0}
    if not active:
        return ("CUSTOM", 0.0, "Net position is flat")

    struct = [
        {"expiry": k[0], "strike": k[1], "option_type": k[2], "net_qty": v}
        for k, v in active.items()
    ]
    return _classify_struct(struct)


def _classify_struct(legs: List[Dict]) -> Tuple[str, float, str]:
    n   = len(legs)
    calls = [l for l in legs if l["option_type"] == "CE"]
    puts  = [l for l in legs if l["option_type"] == "PE"]
    nc, np_ = len(calls), len(puts)

    lc = [l for l in calls if l["net_qty"] > 0]
    sc = [l for l in calls if l["net_qty"] < 0]
    lp = [l for l in puts  if l["net_qty"] > 0]
    sp = [l for l in puts  if l["net_qty"] < 0]

    n_exp = len({l["expiry"] for l in legs})

    if n == 1:
        return _one_leg(legs[0])
    if n == 2:
        return _two_legs(legs, calls, puts, nc, np_, lc, sc, lp, sp, n_exp)
    if n == 3:
        return _three_legs(legs, calls, puts, nc, np_, lc, sc, lp, sp)
    if n == 4:
        return _four_legs(legs, calls, puts, nc, np_, lc, sc, lp, sp, n_exp)
    return _complex(legs, calls, puts, lc, sc, lp, sp, n_exp)


# ─────────────────────────────────────────────────────────────────────────────
# Per-count classifiers
# ─────────────────────────────────────────────────────────────────────────────

def _one_leg(l: Dict) -> Tuple[str, float, str]:
    nq, ot = l["net_qty"], l["option_type"]
    if ot == "CE":
        return ("LONG_CALL", 1.0, "Net long call") if nq > 0 else ("SHORT_CALL", 1.0, "Net short call")
    return ("LONG_PUT", 1.0, "Net long put") if nq > 0 else ("SHORT_PUT", 1.0, "Net short put")


def _two_legs(legs, calls, puts, nc, np_, lc, sc, lp, sp, n_exp) -> Tuple[str, float, str]:
    # ── Both same type ────────────────────────────────────────────────────
    if nc == 2 or np_ == 2:
        grp = calls if nc == 2 else puts
        typ = "CE"  if nc == 2 else "PE"
        s   = sorted(grp, key=lambda x: x["strike"])
        lo, hi = s[0], s[1]
        same_exp    = lo["expiry"] == hi["expiry"]
        same_strike = abs(lo["strike"] - hi["strike"]) < 0.01

        if same_exp and not same_strike:
            q_lo, q_hi = abs(lo["net_qty"]), abs(hi["net_qty"])
            ratio = max(q_lo, q_hi) / min(q_lo, q_hi) if min(q_lo, q_hi) else 1

            if abs(ratio - 1.0) > 0.15:
                return ("RATIO_SPREAD", 0.90, f"Ratio {ratio:.1f}:1 vertical spread")

            if typ == "CE":
                if lo["net_qty"] > 0:
                    return ("BULL_CALL_SPREAD", 0.93, "Long lower CE, short upper CE")
                return ("BEAR_CALL_SPREAD", 0.93, "Short lower CE, long upper CE")
            else:
                if hi["net_qty"] < 0:
                    return ("BULL_PUT_SPREAD", 0.93, "Short higher PE, long lower PE")
                return ("BEAR_PUT_SPREAD", 0.93, "Long higher PE, short lower PE")

        if not same_exp:
            if same_strike:
                q1, q2 = abs(lo["net_qty"]), abs(hi["net_qty"])
                ratio  = max(q1, q2) / min(q1, q2) if min(q1, q2) else 1
                if abs(ratio - 1.0) > 0.15:
                    return ("RATIO_CALENDAR", 0.90, f"Ratio calendar {ratio:.1f}:1")
                return ("CALENDAR_SPREAD", 0.93, "Same strike, different expiries")
            return ("DIAGONAL_SPREAD", 0.85, "Different strikes and expiries")

    # ── One CE + one PE ────────────────────────────────────────────────────
    if nc == 1 and np_ == 1:
        c, p = calls[0], puts[0]
        same_exp    = c["expiry"] == p["expiry"]
        same_strike = abs(c["strike"] - p["strike"]) < 0.01
        both_long   = c["net_qty"] > 0 and p["net_qty"] > 0
        both_short  = c["net_qty"] < 0 and p["net_qty"] < 0

        if same_exp and (both_long or both_short):
            pfx = "Long" if both_long else "Short"
            if same_strike:
                return ("STRADDLE", 0.96, f"{pfx} straddle")
            # Asymmetric qty → still a strangle
            q_ratio = abs(c["net_qty"]) / abs(p["net_qty"]) if p["net_qty"] else 1
            asym    = f" (qty ratio {q_ratio:.1f}:1)" if abs(q_ratio - 1.0) > 0.2 else ""
            return ("STRANGLE", 0.93 if not asym else 0.80, f"{pfx} strangle{asym}")

        # Risk reversal: long call + short put (or reverse)
        if same_exp and not (both_long or both_short):
            return ("CUSTOM", 0.70, "Risk reversal / synthetic position")

        # Cross-expiry CE+PE
        if not same_exp:
            return ("DIAGONAL_SPREAD", 0.72, "CE + PE on different expiries")

    return ("CUSTOM", 0.50, f"2-leg ({nc} CE, {np_} PE)")


def _three_legs(legs, calls, puts, nc, np_, lc, sc, lp, sp) -> Tuple[str, float, str]:
    # ── 3-strike butterfly (all CE or all PE) ─────────────────────────────
    if nc == 3 and np_ == 0:
        r = _butterfly_3(calls, "CE")
        if r:
            return r
    if nc == 0 and np_ == 3:
        r = _butterfly_3(puts, "PE")
        if r:
            return r

    # ── Jade Lizard: short put + short call + long higher call ─────────────
    if nc == 2 and np_ == 1 and len(sc) == 1 and len(lc) == 1 and len(sp) == 1:
        if lc[0]["strike"] > sc[0]["strike"]:
            return ("JADE_LIZARD", 0.84, "Short put + short call spread (Jade Lizard)")

    # ── Reverse Jade Lizard: short call + short put spread ────────────────
    if nc == 1 and np_ == 2 and len(sc) == 1 and len(sp) == 1 and len(lp) == 1:
        if sp[0]["strike"] > lp[0]["strike"]:
            return ("JADE_LIZARD", 0.80, "Reverse Jade Lizard (short call + short put spread)")

    # ── Call / Put back spread (ratio) ────────────────────────────────────
    if nc == 2 and np_ == 0 and len(sc) == 1 and len(lc) == 1:
        if lc[0]["strike"] > sc[0]["strike"]:
            ratio = abs(lc[0]["net_qty"]) / abs(sc[0]["net_qty"]) if sc[0]["net_qty"] else 1
            if ratio >= 1.5:
                return ("RATIO_SPREAD", 0.84, f"Call back spread {ratio:.0f}:1")
    if nc == 0 and np_ == 2 and len(sp) == 1 and len(lp) == 1:
        if lp[0]["strike"] < sp[0]["strike"]:
            ratio = abs(lp[0]["net_qty"]) / abs(sp[0]["net_qty"]) if sp[0]["net_qty"] else 1
            if ratio >= 1.5:
                return ("RATIO_SPREAD", 0.84, f"Put back spread {ratio:.0f}:1")

    # ── 3-leg strangle + hedge ────────────────────────────────────────────
    if nc == 1 and np_ == 2:
        if len(sc) == 1 and len(sp) == 1 and len(lp) == 1:
            return ("CUSTOM", 0.65, "Short call + short put spread (half-IC)")
    if nc == 2 and np_ == 1:
        if len(sc) == 1 and len(sp) == 1 and len(lc) == 1:
            return ("CUSTOM", 0.65, "Short put + short call spread (half-IC)")

    return ("CUSTOM", 0.55,
            f"3-leg: {len(sc)}SC {len(lc)}LC {len(sp)}SP {len(lp)}LP")


def _four_legs(legs, calls, puts, nc, np_, lc, sc, lp, sp, n_exp) -> Tuple[str, float, str]:
    # ── Iron Condor / Iron Fly ────────────────────────────────────────────
    if nc == 2 and np_ == 2 and n_exp == 1 and len(sc) == 1 and len(lc) == 1 and len(sp) == 1 and len(lp) == 1:
        cs_k = sc[0]["strike"]; cl_k = lc[0]["strike"]
        ps_k = sp[0]["strike"]; pl_k = lp[0]["strike"]

        if cs_k < cl_k and ps_k > pl_k:
            if abs(cs_k - ps_k) < 0.01:
                return ("IRON_FLY", 0.95, "Short ATM straddle + long OTM wings")
            # Broken wing (asymmetric widths)
            cw = cl_k - cs_k; pw = ps_k - pl_k
            if abs(cw - pw) / max(cw, pw, 1) > 0.3:
                return ("IRON_CONDOR", 0.88,
                        f"Broken wing IC (call Δ{cw:.0f}, put Δ{pw:.0f})")
            return ("IRON_CONDOR", 0.95, "Short inner strikes, long outer wings")

    # ── All-same-type structures ───────────────────────────────────────────
    if nc == 4 and np_ == 0:
        r = _butterfly_4(calls, "CE")
        if r:
            return r
    if nc == 0 and np_ == 4:
        r = _butterfly_4(puts, "PE")
        if r:
            return r

    # ── Double calendar / diagonal ────────────────────────────────────────
    if n_exp == 2:
        if nc == 2 and np_ == 0:
            return ("CALENDAR_SPREAD", 0.75, "Double call calendar/diagonal")
        if nc == 0 and np_ == 2:
            return ("CALENDAR_SPREAD", 0.75, "Double put calendar/diagonal")
        if nc == 2 and np_ == 2:
            return ("CALENDAR_SPREAD", 0.68, "Double CE+PE calendar/diagonal")

    # ── Ratio spread with 4 legs ──────────────────────────────────────────
    if nc == 4 and np_ == 0 and n_exp == 1:
        if len(sc) == 2 and len(lc) == 2:
            return ("IRON_CONDOR", 0.80, "Call condor (4 strikes)")
    if nc == 0 and np_ == 4 and n_exp == 1:
        if len(sp) == 2 and len(lp) == 2:
            return ("IRON_CONDOR", 0.80, "Put condor (4 strikes)")

    return ("CUSTOM", 0.55,
            f"4-leg: {len(sc)}SC {len(lc)}LC {len(sp)}SP {len(lp)}LP "
            f"({n_exp} expir{'y' if n_exp == 1 else 'ies'})")


def _complex(legs, calls, puts, lc, sc, lp, sp, n_exp) -> Tuple[str, float, str]:
    n = len(legs)
    nc, np_ = len(calls), len(puts)

    if not puts and len(sc) > 0 and len(lc) > 0:
        return ("RATIO_SPREAD", 0.68, f"Complex {n}-leg call structure")
    if not calls and len(sp) > 0 and len(lp) > 0:
        return ("RATIO_SPREAD", 0.68, f"Complex {n}-leg put structure")
    if calls and puts and len(sc) == len(sp) and len(lc) == len(lp):
        return ("IRON_CONDOR", 0.62, f"Extended IC / condor ({n} legs)")

    return ("CUSTOM", 0.50,
            f"{n}-leg: {len(sc)}SC {len(lc)}LC {len(sp)}SP {len(lp)}LP "
            f"across {n_exp} expir{'y' if n_exp == 1 else 'ies'}")


# ─────────────────────────────────────────────────────────────────────────────
# Butterfly / condor helpers
# ─────────────────────────────────────────────────────────────────────────────

def _butterfly_3(legs: List[Dict], typ: str) -> Optional[Tuple[str, float, str]]:
    if len(legs) != 3:
        return None
    s = sorted(legs, key=lambda x: x["strike"])
    lo, mid, hi = s

    long_wings  = lo["net_qty"] > 0 and mid["net_qty"] < 0 and hi["net_qty"] > 0
    short_wings = lo["net_qty"] < 0 and mid["net_qty"] > 0 and hi["net_qty"] < 0

    if not (long_wings or short_wings):
        return None

    label  = "Long" if long_wings else "Short"
    lo_w   = mid["strike"] - lo["strike"]
    hi_w   = hi["strike"] - mid["strike"]
    broken = abs(lo_w - hi_w) / max(lo_w, hi_w, 1) > 0.2

    if broken:
        return ("BUTTERFLY", 0.82, f"{label} broken wing {typ} butterfly")
    return ("BUTTERFLY", 0.90, f"{label} {typ} butterfly")


def _butterfly_4(legs: List[Dict], typ: str) -> Optional[Tuple[str, float, str]]:
    if len(legs) != 4:
        return None
    s    = sorted(legs, key=lambda x: x["strike"])
    dirs = [l["net_qty"] > 0 for l in s]

    if dirs == [True, False, False, True]:
        return ("BUTTERFLY", 0.88, f"Long {typ} condor")
    if dirs == [False, True, True, False]:
        return ("BUTTERFLY", 0.88, f"Short {typ} condor")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Display helpers
# ─────────────────────────────────────────────────────────────────────────────

def _build_leg_structure(cluster: pd.DataFrame) -> List[Dict]:
    structure = []
    for _, t in cluster.sort_values("trade_dt").iterrows():
        direction  = "+" if t["buy_sell"] == "B" else "-"
        structure.append({
            "display":     f"{direction}{int(t['quantity'])} {t.get('underlying','')} "
                           f"{t.get('expiry','')} {int(t.get('strike') or 0)} {t.get('option_type','')}",
            "direction":   direction,
            "quantity":    int(t["quantity"]),
            "strike":      t.get("strike"),
            "expiry":      str(t.get("expiry") or ""),
            "option_type": str(t.get("option_type") or ""),
            "buy_sell":    t["buy_sell"],
            "price":       float(t["price"]),
        })
    return structure
