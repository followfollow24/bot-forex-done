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

[2] DEMO BY DEFAULT — REAL ACCOUNT REQUIRES EXPLICIT --allow-real
    - เชื่อมต่อผ่าน MetaTrader5 package (local terminal IPC)
    - หลัง connect จะตรวจ account type ผ่าน connector.is_demo()
    - ถ้า cfg.dry_run=False (ค่า default) และบัญชีไม่ใช่ DEMO:
        - ไม่มี --allow-real  → REFUSE TO START (sys.exit) — พฤติกรรม default
          ยังคงปลอดภัยเหมือนเดิม ป้องกันการรันบัญชีจริงโดยไม่ตั้งใจ
        - มี --allow-real     → อนุญาตให้รันบัญชีจริงได้ (ต้องตั้งใจส่ง flag
          นี้มาอย่างชัดเจนเท่านั้น ดู argparse --allow-real ด้านล่าง)

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
import tempfile
import time
import urllib.parse
import urllib.request
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
from forex_hybrid_strategy import HybridTrendPullback, FreshHybridTrendPullback
from gold_regime_live_strategy import RegimeFilteredHybridLive, FreshRegimeFilteredHybridLive
from gold_daily_breakout_strategy import GoldDailyDonchianBreakout
from btc_donchian_breakout_strategy import BTCH1DonchianBreakout
from amd_sweep_tpo_strategy import AMDSweepTPO
from ict_tools_strategies import ToolAMD, ToolLQSweep, ToolTPOProfile
from gold_momentum_rsi_strategy import GoldMomentumRSI
# NOTE: GoldManualExitBot is imported lazily inside main() (not here at module
# top-level) because gold_manual_exit_bot.py itself does
# `from forex_live_bot_gold_cwider import GoldCWiderBot` -- importing it here
# would be a circular import that fails since GoldCWiderBot isn't defined yet
# at this point in the module.
from forex_executor import MT5Connector, ForexOrderExecutor, _cfg_has_credentials


# =============================================================================
# CONSTANTS — single source of truth, mirrors validated backtest "C_wider"
# (walk_forward_regime.VARIANTS["C_wider"])
# =============================================================================
SYMBOL              = "XAUUSD"   # overridden at runtime by --symbol arg
TIMEFRAME           = "15m"     # entry TF = M15;  trend TF = H1 (resampled inside strategy)
RISK_PER_TRADE_PCT  = 0.30
SL_ATR              = 3.0       # variant C_wider: SL = 3.0 x ATR
TP_ATR              = 7.0       # variant C_wider: TP = 7.0 x ATR
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
    # [FIX 2026-07-31] "XAUUSD" alone NEVER matches at runtime: main() always
    # does SYMBOL=args.symbol.upper(), and every gold bot is launched with
    # --symbol XAUUSDc (broker cent-suffix), so SYMBOL is always "XAUUSDC"
    # (with the C). This is the EXACT bug already caught and fixed for BTC/ETH
    # below on 2026-07-11 (see BTCUSDC/ETHUSDC comments) but never applied to
    # gold -- every gold variant (gold_h1_manual, gold_daily_breakout, etc.)
    # has been silently falling through to the hash-based fallback magic
    # number the whole time, which is not stable across restarts (Python's
    # str hash is randomized per-process by default) -- a bot that crashes
    # and restarts while holding an open position would get a NEW magic
    # number and fail to recognize its own position. Kept "XAUUSD" (no C)
    # too since preflight_gold_regime.py/preflight_gold_manual.py index this
    # dict directly with that key.
    "XAUUSDC": 555003,
    "EURUSD": 555001,
    "GBPUSD": 555002,
    # BTCUSDc uses a distinct 666000-series base (not 555xxx) so a magic-number
    # collision with any gold variant is impossible even if offsets ever overlap.
    # KEY IS UPPERCASE ("BTCUSDC") — main() always does SYMBOL=args.symbol.upper(),
    # so a mixed-case key here ("BTCUSDc") would NEVER match at runtime and this
    # dict lookup would silently fall through to the hash-based fallback magic
    # number instead (caught live 2026-07-11: real bot startup banner showed
    # Magic=555754, not 666xxx -- both bots killed within seconds of learning
    # this before any signal could fire, no trades were placed). Fixed by using
    # the same canonical-symbol + alias-copy pattern gold already uses (XAUUSD
    # canonical -> XAUUSDc alias via add_symbol_alias in connect()): SYMBOL_MAGIC
    # key AND the pip_size override below both use "BTCUSDC" (post-upper()
    # canonical); resolve_symbol()'s prefix-match branch is case-insensitive
    # (compares s.upper()) so it still correctly resolves to the real broker
    # symbol "BTCUSDc" and add_symbol_alias() copies pip_size across automatically.
    "BTCUSDC": 666000,
    # ETHUSDc, added 2026-07-29. Same uppercase-key rule as BTCUSDC above: this
    # MUST be "ETHUSDC" (post-.upper()) or the lookup silently falls through to
    # the hash fallback and the bot runs on an unintended magic number -- the
    # exact failure caught live on 2026-07-11. 667000-series keeps it clear of
    # both the gold 555xxx block and BTC's 666xxx block.
    "ETHUSDC": 667000,
}
# Magic offset per variant — keeps each symbol+variant pair unique so MT5 can tell positions apart
VARIANT_MAGIC_OFFSET = {
    "cwider": 0, "tp7": 10, "tp8": 20, "mix_a": 30, "mix_b": 40,
    "adx20tp7": 50, "m5tp7": 60, "m5adx18": 70, "adx18tp7": 80,
    # BTC-HF walk-forward-validated variants (see project_btc_hf_edge.md):
    #   btc_cons = ADX15/SL4/TP12 (conservative, 27-window PF>1 15/18)
    #   btc_aggr = ADX12/SL2.5/TP7.5 (aggressive, 27-window PF>1 17/18)
    "btc_cons": 0, "btc_aggr": 10,
    # Gold regime filter (see gold_regime_live_strategy.py): frozen ADX>22 +
    # ADX-rising + EMA-gap>1.2xATR(H1) on top of the same SL3/TP7 M15 entry.
    # Real-engine validated: 2,848 trades/13yr, PF=1.29, MaxDD=10.3%, WF-A
    # yearly PF>1 12/14yr, half-split H1=1.16->H2=1.37 (no overfit signature).
    # Offset 100 is well clear of every other gold offset (0-80) on purpose.
    "regime22": 100,
    # adx20_manual (see gold_manual_exit_bot.py): same adx20tp7 entry/SL,
    # TP effectively disabled (--tp-atr set very high via CLI), user closes
    # manually; bot sends Telegram alerts every +1xATR profit milestone.
    # NO BACKTEST EXISTS for this variant (manual-close outcomes cannot be
    # simulated) -- live-only forward test, risk 0.30% per explicit agreement.
    "adx20_manual": 110,
    # ── H1 manual-exit variants, added 2026-07-29 ────────────────────────────
    # Same HybridTrendPullback signal, moved from M15 to H1 entries, TP disabled
    # so the user closes by hand. The move is driven by cost/ATR: on gold M15
    # the real $2.85 spread+slippage is ~45% of a bar's ATR and only 21% of
    # entries ever reach +1R, versus ~24% and 68-73% on H1. On crypto the ratio
    # is 5-10%, which is why BTC/ETH carry the same signal far better than gold.
    # Offsets 120-140, clear of every existing gold (0-110) and BTC (0-10) slot.
    "btc_h1_manual":  120,
    "eth_h1_manual":  130,
    "gold_h1_manual": 140,
    # gold_daily_breakout (see gold_daily_breakout_strategy.py): Donchian
    # channel breakout, --timeframe 1d only. Structurally different signal
    # from every HybridTrendPullback-family variant above (momentum/breakout,
    # not pullback-to-EMA) -- OOS-validated PF=3.22/DD=2.9%, full-history
    # PF=2.44/DD=3.1%, 12/14 years profitable. Offset 150, clear of every
    # existing slot.
    "gold_daily_breakout": 150,
    # btc_h1_breakout (see btc_donchian_breakout_strategy.py): Donchian
    # breakout on H1, run ALONGSIDE btc_h1_manual (not replacing it) as a
    # second, structurally different BTC signal. OOS PF=1.17/Sharpe=0.87,
    # corr=0.29 to btc_h1_manual. Offset 20, clear of BTC's existing 0/10
    # slots (btc_cons/btc_aggr).
    "btc_h1_breakout": 20,
    # btc_amd_sweep (see amd_sweep_tpo_strategy.py): AMD + liquidity sweep
    # + TPO profile. FAILED backtest (BTC H1 PF 0.96, best of a family that
    # ranged 0.32-0.96) -- deployed as an entry signal only, at reduced
    # risk, by explicit user decision after being shown these numbers.
    "btc_amd_sweep": 30,
    # One bot per ICT tool (see ict_tools_strategies.py). Splitting the
    # three apart beat the combined btc_amd_sweep on every one of them
    # (combined PF 0.96 vs AMD 1.01 / LQ 1.06 / TPO 1.14 on BTC H1) --
    # stacking them as one filter chain was destroying signal.
    "btc_amd": 40,
    "btc_lqsweep": 50,
    "btc_tpo": 60,
    # gold_momentum_rsi (see gold_momentum_rsi_strategy.py): momentum +
    # RSI-recent entry, 3rd iteration of a gold trend+RSI family tested
    # 2026-08-03. FAILS backtest (PF 0.41 real cost, PF 0.93 even at
    # ZERO cost, OOS train/test both negative and consistent -- a real,
    # mild, persistent negative edge, not overfitting). Deployed as
    # entry-signal-only at reduced risk by explicit user decision.
    "gold_momentum_rsi": 70,
}

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# [FIX a] Lock files live in a fixed system temp directory, NOT under
# _BASE_DIR. Rationale: _BASE_DIR is derived from __file__, i.e. it depends
# on which physical copy of this script is executed (e.g.
# Desktop\forex_live_bot_gold_cwider.py vs
# Desktop\bot_repo\forex_live_bot_gold_cwider.py, which commonly coexist
# after a git-based deploy.ps1). If two copies exist and someone runs the
# bot_repo copy by mistake, _BASE_DIR-based lock files would live in a
# DIFFERENT folder per copy, so the single-instance lock (flock/msvcrt on
# LOCK_FILE) would silently fail to detect the duplicate. Both processes
# would share the same magic number (derived only from SYMBOL+VARIANT_TAG,
# unaffected by folder) and could both trade the same position -> duplicate
# real-money orders. tempfile.gettempdir() is a single OS-wide constant
# independent of which file was launched, so any two invocations of the
# same symbol+variant always contend for the exact same lock file.
_LOCK_DIR = tempfile.gettempdir()


