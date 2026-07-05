#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
================================================================================
 forex_live_bot_gold_cwider.py
 XAUUSD · Hybrid Trend-Pullback · Variant "C_wider" · DEMO forward-test
================================================================================
จุดประสงค์: deploy การตั้งค่าที่ผ่านการ validate ด้วย backtest ไปรันบนบัญชี
DEMO ของ Exness (ผ่าน MT5 terminal local) เพื่อวัด "ต้นทุนการส่งคำสั่งจริง" (spread ที่
จ่ายจริง + slippage จริง + commission จริง) — **ไม่ใช้เงินจริง**

[1] SIGNAL/EXIT — ตรงกับ backtest 100% (single source of truth)
    - Signal: forex_hybrid_strategy.HybridTrendPullback
        H1 EMA50/200 trend filter + ADX(14)>=22  →  M15 EMA20 pullback entry
    - Exit (variant "C_wider" จาก walk_forward_regime.VARIANTS):
        SL = 2.5 × ATR(M15)
        TP = 5.0 × ATR(M15)
        ไม่มี Partial-TP / ไม่มี Move-to-Breakeven / ไม่มี Trailing-Stop
        (ไม่ implement code path เหล่านี้เลยในไฟล์นี้ — ตัด ให้สอดคล้องกับ
         backtest C_wider ซึ่งปิดทุกอย่างนี้ด้วยเกณฑ์ trail_*=999)
    - ไม่มี daily rules (no reactive daily stop, no daily loss limit) — เทรดตลอดวัน

[2] DEMO ACCOUNT ONLY
    - เชื่อมต่อผ่าน MetaTrader5 package (local terminal IPC)
    - หลัง connect จะตรวจ account type ผ่าน connector.is_demo()
    - ถ้า cfg.dry_run=False (ค่า default — ส่ง order จริงบน demo) แล้วไม่สามารถ
      ยืนยันได้ว่าเป็นบัญชี DEMO → REFUSE TO START (sys.exit)

[3] EXECUTION-COST LOGGING (หัวใจของการทดสอบนี้)
    ทุก order (OPEN/CLOSE) → append เข้า fills_log_gold_cwider.csv:
      timestamp, action, side, requested_price, fill_price, bid, ask,
      spread_at_fill(=ask-bid), slippage(=fill-requested), lot, commission, comment
    + Daily summary line ตอนข้ามวัน (UTC): avg spread paid, max spread,
      #trades, day PnL

[4] SAFETY
    - Daily loss limit (ข้างบน)
    - Kill-switch file: touch STOP_GOLD_CWIDER  → บล็อกเข้าไม้ใหม่ (ไม้เดิม
      ยังถูกบริหารตามปกติ — SL/TP broker-managed + timeout check ยังทำงาน)
    - Reconnect/retry เมื่อ MT5 connection error
    - MT5-CALL TIMEOUT WATCHDOG (เพิ่มใหม่ — ดูหัวข้อ [6]):
      ทุกการเรียก MT5 ที่อยู่ใน hot-path ของ main loop (fetch candles) ถูกห่อ
      ด้วย ThreadPoolExecutor + timeout เพื่อป้องกัน mt5.copy_rates_from_pos()
      ค้างแบบไม่มีกำหนด (IPC hang กับ terminal) โดยไม่ throw exception —
      ถ้าค้างเกิน timeout จะถูกแปลงเป็น TimeoutError ซึ่งเข้า error-handling
      เดิม (consecutive_errors → reconnect) ได้ตามปกติ
    - Rotating log file (forex_gold_cwider.log)
    - Graceful shutdown (Ctrl+C) — save state ก่อนออก

[5] รันบน Windows VPS (วาง order จริงบนบัญชี demo — ไม่ใช่ dry-run):

    cd C:\Users\Administrator\Desktop
    python forex_live_bot_gold_cwider.py

    ดู log สด:        เปิดไฟล์ forex_gold_cwider.log ใน Notepad/editor
    หยุดเข้าไม้ใหม่:    สร้างไฟล์ STOP_GOLD_CWIDER บน Desktop
    หยุดบอททั้งหมด:    กด Ctrl-C ใน PowerShell window

    หมายเหตุ Windows VPS: ปิด RDP (disconnect) โดยไม่ Sign out —
    process จะรันต่อใน background ตราบใดที่ PowerShell window ยังเปิดอยู่

[6] MT5-CALL TIMEOUT (เพิ่มใหม่ — แก้บั๊ก "log ค้างตั้งแต่ startup, PID ยังอยู่"):
    เหตุผลของบั๊กเดิม:  mt5.copy_rates_from_pos() (เรียกผ่าน
    connector.fetch_ohlcv / fetch_ohlcv_paginated) เป็น blocking IPC call
    ที่ไม่มี timeout ในตัวของแพ็กเกจ MetaTrader5 — ถ้า IPC กับ terminal ค้าง
    (ไม่ throw exception, แค่ไม่ return) main loop ทั้งหมดจะบล็อกตลอดกาล
    โดยที่ `except Exception` ในเดิมจับไม่ได้เลย (ไม่มี exception ให้จับ)
    → consecutive_errors ไม่เพิ่ม → reconnect logic ไม่เคย trigger
    → process ยังอยู่ (PID โผล่) แต่ log ไม่ขยับอีกเลย

    Fix: ห่อ self.connector.fetch_ohlcv() / fetch_ohlcv_paginated() ด้วย
    self._call_with_timeout() (ThreadPoolExecutor.submit + future.result(timeout=..))
    ถ้าค้างเกิน timeout → raise TimeoutError แทน → main loop's except Exception
    จับได้ตามปกติ → reconnect logic (เดิมมีอยู่แล้ว) ทำงาน

    ข้อจำกัดที่ต้องรู้: thread ที่ค้างจริงจะไม่ถูกฆ่า (Python ทำไม่ได้ตรงๆ กับ
    C-extension call ที่ block) มันจะลอยเป็น zombie thread ต่อไป — ถ้า MT5
    IPC hang บ่อยมากในระยะสั้น ThreadPoolExecutor (3 workers) อาจตันในที่สุด
    นี่คือเหตุผลที่ต้องมี watchdog.ps1 (ภายนอก process) เป็นชั้นป้องกันสุดท้าย
    เพราะมันฆ่า process ทั้งตัวได้จริง ซึ่งคืน IPC handle ให้ Windows เคลียร์ให้

