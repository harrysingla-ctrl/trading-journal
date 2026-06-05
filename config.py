"""
Application configuration and Zerodha charge rates.
All rates are for FY 2024-25.
"""

# ── App ─────────────────────────────────────────────────────────────────────
APP_NAME    = "Options Trading Journal"
APP_ICON    = "📊"
VERSION     = "1.0.0"
DB_PATH     = "trading_journal.db"
CLAUDE_MODEL = "claude-sonnet-4-6"

# ── Underlyings ─────────────────────────────────────────────────────────────
INDEX_UNDERLYINGS = {"NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY", "FINNIFTY", "BANKEX"}

EXCHANGE_MAP = {
    "NIFTY":       "NSE",
    "BANKNIFTY":   "NSE",
    "FINNIFTY":    "NSE",
    "MIDCPNIFTY":  "NSE",
    "SENSEX":      "BSE",
    "BANKEX":      "BSE",
}

# ── Zerodha Brokerage ────────────────────────────────────────────────────────
BROKERAGE_FLAT = 20.0    # Rs 20 per executed order (flat)
BROKERAGE_PCT  = 0.0003  # 0.03% of turnover (whichever is lower)

# ── Exchange Transaction Charges ─────────────────────────────────────────────
ETC_NSE_OPTIONS = 0.000530   # 0.053% on premium turnover
ETC_BSE_OPTIONS = 0.000500   # 0.05% on premium turnover

# ── SEBI Charges ─────────────────────────────────────────────────────────────
SEBI_CHARGES = 0.000001      # Rs 10 per crore

# ── STT ──────────────────────────────────────────────────────────────────────
STT_OPTIONS_SELL = 0.000625  # 0.0625% on sell-side premium (Budget 2023)

# ── Stamp Duty ───────────────────────────────────────────────────────────────
STAMP_DUTY_BUY = 0.00003     # 0.003% on buy-side turnover

# ── GST ──────────────────────────────────────────────────────────────────────
GST_RATE = 0.18              # 18% on (brokerage + ETC + SEBI)

# ── Clustering ───────────────────────────────────────────────────────────────
CLUSTER_WINDOW_MINUTES = 10  # max gap between legs of same strategy at entry
ADJUSTMENT_WINDOW_DAYS = 60  # max days back to look for open position

# ── Month maps ───────────────────────────────────────────────────────────────
MONTH_SHORT = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4,
    "MAY": 5, "JUN": 6, "JUL": 7, "AUG": 8,
    "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}
# NSE compact weekly expiry month codes
NSE_COMPACT_MONTHS = {
    "1": 1, "2": 2, "3": 3, "4": 4, "5": 5,
    "6": 6, "7": 7, "8": 8, "9": 9,
    "O": 10, "N": 11, "D": 12,
}

# ── Strategy labels ──────────────────────────────────────────────────────────
STRATEGY_NAMES = {
    "LONG_CALL":        "Long Call",
    "LONG_PUT":         "Long Put",
    "SHORT_CALL":       "Short Call",
    "SHORT_PUT":        "Short Put",
    "BULL_CALL_SPREAD":  "Bull Call Spread",
    "BEAR_CALL_SPREAD":  "Bear Call Spread",
    "BULL_PUT_SPREAD":   "Bull Put Spread",
    "BEAR_PUT_SPREAD":   "Bear Put Spread",
    "CALENDAR_SPREAD":   "Calendar Spread",
    "RATIO_CALENDAR":    "Ratio Calendar",
    "DIAGONAL_SPREAD":   "Diagonal Spread",
    "RATIO_SPREAD":      "Ratio Spread",
    "STRADDLE":          "Straddle",
    "STRANGLE":          "Strangle",
    "IRON_CONDOR":       "Iron Condor",
    "IRON_FLY":          "Iron Fly",
    "BUTTERFLY":         "Butterfly",
    "JADE_LIZARD":       "Jade Lizard",
    "COVERED_CALL":      "Covered Call",
    "COVERED_PUT":       "Covered Put",
    "CUSTOM":            "Custom Structure",
}

STRATEGY_CATEGORY = {
    "LONG_CALL":        "Directional",
    "LONG_PUT":         "Directional",
    "SHORT_CALL":       "Volatility",
    "SHORT_PUT":        "Volatility",
    "BULL_CALL_SPREAD":  "Directional",
    "BEAR_CALL_SPREAD":  "Directional",
    "BULL_PUT_SPREAD":   "Directional",
    "BEAR_PUT_SPREAD":   "Directional",
    "CALENDAR_SPREAD":   "Volatility",
    "RATIO_CALENDAR":    "Volatility",
    "DIAGONAL_SPREAD":   "Volatility",
    "RATIO_SPREAD":      "Directional",
    "STRADDLE":          "Volatility",
    "STRANGLE":          "Volatility",
    "IRON_CONDOR":       "Theta",
    "IRON_FLY":          "Theta",
    "BUTTERFLY":         "Theta",
    "JADE_LIZARD":       "Theta",
    "COVERED_CALL":      "Theta",
    "COVERED_PUT":       "Theta",
    "CUSTOM":            "Custom",
}

# ── Chart colours ─────────────────────────────────────────────────────────────
COLORS = {
    "primary":    "#00d4aa",
    "secondary":  "#7c3aed",
    "profit":     "#22c55e",
    "loss":       "#ef4444",
    "neutral":    "#64748b",
    "warning":    "#f59e0b",
    "bg":         "#0f172a",
    "card":       "#1e293b",
}