def _lock_path(symbol: str, variant: str) -> str:
    return os.path.join(_LOCK_DIR, f"forexbot_{symbol.lower()}_{variant.lower()}.lock")


def _make_paths(symbol: str, variant: str = "cwider") -> tuple:
    """Return (STOP_FILE, STATE_FILE, LOG_FILE, FILLS_LOG_FILE, LOCK_FILE, MAGIC_NUMBER, EQUITY_STOP_FILE, HEARTBEAT_FILE) for symbol+variant."""
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
            _lock_path(symbol, v),
            magic,
            os.path.join(_BASE_DIR, f"EQUITY_STOP_{symbol.upper()}"),
            os.path.join(_BASE_DIR, f"HEARTBEAT_{symbol.upper()}"),
        )
    return (
        os.path.join(_BASE_DIR, f"STOP_{symbol.upper()}_{v.upper()}"),
        os.path.join(_BASE_DIR, f"{slug}_{v}_state.json"),
        os.path.join(_BASE_DIR, f"forex_{slug}_{v}.log"),
        os.path.join(_BASE_DIR, f"fills_log_{slug}_{v}.csv"),
        _lock_path(symbol, v),
        magic,
        os.path.join(_BASE_DIR, f"EQUITY_STOP_{symbol.upper()}_{v.upper()}"),
        os.path.join(_BASE_DIR, f"HEARTBEAT_{symbol.upper()}_{v.upper()}"),
    )

# Initialised with defaults; overwritten in main() after argparse
STOP_FILE, STATE_FILE, LOG_FILE, FILLS_LOG_FILE, LOCK_FILE, MAGIC_NUMBER, EQUITY_STOP_FILE, HEARTBEAT_FILE = _make_paths(SYMBOL)



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
#  Entry gates — pure functions (no MT5 import so they unit-test off-VPS)
#
#  [2026-08-07] Both gates were adversarially verified (independent
#  re-implementation) on the sl3/tp999 base, then RE-validated on the
#  SL2.5/TP15 base the bots now run:
#
#  gate_time_allow (gold_h1_manual):  block entries whose FILL happens in
#    UTC hours {20..23, 0} (pre-rollover / late-NY dead zone).
#    XAUUSD H1 2013-2026, SL2.5/TP15: Sharpe 0.71->0.95, CAGR +5.50->+7.07%,
#    DD 14.6->12.3%, yearly PF>1 10/14 -> 12/14; plateau confirmed
#    (BLK19_01 / BLK20_02 neighbours also beat base).
#    Hour semantics: the backtest blocks by the FILL bar's open hour, and the
#    gold CSV clock was empirically pinned to fixed UTC+0 (DST analysis of
#    714 weekend closures + hourly vol profile). Live fills happen at market
#    immediately after bar close, so hour(now UTC) IS the fill hour — no
#    broker-clock conversion needed, deliberately avoiding a dependency on
#    the broker's (DST-shifting) server timezone.
#
#  gate_r36s_allow_short (btc_h1_manual):  block SHORT entries unless the
#    ETH/BTC close ratio's EMA36 > EMA168 (H1).
#    BTCUSDc H1 2017-2026, SL2.5/TP15: Sharpe 1.33->1.47, CAGR +22.9->+24.2%,
#    DD 22.6->12.2%; H2-half strictly better; 2022 bear loss -23.5%->-12.1%.
#    Robust to 1-2 bar signal lag (SHIFT test Sharpe 1.46/1.48), which is what
#    makes a live fetch-at-entry implementation safe.
#    NOTE: the funding-a70 gate and the a70+r36S stack were REJECTED on this
#    base (2nd half worse than base) — do not add them here without a fresh
#    validation round.
#
#  Design rule: gates only ever REMOVE entries. Every failure path
#  (missing data, fetch timeout, misconfig) fails OPEN = behave exactly like
#  the un-gated bot. A totally broken gate cannot make the bot worse than
#  what ran before it existed.
# =============================================================================

def gate_time_allow(fill_utc_hour: int, lo: int, hi: int) -> bool:
    """Allow-mask for the time gate: block fill hours in wrapped [lo, hi).

    Mirrors block_allow() in _uc_verify_gold_time.py exactly:
    lo<hi  -> block lo <= h < hi ;  lo>=hi -> block h >= lo OR h < hi.
    """
    h = int(fill_utc_hour) % 24
    if lo < hi:
        blocked = (h >= lo) and (h < hi)
    else:
        blocked = (h >= lo) or (h < hi)
    return not blocked


def gate_r36s_allow_short(eth_closes, btc_closes,
                          fast: int = 36, slow: int = 168) -> bool:
    """Allow shorts only while ETH/BTC ratio EMA(fast) > EMA(slow).

    Mirrors short_mask() in _uc_verify_crossasset.py exactly:
    ratio = eth/btc on aligned H1 closes, pandas ewm(span, adjust=False),
    evaluated at the last (= signal) bar. Caller must pass series aligned
    bar-for-bar and ending at the last CLOSED bar. Needs enough history for
    EMA168 convergence — with >=600 bars the initial-condition residue is
    <0.1% weight; callers should fail OPEN below that.
    """
    eth = pd.Series(list(eth_closes), dtype=float)
    btc = pd.Series(list(btc_closes), dtype=float)
    ratio = eth / btc
    e_f = ratio.ewm(span=fast, adjust=False).mean().iloc[-1]
    e_s = ratio.ewm(span=slow, adjust=False).mean().iloc[-1]
    return bool(e_f > e_s)


