"""
Zerodha contract note PDF parser — v2.
Targets Annexure A (individual fills) rather than the WAP summary table.

Real Zerodha contract note structure:
  Page 1      : Header — client info, trade date
  Page 2      : WAP summary table  (SKIP — aggregated, not individual fills)
  Pages 3-4   : Charges, disclaimer
  Pages 5+    : Annexure A — row per fill, format:
                  OrderNo  OrderTime  TradeNo  TradeTime
                  SYMBOL / DD Month YYYY  B/S  Exchange
                  Qty  BrokeragePerUnit  NetRatePerUnit  ClosingRate  NetTotal
"""

import re
import io
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple

import pdfplumber

from config import MONTH_SHORT, NSE_COMPACT_MONTHS, EXCHANGE_MAP

# ── Annexure A line regex ─────────────────────────────────────────────────────
# Matches a single trade fill line extracted as text.
# Handles line-wrap artefact where "2026B NSE" appears (no space before B/S).
#
# Groups: order_no, order_time, trade_no, trade_time,
#         symbol_code, expiry_text, bs, exchange,
#         qty, brokerage_per_unit, net_rate
_FILL_LINE = re.compile(
    r'(\d{5,})\s+'                        # 1  order_no
    r'(\d{2}:\d{2}:\d{2})\s+'            # 2  order_time
    r'(\d{4,})\s+'                        # 3  trade_no
    r'(\d{2}:\d{2}:\d{2})\s+'            # 4  trade_time
    r'([A-Z][A-Z&\-]*(?:\d+)?(?:CE|PE))'      # 5  symbol code  e.g. NIFTY2660923500CE
    r'\s*/\s*'                            #    separator " / "
    r'(\d{1,2}\s+\w+\s+\d{4})\s*'        # 6  expiry      e.g. "09 June 2026"
    r'([BS])\s+'                          # 7  B or S
    r'(NSE|BSE|MCX)\s+'                  # 8  exchange
    r'(\d+)\s+'                           # 9  quantity
    r'([\d.]+)\s+'                        # 10 brokerage per unit
    r'([\d.]+)',                          # 11 net rate per unit  ← price
    re.IGNORECASE,
)

# ── Symbol compact format regex (NSE weekly) ──────────────────────────────────
# e.g.  NIFTY2660923500CE   SENSEX2660474700CE   NIFTY2661623800CE
_PAT_NSE_COMPACT = re.compile(
    r'^([A-Z][A-Z&\-]*)'     # underlying — letters & symbols only (NO digits)
    r'(\d{2})'               # YY  e.g. 26
    r'([1-9OND])'            # month code  1-9 = Jan-Sep, O/N/D = Oct-Dec
    r'(\d{2})'               # DD  e.g. 09
    r'(\d+)'                 # strike  e.g. 23500
    r'(CE|PE)$',
    re.IGNORECASE,
)

# ── Other symbol formats (fallback) ──────────────────────────────────────────
# "NIFTY 27 JUN 24 25000.00 CE"
_PAT_LONG = re.compile(
    r'^([A-Z&\-]+)\s+'
    r'(\d{1,2})\s+'
    r'(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s+'
    r'(\d{2,4})\s+'
    r'([\d,]+\.?\d*)\s+'
    r'(CE|PE)$',
    re.IGNORECASE,
)
# "NIFTY27JUN2425000CE"
_PAT_COMPACT_TEXT = re.compile(
    r'^([A-Z&\-]+)'
    r'(\d{1,2})'
    r'(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)'
    r'(\d{2,4})'
    r'(\d+\.?\d*)'
    r'(CE|PE)$',
    re.IGNORECASE,
)

# ── Header date / client patterns ─────────────────────────────────────────────
_DATE_PATS = [
    re.compile(r'Trade\s+Date\s*[:\-]?\s*(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})', re.IGNORECASE),
    re.compile(r'(\d{2}/\d{2}/\d{4})'),
]
_CLIENT_ID_PAT = re.compile(r'UCC\s*[:\-]?\s*([A-Z]{2}\d{4,})', re.IGNORECASE)


# ── Public API ────────────────────────────────────────────────────────────────

