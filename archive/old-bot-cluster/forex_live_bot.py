#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 forex_live_bot.py  V1  —  ForexBot (ปรับจาก live_bot.py V3)
 Forex Day-Trade Bot  ·  MetaTrader 5  ·  EUR/USD, GBP/USD, USD/JPY ...
================================================================================

สถาปัตยกรรมเหมือน Sovereign Bot (crypto) ทุกชั้น:
  Layer 1  Regime Detection   — Hurst + ADX + Vol Ratio → เลือกกลยุทธ์
  Layer 2  Alpha Generation   — 5 กลยุทธ์ (เปิดเฉพาะตรง regime)
  Layer 3  Risk Management    — Kelly · Circuit Breaker · Partial TP · Trailing
  Layer 4  Execution Safety   — Position sync · Fill tracking · Duplicate guard

สิ่งที่เปลี่ยนจาก crypto bot:
  ✦ ccxt/Binance    →  MetaTrader5 (MT5)
  ✦ qty (coin)      →  lot_size (Forex lot)
  ✦ Fee + Funding   →  Spread + Swap (rollover)
  ✦ Weekend 24/7    →  Session filters + Pre-weekend close
  ✦ 8 coins         →  6 currency pairs หลัก

วิธีรัน:
  python3 forex_live_bot.py                          # dry-run (ไม่ต้องมี MT5)
  python3 forex_live_bot.py --live                   # live (ต้องมี MT5 + .env)
  python3 forex_live_bot.py --capital 5000 --pairs EURUSD,GBPUSD