หมายเหตุ:
  - ENTRY ใช้ M15 (timeframe="15m"), TREND ใช้ H1 (resample ภายใน strategy)
  - SL/TP ส่งไปพร้อม order (broker-managed) — แม้บอทรีสตาร์ท MT5 ก็ยัง
    ปิดไม้ตาม SL/TP ให้เอง บอทจะ poll ตรวจว่า position ถูกปิดโดย broker
    หรือยัง (เพื่อ log fill + อัพเดท day-state)
  - การปิดด้วย Timeout (max_hold_bars) เป็น exit แบบเดียวที่บอทเป็นผู้สั่งปิดเอง
    (MT5 ไม่มี time-based exit ในตัว)
  - commission ใน fills_log เป็น "best-effort": ดึงจาก deal history ผ่าน
    get_position_deals() ถ้า MT5 deal history ไม่มีข้อมูลหรือ error
    → ใส่ 0.0 (จะระบุชัดเจนใน log ว่า "best-effort")
  - ไม่มีการปรับ parameter ใดๆ เพื่อ "เล็ง" ผลลัพธ์ — ใช้ค่าที่ validate แล้วเป๊ะๆ
================================================================================
"""
from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import logging
import logging.handlers
import math
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

try:
    import fcntl
    _HAS_FCNTL = True
except ImportError:
    import msvcrt
    _HAS_FCNTL = False

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forex_config import ForexConfig
from forex_indicators import add_indicators, build_data_dict
from forex_hybrid_strategy import HybridTrendPullback
from forex_executor import MT5Connector, ForexOrderExecutor, _cfg_has_credentials


class HybridM5Strategy(HybridTrendPullback):
    """ADX20_TP7 strategy adapted for M5 entry timeframe.
    H1_BARS=12: 12 × 5min = 1 H1 bar.
    EMA_M15=60: 60 × 5min = 300min ≡ EMA20 on M15 (same real-time lookback).
    """
    H1_BARS  = 12
    EMA_M15  = 60
    MIN_BARS = 200 * 12 + 100   # 2500


# =============================================================================
# CONSTANTS — single source of truth, mirrors validated backtest "C_wider"
# (walk_forward_regime.VARIANTS["C_wider"])
# =============================================================================
SYMBOL              = "XAUUSD"   # overridden at runtime by --symbol arg
TIMEFRAME           = "15m"     # entry TF = M15;  trend TF = H1 (resampled inside strategy)
RISK_PER_TRADE_PCT  = 0.30
SL_ATR              = 3.0       # variant C_wider: SL = 2.5 x ATR
TP_ATR              = 7.0       # variant C_wider: TP = 5.0 x ATR
ADX_MIN             = 22        # ADX threshold: 22=default, 20=adx20tp7
# variant C_wider ใช้ ForexConfig() ตรงๆ โดยไม่ override max_hold_bars เลย
# (walk_forward_regime._run_variant: cfg = ForexConfig(); ไม่มีการแก้ cfg.max_hold_bars
#  สำหรับ "C_wider" — backtest_forex.py ก็ไม่มี CLI override เช่นกัน)
# -> ค่าที่ validate จริงคือ ForexConfig.max_hold_bars default = 64 bars (M15 = 16 ชม.)
# pin ไว้ตรงนี้เป็น single source of truth กันค่า default ของ ForexConfig เปลี่ยนในอนาคต
MAX_HOLD_BARS       = 64         # = 64 x 15m = 16 ชั่วโมง (มิเรอร์ค่า backtest C_wider)
HISTORY_BARS        = 900       # >= strategy.MIN_BARS(850) + warm-up
VARIANT_TAG         = "cwider"  # overridden at runtime by --variant-tag arg

# ── MT5-call timeout watchdog (defense layer #1 — see [6] in module docstring) ──
# วินาทีที่ยอมให้ fetch_ohlcv/fetch_ohlcv_paginated ค้างได้สูงสุดก่อนถือว่า hang
# คำนวณจริงใน __init__ เป็น max(MT5_CALL_TIMEOUT_FLOOR_SEC, poll_interval_sec * 3)
MT5_CALL_TIMEOUT_FLOOR_SEC = 20.0
MT5_IO_WORKERS             = 3

# Magic numbers per symbol (base 555000 + index)
SYMBOL_MAGIC = {
    "XAUUSD": 555003,
    "EURUSD": 555001,
    "GBPUSD": 555002,
}
# Magic offset per variant — keeps each symbol+variant pair unique so MT5 can tell positions apart
VARIANT_MAGIC_OFFSET = {"cwider": 0, "tp7": 10, "tp8": 20, "mix_a": 30, "mix_b": 40, "adx20tp7": 50, "m5tp7": 60, "m5adx18": 70, "adx18tp7": 80}

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def _make_paths(symbol: str, variant: str = "cwider") -> tuple:
    """Return (STOP_FILE, STATE_FILE, LOG_FILE, FILLS_LOG_FILE, LOCK_FILE, MAGIC_NUMBER) for symbol+variant."""
    slug = symbol.lower()
    v    = variant.lower()
    base_magic = SYMBOL_MAGIC.get(symbol, 555000 + abs(hash(symbol)) % 999)
    magic = base_magic + VARIANT_MAGIC_OFFSET.get(v, abs(hash(v)) % 100 + 50)
    if v == "cwider":
        # backward-compat: keep original filenames so existing state/log files are not orphaned
        return (
            os.path.join(_BASE_DIR, f"STOP_{symbol.upper()}"),
            os.path.join(_BASE_DIR, f"{slug}_cwider_state.json"),
            os.path.join(_BASE_DIR, f"forex_{slug}_cwider.log"),
            os.path.join(_BASE_DIR, f"fills_log_{slug}_cwider.csv"),
            os.path.join(_BASE_DIR, f"{slug}_cwider.lock"),
            magic,
        )
    return (
        os.path.join(_BASE_DIR, f"STOP_{symbol.upper()}_{v.upper()}"),
        os.path.join(_BASE_DIR, f"{slug}_{v}_state.json"),
        os.path.join(_BASE_DIR, f"forex_{slug}_{v}.log"),
        os.path.join(_BASE_DIR, f"fills_log_{slug}_{v}.csv"),
        os.path.join(_BASE_DIR, f"{slug}_{v}.lock"),
        magic,
    )

# Initialised with defaults; overwritten in main() after argparse
STOP_FILE, STATE_FILE, LOG_FILE, FILLS_LOG_FILE, LOCK_FILE, MAGIC_NUMBER = _make_paths(SYMBOL)



# =============================================================================
# POSITION (single position at a time — single symbol, single strategy)
# =============================================================================
@dataclass
class Position:
    side:       str    # "long" | "short"
    entry:      float
    sl:         float
    tp:         float
    lot:        float
    trade_id:   str
    entry_atr:  float
    entry_ts:   str
    bars:       int = 0
    entry_comm: float = 0.0


# =============================================================================
# CANDLE BUFFER (M15) — เหมือน forex_live_bot.CandleBuffer แบบย่อ
# =============================================================================
class CandleBuffer:
    def __init__(self, maxlen: int = HISTORY_BARS, min_bars: int = HybridTrendPullback.MIN_BARS):
        self.maxlen   = maxlen
        self._min_bars = min_bars
        self._rows: list = []   # [[ts_ms, o, h, l, c, v], ...]
        self.d: Optional[dict] = None
        self.last_ts: int = 0

    def push(self, candles: list) -> int:
        added = 0
        for c in candles:
            ts = int(c[0])
            if ts > self.last_ts:
                self._rows.append(c)
                self.last_ts = ts
                added += 1
        if added:
            if len(self._rows) > self.maxlen:
                self._rows = self._rows[-self.maxlen:]
            self._rebuild()
        return added

    def _rebuild(self):
        if len(self._rows) < 30:
            self.d = None
            return
        df = pd.DataFrame(self._rows,
                           columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df = add_indicators(df)
        self.d = build_data_dict(df)

    @property
    def ready(self) -> bool:
        return self.d is not None and len(self._rows) >= self._min_bars

    def __len__(self) -> int:
        return len(self._rows)


# =============================================================================
# BOT
# =============================================================================
class GoldCWiderBot:
    def __init__(self, cfg: ForexConfig,
                 max_positions: int = 1,
                 strategy_cls: type = HybridTrendPullback):
        self.cfg = cfg
        self.max_positions = max(1, int(max_positions))
        self.log = self._setup_logging()

        self.strategy = strategy_cls()
        self.strategy.sl_atr = SL_ATR
        self.strategy.tp_atr = TP_ATR
        self.strategy.ADX_MIN = ADX_MIN
        # ไม่มี trail/partial/breakeven ใน bot นี้ — ค่าเหล่านี้ไม่ถูกอ้างถึงเลย

        self.connector: Optional[MT5Connector] = None
        self.executor:  Optional[ForexOrderExecutor] = None
        self.bsym = SYMBOL   # broker symbol (resolved after connect)

        self._lock_fd: Optional[int] = None  # flock fd ของ single-instance lock (live mode เท่านั้น)

        self.buf = CandleBuffer(HISTORY_BARS, min_bars=self.strategy.MIN_BARS)
        self.positions: list[Position] = []

        # ── Day-level state (tracking only, no blocking rules) ──
        self.current_day:      Optional[str] = None
        self.day_realized_pnl: float = 0.0
        self.day_equity_start: float = cfg.total_capital_usd
        self.day_trade_count:  int   = 0
        self.day_spreads:      list  = []

        # ── MT5-call timeout watchdog (defense layer #1) ──
        # ทุก MT5 IPC call ที่อยู่บน hot-path ของ main loop รันผ่าน executor นี้
        # แทนการเรียกตรง เพื่อกัน mt5.copy_rates_from_pos() ค้างไม่มีกำหนด
        self._io_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=MT5_IO_WORKERS, thread_name_prefix=f"mt5-io-{VARIANT_TAG}")
        self._mt5_call_timeout_sec = max(
            MT5_CALL_TIMEOUT_FLOOR_SEC, cfg.poll_interval_sec * 3)

    # ─────────────────────────────────────────────────────────────────────
    # Logging
    # ─────────────────────────────────────────────────────────────────────
    def _setup_logging(self) -> logging.Logger:
        fmt = "%(asctime)s [%(levelname)s] %(message)s"
        handlers: list = [logging.StreamHandler(sys.stdout)]
        try:
            handlers.append(logging.handlers.RotatingFileHandler(
                LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"))
        except Exception:
            pass
        logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers, force=True)
        return logging.getLogger("GoldCWiderBot")

    # ─────────────────────────────────────────────────────────────────────
    # MT5-call timeout wrapper (defense layer #1 — see [6] in module docstring)
    # ─────────────────────────────────────────────────────────────────────
    def _call_with_timeout(self, func, timeout: float, *args, **kwargs):
        """รัน MT5 call ใน thread แยก + timeout.

        ถ้าค้างเกิน `timeout` วินาที จะ raise TimeoutError แทนที่จะปล่อยให้
        main loop บล็อกตลอดกาล (ซึ่งคือสาเหตุของบั๊กเดิม: log ค้างตั้งแต่
        startup ทั้งที่ process/PID ยังอยู่). TimeoutError ที่ raise ออกไป
        จะถูก main loop's `except Exception` จับได้ตามปกติ → consecutive_errors
        เพิ่ม → reconnect logic (เดิมมีอยู่แล้ว) ทำงาน

        หมายเหตุ: thread ที่ค้างจริงจะไม่ถูกฆ่า (Python ทำไม่ได้กับ blocking
        C-extension call) — มันจะลอยเป็น zombie thread ต่อไป ถ้า hang เกิดถี่
        มากในช่วงเวลาสั้นๆ worker pool อาจตันได้ในที่สุด นี่คือเหตุผลที่ต้องมี
        watchdog.ps1 (นอก process) เป็นชั้นป้องกันสุดท้ายที่ฆ่า process ทั้งตัว
        เพื่อคืน IPC handle ให้ Windows เคลียร์ให้จริงๆ
        """
        fut = self._io_executor.submit(func, *args, **kwargs)
        try:
            return fut.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            fname = getattr(func, "__name__", str(func))
            self.log.error(
                f"[MT5-TIMEOUT] {fname} ค้างเกิน {timeout:.0f}s "
                f"— ถือเป็น error เพื่อ trigger reconnect logic")
            raise TimeoutError(f"{fname} timed out after {timeout:.0f}s")

    # ─────────────────────────────────────────────────────────────────────
    # Single-instance lock (กัน orphan/duplicate process แย่ง MT5 connection)
    # ─────────────────────────────────────────────────────────────────────
    def _acquire_single_instance_lock(self):
        """flock-based lock — ปล่อยอัตโนมัติเมื่อ process ตาย (kill -9 ก็ยังปล่อย)
        ไม่เหมือน PID file ที่ค้างเป็น stale lock ได้. ใช้เฉพาะ live mode
        (dry-run ไม่ต่อ MT5 เลย จึงชนกันไม่ได้)."""
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            if _HAS_FCNTL:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                if os.fstat(fd).st_size < 1:
                    os.write(fd, b"\0")
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            os.close(fd)
            self.log.error(
                f"REFUSING TO START: Another {SYMBOL.lower()}_cwider instance is already running "
                f"(lock held on {os.path.basename(LOCK_FILE)}). "
                f"ฆ่า process เดิมก่อน หรือถ้าแน่ใจว่าไม่มี process ค้าง ให้ลบไฟล์ lock นี้ทิ้ง.")
            sys.exit(1)
        # เก็บ fd ไว้ตลอดอายุ process — ห้าม close (ไม่งั้น lock จะหลุดทันที)
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, str(os.getpid()).encode())
        except OSError:
            pass
        self._lock_fd = fd
        self.log.info(f"[LOCK] acquired single-instance lock: {os.path.basename(LOCK_FILE)} (pid={os.getpid()})")

    def _release_single_instance_lock(self):
        if self._lock_fd is not None:
            try:
                if _HAS_FCNTL:
                    fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                else:
                    os.lseek(self._lock_fd, 0, os.SEEK_SET)
                    msvcrt.locking(self._lock_fd, msvcrt.LK_UNLCK, 1)
                os.close(self._lock_fd)
            except OSError:
                pass
            try:
                os.remove(LOCK_FILE)
            except OSError:
                pass
            self._lock_fd = None

    # ─────────────────────────────────────────────────────────────────────
    # Connect + DEMO verification + banner
    # ─────────────────────────────────────────────────────────────────────
    def connect(self):
        self.connector = MT5Connector(self.cfg, self.log)
        ok = self.connector.connect()
        if not ok:
            self.log.error("ไม่สามารถ initialize MT5 terminal — ออกจากโปรแกรม")
            sys.exit(1)
        self.executor = ForexOrderExecutor(self.connector, self.cfg, self.log)

        # ── resolve symbol (gold ชื่อต่างกันแต่ละโบรก) ──
        self.bsym = self.connector.resolve_symbol(SYMBOL)
        if self.bsym != SYMBOL:
            self.cfg.add_symbol_alias(SYMBOL, self.bsym)
            self.log.info(f"[SYMBOL] {SYMBOL} -> {self.bsym}")

        if self.cfg.total_capital_usd <= 0:
            self.cfg.total_capital_usd = self.connector.get_balance()

        self._print_banner_and_verify_demo()

    def _print_banner_and_verify_demo(self):
        info     = self.connector.get_account_info()
        is_demo  = self.connector.is_demo()
        acct_type = info.get("type", "UNKNOWN") if info else "UNKNOWN (dry-run / no connection)"
        broker    = info.get("broker", "?")
        server    = info.get("server", "?")
        login     = info.get("login", "?")
        balance   = float(info.get("balance", self.cfg.total_capital_usd) or self.cfg.total_capital_usd)
        equity    = float(info.get("equity", balance) or balance)
        currency  = info.get("currency", "USD")

        demo_tag = "DEMO ✅" if is_demo else "*** NOT CONFIRMED DEMO *** ❌"

        lines = [
            "=" * 78,
            f" {SYMBOL} C_WIDER LIVE BOT  —  DEMO FORWARD-TEST (execution-cost measurement)",
            "=" * 78,
            f"  ACCOUNT TYPE  : {acct_type}   -> {demo_tag}",
            f"  Broker/Server : {broker} / {server}   Login: {login}",
            f"  Balance       : {balance:,.2f} {currency}   Equity: {equity:,.2f} {currency}",
            f"  Symbol        : {SYMBOL}  ->  resolved broker symbol: {self.bsym}",
            f"  Strategy      : {self.strategy.name}",
            f"  Entry TF      : {TIMEFRAME.upper()}    Trend TF: H1 (resampled, EMA{self.strategy.EMA_H1_FAST}/{self.strategy.EMA_H1_SLOW}, ADX>={self.strategy.ADX_MIN})",
            f"  Exit config   : SL={self.strategy.sl_atr}xATR  TP={self.strategy.tp_atr}xATR"
            f"   | Partial-TP=OFF  Move-to-BE=OFF  Trailing=OFF",
            f"  Max hold      : {self.cfg.max_hold_bars} bars"
            f" (= {self.cfg.max_hold_bars * self.cfg.timeframe_minutes / 60:g} hours)",
            f"  Risk/trade    : {self.cfg.risk_per_trade_pct}%   Magic: {self.cfg.magic_number}"
            f"   Max-positions: {self.max_positions}",
            f"  Daily rules   : OFF (no daily loss limit, no reactive stop)",
            f"  Order mode    : {'DRY-RUN (paper, NO real orders)' if self.cfg.dry_run else 'LIVE — places real orders on the account above'}",
            f"  Kill-switch   : touch {os.path.basename(STOP_FILE)}  (blocks NEW entries only)",
            f"  MT5-call timeout : {self._mt5_call_timeout_sec:.0f}s  (fetch hang -> forced reconnect)",
            f"  Fills log     : {os.path.basename(FILLS_LOG_FILE)}",
            "=" * 78,
        ]
        for l in lines:
            self.log.info(l)

        if not self.cfg.dry_run and not is_demo:
            if not self.cfg.allow_real:
                self.log.error(
                    "REFUSING TO START: ไม่สามารถยืนยันได้ว่าบัญชีนี้เป็น DEMO "
                    "(account info type != ACCOUNT_TRADE_MODE_DEMO). "
                    "ตรวจสอบว่า MT5 terminal login ด้วยบัญชี DEMO เท่านั้น "
                    "หรือรันด้วย --dry-run เพื่อทดสอบโค้ดแบบ paper "
                    "หรือถ้าต้องการรันบนบัญชีเงินจริงโดยตั้งใจ ต้องส่ง --allow-real "
                    "มาด้วยอย่างชัดเจน")
                sys.exit(1)
            else:
                self.log.warning(
                    "*** REAL-MONEY MODE CONFIRMED (--allow-real) *** "
                    f"บัญชีนี้ไม่ใช่ demo — คำสั่งซื้อขายทุกคำสั่งจะใช้เงินจริง "
                    f"บน login={login}  balance={balance:,.2f} {currency}")

        expected_login = os.environ.get("MT5_LOGIN")
        if not self.cfg.dry_run and expected_login and str(login) != str(expected_login):
            self.log.error(
                f"REFUSING TO START: connected login={login} != MT5_LOGIN={expected_login} (env) "
                f"— MT5 terminal login ผิดบัญชี")
            sys.exit(1)

    # ─────────────────────────────────────────────────────────────────────
    # Warm-up
    # ─────────────────────────────────────────────────────────────────────
    def boot_buffer(self):
        self.log.info(f"Warm-up: ดึง {HISTORY_BARS} แท่ง M15 ของ {self.bsym} ...")
        if self.cfg.dry_run:
            self.log.warning("[DRY-RUN] ไม่มี connection จริง — ไม่สามารถสร้าง buffer จากข้อมูลจริงได้")
            return
        for attempt in range(1, 4):
            candles = self._fetch_closed_candles(limit=HISTORY_BARS)
            self.log.info(f"  attempt {attempt}/3: got {len(candles)} bars")
            if candles:
                self.buf.push(candles)
            if len(self.buf) >= HISTORY_BARS:
                break
            if attempt < 3:
                time.sleep(5)
        self.log.info(f"  -> {len(self.buf)} bars  ready={self.buf.ready}")
        if len(self.buf) < HISTORY_BARS:
            self.log.warning(f"  -> only {len(self.buf)}/{HISTORY_BARS} bars after retries — will fill naturally")

    def _fetch_closed_candles(self, limit: int = 5) -> list:
        """ดึงแท่งที่ปิดแล้วจาก MT5 — ห่อด้วย timeout (defense layer #1)
        กัน mt5.copy_rates_from_pos() ค้างไม่มีกำหนดแล้วบล็อก main loop ตาย
        (ดูรายละเอียดเหตุผลใน module docstring หัวข้อ [6])."""
        if limit + 1 > 999:
            candles = self._call_with_timeout(
                self.connector.fetch_ohlcv_paginated, self._mt5_call_timeout_sec,
                self.bsym, TIMEFRAME, limit + 1)
        else:
            candles = self._call_with_timeout(
                self.connector.fetch_ohlcv, self._mt5_call_timeout_sec,
                self.bsym, TIMEFRAME, limit + 1)
        if not candles:
            return []
        now_ms = int(time.time() * 1000)
        tf_ms  = self.cfg.timeframe_ms
        return [c for c in candles if c[0] + tf_ms + 3000 <= now_ms]

    # ─────────────────────────────────────────────────────────────────────
    # fills_log.csv
    # ─────────────────────────────────────────────────────────────────────
    def _log_fill(self, action: str, side: str, requested: float, fill: float,
                   bid: float, ask: float, lot: float, commission: float,
                   comment: str):
        spread   = (ask - bid) if (bid > 0 and ask > 0) else 0.0
        slippage = fill - requested
        write_header = not os.path.exists(FILLS_LOG_FILE)
        try:
            with open(FILLS_LOG_FILE, "a", newline="", encoding="utf-8") as f:
                cols = ["timestamp", "action", "side", "requested_price", "fill_price",
                        "bid", "ask", "spread_at_fill", "slippage", "lot",
                        "commission", "comment"]
                w = csv.DictWriter(f, fieldnames=cols)
                if write_header:
                    w.writeheader()
                w.writerow({
                    "timestamp":       datetime.now(timezone.utc).isoformat(),
                    "action":          action,
                    "side":            side,
                    "requested_price": f"{requested:.5f}",
                    "fill_price":      f"{fill:.5f}",
                    "bid":             f"{bid:.5f}",
                    "ask":             f"{ask:.5f}",
                    "spread_at_fill":  f"{spread:.5f}",
                    "slippage":        f"{slippage:.5f}",
                    "lot":             f"{lot:.2f}",
                    "commission":      f"{commission:.4f}",
                    "comment":         comment,
                })
        except Exception as exc:
            self.log.error(f"_log_fill: {exc}")

        self.day_spreads.append(spread)
        return spread, slippage

    # ─────────────────────────────────────────────────────────────────────
    # Day-level rules (mirrors BacktestEngine._update_day_state)
    # ─────────────────────────────────────────────────────────────────────
    def _rollover_day(self, ts: str, equity: float):
        day = ts[:10]
        if day == self.current_day:
            return
        if self.current_day is not None:
            self._print_daily_summary()
        self.current_day      = day
        self.day_realized_pnl = 0.0
        self.day_equity_start = equity
        self.day_trade_count  = 0
        self.day_spreads      = []
        self.log.info(f"[DAY] เริ่มวันใหม่ {day} (UTC)  equity_start={equity:,.2f}")

    def _print_daily_summary(self):
        n = self.day_trade_count
        avg_spread = (sum(self.day_spreads) / len(self.day_spreads)) if self.day_spreads else 0.0
        max_spread = max(self.day_spreads) if self.day_spreads else 0.0
        self.log.info(
            f"[DAY-SUMMARY] {self.current_day}  #trades={n}  "
            f"avg_spread_paid={avg_spread:.4f}  max_spread={max_spread:.4f}  "
            f"day_PnL={self.day_realized_pnl:+.2f}")

    def _update_day_state(self, net_pnl: float):
        self.day_realized_pnl += net_pnl

    # ─────────────────────────────────────────────────────────────────────
    # State persistence
    # ─────────────────────────────────────────────────────────────────────
    def save_state(self):
        data = {
            "positions": [asdict(p) for p in self.positions],
            "day_state": {
                "current_day":      self.current_day,
                "day_realized_pnl": self.day_realized_pnl,
                "day_equity_start": self.day_equity_start,
                "day_trade_count":  self.day_trade_count,
                "day_spreads":      self.day_spreads,
            },
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as exc:
            self.log.error(f"save_state: {exc}")

    def load_state(self):
        if not os.path.exists(STATE_FILE):
            return
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if "positions" in data:
                self.positions = [Position(**p) for p in data.get("positions") or []]
            elif data.get("position"):
                # backward-compat: migrate old single-position state
                self.positions = [Position(**data["position"])]
            if self.positions:
                self.log.info(f"[STATE] restored {len(self.positions)} open position(s): "
                              + " | ".join(str(p) for p in self.positions))
            ds = data.get("day_state", {})
            self.current_day      = ds.get("current_day")
            self.day_realized_pnl = ds.get("day_realized_pnl", 0.0)
            self.day_equity_start = ds.get("day_equity_start", self.cfg.total_capital_usd)
            self.day_trade_count  = ds.get("day_trade_count", 0)
            self.day_spreads      = ds.get("day_spreads", [])
            self.log.info(f"[STATE] loaded {STATE_FILE}")
        except Exception as exc:
            self.log.warning(f"load_state: {exc}")

    # ─────────────────────────────────────────────────────────────────────
    # Recover an orphaned position after a restart (best-effort)
    # ─────────────────────────────────────────────────────────────────────
    def recover_position(self):
        if self.cfg.dry_run:
            return
        positions = self.connector.get_open_positions()
        mine = [p for p in positions
                if p.get("symbol") == self.bsym
                and int(p.get("magic", 0) or 0) == self.cfg.magic_number]
        broker_ids = {str(p.get("id", "")) for p in mine}

        # 1) drop tracked positions that no longer exist at broker
        #    (closed by SL/TP/manual while the bot was down — can't reconstruct fill)
        still_tracked = []
        for pos in self.positions:
            if pos.trade_id in broker_ids:
                still_tracked.append(pos)
            else:
                self.log.warning(f"[RECOVER] tracked position {pos.trade_id} ไม่มีที่ broker "
                                 f"— เคลียร์ทิ้ง (อาจถูกปิดไปแล้วตอนบอทไม่ทำงาน)")
                self.executor.release_order_key(self.bsym, pos.side, pos.lot)
        self.positions = still_tracked

        # 2) adopt any broker positions we are not yet tracking
        tracked_ids = {p.trade_id for p in self.positions}
        for p in mine:
            pid = str(p.get("id", ""))
            if pid in tracked_ids:
                continue
            side = "long" if str(p.get("type", "")).endswith("BUY") else "short"
            new_pos = Position(
                side=side,
                entry=float(p.get("openPrice", 0) or 0),
                sl=float(p.get("stopLoss", 0) or 0),
                tp=float(p.get("takeProfit", 0) or 0),
                lot=float(p.get("volume", 0) or 0),
                trade_id=pid,
                entry_atr=0.0,
                entry_ts=datetime.now(timezone.utc).isoformat(),
                bars=0,
                entry_comm=0.0,
            )
            self.positions.append(new_pos)
            self.log.warning(f"[RECOVER] adopted existing broker position: {new_pos}")

    # ─────────────────────────────────────────────────────────────────────
    # PnL helper
    # ─────────────────────────────────────────────────────────────────────
    def _net_pnl(self, pos: Position, fill_px: float, close_commission: float) -> float:
        pip_size  = self.cfg.get_pip_size(self.bsym)
        pip_value = self.connector.get_pip_value_live(self.bsym)
        direction = 1 if pos.side == "long" else -1
        gross_pips = (fill_px - pos.entry) / pip_size * direction
        gross_usd  = gross_pips * pip_value * pos.lot
        return gross_usd - pos.entry_comm - close_commission

    # ─────────────────────────────────────────────────────────────────────
    # Open a new position
    # ─────────────────────────────────────────────────────────────────────
    def _open_position(self, sig, d: dict, i: int):
        long_ = sig.action == "BUY"
        atr = float(d["atr"][i])
        if math.isnan(atr) or atr <= 0:
            return

        bid, ask = self.connector.get_current_price(self.bsym)
        if bid <= 0 or ask <= 0:
            self.log.warning("  [SKIP] bid/ask ไม่ valid — ข้ามสัญญาณนี้")
            return

        req_px = ask if long_ else bid
        sl = req_px - atr * self.strategy.sl_atr if long_ else req_px + atr * self.strategy.sl_atr
        tp = req_px + atr * self.strategy.tp_atr if long_ else req_px - atr * self.strategy.tp_atr

        pip_size  = self.cfg.get_pip_size(self.bsym)
        pip_value = self.connector.get_pip_value_live(self.bsym)
        sl_pips   = abs(req_px - sl) / pip_size
        if sl_pips <= 0 or pip_value <= 0:
            return

        equity    = self.connector.get_equity()
        risk_cash = equity * self.cfg.risk_per_trade_pct / 100.0
        lot = max(self.cfg.min_lot, round(risk_cash / (sl_pips * pip_value), 2))
        lot = min(lot, self.cfg.max_lot)

        actual_risk_pct = (sl_pips * pip_value * lot) / equity * 100.0 if equity > 0 else float("inf")
        if actual_risk_pct > self.cfg.max_risk_per_trade_pct:
            self.log.warning(
                f"  [MIN-LOT] risk={actual_risk_pct:.2f}% > target={self.cfg.risk_per_trade_pct}% "
                f"(min_lot={self.cfg.min_lot} forced, SL wide) — trading anyway")

        side    = "long" if long_ else "short"
        comment = f"{VARIANT_TAG[:6].upper()}-{sig.action}"[:16]

        self.log.info(
            f"  [SIGNAL] {sig.action} {self.bsym}  reason={sig.reason}  "
            f"atr={atr:.4f}  entry~={req_px:.2f}  SL={sl:.2f}  TP={tp:.2f}  lot={lot}")

        result = self.executor.open_position(self.bsym, side, lot, sl, tp, comment)
        if not result:
            self.log.error("  [OPEN FAILED] executor returned empty result")
            return

        fill_px    = float(result.get("fill_price", req_px) or req_px)
        commission = float(result.get("commission", 0.0) or 0.0)
        bid2, ask2 = self.connector.get_current_price(self.bsym)
        if bid2 <= 0 or ask2 <= 0:
            bid2, ask2 = bid, ask

        spread, slippage = self._log_fill(
            action="OPEN", side=side, requested=req_px, fill=fill_px,
            bid=bid2, ask=ask2, lot=lot, commission=commission, comment=comment)

        self.positions.append(Position(
            side=side, entry=fill_px, sl=sl, tp=tp, lot=lot,
            trade_id=str(result.get("trade_id", "")),
            entry_atr=atr, entry_ts=str(d["ts"][i]), bars=0,
            entry_comm=commission))

        self.log.info(
            f"  [OPEN] {side.upper()} {self.bsym} lot={lot}  fill={fill_px:.2f}  "
            f"SL={sl:.2f} TP={tp:.2f}  spread={spread:.4f}  slippage={slippage:+.4f}  "
            f"commission={commission:.4f}")

    # ─────────────────────────────────────────────────────────────────────
    # Close due to Timeout (the only bot-initiated close for C_wider)
    # ─────────────────────────────────────────────────────────────────────
    def _close_timeout(self, pos: Position):
        bid, ask = self.connector.get_current_price(self.bsym)
        req_px = bid if pos.side == "long" else ask
        if req_px <= 0:
            req_px = pos.entry

        result = self.executor.close_position_market(
            self.bsym, pos.side, pos.lot, pos.trade_id, comment=f"{VARIANT_TAG[:6].upper()}-Timeout")
        if not result:
            self.log.error("  [CLOSE-TIMEOUT FAILED] executor returned empty result — will retry next poll")
            return
        fill_px    = float(result.get("fill_price", req_px) or req_px)
        commission = float(result.get("commission", 0.0) or 0.0)

        bid2, ask2 = self.connector.get_current_price(self.bsym)
        if bid2 <= 0 or ask2 <= 0:
            bid2, ask2 = bid, ask

        net = self._net_pnl(pos, fill_px, commission)
        spread, slippage = self._log_fill(
            action="CLOSE", side=pos.side, requested=req_px, fill=fill_px,
            bid=bid2, ask=ask2, lot=pos.lot, commission=commission,
            comment=f"{VARIANT_TAG[:6].upper()}-Timeout pnl={net:+.2f}")

        self.log.info(
            f"  [CLOSE-TIMEOUT] {pos.side.upper()} {self.bsym} lot={pos.lot}  "
            f"fill={fill_px:.2f}  net_pnl={net:+.2f}  "
            f"spread={spread:.4f}  slippage={slippage:+.4f}  commission={commission:.4f}")

        self.day_trade_count += 1
        self._update_day_state(net)
        if pos in self.positions:
            self.positions.remove(pos)
        self.executor.release_order_key(self.bsym, pos.side, pos.lot)

    # ─────────────────────────────────────────────────────────────────────
    # Detect broker-side close (SL or TP hit by MT5)
    # ─────────────────────────────────────────────────────────────────────
    def _check_broker_close(self):
        if not self.positions or self.cfg.dry_run:
            return
        positions = self.connector.get_open_positions()
        open_ids = {str(p.get("id", "")) for p in positions}
        for pos in list(self.positions):
            if pos.trade_id not in open_ids:
                self._log_broker_close(pos)   # ปิดไปแล้วฝั่ง broker → log + remove

    def _log_broker_close(self, pos: Position):
        # ปิดไปแล้ว (SL/TP/manual จากฝั่ง broker) — best-effort หา fill/commission จริง
        bid, ask = self.connector.get_current_price(self.bsym)
        deals = self.connector.get_position_deals(pos.trade_id, lookback_minutes=30)
        close_deals = [dl for dl in deals if str(dl.get("entryType", "")).endswith("OUT")]

        fill_px    = None
        commission = 0.0
        reason     = "SL/TP (broker)"
        if close_deals:
            last = close_deals[-1]
            try:
                fill_px = float(last.get("price", 0) or 0) or None
            except (TypeError, ValueError):
                fill_px = None
            commission = sum(float(dl.get("commission", 0) or 0) for dl in close_deals)
            r = str(last.get("reason", "")).upper()
            if "SL" in r:
                reason = "SL (broker)"
            elif "TP" in r:
                reason = "TP (broker)"

        if fill_px is None:
            # fallback: ใช้ bid/ask ปัจจุบัน + เดา reason จากระยะห่าง SL/TP
            fill_px = bid if pos.side == "long" else ask
            if fill_px <= 0:
                fill_px = pos.tp  # last resort
            if abs(fill_px - pos.sl) < abs(fill_px - pos.tp):
                reason = "SL (broker, est.)"
            else:
                reason = "TP (broker, est.)"

        requested = pos.tp if abs(fill_px - pos.tp) < abs(fill_px - pos.sl) else pos.sl
        bid2, ask2 = (bid, ask) if (bid > 0 and ask > 0) else (fill_px, fill_px)

        net = self._net_pnl(pos, fill_px, commission)
        spread, slippage = self._log_fill(
            action="CLOSE", side=pos.side, requested=requested, fill=fill_px,
            bid=bid2, ask=ask2, lot=pos.lot, commission=commission,
            comment=f"{VARIANT_TAG[:6].upper()}-{reason} pnl={net:+.2f}")

        self.log.info(
            f"  [CLOSE-{reason}] {pos.side.upper()} {self.bsym} lot={pos.lot}  "
            f"fill={fill_px:.2f}  net_pnl={net:+.2f}  "
            f"spread={spread:.4f}  slippage={slippage:+.4f}  commission={commission:.4f}")

        self.day_trade_count += 1
        self._update_day_state(net)
        if pos in self.positions:
            self.positions.remove(pos)
        self.executor.release_order_key(self.bsym, pos.side, pos.lot)

    # ─────────────────────────────────────────────────────────────────────
    # Process a newly-closed M15 bar
    # ─────────────────────────────────────────────────────────────────────
    def process_bar(self):
        d = self.buf.d
        i = len(d["c"]) - 1
        ts = str(d["ts"][i])

        equity = self.connector.get_equity()
        self._rollover_day(ts, equity)

        # ── 1) detect broker-side SL/TP close ──
        self._check_broker_close()

        # ── 2) manage open positions: timeout only (SL/TP are broker-managed) ──
        for pos in list(self.positions):
            pos.bars += 1
            if pos.bars >= self.cfg.max_hold_bars:
                self._close_timeout(pos)

        # ── 3) kill switch ──
        kill = os.path.exists(STOP_FILE)
        if kill:
            self.log.info(f"[KILL-SWITCH] {os.path.basename(STOP_FILE)} exists — "
                           f"blocking new entries (existing position still managed)")

        # ── 4) new signal — only if room for another position & not blocked ──
        can_open = (len(self.positions) < self.max_positions
                    and not kill)
        if can_open:
            sig = self.strategy.signal(d, i)
            if sig.action in ("BUY", "SELL"):
                self._open_position(sig, d, i)

        self.save_state()

    # ─────────────────────────────────────────────────────────────────────
    # Status line
    # ─────────────────────────────────────────────────────────────────────
    def log_status(self):
        equity = self.connector.get_equity() if not self.cfg.dry_run else self.cfg.total_capital_usd
        pos_tag = "-"
        if self.positions:
            pos_tag = " | ".join(
                f"{p.side.upper()} lot={p.lot} entry={p.entry:.2f} SL={p.sl:.2f} "
                f"TP={p.tp:.2f} bars={p.bars}"
                for p in self.positions)
        self.log.info(
            f"== STATUS ==  equity={equity:,.2f}  bars={len(self.buf)}  "
            f"position=[{pos_tag}]  day={self.current_day}  "
            f"day_pnl={self.day_realized_pnl:+.2f}  "
            f"trades_today={self.day_trade_count}")

    # ─────────────────────────────────────────────────────────────────────
    # Main loop
    # ─────────────────────────────────────────────────────────────────────
    def run(self):
        if not self.cfg.dry_run:
            self._acquire_single_instance_lock()
        self.connect()
        self.boot_buffer()
        self.load_state()
        self.recover_position()

        if not self.buf.ready:
            self.log.warning(
                f"Buffer ยังไม่พร้อม ({len(self.buf)}/{self.strategy.MIN_BARS} bars) "
                f"— จะรอข้อมูลเพิ่มจนกว่าจะ ready")

        tf_ms = self.cfg.timeframe_ms
        last_status_ms = 0
        consecutive_errors = 0

        self.log.info(f"เริ่ม main loop — poll ทุก {self.cfg.poll_interval_sec}s "
                       f"(MT5-call timeout={self._mt5_call_timeout_sec:.0f}s) ...")

        while True:
            try:
                now_ms = int(time.time() * 1000)

                candles = self._fetch_closed_candles(limit=5)
                added = self.buf.push(candles) if candles else 0
                consecutive_errors = 0

                if added and self.buf.ready:
                    self.process_bar()

                if now_ms - last_status_ms >= tf_ms:
                    self.log_status()
                    last_status_ms = now_ms

                time.sleep(self.cfg.poll_interval_sec)

            except KeyboardInterrupt:
                self.log.info("Shutting down (Ctrl+C) ...")
                self.save_state()
                self.log_status()
                try:
                    self.connector.disconnect()
                except Exception:
                    pass
                self._release_single_instance_lock()
                self._io_executor.shutdown(wait=False, cancel_futures=True)
                break

            except Exception as exc:
                consecutive_errors += 1
                self.log.error(f"Loop error ({consecutive_errors}): {exc}", exc_info=True)
                # ── reconnect/retry ──
                if consecutive_errors >= 3:
                    self.log.warning("กำลังพยายาม reconnect MT5 ...")
                    try:
                        self.connector.disconnect()
                    except Exception:
                        pass
                    time.sleep(10)
                    try:
                        self.connect()
                        consecutive_errors = 0
                    except SystemExit:
                        raise
                    except Exception as exc2:
                        self.log.error(f"reconnect ล้มเหลว: {exc2}")
                        # ไม่ reset consecutive_errors ที่นี่ — ให้ backoff sleep ด้านล่างทำงานปกติ
                        # reset จะเกิดหลัง reconnect สำเร็จ (try block ด้านบน)
                time.sleep(min(60, 10 * consecutive_errors))


# =============================================================================
# MAIN
# =============================================================================
def main():
    global SYMBOL, VARIANT_TAG, SL_ATR, TP_ATR, ADX_MIN, RISK_PER_TRADE_PCT
    global TIMEFRAME, MAX_HOLD_BARS, HISTORY_BARS
    global STOP_FILE, STATE_FILE, LOG_FILE, FILLS_LOG_FILE, LOCK_FILE, MAGIC_NUMBER
    ap = argparse.ArgumentParser(
        description="CWiderBot — Hybrid Trend-Pullback (C_wider) — DEMO forward-test",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
ตัวอย่างการรัน:
  python forex_live_bot_gold_cwider.py                        # XAUUSD (default)
  python forex_live_bot_gold_cwider.py --symbol EURUSD        # EURUSD instance
  python forex_live_bot_gold_cwider.py --symbol GBPUSD        # GBPUSD instance
  python forex_live_bot_gold_cwider.py --dry-run              # paper mode (ไม่ต่อ MT5)
  touch STOP_XAUUSD   # หยุดเข้าไม้ใหม่ของ XAUUSD instance
""")
    ap.add_argument("--symbol", type=str, default="XAUUSD",
                    help="Symbol ที่ต้องการเทรด (default XAUUSD). แต่ละ symbol รัน instance แยก.")
    ap.add_argument("--variant-tag", type=str, default="cwider",
                    help="ชื่อ variant สำหรับ exit params (default 'cwider'). ใช้แยกไฟล์ state/log/fills "
                         "และ magic number ระหว่าง instance ที่รัน symbol เดียวกันแต่ exit ต่างกัน. "
                         "ตัวอย่าง: --variant-tag tp7 → STOP_XAUUSD_TP7, xauusd_tp7_state.json, magic=555013")
    ap.add_argument("--sl-atr", type=float, default=SL_ATR,
                    help=f"SL = N × ATR (default {SL_ATR} = C_wider). TP7 variant ใช้ 3.0")
    ap.add_argument("--tp-atr", type=float, default=TP_ATR,
                    help=f"TP = N × ATR (default {TP_ATR} = C_wider). TP7 variant ใช้ 7.0")
    ap.add_argument("--adx-min", type=float, default=ADX_MIN,
                    help=f"ADX threshold สำหรับ H1 trend filter (default {ADX_MIN}). adx20tp7 ใช้ 20")
    ap.add_argument("--max-positions", type=int, default=1,
                    help="จำนวน position พร้อมกันสูงสุดต่อ instance (default 1 = พฤติกรรมเดิม). "
                         "แต่ละ position independent (own entry/SL/TP/lot/bars).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Paper mode สำหรับทดสอบโค้ด (ไม่เชื่อมต่อ MT5, ไม่ส่ง order จริง). "
                         "Default: ปิด (ส่ง order จริงบน DEMO ตามที่ขอ)")
    ap.add_argument("--timeframe", type=str, default="15m",
                    help="Entry timeframe: '15m' (default), '5m' (M5), '1m' (M1). "
                         "MaxHold auto-set to keep 16h real time: 15m→64, 5m→192, 1m→960 bars.")
    ap.add_argument("--risk", type=float, default=0.0,
                    help="Risk per trade %% (0 = ใช้ค่า default ของ variant). "
                         "M5 แนะนำ 0.15 (ครึ่งนึงของ M15 เพราะ IS MaxDD สูงกว่า).")
    ap.add_argument("--poll-interval", type=int, default=30,
                    help="วินาทีต่อรอบ poll (default 30)")
    ap.add_argument("--capital", type=float, default=0.0,
                    help="Capital USD เริ่มต้น (0 = ดึงจาก MT5 balance)")
    ap.add_argument("--allow-real", action="store_true",
                    help="อนุญาตให้รันบนบัญชีที่ไม่ใช่ DEMO (เช่น Real Cent account). "
                         "ต้องระบุอย่างชัดเจน มิฉะนั้นระบบปฏิเสธการ start เสมอ "
                         "เมื่อ MT5 แจ้งว่า account type != DEMO")
    args = ap.parse_args()

    # Re-initialise all symbol/variant-dependent globals based on CLI args
    SYMBOL      = args.symbol.upper()
    VARIANT_TAG = args.variant_tag.lower()
    SL_ATR      = args.sl_atr
    TP_ATR      = args.tp_atr
    ADX_MIN     = int(args.adx_min)
    STOP_FILE, STATE_FILE, LOG_FILE, FILLS_LOG_FILE, LOCK_FILE, MAGIC_NUMBER = _make_paths(SYMBOL, VARIANT_TAG)

    # Timeframe-driven config
    tf = args.timeframe.lower()
    if tf == "5m":
        TIMEFRAME     = "5m"
        MAX_HOLD_BARS = 192    # 192 × 5min = 960min = 16h
        HISTORY_BARS  = 2700   # >= HybridM5Strategy.MIN_BARS (2500) + buffer
        strategy_cls  = HybridM5Strategy
    elif tf == "1m":
        sys.exit(
            "[REFUSE TO START] --timeframe 1m is an unvalidated placeholder "
            "(reuses HybridTrendPullback, tuned/backtested for M15 — never "
            "backtested on M1). Do not run this live until a dedicated, "
            "validated M1 strategy class exists.")
    else:
        TIMEFRAME     = "15m"
        MAX_HOLD_BARS = 64     # 64 × 15min = 960min = 16h
        HISTORY_BARS  = 900
        strategy_cls  = HybridTrendPullback

    if args.risk > 0:
        RISK_PER_TRADE_PCT = args.risk

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    cfg = ForexConfig()
    cfg.symbols            = [SYMBOL]
    cfg.timeframe          = TIMEFRAME
    cfg.risk_per_trade_pct = RISK_PER_TRADE_PCT  # may have been updated by --risk above
    cfg.magic_number       = MAGIC_NUMBER
    cfg.history_bars       = HISTORY_BARS
    cfg.max_hold_bars      = MAX_HOLD_BARS  # pin ตรงๆ — ไม่พึ่ง ForexConfig default
    cfg.poll_interval_sec  = args.poll_interval
    cfg.dry_run            = args.dry_run
    cfg.allow_real         = args.allow_real
    cfg.state_file         = STATE_FILE
    cfg.log_file           = LOG_FILE
    slug = SYMBOL.lower()
    cfg.trades_csv         = os.path.join(_BASE_DIR, f"forex_{slug}_{VARIANT_TAG}_trades.csv")
    cfg.db_file            = os.path.join(_BASE_DIR, f"forex_{slug}_{VARIANT_TAG}.db")

    if args.capital > 0:
        cfg.total_capital_usd = args.capital

    if not cfg.dry_run and not _cfg_has_credentials(cfg):
        print("[ERROR] แพ็กเกจ MetaTrader5 ใช้งานไม่ได้ — ต้องรันบน Windows ที่ติดตั้ง "
              "MetaTrader5 python package และเปิด MT5 terminal ไว้ "
              "หรือใช้ --dry-run เพื่อทดสอบโค้ดแบบ paper")
        sys.exit(1)

    GoldCWiderBot(cfg,
                  max_positions=args.max_positions,
                  strategy_cls=strategy_cls).run()


if __name__ == "__main__":
    main()
