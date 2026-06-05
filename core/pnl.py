"""
P&L and charge calculation.
All Zerodha charge rates are accurate for FY 2024-25.
"""

from typing import Dict
import pandas as pd

from config import (
    BROKERAGE_FLAT, BROKERAGE_PCT,
    ETC_NSE_OPTIONS, ETC_BSE_OPTIONS,
    SEBI_CHARGES, STT_OPTIONS_SELL,
    STAMP_DUTY_BUY, GST_RATE,
)


def calculate_charges_for_trade(
    price: float,
    quantity: int,
    buy_sell: str,
    exchange: str = "NSE",
) -> Dict[str, float]:
    """
    Return a full charge breakdown for a single option fill.
    """
    turnover = price * quantity

    brokerage = min(BROKERAGE_FLAT, turnover * BROKERAGE_PCT)
    etc_rate  = ETC_NSE_OPTIONS if exchange == "NSE" else ETC_BSE_OPTIONS
    etc       = turnover * etc_rate
    sebi      = turnover * SEBI_CHARGES
    stt       = turnover * STT_OPTIONS_SELL if buy_sell == "S" else 0.0
    stamp     = turnover * STAMP_DUTY_BUY   if buy_sell == "B" else 0.0
    gst       = (brokerage + etc + sebi) * GST_RATE
    total     = brokerage + etc + sebi + stt + stamp + gst

    return {
        "turnover":      round(turnover, 2),
        "brokerage":     round(brokerage, 2),
        "etc":           round(etc, 4),
        "sebi":          round(sebi, 4),
        "stt":           round(stt, 2),
        "stamp_duty":    round(stamp, 4),
        "gst":           round(gst, 2),
        "total_charges": round(total, 2),
    }


def calculate_position_pnl(trades: pd.DataFrame) -> Dict[str, float]:
    """
    Gross P&L = sell proceeds − buy costs.
    Net P&L   = Gross P&L − all charges.
    """
    if trades.empty:
        return {"gross_pnl": 0.0, "net_pnl": 0.0, "total_charges": 0.0}

    gross_pnl     = 0.0
    total_charges = 0.0

    for _, t in trades.iterrows():
        price    = float(t["price"])
        qty      = int(t["quantity"])
        buy_sell = str(t["buy_sell"])
        exchange = str(t.get("exchange") or "NSE")

        gross_pnl += price * qty if buy_sell == "S" else -(price * qty)
        ch = calculate_charges_for_trade(price, qty, buy_sell, exchange)
        total_charges += ch["total_charges"]

    return {
        "gross_pnl":     round(gross_pnl, 2),
        "net_pnl":       round(gross_pnl - total_charges, 2),
        "total_charges": round(total_charges, 2),
    }


def get_detailed_charge_breakdown(trades: pd.DataFrame) -> Dict[str, float]:
    """Sum all charge components across every fill in a position."""
    totals: Dict[str, float] = {
        "brokerage": 0.0, "etc": 0.0, "sebi": 0.0,
        "stt": 0.0, "stamp_duty": 0.0, "gst": 0.0, "total_charges": 0.0,
    }
    for _, t in trades.iterrows():
        ch = calculate_charges_for_trade(
            float(t["price"]), int(t["quantity"]),
            str(t["buy_sell"]), str(t.get("exchange") or "NSE"),
        )
        for key in totals:
            totals[key] += ch.get(key, 0.0)
    return {k: round(v, 2) for k, v in totals.items()}


def estimate_max_capital(trades: pd.DataFrame) -> float:
    """
    Conservative estimate of capital deployed.
    For option buyers: total premium paid.
    For option sellers: sum of margins (approximated as premium received × 5).
    """
    if trades.empty:
        return 0.0
    buy_trades = trades[trades["buy_sell"] == "B"]
    sell_trades = trades[trades["buy_sell"] == "S"]
    premium_paid     = float((buy_trades["price"] * buy_trades["quantity"]).sum())
    premium_received = float((sell_trades["price"] * sell_trades["quantity"]).sum())
    # rough margin estimate for naked/spread sellers
    approx_margin = premium_received * 5
    return round(max(premium_paid, approx_margin), 2)


def position_status_from_trades(trades: pd.DataFrame) -> str:
    """
    Infer OPEN/CLOSED from whether total buy qty equals total sell qty
    for the same contract.
    """
    if trades.empty:
        return "OPEN"

    for (expiry, strike, otype), grp in trades.groupby(
        ["expiry", "strike", "option_type"], dropna=False
    ):
        buy_qty  = int(grp[grp["buy_sell"] == "B"]["quantity"].sum())
        sell_qty = int(grp[grp["buy_sell"] == "S"]["quantity"].sum())
        if buy_qty != sell_qty:
            return "OPEN"

    return "CLOSED"