================================================================================
"""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import logging
import math
import os
import sys
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forex_config import ForexConfig
from forex_indicators import Signal, add_indicators, build_data_dict
from forex_strategies import STRATEGIES
from forex_regime import (
    REGIME_SIZE_MULT, REGIME_STRATEGIES, Regime, RegimeDetector,
    is_market_open_utc, minutes_until_friday_close,
    is_rollover_window, get_active_sessions,
)
from forex_executor import MT5Connector, ForexOrderExecutor, _cfg_has_credentials
from forex_database import (
    init_db, save_trade as _db_save_sync,
    update_daily_pnl as _db_daily_sync,
    enqueue_write, stop_writer,
)

# Wrapper non-blocking สำหรับ DB writes
def db_save_trade(t, is_partial=False): enqueue_write(_db_save_sync, t, is_partial)
def db_update_pnl(s, p, n):            enqueue_write(_db_daily_sync, s, p, n)


# =============================================================================
# 1. CANDLE BUFFER
# =============================================================================
class CandleBuffer:
    def __init__(self, symbol: str, maxlen: int = 1000):
        # default 1000: รองรับ Hybrid H1-EMA200 (850 bars) + buffer
        self.symbol = symbol
        self._buf: deque = deque(maxlen=maxlen)
        self.d: Optional[dict] = None
        self.last_ts: int = 0
        self.regime = Regime.NEUTRAL
        self.regime_info: dict = {}

    def push(self, candles: list) -> int:
        added = 0
        for c in candles:
            ts = int(c[0])
            if ts > self.last_ts:
                self._buf.append(c)
                self.last_ts = ts
                added += 1
        if added > 0:
            self._rebuild()
        return added

    def push_synthetic(self, n: int = 500,
                        start_price: float = 1.1000,
                        bar_vol: float = 0.0008):
        """สร้างข้อมูลจำลองสำหรับ dry_run เมื่อไม่มี MT5.

        start_price: ราคาเริ่มต้น
          FX (EURUSD): ~1.1000
          Gold (XAUUSD): ~2000.0
          JPY (USDJPY): ~145.0
        bar_vol: ความผันผวนต่อแท่ง (fraction ของราคา)
          FX: 0.0008 (~0.08%)
          Gold: 0.0030 (~0.3%)
        """
        rng = np.random.default_rng(42)
        price = start_price
        now_ms = int(time.time() * 1000)
        tf_ms  = 15 * 60_000
        candles = []
        for i in range(n, 0, -1):
            ret   = rng.normal(0, bar_vol)
            price = max(price * (1 + ret), start_price * 0.1)
            wig   = abs(rng.normal(0, bar_vol * 0.6))
            ts    = now_ms - i * tf_ms
            o = price
            h = price * (1 + wig)
            l = price * (1 - wig)
            v = rng.uniform(100, 1000)
            candles.append([ts, o, h, l, price, v])
        self.push(candles)

    def _rebuild(self):
        if len(self._buf) < 30:
            self.d = None
            return
        df = pd.DataFrame(list(self._buf),
                          columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df = add_indicators(df)
        self.d = build_data_dict(df)

    @property
    def ready(self) -> bool:
        return self.d is not None and len(self._buf) >= 150

    @property
    def last_close(self) -> float:
        return float(self.d["c"][-1]) if self.d is not None and len(self.d["c"]) else float("nan")

    def __len__(self) -> int:
        return len(self._buf)


# =============================================================================
# 2. POSITION  (lot-based แทน qty-based)
# =============================================================================
@dataclass
class Position:
    symbol:   str
    strategy: str
    side:     str           # "long" | "short"
    entry:    float
    lot:      float         # ← เปลี่ยนจาก qty (coin) → lot (Forex)
    sl:       float
    tp:       float
    bars:     int = 0
    risk_cash:    float = 0.0
    entry_spread: float = 0.0  # ← แทน entry_fee (spread cost at open)
    entry_ts:  str = ""
    reason:    str = ""
    regime:    str = ""
    session:   str = ""
    pip_value: float = 10.0  # มูลค่า 1 pip ต่อ 1 lot ใน USD (ดึง live จาก MT5)
    # ── Partial TP ──
    partial_tp:     float = 0.0
    partial_closed: bool  = False
    lot_remaining:  float = 0.0   # ← lot_remaining แทน qty_remaining
    # ── Trailing Stop ──
    trail_sl:  float = 0.0
    highest:   float = 0.0
    lowest:    float = 0.0
    entry_atr: float = 0.0
    # ── OANDA trade ID ──
    trade_id: str = ""        # OANDA trade ID (ใช้ปิด/แก้ position)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Position":
        return Position(**{k: v for k, v in d.items()
                           if k in Position.__dataclass_fields__})


# =============================================================================
# 3. STRATEGY STATE  (เหมือน crypto — เปลี่ยน qty → lot)
# =============================================================================
class StrategyState:
    def __init__(self, strategy_class, equity: float, cfg: ForexConfig):
        self.strategy = strategy_class()
        self.initial_equity = equity
        self.equity = equity
        self.cfg = cfg
        self.positions: Dict[str, Position] = {}
        self.cooldowns: Dict[str, int] = {}
        self.trades: List[dict] = []
        self.halted: bool = False
        self.daily_high_equity: float = equity
        self.daily_date: str = ""
        self.circuit_tier: int = 0
        self.lockdown_until: float = 0.0

    @property
    def name(self) -> str:
        return self.strategy.short_name

    def gross_exposure_usd(self, last_prices: Dict[str, float],
                           pip_values: Dict[str, float]) -> float:
        total = 0.0
        for sym, pos in self.positions.items():
            px  = last_prices.get(sym, pos.entry)
            lot = pos.lot_remaining if pos.partial_closed else pos.lot
            pv  = pip_values.get(sym, pos.pip_value)
            pip_size = self.cfg.get_pip_size(sym)
            if not math.isnan(px) and pip_size > 0:
                notional = lot * px * self.cfg.contract_size
                total += notional
        return total

    def can_open(self, symbol: str) -> bool:
        if self.halted or self.circuit_tier >= 3:
            return False
        if symbol in self.positions or self.cooldowns.get(symbol, 0) > 0:
            return False
        return len(self.positions) < self.cfg.max_concurrent

    def tick_cooldowns(self, symbol: str):
        if self.cooldowns.get(symbol, 0) > 0:
            self.cooldowns[symbol] -= 1

    def update_daily_tracking(self):
        today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
        if today != self.daily_date:
            self.daily_date = today
            self.daily_high_equity = self.equity
            if self.circuit_tier < 3:
                self.circuit_tier = 0
        if self.equity > self.daily_high_equity:
            self.daily_high_equity = self.equity

    @property
    def daily_drawdown_pct(self) -> float:
        if self.daily_high_equity <= 0:
            return 0.0
        return (self.daily_high_equity - self.equity) / self.daily_high_equity * 100.0

    def to_dict(self) -> dict:
        return {
            "strategy": self.name, "equity": self.equity,
            "initial_equity": self.initial_equity, "halted": self.halted,
            "positions": {k: v.to_dict() for k, v in self.positions.items()},
            "cooldowns": self.cooldowns, "trades": self.trades[-200:],
            "daily_high_equity": self.daily_high_equity,
            "daily_date": self.daily_date, "circuit_tier": self.circuit_tier,
            "lockdown_until": self.lockdown_until,
        }

    def load_dict(self, d: dict):
        self.equity          = d.get("equity", self.equity)
        self.initial_equity  = d.get("initial_equity", self.initial_equity)
        self.halted          = d.get("halted", False)
        self.cooldowns       = d.get("cooldowns", {})
        self.trades          = d.get("trades", [])
        self.positions       = {k: Position.from_dict(v)
                                for k, v in d.get("positions", {}).items()}
        self.daily_high_equity = d.get("daily_high_equity", self.equity)
        self.daily_date      = d.get("daily_date", "")
        self.circuit_tier    = d.get("circuit_tier", 0)
        self.lockdown_until  = d.get("lockdown_until", 0.0)


# =============================================================================
# 4. FOREX BOT  —  The Main Engine
# =============================================================================
class ForexBot:
    def __init__(self, cfg: ForexConfig):
        self.cfg = cfg
        self.log = self._setup_logging()
        self.connector: Optional[MT5Connector] = None
        self.executor:  Optional[ForexOrderExecutor] = None
        self.buffers:  Dict[str, CandleBuffer] = {}
        self.states:   Dict[str, StrategyState] = {}
        self._last_prices: Dict[str, float] = {}
        self._pip_values:  Dict[str, float] = {}   # live pip values จาก MT5
        self._symbol_map:  Dict[str, str]   = {}   # configured → broker actual name
        self.regime_detector = RegimeDetector(
            hurst_window=cfg.hurst_window,
            adx_period=cfg.adx_period,
            vol_short=cfg.vol_short,
            vol_long=cfg.vol_long,
            h_trend=cfg.h_trend,
            h_mr=cfg.h_mr,
            adx_trend=cfg.adx_trend,
            adx_range=cfg.adx_range,
            vol_expand=cfg.vol_expand,
        )
        self._current_regime = Regime.NEUTRAL
        self._regime_info:   dict = {}

    # ─── Logging ─────────────────────────────────────────────────────────────

    def _setup_logging(self) -> logging.Logger:
        fmt = "%(asctime)s [%(levelname)s] %(message)s"
        handlers: list = [logging.StreamHandler(sys.stdout)]
        try:
            handlers.append(logging.FileHandler(self.cfg.log_file, encoding="utf-8"))
        except Exception:
            pass
        logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers)
        return logging.getLogger("ForexBot")

    # ─── Setup ───────────────────────────────────────────────────────────────

    def _broker_sym(self, sym: str) -> str:
        """คืน broker's actual symbol name (ถ้าต่างจาก configured)."""
        return self._symbol_map.get(sym, sym)

    def _connect(self):
        self.connector = MT5Connector(self.cfg, self.log)
        ok = self.connector.connect()
        if not ok and not self.cfg.dry_run:
            self.log.error("ไม่สามารถเชื่อมต่อ MT5 ผ่าน MetaApi — ออกจากโปรแกรม")
            sys.exit(1)
        self.executor = ForexOrderExecutor(self.connector, self.cfg, self.log)

        # ── Symbol resolver (สำคัญสำหรับทอง — ชื่อต่างกันแต่ละโบรก) ──
        self._resolve_symbols()

        # ── ดึง live pip values (ใช้ประมาณถ้า dry_run) ──
        for sym in self.cfg.symbols:
            bsym = self._broker_sym(sym)
            pv   = self.connector.get_pip_value_live(bsym)
            self._pip_values[sym] = pv

        mode = "DRY-RUN" if self.cfg.dry_run else f"LIVE ({self.cfg.leverage}x)"
        self.log.info(f"MT5 mode: {mode} | Pairs: {self.cfg.symbols}")
        self.log.info(f"Pip values: { {k: f'{v:.2f}' for k, v in self._pip_values.items()} }")

    def _resolve_symbols(self):
        """แปลง configured symbol → broker actual name และ build _symbol_map.

        จำเป็นสำหรับทอง: XAUUSD อาจชื่อ XAUUSD.m / GOLD / XAUUSDm
        ที่แต่ละโบรกเกอร์ ถ้าใช้ชื่อผิด order จะ error.
        """
        if self.cfg.dry_run:
            # dry_run ไม่มี connection จริง — ไม่ต้อง resolve
            self._symbol_map = {s: s for s in self.cfg.symbols}
            return

        self.log.info("Symbol resolver: กำลังตรวจสอบชื่อ symbol กับโบรกเกอร์ ...")
        for sym in self.cfg.symbols:
            resolved = self.connector.resolve_symbol(sym)
            self._symbol_map[sym] = resolved
            if resolved != sym:
                # เพิ่ม alias ใน config เพื่อให้ pip_size/spread/swap ทำงานถูก
                self.cfg.add_symbol_alias(sym, resolved)
                self.log.info(f"  ✓ {sym} → {resolved}  (alias added to config)")
            else:
                self.log.info(f"  ✓ {sym} — ชื่อตรงกัน")

    def _setup_strategies(self, total_equity: float):
        strats = list(STRATEGIES)
        if total_equity < 500:
            strats = strats[:2]   # ทุนน้อย → เปิดน้อย strategy
        elif total_equity < 2000:
            strats = strats[:3]

        cap = total_equity / len(strats)
        for Strat in strats:
            st = StrategyState(Strat, cap, self.cfg)
            self.states[st.name] = st
        self.log.info(f"Strategies ({len(self.states)}): {list(self.states.keys())} "
                      f"| {cap:,.2f}/ea")

    def _get_equity(self) -> float:
        if self.cfg.dry_run or self.connector is None:
            return self.cfg.total_capital_usd
        return self.connector.get_equity()

    # ─── Data ─────────────────────────────────────────────────────────────────

    def _fetch_closed_candles(self, symbol: str, limit: int = 10) -> list:
        """ดึง candles ที่ปิดแล้ว — ใช้ broker name (ไม่ใช่ configured name)."""
        candles = self.connector.fetch_ohlcv(symbol, self.cfg.timeframe, limit + 1)
        if not candles:
            return []
        now_ms = int(time.time() * 1000)
        tf_ms  = self.cfg.timeframe_ms
        return [c for c in candles if c[0] + tf_ms <= now_ms]

    @staticmethod
    def _synthetic_params(sym: str) -> tuple:
        """คืน (start_price, bar_vol) สำหรับ synthetic candles."""
        u = sym.upper()
        if "XAU" in u or "GOLD" in u:  return (2000.0, 0.0030)
        if "XAG" in u or "SILVER" in u: return (25.0,  0.0040)
        if "JPY" in u:                  return (145.0,  0.0006)
        if "GBP" in u and "USD" in u:   return (1.2700, 0.0009)
        return (1.1000, 0.0008)   # EURUSD-like default

    def _boot_buffers(self):
        self.log.info(f"Warm-up: {self.cfg.history_bars} bars/pair ...")
        for sym in self.cfg.symbols:
            bsym = self._broker_sym(sym)
            buf  = CandleBuffer(sym)
            if self.cfg.dry_run:
                sp, vol = self._synthetic_params(sym)
                buf.push_synthetic(self.cfg.history_bars, start_price=sp, bar_vol=vol)
                self.log.info(f"  {sym:8} {len(buf):>5} bars [synthetic p0={sp}]")
            else:
                candles = self._fetch_closed_candles(bsym,
                                                      limit=self.cfg.history_bars)
                if candles:
                    buf.push(candles)
                    self._last_prices[sym] = buf.last_close
                self.log.info(f"  {sym:8} {len(buf):>5} bars | ready={buf.ready}")
                time.sleep(0.1)
            self.buffers[sym] = buf

    # ─── Position Sizing (ใจกลางของการเปลี่ยนแปลง) ────────────────────────────

    def _calc_lot_size(self, symbol: str, risk_cash: float,
                       entry: float, sl: float) -> float:
        """แปลง risk_cash (USD) → lot size สำหรับ Forex.

        สูตร: lot = risk_cash / (sl_pips × pip_value_per_lot)

        ตัวอย่าง EURUSD:
          risk = $75, SL = 20 pips, pip_value = $10/lot
          lot = 75 / (20 × 10) = 0.375 lot ≈ 0.38 lot
        """
        pip_size  = self.cfg.get_pip_size(symbol)
        pip_value = self._pip_values.get(symbol, self.cfg.get_pip_value(symbol))

        sl_pips = abs(entry - sl) / pip_size
        if sl_pips <= 0 or pip_value <= 0:
            return 0.0

        lot = risk_cash / (sl_pips * pip_value)
        return lot

    # ─── PnL Calculation (pip-based แทน qty-based) ─────────────────────────────

    def _calc_pnl(self, pos: Position, exit_px: float,
                  lot: float = 0.0) -> Tuple[float, float]:
        """PnL ใน USD สำหรับ Forex position.

        gross_pips = (exit - entry) / pip_size × direction
        gross_usd  = gross_pips × pip_value × lot

        cost = spread_cost(ปิด) + swap(ข้ามคืน)
        (spread ตอนเปิดถูกบันทึกแยกใน entry_spread แล้ว)
        """
        if lot <= 0:
            lot = pos.lot_remaining if pos.partial_closed else pos.lot

        pip_size  = self.cfg.get_pip_size(pos.symbol)
        pip_value = self._pip_values.get(pos.symbol, pos.pip_value)
        direction = 1 if pos.side == "long" else -1

        gross_pips = (exit_px - pos.entry) / pip_size * direction
        gross_usd  = gross_pips * pip_value * lot

        # Spread เมื่อปิด (entry spread บันทึกแยกแล้ว)
        close_spread = self.cfg.spread_cost_usd(pos.symbol, lot)

        # Swap (ข้ามคืน) — คิดตามวันที่ถือไม้จริง
        hold_days = pos.bars * self.cfg.timeframe_minutes / 1440.0
        swap_cost = self.cfg.swap_cost_usd(pos.symbol, pos.side, lot, hold_days)

        cost = close_spread + swap_cost
        pnl  = gross_usd - cost - (pos.entry_spread * (lot / pos.lot) if pos.lot > 0 else 0)

        return pnl, cost

    def _pips_pnl(self, pos: Position, exit_px: float, lot: float = 0) -> float:
        """คำนวณ PnL ใน pips (สำหรับ log/DB)."""
        if lot <= 0:
            lot = pos.lot_remaining if pos.partial_closed else pos.lot
        pip_size  = self.cfg.get_pip_size(pos.symbol)
        direction = 1 if pos.side == "long" else -1
        return (exit_px - pos.entry) / pip_size * direction

    # ─── Fractional Kelly (เหมือน crypto) ──────────────────────────────────────

    def _kelly_risk_pct(self, st: StrategyState) -> float:
        recent = st.trades[-50:]
        if len(recent) < self.cfg.kelly_min_trades:
            return self.cfg.risk_per_trade_pct

        n = len(recent)
        decay = 0.95
        weights = np.array([decay ** (n - 1 - j) for j in range(n)])
        weights /= weights.sum()

        pnls = np.array([t.get("pnl", 0) for t in recent])
        w_mask = pnls > 0;  l_mask = pnls <= 0

        if not l_mask.any():
            return self.cfg.risk_per_trade_pct

        p     = float(weights[w_mask].sum())
        avg_w = float(np.average(pnls[w_mask], weights=weights[w_mask])) if w_mask.any() else 0.0
        avg_l = abs(float(np.average(pnls[l_mask], weights=weights[l_mask])))

        if avg_l <= 0:
            return self.cfg.risk_per_trade_pct

        b     = avg_w / avg_l
        kelly = (p * (b + 1.0) - 1.0) / b if b > 0 else 0.0
        pct   = kelly * self.cfg.kelly_fraction * 100.0
        return max(self.cfg.kelly_min_risk_pct, min(pct, self.cfg.kelly_max_risk_pct))

    # ─── Circuit Breaker ──────────────────────────────────────────────────────

    def _update_circuit_breaker(self, st: StrategyState):
        st.update_daily_tracking()
        if st.circuit_tier >= 3 and time.time() < st.lockdown_until:
            return
        dd = st.daily_drawdown_pct
        if dd >= self.cfg.cb_tier3_pct:
            if st.circuit_tier < 3:
                st.circuit_tier = 3
                st.lockdown_until = time.time() + 86400
                self.log.warning(f"[CB] {st.name} TIER3 LOCK — dd={dd:.1f}%")
        elif dd >= self.cfg.cb_tier2_pct:
            if st.circuit_tier < 2:
                st.circuit_tier = 2
                self.log.warning(f"[CB] {st.name} TIER2 MR-only — dd={dd:.1f}%")
        elif dd >= self.cfg.cb_tier1_pct:
            if st.circuit_tier < 1:
                st.circuit_tier = 1
                self.log.warning(f"[CB] {st.name} TIER1 reduce — dd={dd:.1f}%")
        if st.circuit_tier >= 3 and time.time() >= st.lockdown_until:
            st.circuit_tier = 0
            self.log.info(f"[CB] {st.name} unlocked")

    def _is_strategy_allowed(self, st: StrategyState, regime: Regime) -> bool:
        if st.circuit_tier >= 3:
            return False
        if st.circuit_tier >= 2 and st.name != "Mean-Reversion":
            return False
        return st.name in REGIME_STRATEGIES.get(regime, set())

    # ─── Session Filter (ใหม่ — ไม่มีใน crypto bot) ────────────────────────────

    def _should_trade_now(self) -> bool:
        """ตรวจสอบว่าควรเทรดในเวลานี้หรือไม่.

        ลำดับการตรวจ:
          1. STOP file (kill switch ฉุกเฉิน)
          2. ตลาดเปิด/ปิด
          3. Rollover window
          4. News blackout
          5. Active session
        """
        # 1. STOP file — สร้างไฟล์นี้เพื่อหยุดเข้าไม้ใหม่ทันที
        stop_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "STOP")
        if os.path.exists(stop_path):
            return False

        # 2. ตลาด
        if not is_market_open_utc():
            return False

        # 3. Rollover window (21:45–22:15 UTC): spread กว้างมาก
        if self.cfg.skip_rollover_window and is_rollover_window():
            return False

        # 4. News blackout — [("13:25","13:35"), ...] ใน UTC
        if self.cfg.news_blackout:
            now_hm = datetime.datetime.utcnow().strftime("%H:%M")
            for start, end in self.cfg.news_blackout:
                if start <= now_hm <= end:
                    self.log.debug(f"News blackout: {start}–{end} — skip")
                    return False

        # 5. Session
        if not self.cfg.require_active_session:
            return True

        active = get_active_sessions()
        if self.cfg.trade_london  and "london"  in active: return True
        if self.cfg.trade_newyork and "newyork" in active: return True
        if self.cfg.trade_tokyo   and "tokyo"   in active: return True
        if self.cfg.trade_sydney  and "sydney"  in active: return True
        return False

    def _should_close_for_weekend(self) -> bool:
        """ปิด position ทั้งหมดก่อนวันหยุดสุดสัปดาห์."""
        mins = minutes_until_friday_close()
        return 0 <= mins <= self.cfg.close_before_weekend_mins

    def _near_weekend_size_mult(self) -> float:
        """ลดขนาดไม้ใกล้วันปิดสัปดาห์."""
        mins = minutes_until_friday_close()
        if 0 <= mins <= self.cfg.close_before_weekend_mins * 2:
            return self.cfg.near_close_size_mult
        return 1.0

    # ─── Open Position ────────────────────────────────────────────────────────

    def _do_open(self, st: StrategyState, sym: str, sig: Signal,
                 d: dict, i: int, regime: Regime,
                 sessions: list) -> Optional[Position]:
        long   = sig.action == "BUY"
        strat  = st.strategy
        bsym   = self._broker_sym(sym)      # ชื่อจริงที่โบรกเกอร์ใช้
        cur_px = float(d["c"][i])
        atr    = float(d["atr"][i])

        if math.isnan(cur_px) or math.isnan(atr) or atr <= 0 or cur_px <= 0:
            return None

        # ── ATR-based spread filter (instrument-agnostic) ──────────────────
        # ใช้ ATR แทน pip เพื่อรองรับทอง (1 pip ทอง ≠ 1 pip FX ในแง่ความสำคัญ)
        # ดึง live spread จาก MT5; ถ้า dry_run ใช้ config fallback
        live_spread_px = 0.0
        if not self.cfg.dry_run and self.connector._connection is not None:
            bid, ask = self.connector.get_current_price(bsym)
            if bid > 0 and ask > bid:
                live_spread_px = ask - bid
                spread_ratio   = live_spread_px / atr
                if spread_ratio > self.cfg.spread_filter_atr_ratio:
                    self.log.debug(
                        f"  [SKIP] {sym} spread={live_spread_px:.{self.cfg.get_price_decimals(sym)}f}"
                        f" / ATR={atr:.{self.cfg.get_price_decimals(sym)}f}"
                        f" = {spread_ratio:.1%} > {self.cfg.spread_filter_atr_ratio:.0%}")
                    return None

        # ── ราคาเข้าจริง (entry) รวม spread ──
        pip_size = self.cfg.get_pip_size(sym)
        if live_spread_px > 0:
            spread_px = live_spread_px   # live
        else:
            spread_px = self.cfg.spread_pips.get(sym, 1.0) * pip_size  # dry_run estimate
        entry = cur_px + spread_px / 2.0 if long else cur_px - spread_px / 2.0

        sl = entry - atr * strat.sl_atr if long else entry + atr * strat.sl_atr
        tp = entry + atr * strat.tp_atr if long else entry - atr * strat.tp_atr

        if abs(entry - sl) <= 0:
            return None

        # ── Kelly + Regime + CB + Near-weekend size ──
        risk_pct   = self._kelly_risk_pct(st)
        size_mult  = REGIME_SIZE_MULT.get(regime, 1.0)
        size_mult *= self._near_weekend_size_mult()
        if st.circuit_tier == 1:
            size_mult *= 0.5

        risk_cash = st.equity * risk_pct / 100.0 * size_mult
        lot = self._calc_lot_size(sym, risk_cash, entry, sl)

        if lot < self.cfg.min_lot:
            return None

        # ── Exposure cap ──
        pip_value = self._pip_values.get(sym, self.cfg.get_pip_value(sym))
        notional  = lot * cur_px * self.cfg.contract_size
        gross     = st.gross_exposure_usd(self._last_prices, self._pip_values)
        max_notional = self.cfg.per_pair_cap_frac * st.equity * self.cfg.leverage
        if notional > max_notional:
            lot = max_notional / (cur_px * self.cfg.contract_size)
            lot = max(self.cfg.min_lot, lot)

        # ── Spread cost (entry) ──
        entry_spread = self.cfg.spread_cost_usd(sym, lot)

        # ── Partial TP + Trail targets ──
        partial_tp = (entry + atr * self.cfg.partial_tp_atr if long
                      else entry - atr * self.cfg.partial_tp_atr)
        trail_sl   = (entry - atr * self.cfg.trail_atr_mult if long
                      else entry + atr * self.cfg.trail_atr_mult)

        # ── ส่ง order — ใช้ broker name สำหรับ MT5 API ──
        order = self.executor.open_position(
            bsym, "long" if long else "short", lot, sl, tp,
            comment=f"{st.name[:8]}-{regime.value[:3]}")
        if not order:
            return None

        # อัปเดต fill price จาก MT5 (live mode)
        if order.get("fill_price", 0.0) > 0 and not self.cfg.dry_run:
            entry = order["fill_price"]
        trade_id = order.get("trade_id", "")

        regime_name = regime.value
        pos = Position(
            symbol=sym, strategy=st.name,
            side="long" if long else "short",
            entry=entry, lot=lot, sl=sl, tp=tp, bars=0,
            risk_cash=risk_cash, entry_spread=entry_spread,
            entry_ts=str(d["ts"][i]), reason=sig.reason,
            regime=regime_name, session=",".join(sessions),
            pip_value=pip_value,
            partial_tp=partial_tp, partial_closed=False, lot_remaining=lot,
            trail_sl=trail_sl, highest=entry, lowest=entry,
            entry_atr=atr, trade_id=trade_id,
        )
        st.positions[sym] = pos

        kelly_tag  = (f"K={risk_pct:.2f}%"
                      if len(st.trades) >= self.cfg.kelly_min_trades else "fix")
        spread_tag = f"spread={self.cfg.spread_pips.get(sym, 1.0):.1f}p"
        self.log.info(
            f"[{st.name:<16}] OPEN  {'LONG' if long else 'SHORT':5} {sym:8} "
            f"@ {entry:.5f}  lot={lot:.2f}  SL={sl:.5f}  TP1={partial_tp:.5f}  "
            f"TP={tp:.5f}  Trail={trail_sl:.5f}  "
            f"[{sig.reason}] {regime_name} {kelly_tag} {spread_tag}")
        return pos

    # ─── Close Position ───────────────────────────────────────────────────────

    def _do_close(self, st: StrategyState, sym: str, pos: Position,
                  exit_px: float, reason: str, cur_ts: str,
                  is_partial: bool = False):
        lot = pos.lot_remaining if pos.partial_closed else pos.lot
        if is_partial:
            lot = pos.lot * self.cfg.partial_tp_frac

        pnl, cost = self._calc_pnl(pos, exit_px, lot)
        pips      = self._pips_pnl(pos, exit_px, lot)
        st.equity += pnl

        trade = {
            "strategy":    st.name,
            "pair":        sym,
            "side":        pos.side,
            "entry":       pos.entry,
            "exit":        exit_px,
            "lot_size":    lot,
            "pnl":         pnl,
            "pips_pnl":    pips,
            "spread_cost": pos.entry_spread + self.cfg.spread_cost_usd(sym, lot),
            "swap_cost":   cost,
            "bars":        pos.bars,
            "reason":      reason,
            "entry_ts":    pos.entry_ts,
            "exit_ts":     cur_ts,
            "regime":      pos.regime,
            "session":     pos.session,
        }
        st.trades.append(trade)

        if is_partial:
            pos.partial_closed = True
            pos.lot_remaining  = pos.lot - lot
            if self.cfg.move_sl_to_breakeven:
                pos.sl = pos.entry
            self.log.info(
                f"[{st.name:<16}] PARTIAL TP {sym:8} @ {exit_px:.5f}  "
                f"lot={lot:.2f}  remain={pos.lot_remaining:.2f}  "
                f"PnL={pnl:+,.2f}  ({pips:+.1f}p)  SL→BE")
        else:
            del st.positions[sym]
            st.cooldowns[sym] = self.cfg.cooldown_bars
            self.log.info(
                f"[{st.name:<16}] CLOSE {pos.side.upper():5} {sym:8} "
                f"@ {exit_px:.5f}  lot={lot:.2f}  "
                f"PnL={pnl:+,.2f}  ({pips:+.1f}p)  "
                f"reason={reason:14}  eq={st.equity:,.2f}")

        self._log_trade(trade, is_partial)

        if not self.cfg.dry_run and not is_partial:
            self.executor.close_position(
                self._broker_sym(sym), pos.side, lot, pos.trade_id)

        db_update_pnl(st.name, pnl, 1)

    # ─── Trailing Stop ────────────────────────────────────────────────────────

    def _update_trailing_stop(self, pos: Position, hi: float, lo: float,
                               trail_mult: float = 0.0,
                               trail_act: float = 0.0):
        """Chandelier-style trailing stop.

        trail_mult / trail_act: ถ้าส่งมา (>0) ใช้ค่าจาก strategy
        ถ้าไม่ส่ง ใช้ค่าจาก config (default)
        """
        atr = pos.entry_atr
        if atr <= 0:
            return

        t_mult = trail_mult if trail_mult > 0 else self.cfg.trail_atr_mult
        t_act  = trail_act  if trail_act  > 0 else self.cfg.trail_activation_atr
        act_dist = atr * t_act

        bsym = self._broker_sym(pos.symbol)

        if pos.side == "long":
            if hi > pos.highest:
                pos.highest = hi
            if pos.highest - pos.entry >= act_dist:
                new_trail = pos.highest - atr * t_mult
                if new_trail > pos.trail_sl:
                    pos.trail_sl = new_trail
                    if not self.cfg.dry_run and pos.trade_id:
                        self.executor.modify_sl(bsym, pos.trade_id, new_trail)
        else:
            if lo < pos.lowest or pos.lowest <= 0:
                pos.lowest = lo
            if pos.entry - pos.lowest >= act_dist:
                new_trail = pos.lowest + atr * t_mult
                if pos.trail_sl <= 0 or new_trail < pos.trail_sl:
                    pos.trail_sl = new_trail
                    if not self.cfg.dry_run and pos.trade_id:
                        self.executor.modify_sl(bsym, pos.trade_id, new_trail)

    # ─── Per-Bar Processing ───────────────────────────────────────────────────

    def _process_bar(self, sym: str, sessions: list):
        buf = self.buffers.get(sym)
        if buf is None or not buf.ready or buf.d is None:
            return
        d = buf.d
        i = len(d["c"]) - 1
        if i < 1:
            return

        cur_ts = str(d["ts"][i])
        hi, lo, cl = float(d["h"][i]), float(d["l"][i]), float(d["c"][i])
        self._last_prices[sym] = cl

        # ── Regime ──
        regime, info = self.regime_detector.detect(d["h"], d["l"], d["c"])
        buf.regime = regime; buf.regime_info = info
        self._current_regime = regime; self._regime_info = info

        for st in self.states.values():
            self._update_circuit_breaker(st)

            # ── 1) Manage existing position ──
            if sym in st.positions:
                pos = st.positions[sym]
                pos.bars += 1
                # ใช้ trail params จาก strategy (ถ้ามี) มิฉะนั้นใช้ config default
                t_mult = getattr(st.strategy, "trail_atr_mult",       0.0)
                t_act  = getattr(st.strategy, "trail_activation_atr",  0.0)
                self._update_trailing_stop(pos, hi, lo, t_mult, t_act)

                # Partial TP check
                if not pos.partial_closed and pos.partial_tp > 0:
                    hit = ((pos.side == "long"  and hi >= pos.partial_tp) or
                           (pos.side == "short" and lo <= pos.partial_tp))
                    if hit:
                        self._do_close(st, sym, pos, pos.partial_tp,
                                       "Partial-TP", cur_ts, is_partial=True)
                        if sym not in st.positions:
                            continue

                # SL / TP / Trail / Timeout
                exit_px, reason = None, None
                if pos.side == "long":
                    if lo <= pos.sl:
                        be = " (BE)" if pos.partial_closed and pos.sl >= pos.entry else ""
                        exit_px, reason = pos.sl, f"Stop-loss{be}"
                    elif pos.trail_sl > pos.sl and lo <= pos.trail_sl:
                        exit_px, reason = pos.trail_sl, "Trail-SL"
                    elif hi >= pos.tp:
                        exit_px, reason = pos.tp, "Take-profit"
                else:
                    if hi >= pos.sl:
                        be = " (BE)" if pos.partial_closed and pos.sl <= pos.entry else ""
                        exit_px, reason = pos.sl, f"Stop-loss{be}"
                    elif pos.trail_sl > 0 and hi >= pos.trail_sl:
                        exit_px, reason = pos.trail_sl, "Trail-SL"
                    elif lo <= pos.tp:
                        exit_px, reason = pos.tp, "Take-profit"

                if exit_px is None and pos.bars >= self.cfg.max_hold_bars:
                    exit_px, reason = cl, "Timeout"

                if exit_px is not None:
                    self._do_close(st, sym, pos, exit_px, reason, cur_ts)

            # ── 2) Kill switch ──
            if not st.halted:
                floor = st.initial_equity * self.cfg.stop_equity_frac
                if st.equity <= floor:
                    st.halted = True
                    if sym in st.positions:
                        self._do_close(st, sym, st.positions[sym], cl,
                                       "Kill-switch", cur_ts)
                    self.log.warning(f"[{st.name}] HALTED eq={st.equity:,.2f}")

            # ── 3) CB tier 3: force close ──
            if st.circuit_tier >= 3 and sym in st.positions:
                self._do_close(st, sym, st.positions[sym], cl, "CB-lockdown", cur_ts)

            # ── 4) New signal (session + regime filtered) ──
            can_trade = (st.can_open(sym)
                         and self._is_strategy_allowed(st, regime)
                         and self._should_trade_now())
            if can_trade:
                sig = st.strategy.signal(d, i)
                if sig.action in ("BUY", "SELL"):
                    self._do_open(st, sym, sig, d, i, regime, sessions)

            # ── 5) Cooldown ──
            st.tick_cooldowns(sym)

    # ─── Pre-weekend Close ────────────────────────────────────────────────────

    def _close_all_for_weekend(self):
        """ปิด position ทั้งหมดก่อนตลาดปิดวันศุกร์."""
        self.log.info("[WEEKEND] ปิด positions ทั้งหมดก่อนตลาดปิด ...")
        buf = next(iter(self.buffers.values()), None)
        cur_ts = datetime.datetime.utcnow().isoformat()
        for st in self.states.values():
            for sym in list(st.positions.keys()):
                pos = st.positions[sym]
                cl  = self._last_prices.get(sym, pos.entry)
                self._do_close(st, sym, pos, cl, "Weekend-close", cur_ts)

    # ─── State persistence ────────────────────────────────────────────────────

    def _save_state(self):
        data = {name: st.to_dict() for name, st in self.states.items()}
        data["_meta"] = {
            "regime": str(self._current_regime),
            "regime_info": self._regime_info,
            "saved_at": datetime.datetime.utcnow().isoformat(),
        }
        try:
            with open(self.cfg.state_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
        except Exception as exc:
            self.log.error(f"save_state: {exc}")

    def _load_state(self) -> bool:
        if not os.path.exists(self.cfg.state_file):
            return False
        try:
            with open(self.cfg.state_file, encoding="utf-8") as f:
                data = json.load(f)
            for name, st in self.states.items():
                if name in data:
                    st.load_dict(data[name])
            self.log.info(f"State loaded: {self.cfg.state_file}")
            return True
        except Exception as exc:
            self.log.warning(f"load_state: {exc}")
            return False

    # ─── Trade Logger ─────────────────────────────────────────────────────────

    def _log_trade(self, trade: dict, is_partial: bool = False):
        db_save_trade(trade, is_partial)
        write_header = not os.path.exists(self.cfg.trades_csv)
        try:
            with open(self.cfg.trades_csv, "a", newline="", encoding="utf-8") as f:
                cols = ["exit_ts", "strategy", "pair", "side", "entry", "exit",
                        "lot_size", "pnl", "pips_pnl", "spread_cost", "swap_cost",
                        "bars", "reason", "regime", "session"]
                w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
                if write_header:
                    w.writeheader()
                w.writerow({
                    "exit_ts":     trade.get("exit_ts", ""),
                    "strategy":    trade.get("strategy", ""),
                    "pair":        trade.get("pair", ""),
                    "side":        trade.get("side", ""),
                    "entry":       f"{trade.get('entry', 0):.5f}",
                    "exit":        f"{trade.get('exit', 0):.5f}",
                    "lot_size":    f"{trade.get('lot_size', 0):.2f}",
                    "pnl":         f"{trade.get('pnl', 0):.2f}",
                    "pips_pnl":    f"{trade.get('pips_pnl', 0):.1f}",
                    "spread_cost": f"{trade.get('spread_cost', 0):.2f}",
                    "swap_cost":   f"{trade.get('swap_cost', 0):.2f}",
                    "bars":        trade.get("bars", 0),
                    "reason":      trade.get("reason", ""),
                    "regime":      trade.get("regime", ""),
                    "session":     trade.get("session", ""),
                })
        except Exception:
            pass

    # ─── Status ───────────────────────────────────────────────────────────────

    def _log_status(self, sessions: list):
        total_eq = sum(st.equity for st in self.states.values())
        init_eq  = self.cfg.total_capital_usd
        ret      = (total_eq / init_eq - 1) * 100 if init_eq > 0 else 0
        n_pos    = sum(len(st.positions) for st in self.states.values())
        n_trades = sum(len(st.trades)   for st in self.states.values())

        rn = self._current_regime.value
        h  = self._regime_info.get("hurst", "?")
        a  = self._regime_info.get("adx", "?")
        vr = self._regime_info.get("vol_ratio", "?")
        ses_tag = f"[{','.join(sessions) or 'NO-SESSION'}]"
        mkt_tag = "" if is_market_open_utc() else " [MARKET-CLOSED]"
        wknd_tag = " [PRE-WEEKEND]" if self._should_close_for_weekend() else ""

        self.log.info(
            f"== STATUS ==  eq={total_eq:,.2f} ({ret:+.1f}%)  pos={n_pos}  "
            f"trades={n_trades}  regime={rn}(H={h} ADX={a} VR={vr})"
            f"{ses_tag}{mkt_tag}{wknd_tag}")
        for st in self.states.values():
            cb = f" [CB-T{st.circuit_tier}]" if st.circuit_tier > 0 else ""
            ht = " [HALT]" if st.halted else ""
            k  = self._kelly_risk_pct(st)
            ps = list(st.positions.keys()) or ["-"]
            self.log.info(
                f"  {st.name:<16} eq={st.equity:,.2f} dd={st.daily_drawdown_pct:.1f}% "
                f"K={k:.2f}% pos={ps} n={len(st.trades)}{cb}{ht}")

    # ─── Main Loop ────────────────────────────────────────────────────────────

    def run(self):
        banner = "DRY-RUN" if self.cfg.dry_run else f"LIVE ({self.cfg.leverage}x)"
        self.log.info("=" * 70)
        self.log.info(f" ForexBot V1  |  {banner}")
        self.log.info("=" * 70)

        init_db(self.cfg.db_file)
        self._connect()
        balance = self.connector.get_balance()
        if self.cfg.total_capital_usd <= 0:
            self.cfg.total_capital_usd = balance
        self._setup_strategies(self.cfg.total_capital_usd)
        self._boot_buffers()
        self._load_state()

        tf_ms = self.cfg.timeframe_ms
        last_status_ms = 0
        weekend_closed = False  # ป้องกันปิด position ซ้ำ
        self.log.info(f"Polling every {self.cfg.poll_interval_sec}s ...")

        while True:
            try:
                now_ms  = int(time.time() * 1000)
                sessions = get_active_sessions()
                market_open = is_market_open_utc()

                # ── ตลาดปิด: ไม่ทำอะไร ──
                if not market_open:
                    if not weekend_closed:
                        self.log.info("[MARKET] ตลาดปิด (weekend) — พักรอ ...")
                        weekend_closed = True
                    time.sleep(self.cfg.poll_interval_sec * 10)
                    continue

                weekend_closed = False

                # ── ใกล้ปิดสัปดาห์: ปิด positions ทั้งหมด ──
                if self._should_close_for_weekend():
                    self._close_all_for_weekend()
                    self._save_state()
                    time.sleep(self.cfg.poll_interval_sec * 5)
                    continue

                # ── ดึงข้อมูลและประมวลผล ──
                found_new = False
                for sym in self.cfg.symbols:
                    if self.cfg.dry_run:
                        # dry_run: เพิ่ม candle จำลอง 1 แท่ง
                        buf = self.buffers.setdefault(sym, CandleBuffer(sym))
                        if not buf.ready:
                            buf.push_synthetic(200)
                        else:
                            rng = np.random.default_rng()
                            last = buf.last_close
                            ret  = rng.normal(0, 0.0006)
                            price = max(last * (1 + ret), 0.5)
                            wig  = abs(rng.normal(0, 0.0004))
                            ts   = now_ms
                            buf.push([[ts, price, price*(1+wig),
                                       price*(1-wig), price, 100]])
                        found_new = True
                    else:
                        bsym = self._broker_sym(sym)
                        candles = self._fetch_closed_candles(bsym, limit=5)
                        if not candles:
                            continue
                        buf = self.buffers.setdefault(sym, CandleBuffer(sym))
                        added = buf.push(candles)
                        if added > 0:
                            found_new = True
                        time.sleep(0.1)

                    if found_new:
                        self._process_bar(sym, sessions)

                if found_new:
                    self._save_state()

                if now_ms - last_status_ms >= tf_ms:
                    self._log_status(sessions)
                    last_status_ms = now_ms

                time.sleep(self.cfg.poll_interval_sec)

            except KeyboardInterrupt:
                self.log.info("Shutting down (Ctrl+C)")
                self._save_state()
                self._log_status(get_active_sessions())
                stop_writer()
                # OANDA ใช้ REST — ไม่ต้อง disconnect
                break
            except Exception as exc:
                self.log.error(f"Loop error: {exc}", exc_info=True)
                time.sleep(60)


# =============================================================================
# 5. MAIN
# =============================================================================
def main():
    ap = argparse.ArgumentParser(
        description="ForexBot V1 — MT5/MetaApi  |  รองรับ FX + Gold (XAUUSD)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
ตัวอย่างการรัน:
  python3 forex_live_bot.py                              # dry-run EURUSD
  python3 forex_live_bot.py --pairs XAUUSD --risk 0.3 --magic 555003
  python3 forex_live_bot.py --pairs EURUSD,GBPUSD --live
  touch STOP   # หยุดเข้าไม้ใหม่ทันที (ไม่ปิด position ที่มีอยู่)
""")
    ap.add_argument("--live",      action="store_true", help="Live trading (ต้องมี MT5)")
    ap.add_argument("--capital",   type=float, default=0.0,  help="Capital USD (0=ดึงจาก MT5)")
    ap.add_argument("--pairs",     type=str,   default="",   help="Pairs (comma-sep)")
    ap.add_argument("--timeframe", type=str,   default="",   help="e.g. 15m, 1h, 4h")
    ap.add_argument("--leverage",  type=int,   default=0,    help="Leverage 1-500")
    ap.add_argument("--risk",      type=float, default=0.0,  help="Risk %% per trade (default 0.75; Gold: 0.3)")
    ap.add_argument("--magic",     type=int,   default=0,    help="MT5 magic number (Gold: 555003)")
    args = ap.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    cfg = ForexConfig()
    cfg.dry_run = not args.live

    if args.capital > 0:
        cfg.total_capital_usd = args.capital
    if args.pairs:
        cfg.symbols = [p.strip().upper() for p in args.pairs.split(",") if p.strip()]
    if args.timeframe:
        cfg.timeframe = args.timeframe.strip()
    if args.leverage > 0:
        cfg.leverage = min(args.leverage, 500)
    if args.risk > 0:
        cfg.risk_per_trade_pct = args.risk
    if args.magic > 0:
        cfg.magic_number = args.magic

    # ── MetaApi credentials จาก .env ──
    cfg.metaapi_token      = os.environ.get("METAAPI_TOKEN",      "")
    cfg.metaapi_account_id = os.environ.get("METAAPI_ACCOUNT_ID", "")

    if not cfg.dry_run and not cfg.metaapi_token:
        print("[ERROR] METAAPI_TOKEN required for --live mode (ตั้งใน .env)")
        sys.exit(1)

    # ── XAUUSD auto-defaults (ถ้าไม่ได้ set ผ่าน CLI) ──
    if cfg.symbols == ["XAUUSD"] or (len(cfg.symbols) == 1 and
                                      "XAU" in cfg.symbols[0].upper()):
        if args.risk == 0:
            cfg.risk_per_trade_pct = 0.30   # Gold: ผันผวนสูงกว่า → risk ต่ำกว่า
        if args.magic == 0:
            cfg.magic_number = 555003       # แยก magic จาก EURUSD instance
        cfg.max_concurrent = 2             # Gold: ไม่จำเป็นต้อง multi-position
        cfg.log_file   = "forex_gold.log"
        cfg.trades_csv = "forex_gold_trades.csv"
        cfg.state_file = "forex_gold_state.json"
        cfg.db_file    = "forex_gold.db"

    # ── Micro-capital auto-config ──
    if 0 < cfg.total_capital_usd <= 500:
        cfg.max_concurrent     = 2
        cfg.risk_per_trade_pct = max(cfg.risk_per_trade_pct, 1.0)
        if not args.pairs:
            cfg.symbols = ["EURUSD", "GBPUSD", "USDJPY"]

    ForexBot(cfg).run()


if __name__ == "__main__":
    main()
