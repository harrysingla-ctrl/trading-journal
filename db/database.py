"""
SQLite database layer.
Abstracts all DB operations — easy to swap for PostgreSQL later.
"""

import sqlite3
import json
import shutil
from pathlib import Path
from typing import List, Dict, Optional, Any
import pandas as pd

from config import DB_PATH


# ── Connection ────────────────────────────────────────────────────────────────

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ── Schema ────────────────────────────────────────────────────────────────────

def init_db() -> None:
    """Create all tables if they don't exist."""
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS contract_notes (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            filename         TEXT NOT NULL,
            trade_date       TEXT,
            client_id        TEXT,
            upload_datetime  TEXT DEFAULT (datetime('now')),
            processed        INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS raw_trades (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            contract_note_id  INTEGER REFERENCES contract_notes(id),
            trade_no          TEXT,
            order_no          TEXT,
            trade_datetime    TEXT NOT NULL,
            underlying        TEXT NOT NULL,
            expiry            TEXT,
            strike            REAL,
            option_type       TEXT,
            buy_sell          TEXT NOT NULL,
            quantity          INTEGER NOT NULL,
            price             REAL NOT NULL,
            gross_amount      REAL,
            brokerage         REAL,
            net_amount        REAL,
            exchange          TEXT DEFAULT 'NSE',
            is_assigned       INTEGER DEFAULT 0,
            created_at        TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS positions (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id      TEXT UNIQUE NOT NULL,
            underlying       TEXT NOT NULL,
            strategy_type    TEXT NOT NULL,
            strategy_label   TEXT,
            confidence       REAL,
            confidence_note  TEXT,
            status           TEXT DEFAULT 'OPEN',
            entry_datetime   TEXT,
            exit_datetime    TEXT,
            n_legs           INTEGER DEFAULT 0,
            n_adjustments    INTEGER DEFAULT 0,
            gross_pnl        REAL,
            net_pnl          REAL,
            total_charges    REAL,
            max_capital      REAL,
            notes            TEXT,
            leg_structure    TEXT,
            created_at       TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS position_legs (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id   TEXT REFERENCES positions(position_id),
            raw_trade_id  INTEGER REFERENCES raw_trades(id),
            leg_role      TEXT NOT NULL,
            sequence_no   INTEGER,
            created_at    TEXT DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_raw_trades_dt
            ON raw_trades(underlying, trade_datetime);
        CREATE INDEX IF NOT EXISTS idx_positions_entry
            ON positions(underlying, entry_datetime);
        CREATE INDEX IF NOT EXISTS idx_pos_legs_pos
            ON position_legs(position_id);

        CREATE TABLE IF NOT EXISTS app_settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT ''
        );
    """)
    conn.commit()
    conn.close()


# ── App Settings ──────────────────────────────────────────────────────────────

def get_setting(key: str, default: str = "") -> str:
    """Retrieve a persisted app setting by key."""
    conn = get_connection()
    row = conn.execute(
        "SELECT value FROM app_settings WHERE key=?", (key,)
    ).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    """Persist an app setting."""
    conn = get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
        (key, value),
    )
    conn.commit()
    conn.close()


# ── Contract Notes ────────────────────────────────────────────────────────────

def insert_contract_note(filename: str, trade_date: str, client_id: str = "") -> int:
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "INSERT INTO contract_notes (filename, trade_date, client_id) VALUES (?, ?, ?)",
        (filename, trade_date, client_id),
    )
    note_id = c.lastrowid
    conn.commit()
    conn.close()
    return note_id


def mark_contract_note_processed(note_id: int) -> None:
    conn = get_connection()
    conn.execute("UPDATE contract_notes SET processed=1 WHERE id=?", (note_id,))
    conn.commit()
    conn.close()


def contract_note_exists(filename: str, trade_date: str) -> bool:
    conn = get_connection()
    row = conn.execute(
        "SELECT id FROM contract_notes WHERE filename=? AND trade_date=?",
        (filename, trade_date),
    ).fetchone()
    conn.close()
    return row is not None


def get_contract_notes() -> pd.DataFrame:
    conn = get_connection()
    df = pd.read_sql(
        "SELECT * FROM contract_notes ORDER BY trade_date DESC", conn
    )
    conn.close()
    return df


# ── Raw Trades ────────────────────────────────────────────────────────────────

def insert_raw_trades(trades: List[Dict], note_id: int) -> List[int]:
    conn = get_connection()
    ids = []
    for t in trades:
        c = conn.cursor()
        c.execute("""
            INSERT INTO raw_trades
              (contract_note_id, trade_no, order_no, trade_datetime, underlying,
               expiry, strike, option_type, buy_sell, quantity, price,
               gross_amount, brokerage, net_amount, exchange)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            note_id,
            t.get("trade_no"),
            t.get("order_no"),
            t.get("trade_datetime"),
            t.get("underlying"),
            t.get("expiry"),
            t.get("strike"),
            t.get("option_type"),
            t.get("buy_sell"),
            t.get("quantity"),
            t.get("price"),
            t.get("gross_amount"),
            t.get("brokerage"),
            t.get("net_amount"),
            t.get("exchange", "NSE"),
        ))
        ids.append(c.lastrowid)
    conn.commit()
    conn.close()
    return ids


def get_all_raw_trades() -> pd.DataFrame:
    conn = get_connection()
    df = pd.read_sql(
        "SELECT * FROM raw_trades ORDER BY trade_datetime", conn
    )
    conn.close()
    return df


def mark_trades_assigned(trade_ids: List[int]) -> None:
    if not trade_ids:
        return
    conn = get_connection()
    placeholders = ",".join("?" * len(trade_ids))
    conn.execute(
        f"UPDATE raw_trades SET is_assigned=1 WHERE id IN ({placeholders})",
        trade_ids,
    )
    conn.commit()
    conn.close()


# ── Positions ─────────────────────────────────────────────────────────────────

def insert_position(pos: Dict) -> None:
    conn = get_connection()
    conn.execute("""
        INSERT OR REPLACE INTO positions
          (position_id, underlying, strategy_type, strategy_label,
           confidence, confidence_note, status,
           entry_datetime, exit_datetime,
           n_legs, n_adjustments, gross_pnl, net_pnl, total_charges,
           max_capital, notes, leg_structure)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        pos["position_id"],
        pos["underlying"],
        pos["strategy_type"],
        pos.get("strategy_label"),
        pos.get("confidence"),
        pos.get("confidence_note"),
        pos.get("status", "OPEN"),
        pos.get("entry_datetime"),
        pos.get("exit_datetime"),
        pos.get("n_legs", 0),
        pos.get("n_adjustments", 0),
        pos.get("gross_pnl"),
        pos.get("net_pnl"),
        pos.get("total_charges"),
        pos.get("max_capital"),
        pos.get("notes"),
        json.dumps(pos.get("leg_structure", [])),
    ))
    conn.commit()
    conn.close()


def insert_position_legs(legs: List[Dict]) -> None:
    if not legs:
        return
    conn = get_connection()
    for leg in legs:
        conn.execute("""
            INSERT INTO position_legs (position_id, raw_trade_id, leg_role, sequence_no)
            VALUES (?,?,?,?)
        """, (leg["position_id"], leg["raw_trade_id"], leg["leg_role"], leg["sequence_no"]))
    conn.commit()
    conn.close()


def get_all_positions() -> pd.DataFrame:
    conn = get_connection()
    df = pd.read_sql(
        "SELECT * FROM positions ORDER BY entry_datetime DESC", conn
    )
    conn.close()
    return df


def get_position_trades(position_id: str) -> pd.DataFrame:
    conn = get_connection()
    df = pd.read_sql("""
        SELECT rt.*, pl.leg_role, pl.sequence_no
        FROM raw_trades rt
        JOIN position_legs pl ON rt.id = pl.raw_trade_id
        WHERE pl.position_id = ?
        ORDER BY rt.trade_datetime
    """, conn, params=(position_id,))
    conn.close()
    return df


def get_positions_by_strategy() -> pd.DataFrame:
    conn = get_connection()
    df = pd.read_sql("""
        SELECT
            strategy_label,
            COUNT(*) AS total,
            SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) AS wins,
            SUM(net_pnl) AS total_pnl,
            AVG(net_pnl) AS avg_pnl,
            MAX(net_pnl) AS best,
            MIN(net_pnl) AS worst
        FROM positions
        WHERE status='CLOSED' AND net_pnl IS NOT NULL
        GROUP BY strategy_label
        ORDER BY total_pnl DESC
    """, conn)
    conn.close()
    return df


# ── Export / Import ───────────────────────────────────────────────────────────

def export_db_bytes() -> bytes:
    with open(DB_PATH, "rb") as f:
        return f.read()


def import_db_bytes(data: bytes) -> None:
    backup = DB_PATH + ".bak"
    if Path(DB_PATH).exists():
        shutil.copy2(DB_PATH, backup)
    with open(DB_PATH, "wb") as f:
        f.write(data)
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("SELECT name FROM sqlite_master LIMIT 1")
        conn.close()
    except Exception:
        if Path(backup).exists():
            shutil.copy2(backup, DB_PATH)
        raise ValueError("Uploaded file is not a valid SQLite database.")