def parse_contract_note(
    file_bytes: bytes,
    filename: str = "",
    password: str = "",
) -> Dict[str, Any]:
    """
    Parse a Zerodha contract note PDF.

    Returns dict:
        trade_date      : "YYYY-MM-DD"
        client_id       : str
        trades          : List[Dict]
        parse_warnings  : List[str]
        needs_password  : bool  — True if PDF is locked and no/wrong password given
    """
    warnings: List[str] = []
    trades:   List[Dict] = []
    trade_date = None
    client_id  = ""

    open_kwargs: Dict[str, Any] = {}
    if password:
        open_kwargs["password"] = password

    try:
        with pdfplumber.open(io.BytesIO(file_bytes), **open_kwargs) as pdf:

            # ── Date + client from first page ─────────────────────────────
            first_text = pdf.pages[0].extract_text() or ""
            trade_date = _extract_trade_date(first_text)
            client_id  = _extract_client_id(first_text)

            if not trade_date:
                warnings.append(
                    "Could not detect trade date automatically. Defaulting to today."
                )
                trade_date = datetime.today().strftime("%Y-%m-%d")

            # ── Parse Annexure A fills ────────────────────────────────────
            # Strategy 1: text-line regex (most reliable for this format)
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                _parse_annexure_text(page_text, trade_date, trades, warnings)

            # Strategy 2: table extraction fallback (if text yielded nothing)
            if not trades:
                warnings.append(
                    "Text-line extraction yielded no trades; attempting table extraction."
                )
                for page in pdf.pages:
                    _parse_annexure_tables(page, trade_date, trades, warnings)

            if not trades:
                warnings.append(
                    "No trades extracted. "
                    "Ensure this is a Zerodha F&O contract note (Annexure A required)."
                )

    except Exception as exc:
        err = str(exc).lower()
        if any(k in err for k in ("password", "encrypt", "decrypt", "incorrect")):
            return {
                "trade_date":     None,
                "client_id":      "",
                "trades":         [],
                "parse_warnings": [
                    "🔒 PDF is password-protected. "
                    "Configure the correct password in the Upload page sidebar."
                ],
                "needs_password": True,
            }
        warnings.append(f"PDF error: {exc}")

    # De-duplicate on (trade_no, trade_datetime)
    seen: set = set()
    unique: List[Dict] = []
    for t in trades:
        key = (t.get("trade_no", ""), t["trade_datetime"])
        if key not in seen:
            seen.add(key)
            unique.append(t)

    return {
        "trade_date":     trade_date,
        "client_id":      client_id,
        "trades":         unique,
        "parse_warnings": warnings,
        "needs_password": False,
    }


# ── Strategy 1: Text-line regex ───────────────────────────────────────────────

def _parse_annexure_text(
    page_text: str,
    trade_date: str,
    trades: List[Dict],
    warnings: List[str],
) -> None:
    """
    Scan every line of extracted page text for the Annexure A fill pattern.
    Handles the PDF text-wrap artefact where a newline falls inside a row.
    """
    # Join continuation lines: if a line ends mid-row (no B/S yet) and
    # the next starts with B or S followed by NSE/BSE, merge them.
    lines = page_text.splitlines()
    merged: List[str] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        # Detect a line that looks like a continuation: starts with B/S + exchange
        if (i > 0
                and re.match(r'^[BS]\s+(NSE|BSE|MCX)', line, re.IGNORECASE)
                and merged):
            merged[-1] = merged[-1] + " " + line
        else:
            merged.append(line)
        i += 1

    for line in merged:
        m = _FILL_LINE.search(line)
        if not m:
            continue
        trade = _fill_from_match(m, trade_date, warnings)
        if trade:
            trades.append(trade)


def _fill_from_match(m: re.Match, trade_date: str, warnings: List[str]) -> Optional[Dict]:
    """Convert a regex match from _FILL_LINE into a trade dict."""
    try:
        order_no      = m.group(1)
        order_time    = m.group(2)
        trade_no      = m.group(3)
        trade_time    = m.group(4)
        symbol_code   = m.group(5)
        expiry_text   = m.group(6).strip()
        buy_sell      = m.group(7).upper()
        exchange      = m.group(8).upper()
        quantity      = int(m.group(9))
        # group(10) = brokerage per unit  (we recalculate anyway)
        price         = float(m.group(11))

        # Parse symbol to get underlying + strike + option_type
        sym = _parse_compact_symbol(symbol_code)
        if not sym:
            warnings.append(f"Unrecognised symbol: {symbol_code}")
            return None

        # Use the explicit expiry date from the description (more reliable)
        explicit_expiry = _parse_expiry_text(expiry_text)
        expiry = explicit_expiry or sym["expiry"]

        return {
            "trade_no":       trade_no,
            "order_no":       order_no,
            "trade_datetime": f"{trade_date} {trade_time}",
            "underlying":     sym["underlying"],
            "expiry":         expiry,
            "strike":         sym["strike"],
            "option_type":    sym["option_type"],
            "buy_sell":       buy_sell,
            "quantity":       quantity,
            "price":          price,
            "gross_amount":   round(quantity * price, 2),
            "brokerage":      20.0,   # recalculated accurately in pnl.py
            "net_amount":     round(quantity * price, 2),
            "exchange":       exchange,
        }
    except Exception as exc:
        warnings.append(f"Fill match error ({exc}): {m.group(0)[:80]}")
        return None


