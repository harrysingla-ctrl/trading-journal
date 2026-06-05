"""
Zerodha contract note PDF parser.
Handles multiple versions of Zerodha's contract note format.
"""

import re
import io
from datetime import datetime
from typing import Optional, List, Dict, Any

import pdfplumber

from config import MONTH_SHORT, NSE_COMPACT_MONTHS, EXCHANGE_MAP

# ── Symbol regex patterns ─────────────────────────────────────────────────────

# "NIFTY 27 JUN 24 25000.00 CE"  or  "RELIANCE 27 JUN 2024 3000.00 CE"
_PAT_LONG = re.compile(
    r'^([A-Z&\-]+)\s+'
    r'(\d{1,2})\s+'
    r'(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s+'
    r'(\d{2,4})\s+'
    r'([\d,]+\.?\d*)\s+'
    r'(CE|PE)$',
    re.IGNORECASE,
)

# "NIFTY27JUN2425000CE"  (no spaces, month as text)
_PAT_COMPACT_TEXT = re.compile(
    r'^([A-Z&\-]+)'
    r'(\d{1,2})'
    r'(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)'
    r'(\d{2,4})'
    r'(\d+\.?\d*)'
    r'(CE|PE)$',
    re.IGNORECASE,
)

# NSE compact weekly "NIFTY2461225000CE"  (YY + single-char-month + DD + strike)
_PAT_NSE_COMPACT = re.compile(
    r'^([A-Z&\-]+)'
    r'(\d{2})'           # YY
    r'([1-9OND])'        # month code
    r'(\d{2})'           # DD
    r'(\d+)'             # strike
    r'(CE|PE)$',
    re.IGNORECASE,
)

# Date in header
_DATE_PATTERNS = [
    re.compile(r'Trade\s+Date\s*[:\-]?\s*(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})', re.IGNORECASE),
    re.compile(r'Contract\s+Note.*?(\d{2}/\d{2}/\d{4})', re.IGNORECASE | re.DOTALL),
    re.compile(r'Date\s*[:\-]\s*(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})', re.IGNORECASE),
    re.compile(r'(\d{2}/\d{2}/\d{4})'),
]

_CLIENT_ID_PAT = re.compile(r'Client\s+(?:ID|Code)\s*[:\-]?\s*([A-Z]{2}\d{4,})', re.IGNORECASE)


# ── Public API ────────────────────────────────────────────────────────────────

def parse_contract_note(file_bytes: bytes, filename: str = "") -> Dict[str, Any]:
    """
    Parse a Zerodha contract note PDF.

    Returns:
        trade_date   : "YYYY-MM-DD"
        client_id    : str
        trades       : List[Dict]
        parse_warnings: List[str]
    """
    warnings: List[str] = []
    trades: List[Dict] = []
    trade_date = None
    client_id = ""

    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            first_text = pdf.pages[0].extract_text() or ""
            trade_date = _extract_trade_date(first_text)
            client_id  = _extract_client_id(first_text)

            if not trade_date:
                warnings.append(
                    "Could not auto-detect trade date. Defaulting to today."
                )
                trade_date = datetime.today().strftime("%Y-%m-%d")

            for page_num, page in enumerate(pdf.pages):
                rows = _rows_from_page(page, page_num, warnings)
                for row in rows:
                    trade = _parse_row(row, trade_date, warnings)
                    if trade:
                        trades.append(trade)

    except Exception as exc:
        warnings.append(f"PDF read error: {exc}")

    # De-duplicate exact fills (same trade_no + datetime)
    seen = set()
    unique_trades = []
    for t in trades:
        key = (t.get("trade_no"), t["trade_datetime"], t["buy_sell"])
        if key not in seen:
            seen.add(key)
            unique_trades.append(t)

    return {
        "trade_date":     trade_date,
        "client_id":      client_id,
        "trades":         unique_trades,
        "parse_warnings": warnings,
    }


# ── Date / client extraction ──────────────────────────────────────────────────

def _extract_trade_date(text: str) -> Optional[str]:
    for pat in _DATE_PATTERNS:
        m = pat.search(text)
        if m:
            return _norm_date(m.group(1))
    return None


def _norm_date(raw: str) -> Optional[str]:
    raw = raw.strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def _extract_client_id(text: str) -> str:
    m = _CLIENT_ID_PAT.search(text)
    return m.group(1) if m else ""


# ── Row extraction from page ──────────────────────────────────────────────────

def _rows_from_page(page, page_num: int, warnings: List[str]) -> List[List[str]]:
    rows: List[List[str]] = []

    # Primary: table extraction
    try:
        tables = page.extract_tables()
        for table in (tables or []):
            for row in (table or []):
                if row and _is_trade_row(row):
                    rows.append([str(c).strip() if c else "" for c in row])
    except Exception as exc:
        warnings.append(f"Page {page_num + 1} table extraction failed: {exc}")

    # Fallback: text lines
    if not rows:
        try:
            text = page.extract_text() or ""
            for line in text.splitlines():
                parts = line.split()
                if len(parts) >= 7 and _is_trade_row_text(parts):
                    rows.append(parts)
        except Exception:
            pass

    return rows


def _is_trade_row(row: List) -> bool:
    """Heuristic: does this table row look like an option trade?"""
    if len(row) < 6:
        return False
    text = " ".join(str(c or "")).upper()
    has_bs  = bool(re.search(r'\b(B|S|BUY|SELL)\b', text))
    has_num = any(re.match(r'^\d', str(c or "")) for c in row)
    has_opt = bool(re.search(r'\b(CE|PE)\b', text))
    return has_bs and has_num and has_opt


