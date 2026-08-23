#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 forex_database.py  —  SQLite Trade History (Forex Edition)
================================================================================
ปรับจาก database.py:
  - เพิ่มคอลัมน์: lot_size, pips_pnl, spread_cost, swap_cost
  - ลบ: funding_bps (ใช้ swap แทน)
  - เปลี่ยน: qty → lot_size (ยังเก็บ qty ด้วยเพื่อ compatibility)
================================================================================
"""
from __future__ import annotations

import queue
import sqlite3
import threading
from datetime import datetime

DB_FILE = "forex_bot.db"
_db_lock = threading.Lock()
_write_queue: queue.Queue = queue.Queue()
_writer_thread: threading.Thread = None


# ── Async Write Queue ────────────────────────────────────────────────────────

def _writer_worker():
    while True:
        try:
            job = _write_queue.get(timeout=5)
        except queue.Empty:
            continue
        if job is None:
            break
        func, args, kwargs = job
        try:
            func(*args, **kwargs)
        except Exception:
            pass
        _write_queue.task_done()


def start_writer():
    global _writer_thread
    if _writer_thread and _writer_thread.is_alive():
        return
    _writer_thread = threading.Thread(target=_writer_worker, daemon=True,
                                      name="forex-db-writer")
    _writer_thread.start()


def stop_writer():
    _write_queue.put(None)
    if _writer_thread:
        _writer_thread.join(timeout=5)


def enqueue_write(func, *args, **kwargs):
    _write_queue.put((func, args, kwargs))


def get_conn(db_file: str = DB_FILE):
    conn = sqlite3.connect(db_file, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_file: str = DB_FILE):
    global DB_FILE
    DB_FILE = db_file
    with _db_lock:
        conn = get_conn(db_file)
        c = conn.cursor()
        # trades table (ปรับสำหรับ forex)
        c.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT,
                strategy    TEXT,
                pair        TEXT,
                side        TEXT,
                entry       REAL,
                exit        REAL,
                lot_size    REAL,
                pnl_usd     REAL,
                pips_pnl    REAL,
                spread_cost REAL,
                swap_cost   REAL,
                bars        INTEGER,
                reason      TEXT,
                regime      TEXT,
                session     TEXT,
                is_partial  INTEGER DEFAULT 0,
                created_at  TEXT DEFAULT (datetime('now'))
            )
        """)
        # daily summary
        c.execute("""
            CREATE TABLE IF NOT EXISTS daily_pnl (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                date     TEXT,
                strategy TEXT,
                pnl      REAL,
                trades   INTEGER,
                UNIQUE(date, strategy)
            )
        """)
        conn.commit()
        conn.close()
    start_writer()


def save_trade(trade: dict, is_partial: bool = False):
    with _db_lock:
        conn = get_conn(DB_FILE)
        conn.execute("""
            INSERT INTO trades
            (timestamp, strategy, pair, side, entry, exit,
             lot_size, pnl_usd, pips_pnl, spread_cost, swap_cost,
             bars, reason, regime, session, is_partial)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trade.get("exit_ts", ""),
            trade.get("strategy", ""),
            trade.get("pair", trade.get("symbol", "")),
            trade.get("side", ""),
            trade.get("entry", 0),
            trade.get("exit", 0),
            trade.get("lot_size", trade.get("qty", 0)),
            trade.get("pnl", 0),
            trade.get("pips_pnl", 0),
            trade.get("spread_cost", 0),
            trade.get("swap_cost", 0),
            trade.get("bars", 0),
            trade.get("reason", ""),
            trade.get("regime", ""),
            trade.get("session", ""),
            int(is_partial),
        ))
        conn.commit()
        conn.close()


def get_trades(strategy: str = None, pair: str = None,
               limit: int = 200) -> list:
    with _db_lock:
        conn = get_conn(DB_FILE)
        sql, params, wheres = "SELECT * FROM trades", [], []
        if strategy:
            wheres.append("strategy = ?");  params.append(strategy)
        if pair:
            wheres.append("pair = ?");      params.append(pair)
        if wheres:
            sql += " WHERE " + " AND ".join(wheres)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]


def get_stats(strategy: str = None) -> dict:
    trades = get_trades(strategy=strategy, limit=10000)
    full   = [t for t in trades if not t.get("is_partial")]
    wins   = [t for t in full  if t.get("pnl_usd", t.get("pnl", 0)) > 0]
    total_pnl  = sum(t.get("pnl_usd", t.get("pnl", 0)) for t in trades)
    total_cost = sum(t.get("spread_cost", 0) + t.get("swap_cost", 0) for t in trades)
    wr = len(wins) / len(full) * 100 if full else 0.0
    return {
        "total_trades": len(full),
        "wins": len(wins),
        "win_rate": round(wr, 1),
        "partial_trades": len([t for t in trades if t.get("is_partial")]),
        "total_pnl_usd": round(total_pnl, 2),
        "total_costs":   round(total_cost, 2),
    }


def update_daily_pnl(strategy: str, pnl: float, n_trades: int):
    today = datetime.utcnow().strftime("%Y-%m-%d")
    with _db_lock:
        conn = get_conn(DB_FILE)
        conn.execute("""
            INSERT INTO daily_pnl (date, strategy, pnl, trades)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(date, strategy) DO UPDATE SET
            pnl    = pnl    + excluded.pnl,
            trades = trades + excluded.trades
        """, (today, strategy, pnl, n_trades))
        conn.commit()
        conn.close()