# ── Strategy 2: Table extraction fallback ────────────────────────────────────

def _parse_annexure_tables(
    page,
    trade_date: str,
    trades: List[Dict],
    warnings: List[str],
) -> None:
    """
    Try pdfplumber table extraction as fallback.
    Skips the WAP summary table (page 2) and targets Annexure A tables.
    """
    try:
        tables = page.extract_tables() or []
    except Exception:
        return

    for table in tables:
        if not table or len(table) < 2:
            continue

        # Skip WAP summary (identified by header containing WAP or Weighted)
        header_text = " ".join(str(c or "") for c in table[0]).upper()
        if "WAP" in header_text or "WEIGHTED" in header_text:
            continue

        # Only process if looks like Annexure A
        if not _looks_like_annexure_table(table):
            continue

        for row in table[1:]:
            trade = _parse_table_row(row, trade_date, warnings)
            if trade:
                trades.append(trade)


def _looks_like_annexure_table(table: list) -> bool:
    """Heuristic: does this table look like Annexure A fills?"""
    # Check header
    if table[0]:
        hdr = " ".join(str(c or "").upper() for c in table[0])
        if "TRADE NO" in hdr or "TRADE TIME" in hdr:
            return True

    # Check first few data rows for " / " and CE/PE
    for row in table[1:min(4, len(table))]:
        if row:
            txt = " ".join(str(c or "") for c in row)
            if " / " in txt and re.search(r'\b(CE|PE)\b', txt, re.IGNORECASE):
                return True
    return False


def _parse_table_row(
    row: list,
    trade_date: str,
    warnings: List[str],
) -> Optional[Dict]:
    """Parse a single Annexure A table row."""
    try:
        cells = [str(c).strip() if c else "" for c in row]
        full  = " ".join(cells)

        # Skip header rows
        if re.search(r'order\s*no|trade\s*no|contract\s*desc', full, re.IGNORECASE):
            return None

        # Must contain CE or PE
        if not re.search(r'\b(CE|PE)\b', full, re.IGNORECASE):
            return None

        # Find B/S
        bs_idx = None
        for i, cell in enumerate(cells):
            if cell.upper() in ("B", "S"):
                bs_idx = i
                break
        if bs_idx is None:
            return None

        buy_sell = cells[bs_idx].upper()

        # Trade time — last HH:MM:SS before B/S
        trade_time = "09:15:00"
        for cell in cells[:bs_idx]:
            if re.match(r'^\d{2}:\d{2}:\d{2}$', cell):
                trade_time = cell

        # Contract description — cell(s) before B/S containing CE/PE
        contract_desc = _find_contract_desc(cells[:bs_idx])
        if not contract_desc:
            return None

        sym, expiry = _parse_desc(contract_desc, warnings)
        if not sym:
            return None

        # Exchange: first "NSE"/"BSE"/"MCX" after B/S
        exchange = EXCHANGE_MAP.get(sym["underlying"], "NSE")
        for cell in cells[bs_idx + 1: bs_idx + 3]:
            if cell.upper() in ("NSE", "BSE", "MCX"):
                exchange = cell.upper()
                break

        # Numbers after B/S: qty, brokerage, net_rate
        numbers: List[float] = []
        for cell in cells[bs_idx + 1:]:
            n = _clean_number(cell)
            if n is not None and n > 0:
                numbers.append(n)

        if len(numbers) < 2:
            return None

        quantity = int(numbers[0])
        # Annexure A columns after qty: brokerage, net_rate, closing_rate, net_total
        price = numbers[2] if len(numbers) > 2 else numbers[1]

        # Order / trade numbers
        long_nums = [c for c in cells[:5] if re.match(r'^\d{5,}$', c)]
        order_no = long_nums[0] if long_nums else ""
        trade_no = long_nums[1] if len(long_nums) > 1 else ""

        return {
            "trade_no":       trade_no,
            "order_no":       order_no,
            "trade_datetime": f"{trade_date} {trade_time}",
            "underlying":     sym["underlying"],
            "expiry":         expiry,
            "strike":         sym["strike"],
            "option_type":    sym["option_type"],
            "buy_sell":       buy_sell,
            "quantity":       quantity,
            "price":          price,
            "gross_amount":   round(quantity * price, 2),
            "brokerage":      20.0,
            "net_amount":     round(quantity * price, 2),
            "exchange":       exchange,
        }

    except Exception as exc:
        warnings.append(f"Table row error ({exc}): {str(row)[:80]}")
        return None