def _is_trade_row_text(parts: List[str]) -> bool:
    has_time = any(re.match(r'^\d{2}:\d{2}:\d{2}$', p) for p in parts)
    has_bs   = any(p.upper() in ("B", "S", "BUY", "SELL") for p in parts)
    has_opt  = any(p.upper() in ("CE", "PE") for p in parts)
    return has_time and has_bs and has_opt


# ── Row parsing ───────────────────────────────────────────────────────────────

def _parse_row(row: List[str], trade_date: str, warnings: List[str]) -> Optional[Dict]:
    try:
        # Clean row
        row = [s.strip() for s in row if s and str(s).strip()]

        # Find B/S position
        bs_idx = None
        for i, cell in enumerate(row):
            if cell.upper() in ("B", "S", "BUY", "SELL"):
                bs_idx = i
                break
        if bs_idx is None:
            return None

        buy_sell = "B" if row[bs_idx].upper() in ("B", "BUY") else "S"

        # Find trade time
        trade_time = "00:00:00"
        for cell in row:
            if re.match(r'^\d{2}:\d{2}:\d{2}$', cell):
                trade_time = cell
                break

        trade_datetime = f"{trade_date} {trade_time}"

        # Find option symbol in the cells before B/S
        symbol_parsed = None
        # Try single cells first
        for i in range(max(0, bs_idx - 5), bs_idx):
            parsed = parse_option_symbol(row[i])
            if parsed:
                symbol_parsed = parsed
                break

        # Try joining adjacent cells (some PDFs split the description)
        if not symbol_parsed:
            for w in range(3, 8):
                for start in range(max(0, bs_idx - w - 1), bs_idx):
                    joined = " ".join(row[start : start + w])
                    parsed = parse_option_symbol(joined)
                    if parsed:
                        symbol_parsed = parsed
                        break
                if symbol_parsed:
                    break

        if not symbol_parsed:
            warnings.append(f"Symbol not found in row: {row[:bs_idx]}")
            return None

        # Quantity and price (first two valid numbers after B/S)
        numbers: List[float] = []
        for cell in row[bs_idx + 1:]:
            n = _clean_number(cell)
            if n is not None and n > 0:
                numbers.append(n)

        if len(numbers) < 2:
            warnings.append(f"Could not extract qty/price from: {row}")
            return None

        quantity = int(numbers[0])
        price    = numbers[1]

        # Trade / order numbers (numeric tokens before B/S)
        num_tokens = [c for c in row[:bs_idx] if re.match(r'^\d{5,}$', c)]
        trade_no = num_tokens[-2] if len(num_tokens) >= 2 else (num_tokens[-1] if num_tokens else "")
        order_no = num_tokens[-1] if len(num_tokens) >= 2 else ""

        underlying = symbol_parsed["underlying"]
        exchange   = EXCHANGE_MAP.get(underlying, "NSE")

        return {
            "trade_no":       trade_no,
            "order_no":       order_no,
            "trade_datetime": trade_datetime,
            "underlying":     underlying,
            "expiry":         symbol_parsed["expiry"],
            "strike":         symbol_parsed["strike"],
            "option_type":    symbol_parsed["option_type"],
            "buy_sell":       buy_sell,
            "quantity":       quantity,
            "price":          price,
            "gross_amount":   round(quantity * price, 2),
            "brokerage":      20.0,
            "net_amount":     round(quantity * price, 2),
            "exchange":       exchange,
        }

    except Exception as exc:
        warnings.append(f"Row parse error ({exc}): {row}")
        return None


# ── Symbol parser (public — used in clustering too) ───────────────────────────

def parse_option_symbol(symbol: str) -> Optional[Dict]:
    """
    Parse an option symbol string.
    Returns dict with: underlying, expiry (YYYY-MM-DD), strike (float), option_type.
    Returns None if not recognised.
    """
    if not symbol or len(symbol) < 6:
        return None

    s = symbol.strip().upper().replace(",", "")

    # Pattern 1: "NIFTY 27 JUN 24 25000.00 CE"
    m = _PAT_LONG.match(s)
    if m:
        underlying, day, month, year, strike, opt = m.groups()
        expiry = _make_expiry(int(day), MONTH_SHORT[month], _norm_year(year))
        return _sym(underlying, expiry, strike, opt)

    # Pattern 2: "NIFTY27JUN2425000CE"
    m = _PAT_COMPACT_TEXT.match(s)
    if m:
        underlying, day, month, year, strike, opt = m.groups()
        expiry = _make_expiry(int(day), MONTH_SHORT[month], _norm_year(year))
        return _sym(underlying, expiry, strike, opt)

    # Pattern 3: NSE compact "NIFTY2461225000CE"
    m = _PAT_NSE_COMPACT.match(s)
    if m:
        underlying, yy, mc, dd, strike, opt = m.groups()
        month_num = NSE_COMPACT_MONTHS.get(mc.upper())
        if not month_num:
            return None
        year = 2000 + int(yy)
        expiry = _make_expiry(int(dd), month_num, year)
        return _sym(underlying, expiry, strike, opt)

    return None


def _sym(underlying: str, expiry: str, strike_str: str, opt_type: str) -> Dict:
    return {
        "underlying":  underlying.upper(),
        "expiry":      expiry,
        "strike":      float(str(strike_str).replace(",", "")),
        "option_type": opt_type.upper(),
    }


def _make_expiry(day: int, month: int, year: int) -> str:
    try:
        return datetime(year, month, day).strftime("%Y-%m-%d")
    except ValueError:
        return f"{year:04d}-{month:02d}-{day:02d}"


def _norm_year(y: str) -> int:
    v = int(y)
    return 2000 + v if v < 100 else v


def _clean_number(s: str) -> Optional[float]:
    s = str(s).strip().replace(",", "").replace("(", "-").replace(")", "")
    try:
        return float(s)
    except ValueError:
        return None