# =============================================================================
# BOT
# =============================================================================
class GoldCWiderBot:
    def __init__(self, cfg: ForexConfig,
                 max_positions: int = 1,
                 strategy_cls: type = HybridTrendPullback,
                 sl_atr: Optional[float] = None,
                 tp_atr: Optional[float] = None,
                 adx_min: Optional[float] = None,
                 touch_tolerance: Optional[float] = None,
                 fresh_maturity: Optional[int] = None,
                 donch_win: Optional[int] = None,
                 breakout_margin_atr: Optional[float] = None,
                 symbol: Optional[str] = None,
                 variant_tag: Optional[str] = None,
                 timeframe: Optional[str] = None,
                 stop_file: Optional[str] = None,
                 state_file: Optional[str] = None,
                 log_file: Optional[str] = None,
                 fills_log_file: Optional[str] = None,
                 lock_file: Optional[str] = None,
                 equity_stop_file: Optional[str] = None,
                 heartbeat_file: Optional[str] = None,
                 block_hours: Optional[str] = None,
                 xasset_short_gate: Optional[str] = None):
        self.cfg = cfg
        self.max_positions = max(1, int(max_positions))

        # ── Entry gates (validated 2026-08-07 — see the gate_* functions'
        #    docblock above for the numbers). Parse early so a malformed flag
        #    kills the bot AT START (visible) instead of silently at the first
        #    entry decision hours later.
        self.block_hours: Optional[tuple] = None          # (lo, hi) wrapped UTC
        if block_hours:
            lo_s, hi_s = block_hours.split("-")
            self.block_hours = (int(lo_s) % 24, int(hi_s) % 24)
        self.xasset_gate: Optional[tuple] = None          # (symbol, fast, slow)
        if xasset_short_gate:
            g_sym, g_fast, g_slow = xasset_short_gate.split(":")
            self.xasset_gate = (g_sym, int(g_fast), int(g_slow))

        # [FIX] symbol/variant_tag/timeframe and every *_FILE path are passed
        # explicitly (falling back to the module globals only when the caller
        # omits them) rather than read purely from the SYMBOL/VARIANT_TAG/
        # TIMEFRAME/STOP_FILE/STATE_FILE/LOG_FILE/FILLS_LOG_FILE/LOCK_FILE/
        # EQUITY_STOP_FILE/HEARTBEAT_FILE globals -- same root cause as the
        # sl_atr/tp_atr/adx_min fix below: main()'s
        # `from gold_manual_exit_bot import GoldManualExitBot` (used for
        # --manual-exit) makes Python import this same file a second time
        # under the name "forex_live_bot_gold_cwider" (distinct from the
        # already-running "__main__" copy), re-running all module-level code
        # with the hardcoded defaults and never seeing the CLI-arg
        # reassignments that happen inside __main__'s main(). Since that
        # second copy is what defines the GoldCWiderBot class actually
        # instantiated for --manual-exit runs, every one of these globals
        # silently stayed at its "cwider"/XAUUSD default for adx20_manual
        # (confirmed live: state file was xauusd_cwider_state.json instead of
        # xauusd_adx20_manual_state.json, fills log was
        # fills_log_xauusd_cwider.csv, and critically STOP_FILE/
        # EQUITY_STOP_FILE/LOCK_FILE were the SHARED "cwider" filenames --
        # meaning the documented kill-switch STOP_XAUUSD_ADX20_MANUAL never
        # actually blocked this bot's entries). Explicit constructor args
        # close that gap the same way sl_atr/tp_atr/adx_min already do.
        self.symbol       = SYMBOL if symbol is None else symbol
        self.variant_tag  = VARIANT_TAG if variant_tag is None else variant_tag
        self.timeframe    = TIMEFRAME if timeframe is None else timeframe
        self.stop_file         = STOP_FILE if stop_file is None else stop_file
        self.state_file        = STATE_FILE if state_file is None else state_file
        self.log_file           = LOG_FILE if log_file is None else log_file
        self.fills_log_file     = FILLS_LOG_FILE if fills_log_file is None else fills_log_file
        self.lock_file          = LOCK_FILE if lock_file is None else lock_file
        self.equity_stop_file   = EQUITY_STOP_FILE if equity_stop_file is None else equity_stop_file
        self.heartbeat_file     = HEARTBEAT_FILE if heartbeat_file is None else heartbeat_file

        self.log = self._setup_logging()

        self.strategy = strategy_cls()
        # [FIX 2026-07-30] TIMEFRAME_SECONDS must match self.timeframe (the
        # ACTUAL entry-bar spacing fetched from MT5), not the class default of
        # 900 (15m). _bucket_seconds() = TIMEFRAME_SECONDS * H1_BARS(4) -- if
        # this is left at 900 while --timeframe 1h feeds native H1 candles,
        # the trend bucket (meant to be 4x the entry bar = H4) collapses to
        # exactly 1 entry bar per bucket, silently disabling all H4
        # aggregation (EMA50/200/ADX14 would run directly on raw H1 bars
        # instead of on 4-bar H4 candles). Confirmed by tracing through
        # _bucket_seconds()/_build_h1_trend_array before this fix existed.
        tf_mul_sec = {"m": 60, "h": 3600, "d": 86400}
        _tf_v, _tf_u = int(self.timeframe[:-1]), self.timeframe[-1]
        self.strategy.TIMEFRAME_SECONDS = _tf_v * tf_mul_sec.get(_tf_u, 60)
        self.strategy.sl_atr = SL_ATR if sl_atr is None else sl_atr
        self.strategy.tp_atr = TP_ATR if tp_atr is None else tp_atr
        self.strategy.ADX_MIN = ADX_MIN if adx_min is None else adx_min
        # [2026-08-05] TOUCH_TOLERANCE = how far price may sit from the M15/entry
        # EMA20 and still count as a "pullback touch". Class default 0.0015 was
        # never tuned; a wider band (0.012) tested materially better on BTC H1
        # (OOS Sharpe 0.81 -> 1.00, CAGR +11% -> +21%, 9/10 yrs PF>1, same DD
        # and near-identical trade frequency). Only the trend-pullback family
        # has this attribute -- hasattr guard keeps donchian/tool_*/mom_rsi safe.
        if touch_tolerance is not None and hasattr(self.strategy, "TOUCH_TOLERANCE"):
            self.strategy.TOUCH_TOLERANCE = touch_tolerance
        if fresh_maturity is not None and fresh_maturity > 0:
            # only meaningful if strategy_cls is one of the Fresh* combo
            # classes (main() only sets this when --fresh-maturity > 0, which
            # is exactly when it also swaps strategy_cls) -- setting the
            # attribute on a non-Fresh class is harmless (unused) rather than
            # an error, so this stays safe even if called directly.
            self.strategy.MAX_MATURITY = fresh_maturity
        if donch_win is not None and hasattr(self.strategy, "DONCH_WIN"):
            self.strategy.DONCH_WIN = donch_win
        if breakout_margin_atr is not None and hasattr(self.strategy, "BREAKOUT_MARGIN_ATR"):
            self.strategy.BREAKOUT_MARGIN_ATR = breakout_margin_atr
        # ไม่มี trail/partial/breakeven ใน bot นี้ — ค่าเหล่านี้ไม่ถูกอ้างถึงเลย

        self.connector: Optional[MT5Connector] = None
        self.executor:  Optional[ForexOrderExecutor] = None
        self.bsym = self.symbol   # broker symbol (resolved after connect)

        self._lock_fd: Optional[int] = None  # flock fd ของ single-instance lock (live mode เท่านั้น)

        self.buf = CandleBuffer(HISTORY_BARS, min_bars=self.strategy.MIN_BARS)
        self.positions: list[Position] = []

        # ── Day-level state (tracking only, no blocking rules) ──
        self.current_day:      Optional[str] = None
        self.day_realized_pnl: float = 0.0
        self.day_equity_start: float = cfg.total_capital_usd
        self.day_trade_count:  int   = 0
        self.day_spreads:      list  = []

        # ── Portfolio-level equity-stop circuit breaker (defense layer) ──
        # Tracks the highest equity ever observed and, if current equity
        # drops more than cfg.stop_equity_frac below that peak, blocks new
        # entries (existing positions still managed normally, same as the
        # manual kill-switch file). Unlike per-trade SL, this catches a
        # losing streak accumulated across many trades rather than damage
        # from a single position. Requires a human to delete
        # EQUITY_STOP_FILE before new entries resume — it will NOT clear
        # itself automatically, by design.
        self.peak_equity:           float = cfg.total_capital_usd
        self.equity_stop_triggered: bool  = False

        # ── MT5-call timeout watchdog (defense layer #1) ──
        # ทุก MT5 IPC call ที่อยู่บน hot-path ของ main loop รันผ่าน executor นี้
        # แทนการเรียกตรง เพื่อกัน mt5.copy_rates_from_pos() ค้างไม่มีกำหนด
        self._io_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=MT5_IO_WORKERS, thread_name_prefix=f"mt5-io-{self.variant_tag}")
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
                self.log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"))
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
        เพิ่ม → reconnect logic เดิม (disconnect + connect ใหม่) ทำงานเอง

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
        fd = os.open(self.lock_file, os.O_CREAT | os.O_RDWR, 0o644)
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
                f"REFUSING TO START: Another {self.symbol.lower()}_{self.variant_tag} instance is already running "
                f"(lock held on {os.path.basename(self.lock_file)}). "
                f"ฆ่า process เดิมก่อน หรือถ้าแน่ใจว่าไม่มี process ค้าง ให้ลบไฟล์ lock นี้ทิ้ง.")
            sys.exit(1)
        # เก็บ fd ไว้ตลอดอายุ process — ห้าม close (ไม่งั้น lock จะหลุดทันที)
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, str(os.getpid()).encode())
        except OSError:
            pass
        self._lock_fd = fd
        self.log.info(f"[LOCK] acquired single-instance lock: {os.path.basename(self.lock_file)} (pid={os.getpid()})")

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
                os.remove(self.lock_file)
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
        self.bsym = self.connector.resolve_symbol(self.symbol)
        if self.bsym != self.symbol:
            self.cfg.add_symbol_alias(self.symbol, self.bsym)
            self.log.info(f"[SYMBOL] {self.symbol} -> {self.bsym}")

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
            f" {self.symbol} C_WIDER LIVE BOT  —  DEMO FORWARD-TEST (execution-cost measurement)",
            "=" * 78,
            f"  ACCOUNT TYPE  : {acct_type}   -> {demo_tag}",
            f"  Broker/Server : {broker} / {server}   Login: {login}",
            f"  Balance       : {balance:,.2f} {currency}   Equity: {equity:,.2f} {currency}",
            f"  Symbol        : {self.symbol}  ->  resolved broker symbol: {self.bsym}",
            f"  Strategy      : {self.strategy.name}",
            # [FIX 2026-08-05] The trend timeframe was hardcoded as "H1" here,
            # which was wrong for every bot launched with --timeframe 1h. The
            # real trend bucket is _bucket_seconds() = TIMEFRAME_SECONDS x
            # H1_BARS(4), so an H1-entry bot has always used an H4 trend, and a
            # 15m-entry bot an H1 trend. The banner claimed H1 in both cases,
            # which is exactly the sort of thing that makes an audit of "what
            # timeframes am I actually trading" come out wrong. Derived now.
            (f"  Entry TF      : {self.timeframe.upper()}    "
             f"Trend TF: {self._fmt_trend_tf()} (resampled, "
             f"EMA{self.strategy.EMA_H1_FAST}/{self.strategy.EMA_H1_SLOW}, ADX>={self.strategy.ADX_MIN})"
             if hasattr(self.strategy, "EMA_H1_FAST") else
             f"  Entry TF      : {self.timeframe.upper()}    "
             f"Signal: {self.strategy.DONCH_WIN}-bar Donchian breakout"
             f" (margin={self.strategy.BREAKOUT_MARGIN_ATR}xATR)"
             if hasattr(self.strategy, "DONCH_WIN") else
             f"  Entry TF      : {self.timeframe.upper()}"),
            f"  Exit config   : SL={self.strategy.sl_atr}xATR  TP={self.strategy.tp_atr}xATR"
            + (f"   | Trailing={self.strategy.trail_atr_mult}xATR"
               f" activate@{self.strategy.trail_activation_atr}xATR"
               if getattr(self.strategy, "trail_atr_mult", 999.0) < 900
               else "   | Partial-TP=OFF  Move-to-BE=OFF  Trailing=OFF"),
            f"  Fresh-filter  : {'ON, max maturity=' + str(self.strategy.MAX_MATURITY) + ' entry-bars' if hasattr(self.strategy, 'MAX_MATURITY') else 'OFF'}",
            f"  Entry gates   : time-block="
            + (f"UTC [{self.block_hours[0]:02d},{self.block_hours[1]:02d}) ON"
               if self.block_hours else "OFF")
            + "  xasset-short="
            + (f"{self.xasset_gate[0]} EMA{self.xasset_gate[1]}/{self.xasset_gate[2]} ON"
               if self.xasset_gate else "OFF"),
            f"  Max hold      : {self.cfg.max_hold_bars} bars"
            f" (= {self.cfg.max_hold_bars * self.cfg.timeframe_minutes / 60:g} hours)",
            f"  Risk/trade    : {self.cfg.risk_per_trade_pct}%   Magic: {self.cfg.magic_number}"
            f"   Max-positions: {self.max_positions}",
            f"  Daily rules   : OFF (no daily loss limit, no reactive stop)",
            f"  Order mode    : {'DRY-RUN (paper, NO real orders)' if self.cfg.dry_run else 'LIVE — places real orders on the account above'}",
            f"  Kill-switch   : touch {os.path.basename(self.stop_file)}  (blocks NEW entries only)",
            f"  Equity-stop   : {self.cfg.stop_equity_frac*100:.0f}% drawdown from peak -> "
            f"touch {os.path.basename(self.equity_stop_file)} auto-created, blocks NEW entries "
            f"(delete file to resume)",
            f"  Telegram alert: {'configured ✅' if (os.environ.get('TELEGRAM_BOT_TOKEN') and os.environ.get('TELEGRAM_CHAT_ID')) else 'NOT configured ⚠️ (set TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID in .env)'}",
            f"  MT5-call timeout : {self._mt5_call_timeout_sec:.0f}s  (fetch hang -> forced reconnect)",
            f"  Fills log     : {os.path.basename(self.fills_log_file)}",
            "=" * 78,
        ]
        for l in lines:
            self.log.info(l)

        if not self.cfg.dry_run and not is_demo:
            if not self.cfg.allow_real:
                self.log.error(
                    "REFUSING TO START: ไม่สามารถยืนยันได้ว่าบัญชีนี้เป็น DEMO "
                    "(account info type != ACCOUNT_TRADE_MODE_DEMO). "
                    "ถ้าตั้งใจรันบัญชีจริง (เช่น Real Cent account) ต้องส่ง "
                    "--allow-real มาด้วยอย่างชัดเจน มิฉะนั้นระบบจะปฏิเสธการ "
                    "start เสมอเพื่อป้องกันการต่อ MT5 terminal ผิดบัญชีโดยไม่ตั้งใจ")
                sys.exit(1)
            else:
                self.log.warning(
                    "*** REAL-MONEY MODE CONFIRMED (--allow-real) *** "
                    f"บัญชีนี้ไม่ใช่ demo — คำสั่งซื้อขายทุกคำสั่งจะใช้เงินจริง "
                    f"บน login={login}  balance={balance:,.2f} {currency}")

        expected_login = os.environ.get("MT5_LOGIN")
        if not self.cfg.dry_run:
            if expected_login:
                if str(login) != str(expected_login):
                    self.log.error(
                        f"REFUSING TO START: connected login={login} != MT5_LOGIN={expected_login} (env) "
                        f"— MT5 terminal login ผิดบัญชี")
                    sys.exit(1)
            else:
                # [FIX C1-4] MT5_LOGIN not set → safety check is disabled; warn loudly
                # so the operator knows this layer is inactive (was a silent skip before).
                self.log.warning(
                    "MT5_LOGIN ไม่ได้ตั้งไว้ใน environment — "
                    "login-match safety check ไม่ทำงาน (แนะนำให้ตั้งค่านี้ "
                    "ใน .env เพื่อป้องกันการต่อผิดบัญชีโดยไม่ตั้งใจ)")

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
                self.bsym, self.timeframe, limit + 1)
        else:
            candles = self._call_with_timeout(
                self.connector.fetch_ohlcv, self._mt5_call_timeout_sec,
                self.bsym, self.timeframe, limit + 1)
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
        write_header = not os.path.exists(self.fills_log_file)
        try:
            with open(self.fills_log_file, "a", newline="", encoding="utf-8") as f:
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
            "equity_stop_state": {
                "peak_equity":           self.peak_equity,
                "equity_stop_triggered": self.equity_stop_triggered,
            },
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as exc:
            self.log.error(f"save_state: {exc}")

    def load_state(self):
        if not os.path.exists(self.state_file):
            return
        try:
            with open(self.state_file, encoding="utf-8") as f:
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

            es = data.get("equity_stop_state", {})
            self.peak_equity           = es.get("peak_equity", self.cfg.total_capital_usd)
            self.equity_stop_triggered = es.get("equity_stop_triggered", False)
            if self.equity_stop_triggered:
                self.log.warning(
                    f"[STATE] equity-stop was previously triggered (peak_equity="
                    f"{self.peak_equity:,.2f}) — remains latched until "
                    f"{os.path.basename(self.equity_stop_file)} is deleted")

            self.log.info(f"[STATE] loaded {self.state_file}")
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

        # 1) any tracked position no longer at the broker was closed while
        #    this bot was down (or fell into the once-per-hour close-detection
        #    gap that existed before the 2026-08-05 on_poll() fix). Report it
        #    like a normal close instead of silently discarding it.
        #    [FIX 2026-08-05] This branch previously only logged a warning
        #    and dropped the position -- the user got ZERO notification for
        #    any position that happened to close while a bot was mid-restart.
        #    Every deploy restarts one or more bots, so on a day with several
        #    deploys this was a real, recurring silent-close path, not a rare
        #    edge case -- root-caused from a live user report of "only one
        #    notification the whole time" for a trade that, on inspection,
        #    closed at the broker exactly during a restart window. Reuses
        #    _log_broker_close() (same as a close detected live) with a wide
        #    lookback since we don't know how long the bot was down for.
        for pos in list(self.positions):
            if pos.trade_id not in broker_ids:
                self.log.warning(f"[RECOVER] tracked position {pos.trade_id} ไม่มีที่ broker "
                                 f"— ปิดไปแล้วตอนบอทไม่ทำงาน, ส่ง best-effort close report")
                self._log_broker_close(pos, lookback_minutes=1440)

        # 2) adopt any broker positions we are not yet tracking
        tracked_ids = {p.trade_id for p in self.positions}
        for p in mine:
            pid = str(p.get("id", ""))
            if pid in tracked_ids:
                continue
            side = "long" if str(p.get("type", "")).endswith("BUY") else "short"
            # [FIX 2026-08-05] entry_atr was hardcoded 0.0 here, which
            # PERMANENTLY silences the +/-ATR milestone alerts for any
            # adopted position (_check_atr_milestones skips entry_atr<=0
            # unconditionally, for the whole life of the trade). The real
            # entry-bar ATR is unknowable for a position this bot never
            # opened itself, but boot_buffer() has already run by this point
            # in startup, so the buffer's most recent ATR is a reasonable
            # stand-in -- far better than a value that guarantees silence.
            est_atr = 0.0
            try:
                if self.buf.d is not None:
                    a = self.buf.d["atr"][-1]
                    if a and a > 0:
                        est_atr = float(a)
            except Exception:
                pass
            new_pos = Position(
                side=side,
                entry=float(p.get("openPrice", 0) or 0),
                sl=float(p.get("stopLoss", 0) or 0),
                tp=float(p.get("takeProfit", 0) or 0),
                lot=float(p.get("volume", 0) or 0),
                trade_id=pid,
                entry_atr=est_atr,
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
            self.log.info(
                f"  [SKIP] risk cap exceeded: actual={actual_risk_pct:.2f}% "
                f"> max={self.cfg.max_risk_per_trade_pct}%  (lot would be {lot})")
            return

        side    = "long" if long_ else "short"
        comment = f"{self.variant_tag[:6].upper()}-{sig.action}"[:16]

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

        self._send_telegram_alert(
            f"\U0001F7E2 OPEN {side.upper()} — {self.variant_tag} ({self.bsym})\n"
            f"Lot: {lot}\n"
            f"Entry: {fill_px:.2f}\n"
            f"SL: {sl:.2f}   TP: {tp:.2f}\n"
            f"Spread: {spread:.4f}   Slippage: {slippage:+.4f}")

    # ─────────────────────────────────────────────────────────────────────
    # Close due to Timeout (the only bot-initiated close for C_wider)
    # ─────────────────────────────────────────────────────────────────────
    def _close_timeout(self, pos: Position):
        bid, ask = self.connector.get_current_price(self.bsym)
        req_px = bid if pos.side == "long" else ask
        if req_px <= 0:
            req_px = pos.entry

        result = self.executor.close_position_market(
            self.bsym, pos.side, pos.lot, pos.trade_id, comment=f"{self.variant_tag[:6].upper()}-Timeout")
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
            comment=f"{self.variant_tag[:6].upper()}-Timeout pnl={net:+.2f}")

        self.log.info(
            f"  [CLOSE-TIMEOUT] {pos.side.upper()} {self.bsym} lot={pos.lot}  "
            f"fill={fill_px:.2f}  net_pnl={net:+.2f}  "
            f"spread={spread:.4f}  slippage={slippage:+.4f}  commission={commission:.4f}")

        self._send_telegram_alert(
            f"\U000023F1 CLOSE-TIMEOUT {pos.side.upper()} — {self.variant_tag} ({self.bsym})\n"
            f"Lot: {pos.lot}\n"
            f"Entry: {pos.entry:.2f}   Fill: {fill_px:.2f}\n"
            f"Net PnL: {net:+.2f}")

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

    def _log_broker_close(self, pos: Position, lookback_minutes: int = 30):
        # ปิดไปแล้ว (SL/TP/manual จากฝั่ง broker) — best-effort หา fill/commission จริง
        bid, ask = self.connector.get_current_price(self.bsym)
        deals = self.connector.get_position_deals(pos.trade_id, lookback_minutes=lookback_minutes)
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
            comment=f"{self.variant_tag[:6].upper()}-{reason} pnl={net:+.2f}")

        self.log.info(
            f"  [CLOSE-{reason}] {pos.side.upper()} {self.bsym} lot={pos.lot}  "
            f"fill={fill_px:.2f}  net_pnl={net:+.2f}  "
            f"spread={spread:.4f}  slippage={slippage:+.4f}  commission={commission:.4f}")

        emoji = "\U0001F534" if net < 0 else "\U0001F7E2"
        self._send_telegram_alert(
            f"{emoji} CLOSE-{reason} {pos.side.upper()} — {self.variant_tag} ({self.bsym})\n"
            f"Lot: {pos.lot}\n"
            f"Entry: {pos.entry:.2f}   Fill: {fill_px:.2f}\n"
            f"Net PnL: {net:+.2f}")

        self.day_trade_count += 1
        self._update_day_state(net)
        if pos in self.positions:
            self.positions.remove(pos)
        self.executor.release_order_key(self.bsym, pos.side, pos.lot)

    # ─────────────────────────────────────────────────────────────────────
    # Telegram alert (best-effort — never blocks or crashes the bot)
    # ─────────────────────────────────────────────────────────────────────
    def _send_telegram_alert(self, message: str):
        """ส่งข้อความแจ้งเตือนผ่าน Telegram Bot API — best-effort เท่านั้น.

        อ่าน TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID จาก environment (.env)
        ถ้าไม่ได้ตั้งค่าไว้ จะข้ามเงียบๆ (debug log เท่านั้น ไม่ error) เพื่อ
        ไม่ให้บอทกระทบการทำงานหลักถ้า Telegram ยังไม่ได้ setup หรือ network
        มีปัญหาชั่วคราว — ใช้ urllib มาตรฐานของ Python เพื่อไม่ต้องเพิ่ม
        dependency ใหม่ (เช่น `requests`) ที่อาจไม่มีติดตั้งบน VPS.
        """
        token   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        if not token or not chat_id:
            self.log.debug(
                "[TELEGRAM] TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set in "
                "environment — skipping alert")
            return
        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            data = urllib.parse.urlencode({"chat_id": chat_id, "text": message}).encode()
            req = urllib.request.Request(url, data=data)
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
            self.log.info("[TELEGRAM] alert sent")
        except Exception as exc:
            self.log.error(f"[TELEGRAM] failed to send alert: {exc}")

    # ─────────────────────────────────────────────────────────────────────
    # Equity-stop circuit breaker (portfolio-level, see __init__ comment)
    # ─────────────────────────────────────────────────────────────────────
    def _check_equity_stop(self, equity: float):
        """อัปเดต peak equity และเช็คว่าต้อง trigger circuit breaker ไหม.

        Trigger เมื่อ equity ปัจจุบันต่ำกว่า peak เกิน cfg.stop_equity_frac
        (เช่น 0.35 = 35%) — เขียนไฟล์ EQUITY_STOP_FILE ทันทีเพื่อบล็อกไม้
        ใหม่ (ไม้เก่ายังบริหารตามปกติผ่าน broker SL/TP/timeout) และไม่
        auto-clear เอง ต้องลบไฟล์ด้วยมือหลังตรวจสอบสถานการณ์แล้วเท่านั้น
        """
        if equity > self.peak_equity:
            self.peak_equity = equity

        if self.equity_stop_triggered:
            return  # already triggered — stay latched until a human clears it

        floor = self.peak_equity * (1.0 - self.cfg.stop_equity_frac)
        if equity <= floor:
            self.equity_stop_triggered = True
            drawdown_pct = (1.0 - equity / self.peak_equity) * 100.0 if self.peak_equity > 0 else 0.0
            self.log.critical(
                f"[EQUITY-STOP TRIGGERED] equity={equity:,.2f} <= floor={floor:,.2f} "
                f"(peak={self.peak_equity:,.2f}, drawdown={drawdown_pct:.1f}% >= "
                f"{self.cfg.stop_equity_frac*100:.0f}% threshold). "
                f"Blocking NEW entries. Existing positions still managed normally. "
                f"To resume: investigate first, then delete {os.path.basename(self.equity_stop_file)}")
            try:
                with open(self.equity_stop_file, "w", encoding="utf-8") as f:
                    f.write(
                        f"Triggered at {datetime.now(timezone.utc).isoformat()}\n"
                        f"peak_equity={self.peak_equity:.2f}\n"
                        f"equity_at_trigger={equity:.2f}\n"
                        f"drawdown_pct={drawdown_pct:.1f}\n"
                        f"threshold_pct={self.cfg.stop_equity_frac*100:.0f}\n"
                        f"Delete this file to resume new entries after investigating.\n")
            except Exception as exc:
                self.log.error(f"_check_equity_stop: failed to write EQUITY_STOP_FILE: {exc}")

            self._send_telegram_alert(
                f"\U0001F6A8 EQUITY-STOP TRIGGERED \U0001F6A8\n"
                f"Variant: {self.variant_tag} ({self.symbol})\n"
                f"Peak equity: {self.peak_equity:,.2f}\n"
                f"Current equity: {equity:,.2f}\n"
                f"Drawdown: {drawdown_pct:.1f}% (threshold: {self.cfg.stop_equity_frac*100:.0f}%)\n"
                f"New entries are BLOCKED. Existing positions still managed normally.\n"
                f"To resume: investigate, then delete {os.path.basename(self.equity_stop_file)} on the VPS.")

    # ─────────────────────────────────────────────────────────────────────
    # Process a newly-closed M15 bar
    # ─────────────────────────────────────────────────────────────────────
    def _fmt_trend_tf(self) -> str:
        """Human label for the ACTUAL trend bucket the strategy aggregates to.

        _bucket_seconds() = TIMEFRAME_SECONDS * H1_BARS, so this is derived
        rather than assumed -- see the banner comment for why that matters.
        """
        try:
            secs = int(self.strategy._bucket_seconds())
        except Exception:
            return "?"
        if secs % 86400 == 0:
            return f"D{secs // 86400}" if secs != 86400 else "D1"
        if secs % 3600 == 0:
            return f"H{secs // 3600}"
        return f"M{max(1, secs // 60)}"

    def on_poll(self):
        """Hook that runs on EVERY main-loop poll (~poll_interval_sec), not only
        when a new bar closes. Base implementation is a no-op.

        [2026-08-05] Added because GoldManualExitBot's +/-1xATR Telegram alerts
        were wired into process_bar(), which the main loop only calls when
        `added` > 0 -- i.e. once per CLOSED BAR. On the H1 bots that meant the
        alert could only ever fire once an hour (once a DAY for
        gold_daily_breakout), using the just-closed bar's close price. A move
        that reached +2xATR mid-hour and retraced before the bar closed
        produced no alert at all -- exactly the case that matters most for
        bots running --manual-exit, where the user is the one deciding when to
        close. Overridden in GoldManualExitBot to run the ATR-milestone check
        against the LIVE tick instead.

        [FIX 2026-08-05, part 2] Also runs _check_broker_close() here now.
        That check -- and the Telegram CLOSE alert it sends -- used to live
        exclusively in process_bar(), so a position closed by the broker (SL
        hit, or the user closing it by hand in the MT5 terminal) was only
        NOTICED, and only alerted on, at the next bar close: up to an hour
        late on the H1 bots. The open/milestone alerts arrived promptly
        (open is decided at bar close, which is already timely; milestones
        were already fixed in part 1 above) but the close alert lagged behind
        by however long was left in the hour -- exactly the "profit went up,
        then it closed, and I only got one notification the whole time"
        pattern this was fixed for. Now checked every ~30s like the
        milestones are. Idempotent: a position already removed from
        self.positions on a prior poll is simply not in the loop again.
        """
        self._check_broker_close()

    def process_bar(self):
        d = self.buf.d
        i = len(d["c"]) - 1
        ts = str(d["ts"][i])

        equity = self.connector.get_equity()
        self._rollover_day(ts, equity)
        self._check_equity_stop(equity)

        # ── 1) detect broker-side SL/TP close ──
        # [2026-08-05] Also covered every ~30s by on_poll() now (see above);
        # kept here too so a position that closed and reopened within the
        # same bar is still caught even if on_poll() is ever disabled.
        self._check_broker_close()

        # ── 2) manage open positions: timeout only (SL/TP are broker-managed) ──
        for pos in list(self.positions):
            pos.bars += 1
            if pos.bars >= self.cfg.max_hold_bars:
                self._close_timeout(pos)

        # ── 3) kill switch (manual file OR equity-stop circuit breaker) ──
        kill = os.path.exists(self.stop_file) or os.path.exists(self.equity_stop_file) or self.equity_stop_triggered
        if os.path.exists(self.stop_file):
            self.log.info(f"[KILL-SWITCH] {os.path.basename(self.stop_file)} exists — "
                           f"blocking new entries (existing position still managed)")
        if os.path.exists(self.equity_stop_file) or self.equity_stop_triggered:
            self.log.warning(f"[EQUITY-STOP] active — blocking new entries "
                              f"(existing position still managed). Delete "
                              f"{os.path.basename(self.equity_stop_file)} to resume.")

        # ── 4) new signal — only if room for another position & not blocked ──
        can_open = (len(self.positions) < self.max_positions
                    and not kill)
        if can_open:
            sig = self.strategy.signal(d, i)
            if sig.action in ("BUY", "SELL") and self._entry_gates_allow(sig, d, i):
                self._open_position(sig, d, i)

        self.save_state()

    # ─────────────────────────────────────────────────────────────────────
    # Entry gates (see gate_* pure functions near CandleBuffer for the
    # validation record; this method only wires them to live data)
    # ─────────────────────────────────────────────────────────────────────
    def _entry_gates_allow(self, sig, d: dict, i: int) -> bool:
        """Return False to veto this entry. EVERY failure path returns True
        (fail-open): a gate that cannot evaluate must behave like no gate,
        never like a stuck kill-switch."""
        # ── time gate: live fills happen at market right after bar close, so
        #    the wall clock AT THE ENTRY DECISION is the fill time. Using
        #    datetime.now(UTC) instead of bar-timestamp+offset removes the
        #    broker-server-timezone dependency entirely (Exness-style servers
        #    shift with DST; the validated gate window is fixed UTC).
        if self.block_hours is not None:
            try:
                h = datetime.now(timezone.utc).hour
                if not gate_time_allow(h, *self.block_hours):
                    self.log.info(
                        f"[GATE][time] veto {sig.action} — fill hour {h:02d} UTC "
                        f"inside blocked [{self.block_hours[0]:02d},"
                        f"{self.block_hours[1]:02d}) window")
                    return False
            except Exception as e:
                self.log.warning(f"[GATE][time] evaluation failed ({e}) — fail-open")

        # ── cross-asset short gate (SELL only)
        if self.xasset_gate is not None and sig.action == "SELL":
            try:
                g_sym, g_fast, g_slow = self.xasset_gate
                # 990 bars: below fetch_ohlcv's single-call cap AND enough for
                # EMA168 convergence (initial-condition weight ~6e-5)
                candles = self._call_with_timeout(
                    self.connector.fetch_ohlcv, self._mt5_call_timeout_sec,
                    g_sym, self.timeframe, 990)
                if not candles:
                    self.log.warning(
                        f"[GATE][r36s] no {g_sym} candles — fail-open")
                    return True
                # closed bars only, same rule as _fetch_closed_candles()
                now_ms = int(time.time() * 1000)
                tf_ms  = self.cfg.timeframe_ms
                closed = {int(c[0]): float(c[4]) for c in candles
                          if c[0] + tf_ms + 3000 <= now_ms}
                # align bar-for-bar with our own buffer on bar-open timestamps
                pairs = [(float(r[4]), closed[int(r[0])])
                         for r in self.buf._rows if int(r[0]) in closed]
                if len(pairs) < 600:
                    self.log.warning(
                        f"[GATE][r36s] only {len(pairs)} aligned bars (<600) — "
                        f"fail-open")
                    return True
                btc_closes = [p[0] for p in pairs]
                gate_closes = [p[1] for p in pairs]
                if not gate_r36s_allow_short(gate_closes, btc_closes,
                                             g_fast, g_slow):
                    self.log.info(
                        f"[GATE][r36s] veto SELL — {g_sym}/{self.bsym} ratio "
                        f"EMA{g_fast} <= EMA{g_slow} (shorting against the "
                        f"cross-asset regime)")
                    return False
            except Exception as e:
                self.log.warning(f"[GATE][r36s] evaluation failed ({e}) — fail-open")

        return True

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
    # Heartbeat file (logging-module-independent liveness signal)
    # ─────────────────────────────────────────────────────────────────────
    def _write_heartbeat(self):
        """Write the current UTC timestamp to HEARTBEAT_FILE every loop iteration.

        [WHY] A real incident showed the logging RotatingFileHandler silently
        stop writing the .log file (no exception) while the process kept running
        and trading correctly for hours (proven by stdout/console + real OPEN/
        CLOSE-SL events). That blinded watchdog.ps1 (which parsed "== STATUS =="
        from the same log file) into thinking a perfectly healthy bot was hung,
        risking an unnecessary kill+restart.

        This heartbeat is written with a plain open()/write(), NOT through the
        logging module, so it cannot fail the same silent way. watchdog.ps1
        checks this file's mtime instead of parsing the log. A write failure
        (disk full/permission) is caught here and must NOT stop the bot trading.
        """
        try:
            with open(self.heartbeat_file, "w", encoding="utf-8") as f:
                f.write(datetime.now(timezone.utc).isoformat())
        except Exception as exc:
            self.log.debug(f"_write_heartbeat: failed: {exc}")

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
                self._write_heartbeat()

                candles = self._fetch_closed_candles(limit=5)
                added = self.buf.push(candles) if candles else 0
                consecutive_errors = 0

                if added and self.buf.ready:
                    self.process_bar()

                # Every poll, regardless of whether a bar closed. Keep this
                # AFTER process_bar() so a just-opened position is already in
                # self.positions on the same iteration. Must never raise --
                # the trading loop takes priority over notifications.
                if self.buf.ready:
                    try:
                        self.on_poll()
                    except Exception as exc:
                        self.log.error(f"[ON_POLL] ignored error: {exc}")

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
                time.sleep(min(60, 10 * consecutive_errors))