def _find_contract_desc(cells: List[str]) -> Optional[str]:
    """Find the cell (or joined cells) containing the contract description."""
    # Try single cells first
    for cell in cells:
        if re.search(r'(CE|PE)', cell, re.IGNORECASE) and len(cell) > 5:
            return cell
    # Try joining adjacent cells
    for i in range(len(cells)):
        for j in range(i + 2, min(i + 6, len(cells) + 1)):
            joined = " ".join(cells[i:j])
            if re.search(r'(CE|PE)', joined, re.IGNORECASE) and len(joined) > 8:
                return joined
    return None


def _parse_desc(
    desc: str,
    warnings: List[str],
) -> Tuple[Optional[Dict], str]:
    """
    Parse a contract description like 'NIFTY2660923500CE / 09 June 2026'.
    Returns (symbol_dict, expiry_string).
    """
    parts = re.split(r'\s*/\s*', desc, maxsplit=1)
    symbol_code = parts[0].strip()
    expiry_text = parts[1].strip() if len(parts) > 1 else ""

    sym = parse_option_symbol(symbol_code)
    if not sym:
        warnings.append(f"Could not parse symbol: {symbol_code}")
        return None, ""

    explicit = _parse_expiry_text(expiry_text)
    expiry = explicit or sym["expiry"]
    return sym, expiry


# ── Symbol parsers ────────────────────────────────────────────────────────────

def parse_option_symbol(symbol: str) -> Optional[Dict]:
    """
    Public: parse any supported option symbol format.
    Returns dict with: underlying, expiry (YYYY-MM-DD), strike (float), option_type.
    """
    if not symbol or len(symbol) < 6:
        return None
    s = symbol.strip().upper().replace(",", "")

    # NSE compact  NIFTY2660923500CE
    result = _parse_compact_symbol(s)
    if result:
        return result

    # Long with spaces  "NIFTY 27 JUN 24 25000.00 CE"
    m = _PAT_LONG.match(s)
    if m:
        underlying, day, month, year, strike, opt = m.groups()
        return _sym(
            underlying,
            _make_expiry(int(day), MONTH_SHORT[month.upper()], _norm_year(year)),
            strike, opt,
        )

    # Compact text  "NIFTY27JUN2425000CE"
    m = _PAT_COMPACT_TEXT.match(s)
    if m:
        underlying, day, month, year, strike, opt = m.groups()
        return _sym(
            underlying,
            _make_expiry(int(day), MONTH_SHORT[month.upper()], _norm_year(year)),
            strike, opt,
        )

    return None


def _parse_compact_symbol(s: str) -> Optional[Dict]:
    """Parse NSE compact format: NIFTY2660923500CE."""
    s = s.strip().upper()
    m = _PAT_NSE_COMPACT.match(s)
    if not m:
        return None
    underlying, yy, mc, dd, strike, opt = m.groups()
    month_num = NSE_COMPACT_MONTHS.get(mc.upper())
    if not month_num:
        return None
    year = 2000 + int(yy)
    expiry = _make_expiry(int(dd), month_num, year)
    return _sym(underlying, expiry, strike, opt)


# ── Expiry / date helpers ─────────────────────────────────────────────────────

def _parse_expiry_text(text: str) -> Optional[str]:
    """Parse explicit expiry text like '09 June 2026' → '2026-06-09'."""
    text = text.strip()
    for fmt in ("%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def _extract_trade_date(text: str) -> Optional[str]:
    for pat in _DATE_PATS:
        m = pat.search(text)
        if m:
            return _norm_date(m.group(1))
    return None


def _norm_date(raw: str) -> Optional[str]:
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y"):
        try:
            return datetime.strptime(raw.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def _extract_client_id(text: str) -> str:
    m = _CLIENT_ID_PAT.search(text)
    return m.group(1) if m else ""


# ── Small utilities ───────────────────────────────────────────────────────────

def _sym(underlying: str, expiry: str, strike_str, opt_type: str) -> Dict:
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