# =============================================================================
# MAIN
# =============================================================================
def main():
    global SYMBOL, VARIANT_TAG, SL_ATR, TP_ATR, ADX_MIN, RISK_PER_TRADE_PCT
    global TIMEFRAME, MAX_HOLD_BARS, HISTORY_BARS
    global STOP_FILE, STATE_FILE, LOG_FILE, FILLS_LOG_FILE, LOCK_FILE, MAGIC_NUMBER, EQUITY_STOP_FILE, HEARTBEAT_FILE
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
    ap.add_argument("--touch-tolerance", type=float, default=None,
                    help="ความกว้างของแถบ pullback รอบ EMA20 (class default 0.0015). "
                         "BTC H1 validated 2026-08-05: 0.012 ให้ OOS Sharpe 1.00 (vs 0.81 ที่ค่า default) "
                         "CAGR +21%% (vs +11%%) DD ใกล้เดิม 9/10 ปี PF>1 -- ใช้กับ trend-pullback family เท่านั้น")
    ap.add_argument("--regime-filter", action="store_true",
                    help="ใช้ RegimeFilteredHybridLive แทน HybridTrendPullback ธรรมดา -- เพิ่มเงื่อนไข "
                         "ADX rising + |EMA50-EMA200|>1.2xATR(H1) บน H1 trend array เดิม (ADX_MIN "
                         "threshold ยังมาจาก --adx-min ตามปกติ, ค่าอื่นทั้งหมดไม่เปลี่ยน). "
                         "Real-engine validated 2026-07-12: 2,848 trades/13yr PF=1.29 MaxDD=10.3%% "
                         "WF-A 12/14yr. ดู gold_regime_live_strategy.py. ใช้กับ --variant-tag regime22.")
    ap.add_argument("--manual-exit", action="store_true",
                    help="ใช้ GoldManualExitBot แทน GoldCWiderBot ปกติ -- entry/SL เหมือนเดิม "
                         "แต่ TP ไม่ auto-close (ต้องตั้ง --tp-atr สูงมาก เช่น 999 คู่กัน) "
                         "ส่ง Telegram alert ทุก +1xATR แทน ไม่มี backtest รองรับ ใช้กับ "
                         "--variant-tag adx20_manual")
    ap.add_argument("--fresh-maturity", type=int, default=0,
                    help="Skip entries whose H1/H4 trend alignment is already older than "
                         "N entry-bars (0 = disabled). CAUTION: the 2026-07-30 numbers "
                         "this flag was originally validated against were computed with a "
                         "since-fixed look-ahead bug in the H1/H4 bucket calculation -- "
                         "re-validated 2026-07-31 with the corrected engine: gold regime22 "
                         "has NO edge with or without this filter (PF<1, DD>30%%), and for "
                         "BTC/ETH the filter made results WORSE than no filter, not better. "
                         "Do not enable this without rerunning "
                         "_revalidate_fixed_engine.py-style validation first. See "
                         "FreshTrendFilterMixin in forex_hybrid_strategy.py.")
    ap.add_argument("--strategy", type=str, default="auto", choices=["auto", "donchian", "amd", "tool_amd", "tool_lqsweep", "tool_tpo", "gold_mom_rsi"],
                    help="'auto' (default): pick strategy_cls from --timeframe/--regime-filter/"
                         "--fresh-maturity as usual. 'donchian': force "
                         "GoldDailyDonchianBreakout regardless of --timeframe (works on any "
                         "entry timeframe -- window/margin are in units of entry bars, not "
                         "calendar days, once --timeframe != 1d), ignoring --regime-filter/"
                         "--fresh-maturity/--adx-min (all HybridTrendPullback-family options "
                         "that don't apply to a Donchian channel). 2026-07-31: validated for "
                         "BTCUSDc on --timeframe 1h (OOS PF=1.17, Sharpe=0.87, 7/10yr PF>1, "
                         "corr=0.29 to the existing pullback signal) -- see "
                         "btc_donchian_breakout_strategy.py.")
    ap.add_argument("--crypto-killzone", action="store_true",
                    help="Restrict --strategy tool_amd/tool_lqsweep/tool_tpo entries to the "
                         "crypto liquidity killzones (Thai 06:00-08:00 daily-candle roll, and "
                         "20:00-22:00 US equity/ETF overlap). Validated 2026-08-02: improved "
                         "full-history PF in 8 of 9 market x tool combinations and cut DD "
                         "sharply (BTC LQ-Sweep DD 59.4%%->37.1%%). See CRYPTO_KZ_THAI_HOURS "
                         "in ict_tools_strategies.py.")
    ap.add_argument("--donch-win", type=int, default=None,
                    help="Donchian channel lookback in entry-bars, overrides the strategy "
                         "class default (80). Only meaningful with --strategy donchian or "
                         "--timeframe 1d.")
    ap.add_argument("--breakout-margin-atr", type=float, default=None,
                    help="Require the close to clear the channel by this many xATR before "
                         "counting as a breakout, overrides the class default (0.25). Only "
                         "meaningful with --strategy donchian or --timeframe 1d.")
    ap.add_argument("--max-positions", type=int, default=1,
                    help="จำนวน position พร้อมกันสูงสุดต่อ instance (default 1 = พฤติกรรมเดิม). "
                         "แต่ละ position independent (own entry/SL/TP/lot/bars).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Paper mode สำหรับทดสอบโค้ด (ไม่เชื่อมต่อ MT5, ไม่ส่ง order จริง). "
                         "Default: ปิด (ส่ง order จริงบน DEMO ตามที่ขอ)")
    ap.add_argument("--allow-real", action="store_true",
                    help="อนุญาตให้รันบนบัญชีที่ไม่ใช่ DEMO (เช่น Real Cent account). "
                         "ต้องระบุอย่างชัดเจน มิฉะนั้นระบบปฏิเสธการ start เสมอถ้า "
                         "MT5 terminal ต่อบัญชีที่ไม่ใช่ demo ไว้ — ป้องกันการเทรดเงินจริง "
                         "โดยไม่ตั้งใจ")
    ap.add_argument("--timeframe", type=str, default="15m",
                    help="Entry timeframe: '15m' (default), '1h' (validated 2026-07-29 — "
                         "far better cost/ATR and entry quality than 15m), "
                         "'1d' (Donchian breakout only — see gold_daily_breakout_strategy.py; "
                         "auto-selects GoldDailyDonchianBreakout, ignores "
                         "--regime-filter/--fresh-maturity/--adx-min), "
                         "'5m' (blocked — retired after live testing), "
                         "'1m' (blocked — unvalidated).")
    ap.add_argument("--risk", type=float, default=0.0,
                    help="Risk per trade %% (0 = ใช้ค่า default ของ variant). "
                         "M5 แนะนำ 0.15 (ครึ่งนึงของ M15 เพราะ IS MaxDD สูงกว่า).")
    ap.add_argument("--poll-interval", type=int, default=30,
                    help="วินาทีต่อรอบ poll (default 30)")
    ap.add_argument("--block-hours", type=str, default=None,
                    help="Entry time-gate: block entries whose fill hour (UTC, "
                         "wall clock at the entry decision) falls in the wrapped "
                         "window 'LO-HI', e.g. '20-01' blocks 20:00-00:59 UTC. "
                         "Validated 2026-08-07 on gold H1 SL2.5/TP15 "
                         "(Sharpe 0.71->0.95). Fail-open on any error.")
    ap.add_argument("--xasset-short-gate", type=str, default=None,
                    help="Cross-asset SHORT gate 'SYMBOL:FAST:SLOW', e.g. "
                         "'ETHUSDc:36:168': block SELL entries unless the "
                         "SYMBOL/own-symbol close ratio EMA(FAST) > EMA(SLOW) "
                         "on the entry timeframe. Validated 2026-08-07 on BTC "
                         "H1 SL2.5/TP15 (Sharpe 1.33->1.47, DD 22.6->12.2%). "
                         "Fail-open on any error.")
    ap.add_argument("--capital", type=float, default=0.0,
                    help="Capital USD เริ่มต้น (0 = ดึงจาก MT5 balance)")
    args = ap.parse_args()

    # Re-initialise all symbol/variant-dependent globals based on CLI args
    SYMBOL      = args.symbol.upper()
    VARIANT_TAG = args.variant_tag.lower()
    SL_ATR      = args.sl_atr
    TP_ATR      = args.tp_atr
    ADX_MIN     = int(args.adx_min)
    STOP_FILE, STATE_FILE, LOG_FILE, FILLS_LOG_FILE, LOCK_FILE, MAGIC_NUMBER, EQUITY_STOP_FILE, HEARTBEAT_FILE = _make_paths(SYMBOL, VARIANT_TAG)

    # Timeframe-driven config
    tf = args.timeframe.lower()
    if tf == "5m":
        # [FIX C2-1] Hard-block --timeframe 5m — this is stronger than the 1m
        # block below because 5m (m5tp7) was actually run live and confirmed to
        # lose money, not just an unvalidated placeholder. The message is
        # intentionally explicit about the live-data evidence so that anyone
        # who finds this block understands why it exists and what it would take
        # to justify reopening the path (a fundamentally different strategy,
        # not just optimism).
        sys.exit(
            "[REFUSE TO START] --timeframe 5m (the m5tp7 variant) has been "
            "PERMANENTLY RETIRED after live forward-testing on the Real Cent "
            "account confirmed poor performance: PF=0.203, win_rate=9.1%, "
            "net -$271.85 over 11 closed trades. This is not a theoretical "
            "concern like the M1 path below — it was tested live with real "
            "signals and lost money consistently. Do not run this again without "
            "a deliberate code change here AND a clear reason to believe "
            "something has fundamentally changed (different signal logic, "
            "re-validated backtest + OOS cycle), not just by removing this "
            "sys.exit.")
    elif tf == "1m":
        sys.exit(
            "[REFUSE TO START] --timeframe 1m is an unvalidated placeholder "
            "(reuses HybridTrendPullback, tuned/backtested for M15 — never "
            "backtested on M1). Do not run this live until a dedicated, "
            "validated M1 strategy class exists.")
    elif tf == "1h":
        # [H1] Added 2026-07-29 after the cost/ATR analysis. The M15 path pays
        # ~$2.85 of spread+slippage against a ~$6 M15 ATR on gold -- ~45% of the
        # move -- and backtests at PF 0.50 once that real cost is charged. The
        # SAME strategy on H1 bars faces roughly half that ratio (ATR ~$12), and
        # on crypto H1 it is ~5-10%. Entry quality measured on MFE improves in
        # step: on gold, 21% of M15 entries ever reach +1R versus 68-73% on H1.
        #
        # Nothing about the signal changes here. HybridTrendPullback resamples
        # its trend filter from the entry timeframe internally (H1_BARS=4), so
        # on H1 bars the trend filter becomes H4 -- which is exactly what the
        # backtest that produced those numbers did (resample to 1h, same class).
        TIMEFRAME     = "1h"
        MAX_HOLD_BARS = 64     # 64 × 1h = 64h ≈ 2.7 days
        HISTORY_BARS  = 900    # 900 H1 bars ≈ 37 days; covers EMA200-on-H4 warm-up
        strategy_cls  = HybridTrendPullback
    elif tf in ("1d", "d", "daily"):
        # [DAILY] Added 2026-07-31 for gold_daily_breakout -- see
        # gold_daily_breakout_strategy.py for the full validation summary.
        # Structurally different from every branch above (Donchian breakout,
        # not H1_BARS trend-pullback), so --regime-filter/--fresh-maturity
        # don't apply here and are ignored below with a warning if passed.
        TIMEFRAME     = "1d"
        MAX_HOLD_BARS = 20     # 20 trading days cap; trailing stop does most exits
        HISTORY_BARS  = 500    # ~500 daily bars covers DONCH_WIN=80 + margin
        strategy_cls  = GoldDailyDonchianBreakout
    else:
        TIMEFRAME     = "15m"
        MAX_HOLD_BARS = 64     # 64 × 15min = 960min = 16h
        HISTORY_BARS  = 900
        strategy_cls  = HybridTrendPullback

    if tf in ("1d", "d", "daily") and (args.regime_filter or args.fresh_maturity > 0):
        print("[WARN] --regime-filter/--fresh-maturity are HybridTrendPullback-"
              "family options (H1_BARS trend array) and do not apply to "
              "GoldDailyDonchianBreakout (--timeframe 1d) -- ignoring them.",
              file=sys.stderr)
    elif args.regime_filter:
        # Swaps only the H1 trend array construction (adds frozen ADX-rising +
        # EMA-gap>1.2xATR conditions on top of the same ADX_MIN threshold) --
        # M15 entry/SL/TP/cost model untouched. See gold_regime_live_strategy.py.
        strategy_cls = RegimeFilteredHybridLive

    if tf not in ("1d", "d", "daily") and args.fresh_maturity > 0:
        # Compose with the fresh-trend filter on top of whichever base class
        # --regime-filter already selected, using the pre-built combo classes
        # rather than dynamic type() composition (simpler to reason about,
        # and both combos are already validated as named classes).
        strategy_cls = (FreshRegimeFilteredHybridLive if args.regime_filter
                        else FreshHybridTrendPullback)

    if args.strategy == "donchian":
        # Overrides whatever --timeframe/--regime-filter/--fresh-maturity
        # picked above -- Donchian breakout is a completely different signal
        # (single rolling channel, no H1_BARS trend bucketing at all), so
        # those HybridTrendPullback-family options don't compose with it.
        # See btc_donchian_breakout_strategy.py for the BTC H1 validation
        # this was built for; reuses the same generic mechanics as
        # gold_daily_breakout_strategy.py (--timeframe 1d path above).
        if args.regime_filter or args.fresh_maturity > 0:
            print("[WARN] --strategy donchian ignores --regime-filter/"
                  "--fresh-maturity/--adx-min (none of them apply to a "
                  "Donchian channel) -- proceeding without them.",
                  file=sys.stderr)
        strategy_cls = BTCH1DonchianBreakout

    if args.strategy == "amd":
        # AMD + liquidity sweep + TPO profile. Like 'donchian' this is a
        # standalone signal -- none of the HybridTrendPullback-family
        # options compose with it. See amd_sweep_tpo_strategy.py for the
        # (failing) validation record this was deployed against.
        if args.regime_filter or args.fresh_maturity > 0:
            print("[WARN] --strategy amd ignores --regime-filter/"
                  "--fresh-maturity/--adx-min -- proceeding without them.",
                  file=sys.stderr)
        strategy_cls = AMDSweepTPO

    _TOOL_MAP = {"tool_amd": ToolAMD, "tool_lqsweep": ToolLQSweep,
                 "tool_tpo": ToolTPOProfile}
    if args.strategy in _TOOL_MAP:
        # One ICT tool in isolation -- see ict_tools_strategies.py for the
        # per-tool OOS record. Standalone signals: none of the
        # HybridTrendPullback-family options compose with them.
        if args.regime_filter or args.fresh_maturity > 0:
            print("[WARN] --strategy %s ignores --regime-filter/"
                  "--fresh-maturity/--adx-min -- proceeding without them."
                  % args.strategy, file=sys.stderr)
        strategy_cls = _TOOL_MAP[args.strategy]
        if args.crypto_killzone:
            strategy_cls = type(strategy_cls.__name__ + "KZ", (strategy_cls,),
                                {"USE_CRYPTO_KZ": True})

    if args.strategy == "gold_mom_rsi":
        # Standalone signal -- see gold_momentum_rsi_strategy.py for the
        # (failing) validation record this was deployed against.
        if args.regime_filter or args.fresh_maturity > 0:
            print("[WARN] --strategy gold_mom_rsi ignores --regime-filter/"
                  "--fresh-maturity/--adx-min -- proceeding without them.",
                  file=sys.stderr)
        strategy_cls = GoldMomentumRSI

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

    # [BTC-HF] BTCUSDc pip_size/pip_value are NOT in ForexConfig's default dicts
    # (that dataclass only ships FX-pair + XAUUSD entries). SYMBOL here is
    # ALWAYS the post-.upper() canonical form ("BTCUSDC" — see
    # SYMBOL = args.symbol.upper() above), which never exact-matches the real
    # broker symbol "BTCUSDc" (mixed case). resolve_symbol()'s prefix-match
    # branch handles this case-insensitively and resolves self.bsym to the
    # real "BTCUSDc", after which connect() calls add_symbol_alias(SYMBOL,
    # self.bsym) — i.e. add_symbol_alias("BTCUSDC", "BTCUSDc") — which copies
    # this canonical entry across automatically, same pattern as gold's
    # XAUUSD -> XAUUSDc. Convention: 1 "pip" = $1 of BTC price movement
    # (matches the validated backtest — btc_walkforward.PIP_SIZE=1.0/
    # PIP_VALUE=0.01 — so live sizing reproduces the exact same
    # $-per-lot-per-$1-move math the walk-forward numbers were computed
    # with). Live pip VALUE still comes from get_pip_value_live() (real MT5
    # trade_tick_value), matching gold's already-fixed approach —
    # pip_value_usd_approx below is fallback-only.
    if SYMBOL == "BTCUSDC":
        cfg.pip_size["BTCUSDC"] = 1.0
        cfg.pip_value_usd_approx["BTCUSDC"] = 0.01
    elif SYMBOL == "ETHUSDC":
        # Same treatment as BTCUSDC: 1 "pip" = $1 of ETH price. pip_value here
        # is fallback only -- the live value comes from get_pip_value_live()
        # reading the real MT5 trade_tick_value, so an imprecise fallback
        # cannot mis-size a live order, it only affects dry-run.
        cfg.pip_size["ETHUSDC"] = 1.0
        cfg.pip_value_usd_approx["ETHUSDC"] = 0.01

    if not cfg.dry_run and not _cfg_has_credentials(cfg):
        print("[ERROR] แพ็กเกจ MetaTrader5 ใช้งานไม่ได้ — ต้องรันบน Windows ที่ติดตั้ง "
              "MetaTrader5 python package และเปิด MT5 terminal ไว้ "
              "หรือใช้ --dry-run เพื่อทดสอบโค้ดแบบ paper")
        sys.exit(1)

    bot_cls = GoldCWiderBot
    if args.manual_exit:
        from gold_manual_exit_bot import GoldManualExitBot
        bot_cls = GoldManualExitBot

    bot_cls(cfg,
            max_positions=args.max_positions,
            strategy_cls=strategy_cls,
            sl_atr=SL_ATR,
            tp_atr=TP_ATR,
            adx_min=ADX_MIN,
            touch_tolerance=args.touch_tolerance,
            fresh_maturity=args.fresh_maturity,
            donch_win=args.donch_win,
            breakout_margin_atr=args.breakout_margin_atr,
            symbol=SYMBOL,
            variant_tag=VARIANT_TAG,
            timeframe=TIMEFRAME,
            stop_file=STOP_FILE,
            state_file=STATE_FILE,
            log_file=LOG_FILE,
            fills_log_file=FILLS_LOG_FILE,
            lock_file=LOCK_FILE,
            equity_stop_file=EQUITY_STOP_FILE,
            heartbeat_file=HEARTBEAT_FILE,
            block_hours=args.block_hours,
            xasset_short_gate=args.xasset_short_gate).run()


if __name__ == "__main__":
    main()
