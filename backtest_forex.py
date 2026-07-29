#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 backtest_forex.py  —  Hybrid Trend-Pullback Strategy Backtest Harness
================================================================================
 Import logic โดยตรงจากบอท (ไม่เขียนซ้ำ):
   HybridTrendPullback.signal()  ←  forex_hybrid_strategy.py
   add_indicators / build_data_dict  ←  forex_indicators.py
   ForexConfig  ←  forex_config.py

 No-look-ahead rule (ห้าม cheat):
   สัญญาณ = แท่ง i ปิด  →  entry = open ของแท่ง i+1
   ไม่ใช้ข้อมูลแท่งปัจจุบันเพื่อตัดสินใจ entry ของแท่งเดียวกัน

 Cost model (ทำให้ backtest ไม่โกหก):
   entry cost = ½ spread  (ฝั่งเดียว)
   exit cost  = ½ spread + commission_per_lot
   ใส่ real spread/commission ของโบรกคุณ ไม่งั้นผลจะดีเกินจริง

 Conservative rule:
   แท่งเดียวแตะทั้ง SL และ TP  →  สมมติ SL ก่อน (worst-case)

 วิธีรัน:
   python3 backtest_forex.py                               # synthetic XAUUSD
   python3 backtest_forex.py --symbol XAUUSD --years 5    # ต้องมี MetaApi
   python3 backtest_forex.py --symbol EURUSD --years 3 \\
       --spread-price 0.00005 --commission-per-lot 7 --start-cash 10000

 ผลที่ได้:
   - Console: metrics table
   - {SYMBOL}_backtest_trades.csv  : ทุก trade
   - {SYMBOL}_backtest_equity.csv  : equity curve ทุกแท่ง
================================================================================
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import math
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# ── Import logic จากบอทโดยตรง ─────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forex_config import ForexConfig
from forex_hybrid_strategy import HybridTrendPullback
from forex_indicators import Signal, add_indicators, build_data_dict


# =============================================================================
# 1. DATA LOADER — MT5 via MetaApi หรือ Synthetic
# =============================================================================
class DataLoader:
    """โหลด OHLCV data จาก MetaApi/MT5 หรือสร้าง synthetic."""

    def __init__(self, log_fn=print):
        self.log = log_fn

    def load(self, symbol: str, years: float, cfg: ForexConfig,
             csv_path: Optional[str] = None,
             allow_synthetic: bool = False) -> Tuple[pd.DataFrame, str]:
        """
        โหลด OHLCV — คืน (df, data_source_description)

        ลำดับความสำคัญ:
          1. --csv PATH      → โหลดจากไฟล์ CSV (ถ้า fail → ERROR + exit, ไม่ fallback)
          2. MetaApi creds   → ลองดึงจาก MetaApi
                                ถ้า fail และ allow_synthetic=False → ERROR + exit
                                ถ้า fail และ allow_synthetic=True  → synthetic
          3. allow_synthetic → synthetic (เฉพาะตอนไม่มี --csv และไม่มี MetaApi creds)
          ไม่งั้น → ERROR + exit (ห้าม silent fallback)
        """
        if csv_path:
            df = self._load_csv(csv_path)
            return df, f"CSV file: {csv_path}"

        if cfg.metaapi_token and cfg.metaapi_account_id:
            try:
                self.log(f"  Fetching {symbol} M15 ({years:.1f} yrs) from MetaApi ...")
                df = self._load_mt5(symbol, years, cfg)
                return df, f"MetaApi (account {cfg.metaapi_account_id[:8]}...)"
            except Exception as exc:
                if allow_synthetic:
                    self.log(f"  [WARN] MetaApi failed: {exc} — using synthetic (--synthetic ระบุไว้)")
                else:
                    print(f"\n[ERROR] MetaApi failed: {exc}")
                    print("[ERROR] ปฏิเสธที่จะ fallback ไป synthetic data แบบเงียบๆ")
                    print("[ERROR] ใช้ --synthetic เพื่ออนุญาต synthetic data อย่างชัดเจน "
                          "หรือ --csv <path> เพื่อโหลดจากไฟล์จริง")
                    sys.exit(1)

        if allow_synthetic:
            df = self._synthetic(symbol, years)
            return df, f"SYNTHETIC (seed=42, {symbol})"

        print("\n[ERROR] ไม่มีแหล่งข้อมูลให้ใช้:")
        print("  - ไม่ได้ระบุ --csv")
        print("  - ไม่มี MetaApi credentials (หรือ MetaApi fail) และไม่ได้ระบุ --synthetic")
        print("  ใช้ --csv <path>, ตั้งค่า MetaApi credentials ให้ถูกต้อง, "
              "หรือระบุ --synthetic อย่างชัดเจน")
        sys.exit(1)

    # ── CSV (MT5/Exness export) ──────────────────────────────────────────────

    @staticmethod
    def _parse_ts_series(s: pd.Series) -> pd.Series:
        """
        Parse คอลัมน์ datetime แบบ robust รองรับ:
          - epoch ms / epoch s  (ตัวเลขล้วน, dukascopy-node)
          - MT5 style:        "2021.01.04" หรือ "2021.01.04 00:00:00"  (YYYY.MM.DD)
          - Dukascopy web:    "01.01.2021 00:00:00.000" หรือ "...GMT+0200" (DD.MM.YYYY)
          - ISO / มาตรฐานอื่นที่ pandas parse เองได้
        คืนค่า naive datetime (ตัด timezone suffix ออก ถ้ามี — ใช้เวลาตามที่ระบุในไฟล์)
        """
        s = s.astype(str).str.strip()

        # epoch ms / s — เป็นตัวเลขล้วนทุกแถว
        if s.str.match(r'^\d{10,13}$').all():
            nums = pd.to_numeric(s, errors="coerce")
            unit = "ms" if nums.dropna().median() > 1e11 else "s"
            return pd.to_datetime(nums, unit=unit, errors="coerce")

        # ตัด timezone suffix เช่น "GMT+0200" / "GMT-0500" / "UTC" ออก
        s_clean = s.str.replace(r'\s*(GMT[+-]\d{2,4}|UTC)\s*$', '', regex=True).str.strip()

        sample = next((x for x in s_clean if x and x.lower() != "nan"), "")

        # แทนที่ "." เฉพาะในส่วนวันที่ที่ขึ้นต้น (YYYY.MM.DD หรือ DD.MM.YYYY) เท่านั้น
        # — ห้ามแทนที่ "." ของ milliseconds เช่น "00:00:00.000"
        if re.match(r'^\d{4}\.\d{2}\.\d{2}', sample):          # MT5: YYYY.MM.DD
            s_fixed = s_clean.str.replace(r'^(\d{4})\.(\d{2})\.(\d{2})', r'\1-\2-\3', regex=True)
            return pd.to_datetime(s_fixed, errors="coerce")
        if re.match(r'^\d{2}\.\d{2}\.\d{4}', sample):           # Dukascopy: DD.MM.YYYY
            s_fixed = s_clean.str.replace(r'^(\d{2})\.(\d{2})\.(\d{4})', r'\1-\2-\3', regex=True)
            return pd.to_datetime(s_fixed, errors="coerce", dayfirst=True)
        return pd.to_datetime(s_clean, errors="coerce")         # ISO / อื่นๆ — ให้ pandas เดา

    def _load_csv(self, path: str) -> pd.DataFrame:
        """
        โหลด OHLCV จากไฟล์ CSV — รองรับหลายแหล่ง:
          - MT5/Exness export:   tab/comma, <DATE>,<TIME>,<OPEN>... หรือ date,time,open,...
                                  date "2021.06.11", time "HH:MM"/"HH:MM:SS"
          - Dukascopy web export: "Gmt time"/"Local time","Open","High","Low","Close","Volume"
                                  date "01.01.2021 00:00:00.000" (+ optional "GMT+0200")
          - dukascopy-node CSV:   timestamp(epoch ms),open,high,low,close,volume
          - date+time แยกคอลัมน์ หรือรวมอยู่ใน column เดียว
        """
        if not os.path.exists(path):
            print(f"\n[ERROR] ไม่พบไฟล์ CSV: {path}")
            sys.exit(1)

        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                first_line = f.readline()
            sep = "\t" if "\t" in first_line else ","

            raw = pd.read_csv(path, sep=sep)
            # Normalize column names: ตัด <>, lowercase, strip
            raw.columns = [str(c).strip().strip("<>").strip().lower() for c in raw.columns]

            # Map alias columns
            alias = {
                "tickvol": "volume", "tick_volume": "volume",
                "vol": "volume", "real_volume": "volume",
                "datetime": "date", "time stamp": "timestamp",
                "gmt time": "date", "local time": "date",
                "time (utc)": "date", "time (eet)": "date",
            }
            raw = raw.rename(columns={k: v for k, v in alias.items() if k in raw.columns})
            # ป้องกันคอลัมน์ซ้ำหลัง rename (เช่น tickvol + vol ทั้งคู่ → volume)
            if raw.columns.duplicated().any():
                raw = raw.loc[:, ~raw.columns.duplicated()]

            required = {"open", "high", "low", "close"}
            missing = required - set(raw.columns)
            if missing:
                raise ValueError(f"CSV ขาดคอลัมน์ที่จำเป็น: {missing}  "
                                 f"(พบ: {list(raw.columns)})")

            # สร้าง timestamp
            if "date" in raw.columns and "time" in raw.columns:
                date_str = raw["date"].astype(str).str.replace(".", "-", regex=False)
                time_str = raw["time"].astype(str)
                ts = pd.to_datetime(date_str + " " + time_str, errors="coerce")
            elif "date" in raw.columns:
                ts = self._parse_ts_series(raw["date"])
            elif "timestamp" in raw.columns:
                ts = self._parse_ts_series(raw["timestamp"])
            else:
                raise ValueError(f"CSV ไม่มีคอลัมน์ date/time/timestamp  "
                                 f"(พบ: {list(raw.columns)})")

            if ts.isna().all():
                raise ValueError("parse timestamp ไม่ได้เลยสักแถว — ตรวจรูปแบบวันที่/เวลาในไฟล์")

            if "volume" not in raw.columns:
                raw["volume"] = 1.0

            df = pd.DataFrame({
                "timestamp": ts,
                "open":   pd.to_numeric(raw["open"],  errors="coerce"),
                "high":   pd.to_numeric(raw["high"],  errors="coerce"),
                "low":    pd.to_numeric(raw["low"],   errors="coerce"),
                "close":  pd.to_numeric(raw["close"], errors="coerce"),
                "volume": pd.to_numeric(raw["volume"], errors="coerce").fillna(1.0),
            })
            n_before = len(df)
            df = df.dropna(subset=["timestamp", "open", "high", "low", "close"])
            df = df.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
            n_after = len(df)

            if df.empty:
                raise ValueError("parse ได้ 0 แถวที่ใช้งานได้จาก CSV นี้")

            if n_after < n_before:
                self.log(f"  [WARN] CSV: ตัดทิ้ง {n_before - n_after} แถว "
                         f"(NaN/duplicate timestamp)")

            self.log(f"  Loaded {len(df):,} bars from CSV: {path}")
            return df

        except Exception as exc:
            print(f"\n[ERROR] อ่าน CSV ไม่สำเร็จ: {path}")
            print(f"[ERROR] {type(exc).__name__}: {exc}")
            sys.exit(1)

    # ── MetaApi ───────────────────────────────────────────────────────────────

    def _load_mt5(self, symbol: str, years: float, cfg: ForexConfig) -> pd.DataFrame:
        from metaapi_cloud_sdk import MetaApi  # lazy import

        runner = _AsyncRunner()

        async def _fetch():
            api     = MetaApi(cfg.metaapi_token)
            account = await api.metatrader_account_api.get_account(cfg.metaapi_account_id)
            if account.state not in ("DEPLOYED", "DEPLOYING"):
                await account.deploy()
            await account.wait_connected(timeout_in_seconds=60)
            conn = account.get_rpc_connection()
            await conn.connect()
            await conn.wait_synchronized(timeout_in_seconds=60)

            # ดึงข้อมูลย้อนหลัง
            start = datetime.now(timezone.utc) - timedelta(days=int(365 * years + 30))
            n_req = int(365 * years * 96 + 1000)   # M15: ~96 bars/day

            self.log(f"  Requesting up to {n_req:,} candles from {start.date()} ...")
            candles = await conn.get_historical_candles(symbol, "15m", start, n_req)

            await conn.close()
            return candles

        candles = runner.run(_fetch(), timeout=300)

        if not candles:
            raise RuntimeError("MetaApi returned empty candles")

        rows = []
        for c in candles:
            t = c["time"]
            ts = int(t.timestamp() * 1000) if isinstance(t, datetime) else \
                 int(datetime.fromisoformat(str(t)).timestamp() * 1000)
            rows.append({
                "timestamp": ts,
                "open":  float(c["open"]),
                "high":  float(c["high"]),
                "low":   float(c["low"]),
                "close": float(c["close"]),
                "volume": float(c.get("tickVolume", 1)),
            })

        df = pd.DataFrame(rows)
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df = df.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
        self.log(f"  Loaded {len(df):,} M15 bars  ({df['timestamp'].iloc[0].date()} "
                 f"→ {df['timestamp'].iloc[-1].date()})")
        return df

    # ── Synthetic ─────────────────────────────────────────────────────────────

    def _synthetic(self, symbol: str, years: float) -> pd.DataFrame:
        """GBM random walk ที่มีลักษณะคล้าย XAUUSD/EURUSD."""
        sym_u = symbol.upper()
        if "XAU" in sym_u or "GOLD" in sym_u:
            p0, mu, sigma = 1950.0, 0.00003, 0.003   # Gold: drift + ATR ~$6
        elif "JPY" in sym_u:
            p0, mu, sigma = 145.0,  0.00001, 0.0008
        elif "GBP" in sym_u:
            p0, mu, sigma = 1.27,   0.00001, 0.0009
        else:
            p0, mu, sigma = 1.10,   0.00001, 0.0008   # EURUSD-like

        # M15 bars: ~96/day × 252 trading days × years
        n = max(int(96 * 252 * years) + 1000, 2000)
        rng = np.random.default_rng(42)

        # Geometric Brownian Motion
        ret = rng.normal(mu, sigma, n)
        prices = p0 * np.exp(np.cumsum(ret))

        # OHLCV
        wig = np.abs(rng.normal(0, sigma * 0.6, n))
        opens  = prices
        closes = prices * (1 + rng.normal(0, sigma * 0.3, n))
        highs  = np.maximum(opens, closes) * (1 + wig)
        lows   = np.minimum(opens, closes) * (1 - wig)
        lows   = np.maximum(lows, 0.001)

        now_ms = int(time.time() * 1000)
        tf_ms  = 15 * 60_000
        timestamps = [datetime.utcfromtimestamp((now_ms - (n - i) * tf_ms) / 1000)
                      for i in range(n)]

        df = pd.DataFrame({
            "timestamp": timestamps,
            "open":  opens,
            "high":  highs,
            "low":   lows,
            "close": closes,
            "volume": rng.uniform(100, 1000, n),
        })
        self.log(f"  Generated {len(df):,} synthetic M15 bars for {symbol}")
        return df


# =============================================================================
# 2. INDICATORS — เรียก add_indicators เดียวกับบอท live
# =============================================================================
def prepare_data(df: pd.DataFrame) -> Optional[dict]:
    """เรียก pipeline indicators เดิม — คืน data dict ที่ strategy อ่านได้."""
    if len(df) < 200:
        return None
    df2 = add_indicators(df.copy())
    return build_data_dict(df2)


# =============================================================================
# 2.5. FAST STRATEGY VARIANT (backtest-only, ไม่กระทบบอท live)
# =============================================================================
class FastHybridTrendPullback(HybridTrendPullback):
    """เหมือน HybridTrendPullback ทุกประการ — entry/trend logic ไม่เปลี่ยน —
    แต่ precompute EMA20(M15) เป็น array เดียวครั้งเดียวใน precompute() แทนการ
    recompute `_ema(d["c"][:i+1], 20)` (O(i)) ใหม่ทุก bar ใน _m15_entry()
    (ของเดิมคือ O(n^2)-ish รวมทั้งช่วง).

    ผลลัพธ์ตัวเลขเหมือนเดิมทุก bit เพราะ EMA เป็น causal recursion ที่เริ่มจาก
    index 0 เสมอ:  _ema(prices[:i+1], span)[-1] == _ema(prices, span)[i]

    ใช้เฉพาะตอน backtest ชุดข้อมูลยาว (หลายปี) ที่ original รันไม่ไหว —
    บอท live (HybridTrendPullback เดิม) ไม่ถูกแก้ไขเลย.
    """

    _ema20_arr = None

    def precompute(self, d: dict):
        super().precompute(d)
        self._ema20_arr = self._ema(d["c"], self.EMA_M15)

    def _m15_entry(self, d: dict, i: int, trend: int) -> Signal:
        if i < self.EMA_M15 + 3:
            return Signal()

        if self._ema20_arr is not None:
            e_cur  = self._ema20_arr[i]
            e_prev = self._ema20_arr[i - 1]
        else:
            ema20  = self._ema(d["c"][:i + 1], self.EMA_M15)
            e_cur  = ema20[-1]
            e_prev = ema20[-2]

        if math.isnan(e_cur) or math.isnan(e_prev):
            return Signal()

        c_cur  = d["c"][i]
        o_cur  = d["o"][i]
        h_prev = d["h"][i - 1]
        l_prev = d["l"][i - 1]

        tol = self.TOUCH_TOLERANCE

        if trend == 1:  # ── LONG: pullback ลงมาแตะ EMA20 แล้วเด้งขึ้น
            touched   = l_prev <= e_prev * (1 + tol)
            confirmed = c_cur > e_cur and c_cur > o_cur
            if touched and confirmed:
                return Signal("BUY", f"H1-bull pullback e20={e_cur:.5f}")

        elif trend == -1:  # ── SHORT: pullback ขึ้นมาแตะ EMA20 แล้วดิ่งลง
            touched   = h_prev >= e_prev * (1 - tol)
            confirmed = c_cur < e_cur and c_cur < o_cur
            if touched and confirmed:
                return Signal("SELL", f"H1-bear pullback e20={e_cur:.5f}")

        return Signal()


# =============================================================================
# 3. BACKTEST POSITION
# =============================================================================
@dataclass
class BTPosition:
    side:          str    # 'long' | 'short'
    entry:         float
    lot:           float
    sl:            float
    tp:            float
    partial_tp:    float
    trail_sl:      float
    highest:       float
    lowest:        float
    entry_atr:     float
    entry_ts:      str
    entry_bar:     int

    # mutable state
    partial_closed: bool  = False
    lot_remaining:  float = 0.0
    bars:           int   = 0
    partial_pnl:    float = 0.0   # PnL locked at partial close
    entry_comm:     float = 0.0   # commission paid at entry (deducted from equity in _enter)

    def __post_init__(self):
        if self.lot_remaining == 0.0:
            self.lot_remaining = self.lot

    @property
    def direction(self) -> int:
        return 1 if self.side == "long" else -1


# =============================================================================
# 4. SIMULATION ENGINE
# =============================================================================
class BacktestEngine:
    """Bar-by-bar simulation — no look-ahead, no magic numbers."""

    def __init__(self,
                 d: dict,
                 cfg: ForexConfig,
                 strategy: HybridTrendPullback,
                 spread_price: float,
                 commission_per_lot: float,
                 symbol: str,
                 daily_loss_limit_pct: Optional[float] = None,
                 reactive_daily_stop: bool = False,
                 halt_after_losing_days: Optional[int] = None,
                 halt_cooldown_days: Optional[int] = None):
        self.d    = d
        self.cfg  = cfg
        self.strat = strategy
        self.sym   = symbol

        # Cost model
        self.spread_price       = spread_price        # in price units (e.g. $0.35)
        self.commission_per_lot = commission_per_lot  # per-side ($)

        # Symbol info
        self.pip_size  = cfg.get_pip_size(symbol)
        self.pip_value = cfg.get_pip_value(symbol)

        # State
        self.position: Optional[BTPosition] = None
        self._pending: Optional[Signal]     = None    # signal pending entry at next bar
        self.equity   = cfg.total_capital_usd
        self.trades:  List[dict] = []
        self.equity_curve: List[float] = []
        self.n = len(d["c"])
        self.skipped_risk_cap = 0   # นับไม้ที่ข้ามเพราะความเสี่ยงเกิน max_risk_per_trade_pct

        # ── Optional day-level rule toggles (Phase 3 / signal-gen only) ────────
        self.daily_loss_limit_pct = daily_loss_limit_pct  # e.g. 2.0 -> block at -2% of day-start equity
        self.reactive_daily_stop  = reactive_daily_stop   # block after first net-loss trade once day was green
        self.current_day:  Optional[str] = None  # "YYYY-MM-DD" (UTC, from bar timestamp)
        self.day_realized_pnl = 0.0
        self.day_equity_start = cfg.total_capital_usd
        self.day_blocked      = False
        self.day_was_positive = False

        # ── Optional cross-day "stop-when-losing, resume-after-cooldown" overlay ──
        # N consecutive losing calendar days (net realized P&L < 0) -> block new
        # entries for the next C calendar days (cooldown), then resume. Open
        # positions still close per their normal rules.
        self.halt_n             = halt_after_losing_days  # N: trigger threshold (consecutive losing days)
        self.halt_cooldown_days = halt_cooldown_days      # C: cooldown length in calendar days
        self.consec_losing_days = 0
        self.cooldown_days_left = 0

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self, start_i: Optional[int] = None, end_i: Optional[int] = None,
            quiet: bool = False, do_precompute: bool = True) -> "BacktestEngine":
        warm = self.strat.MIN_BARS
        start_i = warm if start_i is None else max(start_i, warm)
        end_i   = (self.n - 1) if end_i is None else min(end_i, self.n - 1)

        if not quiet:
            print(f"  Simulating {max(end_i - start_i, 0):,} bars "
                  f"(range [{start_i}:{end_i}], warm={warm}) ...")
        t0 = time.time()

        # ── Pre-compute H1 trend array ครั้งเดียว (O(n) แทน O(n²)) ──────
        if do_precompute:
            if not quiet:
                print("  Pre-computing H1 trend array ...")
            self.strat.precompute(self.d)
        t1 = time.time()
        if not quiet:
            print(f"  Pre-compute done in {t1-t0:.1f}s — starting bar-by-bar loop ...")

        for i in range(start_i, end_i):
            self._step(i)

        # Force-close any open position at end of period
        if self.position and end_i - 1 >= start_i:
            last = end_i - 1
            self._close(last, float(self.d["c"][last]), "EndOfData")

        elapsed = time.time() - t0
        if not quiet:
            print(f"  Simulation done: {len(self.trades)} trades in {elapsed:.1f}s")
            print(f"  Skipped by risk cap (>{self.cfg.max_risk_per_trade_pct}% risk/trade): "
                  f"{self.skipped_risk_cap}")

        # ── Reconciliation check ────────────────────────────────────────────
        # sum(net_pnl) ต้องเท่ากับ final_equity - initial_equity (ภายใน rounding)
        sum_net_pnl   = sum(t["net_pnl"] for t in self.trades)
        equity_change = self.equity - self.cfg.total_capital_usd
        diff = abs(sum_net_pnl - equity_change)
        self.reconcile_diff = diff
        if not quiet:
            print(f"  [RECONCILE] sum(net_pnl)={sum_net_pnl:,.2f}  "
                  f"equity_change={equity_change:,.2f}  diff={diff:.6f}")
        if self.trades:
            assert diff < 0.01 * max(1, len(self.trades)), \
                f"Accounting mismatch: sum(net_pnl)={sum_net_pnl:.2f} != " \
                f"equity_change={equity_change:.2f} (diff={diff:.2f})"

        return self

    # ── Per-bar step ──────────────────────────────────────────────────────────

    def _step(self, i: int):
        o = float(self.d["o"][i])
        h = float(self.d["h"][i])
        l = float(self.d["l"][i])
        c = float(self.d["c"][i])
        ts = str(self.d["ts"][i])

        # ── Day rollover: reset day-level rule state on calendar-day change ──
        day = ts[:10]
        if day != self.current_day:
            # Finalize the day that just ended (skip on the very first bar)
            if self.current_day is not None:
                # Cross-day halt/cooldown overlay bookkeeping
                if self.cooldown_days_left > 0:
                    self.cooldown_days_left -= 1

                if self.day_realized_pnl < 0:
                    self.consec_losing_days += 1
                else:
                    self.consec_losing_days = 0

                if (self.halt_n is not None and self.halt_cooldown_days is not None
                        and self.cooldown_days_left == 0
                        and self.consec_losing_days >= self.halt_n):
                    self.cooldown_days_left = self.halt_cooldown_days
                    self.consec_losing_days = 0

            self.current_day      = day
            self.day_realized_pnl = 0.0
            self.day_equity_start = self.equity
            self.day_blocked      = False
            self.day_was_positive = False

        # ── Phase 1: fill pending entry at current bar's open ──────────────
        if self._pending is not None and self.position is None:
            self._enter(i, o, ts)
            self._pending = None

        # ── Phase 2: manage open position ─────────────────────────────────
        if self.position is not None:
            # Partial TP (checked before main exit)
            if not self.position.partial_closed:
                self._check_partial_tp(i, h, l, c, ts)

            # Main exit check
            exit_px, reason = self._check_exit(h, l, c)
            if exit_px is not None:
                self._close(i, exit_px, reason)
            else:
                # No exit → update trailing + bar counter
                self._update_trail(h, l)
                self.position.bars += 1
                if self.position.bars >= self.cfg.max_hold_bars:
                    self._close(i, c, "Timeout")

        # ── Phase 3: generate signal at current bar's close ───────────────
        # (entry will happen at NEXT bar's open)
        if (self.position is None and self._pending is None
                and not self.day_blocked and self.cooldown_days_left == 0):
            sig = self.strat.signal(self.d, i)
            if sig.action in ("BUY", "SELL"):
                self._pending = sig

        # ── Record equity ──────────────────────────────────────────────────
        open_pnl = 0.0
        if self.position:
            pos = self.position
            lot = pos.lot_remaining if pos.partial_closed else pos.lot
            pips = (c - pos.entry) / self.pip_size * pos.direction
            open_pnl = pips * self.pip_value * lot
        self.equity_curve.append(self.equity + open_pnl)

    # ── Entry ─────────────────────────────────────────────────────────────────

    def _enter(self, i: int, bar_open: float, ts: str):
        sig   = self._pending
        long  = sig.action == "BUY"
        atr   = float(self.d["atr"][i - 1])   # signal bar's ATR (i-1)
        if math.isnan(atr) or atr <= 0:
            return

        # Fill price with half-spread slippage
        hs    = self.spread_price / 2.0
        fill  = bar_open + hs if long else bar_open - hs

        # SL / TP / Partial TP / Trail
        sl = fill - atr * self.strat.sl_atr  if long else fill + atr * self.strat.sl_atr
        tp = fill + atr * self.strat.tp_atr  if long else fill - atr * self.strat.tp_atr
        ptp = (fill + atr * self.cfg.partial_tp_atr if long
               else fill - atr * self.cfg.partial_tp_atr)
        tsl = (fill - atr * self.strat.trail_atr_mult if long
               else fill + atr * self.strat.trail_atr_mult)

        # Lot size: risk_cash / (sl_pips × pip_value)
        sl_pips   = abs(fill - sl) / self.pip_size
        risk_cash = self.equity * self.cfg.risk_per_trade_pct / 100.0
        if sl_pips <= 0 or self.pip_value <= 0:
            return
        lot = max(self.cfg.min_lot, round(risk_cash / (sl_pips * self.pip_value), 2))
        lot = min(lot, self.cfg.max_lot)

        # ── Risk cap: ถ้า min_lot ดันความเสี่ยงจริงเกิน max_risk_per_trade_pct
        #    → ข้ามไม้นี้ไปเลย (ดีกว่าฝืนเข้าด้วย risk เกินเพดานแบบเงียบๆ)
        if self.equity > 0:
            actual_risk_pct = (sl_pips * self.pip_value * lot) / self.equity * 100.0
        else:
            actual_risk_pct = float("inf")
        if actual_risk_pct > self.cfg.max_risk_per_trade_pct:
            self.skipped_risk_cap += 1
            return

        # Deduct entry commission immediately
        entry_comm = self.commission_per_lot * lot
        self.equity -= entry_comm

        self.position = BTPosition(
            side       = "long" if long else "short",
            entry      = fill,
            lot        = lot,
            sl         = sl,
            tp         = tp,
            partial_tp = ptp,
            trail_sl   = tsl,
            highest    = fill,
            lowest     = fill,
            entry_atr  = atr,
            entry_ts   = ts,
            entry_bar  = i,
            entry_comm = entry_comm,
        )

    # ── Partial TP ────────────────────────────────────────────────────────────

    def _check_partial_tp(self, i: int, h: float, l: float, c: float, ts: str):
        pos  = self.position
        hit  = ((pos.side == "long"  and h >= pos.partial_tp) or
                (pos.side == "short" and l <= pos.partial_tp))
        if not hit:
            return

        lot  = pos.lot * self.cfg.partial_tp_frac
        pips = (pos.partial_tp - pos.entry) / self.pip_size * pos.direction
        pnl  = pips * self.pip_value * lot - self.commission_per_lot * lot

        self.equity       += pnl
        pos.partial_pnl    = pnl
        pos.partial_closed = True
        pos.lot_remaining  = pos.lot - lot
        if self.cfg.move_sl_to_breakeven:
            pos.sl = pos.entry    # move SL to breakeven

    # ── Exit check ────────────────────────────────────────────────────────────

    def _check_exit(self, h: float, l: float, c: float) -> Tuple[Optional[float], Optional[str]]:
        pos  = self.position

        if pos.side == "long":
            # Conservative: SL before TP when both in same bar
            if l <= pos.sl:
                return pos.sl, "SL"
            if pos.trail_sl > pos.sl and l <= pos.trail_sl:
                return pos.trail_sl, "Trail-SL"
            if h >= pos.tp:
                return pos.tp, "TP"
        else:
            if h >= pos.sl:
                return pos.sl, "SL"
            if 0 < pos.trail_sl < pos.sl and h >= pos.trail_sl:
                return pos.trail_sl, "Trail-SL"
            if l <= pos.tp:
                return pos.tp, "TP"

        return None, None

    def _close(self, i: int, exit_px: float, reason: str):
        pos = self.position
        lot = pos.lot_remaining if pos.partial_closed else pos.lot

        pips     = (exit_px - pos.entry) / self.pip_size * pos.direction
        gross    = pips * self.pip_value * lot
        # Spread already paid at entry (half-spread in fill price)
        # Exit cost = commission only
        exit_cst = self.commission_per_lot * lot

        # Equity: only book the NOT-YET-BOOKED portion here.
        # partial_pnl (if any) was already added to self.equity inside
        # _check_partial_tp — adding it again here would double-count it.
        not_yet_booked = gross - exit_cst
        self.equity += not_yet_booked

        # net_pnl = TRUE total effect of this trade on equity
        #         = partial leg (already booked) + remaining leg (booked above)
        #           - entry commission (booked at _enter, not yet reflected anywhere else)
        net = pos.partial_pnl + not_yet_booked - pos.entry_comm

        # รวม commission ทั้งหมดของ trade นี้เพื่อ reporting
        # entry commission + partial TP commission (ถ้ามี) + exit commission
        total_comm   = pos.entry_comm + exit_cst

        self.trades.append({
            "entry_ts":    pos.entry_ts,
            "exit_ts":     str(self.d["ts"][min(i, self.n - 1)]),
            "side":        pos.side,
            "entry_price": round(pos.entry, 5),
            "exit_price":  round(exit_px, 5),
            "lot":         round(lot, 3),       # lot ที่ถูกปิดจริง (= lot_remaining หลัง partial TP)
            "lot_orig":    round(pos.lot, 3),   # lot เดิมตอนเปิด position (สำหรับ audit)
            "pips":        round(pips, 1),
            "gross_usd":   round(gross, 2),
            "partial_pnl": round(pos.partial_pnl, 2),
            "costs_usd":   round(total_comm, 2),
            "net_pnl":     round(net, 2),
            "reason":      reason,
            "bars_held":   pos.bars,
            "entry_atr":   round(pos.entry_atr, 4),
            "equity_after":round(self.equity, 2),
            "entry_bar":   pos.entry_bar,
        })
        self.position = None
        self._update_day_state(net)

    # ── Day-level rule state update (called after every trade close) ──────────

    def _update_day_state(self, net_pnl: float):
        # (b) Reactive daily stop: once today has been net-positive at some
        # point, a subsequent net-loss trade blocks new entries for the rest
        # of the day. Already-open positions still close per normal rules.
        if self.reactive_daily_stop and self.day_was_positive and net_pnl < 0:
            self.day_blocked = True

        self.day_realized_pnl += net_pnl
        if self.day_realized_pnl > 0:
            self.day_was_positive = True

        # (a) Daily loss limit: if today's realized P&L hits -X% of the
        # equity at the START of this calendar day, block new entries for
        # the rest of the day.
        if (self.daily_loss_limit_pct and self.daily_loss_limit_pct > 0
                and self.day_equity_start > 0):
            loss_threshold = -(self.daily_loss_limit_pct / 100.0) * self.day_equity_start
            if self.day_realized_pnl <= loss_threshold:
                self.day_blocked = True

    # ── Chandelier trailing (เหมือนบอท live) ─────────────────────────────────

    def _update_trail(self, h: float, l: float):
        pos  = self.position
        atr  = pos.entry_atr
        act  = atr * self.strat.trail_activation_atr
        mult = self.strat.trail_atr_mult

        if pos.side == "long":
            if h > pos.highest:
                pos.highest = h
            if pos.highest - pos.entry >= act:
                new_trail = pos.highest - atr * mult
                if new_trail > pos.trail_sl:
                    pos.trail_sl = new_trail
        else:
            if l < pos.lowest or pos.lowest <= 0:
                pos.lowest = l
            if pos.entry - pos.lowest >= act:
                new_trail = pos.lowest + atr * mult
                if pos.trail_sl <= 0 or new_trail < pos.trail_sl:
                    pos.trail_sl = new_trail


# =============================================================================
# 5. METRICS
# =============================================================================
def compute_metrics(trades: List[dict],
                    equity_curve: List[float],
                    initial_equity: float) -> dict:
    """คำนวณ metrics ทั้งหมด — คืน dict."""
    if not trades:
        return {"trades": 0, "error": "no trades"}

    pnls   = [t["net_pnl"] for t in trades]
    pips   = [t["pips"]    for t in trades]
    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    n       = len(pnls)
    win_n   = len(wins)
    win_rate = win_n / n if n > 0 else 0.0

    sum_wins  = sum(wins)
    sum_loss  = abs(sum(losses))
    pf = (sum_wins / sum_loss) if sum_loss > 0 else float("inf")

    avg_win  = sum_wins / win_n       if win_n > 0 else 0.0
    avg_loss = sum_loss / (n - win_n) if (n - win_n) > 0 else 0.0
    avg_pips = sum(pips) / n          if n > 0 else 0.0

    expectancy = sum(pnls) / n  if n > 0 else 0.0    # USD/trade

    # Expectancy ratio (เส้นกราฟ edge)
    exp_ratio = (win_rate * avg_win - (1 - win_rate) * avg_loss) / avg_loss \
                if avg_loss > 0 else 0.0

    # Max Drawdown (peak-to-trough)
    peak = initial_equity
    max_dd_usd = 0.0
    max_dd_pct = 0.0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd_usd = peak - eq
        dd_pct = dd_usd / peak if peak > 0 else 0
        max_dd_usd = max(max_dd_usd, dd_usd)
        max_dd_pct = max(max_dd_pct, dd_pct)

    final_equity = equity_curve[-1] if equity_curve else initial_equity
    total_return = (final_equity - initial_equity) / initial_equity * 100.0

    # Annualized return
    n_bars   = len(equity_curve)
    # M15: ~96 bars/trading day for FX (gold is 96 bars/day 5 days/wk)
    # 96 × 252 = 24,192 M15 bars per year
    bars_per_year = 96 * 252
    years_covered = n_bars / bars_per_year if bars_per_year > 0 else 1
    ann_return = ((final_equity / initial_equity) ** (1 / max(years_covered, 0.1)) - 1) * 100

    # Sharpe (M15 → annualized)
    eq_arr = np.array(equity_curve)
    rets   = np.diff(eq_arr) / eq_arr[:-1]
    rets   = rets[np.isfinite(rets)]
    sharpe = 0.0
    if len(rets) > 1 and np.std(rets) > 0:
        sharpe = np.mean(rets) / np.std(rets) * math.sqrt(bars_per_year)

    # Avg hold time
    avg_bars = sum(t["bars_held"] for t in trades) / n if n > 0 else 0

    # Trade frequency
    trades_per_year = n / max(years_covered, 0.01)

    # Consecutive wins/losses
    max_consec_wins = max_consec_losses = cur_w = cur_l = 0
    for p in pnls:
        if p > 0:
            cur_w += 1; cur_l = 0
        else:
            cur_l += 1; cur_w = 0
        max_consec_wins   = max(max_consec_wins,   cur_w)
        max_consec_losses = max(max_consec_losses, cur_l)

    return {
        "trades":             n,
        "win_rate":           win_rate,
        "profit_factor":      pf,
        "expectancy_usd":     expectancy,
        "expectancy_ratio":   exp_ratio,
        "avg_win_usd":        avg_win,
        "avg_loss_usd":       avg_loss,
        "avg_pips":           avg_pips,
        "sum_wins_usd":       sum_wins,
        "sum_losses_usd":     sum_loss,
        "total_return_pct":   total_return,
        "ann_return_pct":     ann_return,
        "sharpe":             sharpe,
        "max_dd_usd":         max_dd_usd,
        "max_dd_pct":         max_dd_pct * 100,
        "max_consec_wins":    max_consec_wins,
        "max_consec_losses":  max_consec_losses,
        "avg_bars_held":      avg_bars,
        "trades_per_year":    trades_per_year,
        "years_covered":      years_covered,
        "initial_equity":     initial_equity,
        "final_equity":       final_equity,
    }


# =============================================================================
# 6. REPORT
# =============================================================================
def print_report(m: dict, symbol: str, cfg: ForexConfig,
                 spread_price: float, commission: float):
    """พิมพ์ผล backtest — สั้นและอ่านง่าย."""
    verdict  = ""
    pf       = m.get("profit_factor", 0)
    n        = m.get("trades", 0)

    if n == 0:
        print("\n[BACKTEST] ไม่มี trade เลย — ตรวจสอบ warm-up bars หรือเงื่อนไข session")
        return

    if pf >= 1.5:      verdict = "✅ Edge ชัดเจน (PF ≥ 1.5)"
    elif pf >= 1.2:    verdict = "🟡 Edge เล็กน้อย (PF 1.2–1.5) — ต้องทดสอบ OOS"
    elif pf >= 1.0:    verdict = "⚠️  ไม่มีกำไรสุทธิที่แน่ชัด (PF 1.0–1.2)"
    else:               verdict = "❌ ขาดทุน (PF < 1.0) — อย่าเอาไป live"

    print()
    print("=" * 65)
    print(f" BACKTEST RESULT  —  {symbol}  Hybrid Trend-Pullback")
    print("=" * 65)
    print(f" Data:          {m['years_covered']:.1f} yrs  ({n} trades, "
          f"{m['trades_per_year']:.0f}/yr)")
    print(f" Capital:       ${m['initial_equity']:,.0f}  →  ${m['final_equity']:,.2f}")
    print(f" Cost model:    spread={spread_price}  commission=${commission}/lot/side")
    print("-" * 65)
    print(f" Win Rate:      {m['win_rate']:.1%}   ({int(m['win_rate']*n)}/{n})")
    print(f" Profit Factor: {pf:.3f}             {verdict}")
    print(f" Expectancy:    ${m['expectancy_usd']:+.2f}/trade  "
          f"({m['expectancy_ratio']:+.2f}× avg_loss)")
    print(f" Avg Win:       ${m['avg_win_usd']:,.2f}  |  Avg Loss: ${m['avg_loss_usd']:,.2f}")
    print(f" Avg Pips:      {m['avg_pips']:+.1f} pips/trade")
    print(f" Avg Hold:      {m['avg_bars_held']:.1f} bars (M15) = "
          f"{m['avg_bars_held']*15/60:.1f} hrs")
    print("-" * 65)
    print(f" Total Return:  {m['total_return_pct']:+.1f}%  "
          f"({m['ann_return_pct']:+.1f}% annualized)")
    print(f" Sharpe Ratio:  {m['sharpe']:.2f}")
    print(f" Max Drawdown:  ${m['max_dd_usd']:,.2f}  ({m['max_dd_pct']:.1f}%)")
    print(f" Max Consec L:  {m['max_consec_losses']}  |  "
          f"Max Consec W: {m['max_consec_wins']}")
    print("=" * 65)
    print(f" {verdict}")
    print("=" * 65)

    # Tips
    if pf < 1.0:
        print()
        print("  แนวทางปรับ (เรียงตามผลกระทบ):")
        print("  1. ADX_MIN: ลองเพิ่มเป็น 25–30 (กรอง noise มากขึ้น)")
        print("  2. sl_atr: ลองเพิ่มเป็น 2.0–2.5 (ป้องกัน SL ถูก wick ปลิว)")
        print("  3. EMA_M15: ลองเป็น 10 หรือ 50 (pullback zone ต่างกัน)")
        print("  4. session: จำกัดแค่ London+NY overlap (12-16 UTC) เท่านั้น")
    elif pf < 1.3:
        print()
        print("  แนวทางเพิ่ม edge:")
        print("  1. เพิ่ม ADX_MIN → 25 เพื่อ filter เฉพาะ trend ชัดเจน")
        print("  2. ลอง R:R 1:1.5 (tp_atr=2.25) — อาจเพิ่ม win rate")


# =============================================================================
# 7. CSV EXPORT
# =============================================================================
def save_trades_csv(trades: List[dict], symbol: str):
    fname = f"{symbol}_backtest_trades.csv"
    if not trades:
        return fname
    cols = ["entry_ts", "exit_ts", "side", "entry_price", "exit_price",
            "lot", "lot_orig", "pips", "gross_usd", "partial_pnl", "costs_usd",
            "net_pnl", "reason", "bars_held", "entry_atr", "equity_after"]
    with open(fname, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(trades)
    return fname


def save_equity_csv(equity_curve: List[float], symbol: str):
    fname = f"{symbol}_backtest_equity.csv"
    with open(fname, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["bar", "equity"])
        for i, eq in enumerate(equity_curve):
            w.writerow([i, round(eq, 2)])
    return fname


# =============================================================================
# 8. ASYNC BRIDGE (copy minimal version — ไม่ต้อง import forex_executor)
# =============================================================================
class _AsyncRunner:
    def __init__(self):
        self._loop = asyncio.new_event_loop()
        t = threading.Thread(target=self._loop.run_forever, daemon=True)
        t.start()

    def run(self, coro, timeout: int = 300):
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)


# =============================================================================
# 9. MAIN
# =============================================================================
def main():
    ap = argparse.ArgumentParser(
        description="Hybrid Trend-Pullback Backtest — XAUUSD / FX",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
ตัวอย่าง:
  python3 backtest_forex.py                                # synthetic XAUUSD
  python3 backtest_forex.py --symbol XAUUSD --years 5 \\
      --spread-price 0.30 --commission-per-lot 7 --start-cash 10000
  python3 backtest_forex.py --symbol EURUSD --years 3 \\
      --spread-price 0.00005 --commission-per-lot 5

หมายเหตุ:
  --spread-price ใส่เป็นหน่วยราคา (price units):
      XAUUSD:  0.30 = $0.30/trade
      EURUSD:  0.00005 = 0.5 pip
  --commission-per-lot = ค่า commission ต่อ 1 lot ต่อฝั่ง (USD)
""")
    ap.add_argument("--symbol",   default="XAUUSD", help="Symbol (default XAUUSD)")
    ap.add_argument("--years",    type=float, default=3.0, help="Years of history (default 3)")
    ap.add_argument("--start-cash", type=float, default=10_000.0, help="Starting capital USD")
    ap.add_argument("--risk",     type=float, default=0.30, help="Risk %% per trade (default 0.30)")
    ap.add_argument("--spread-price", type=float, default=0.30,
                    help="Spread in price units (XAUUSD: 0.30; EURUSD: 0.00005)")
    ap.add_argument("--commission-per-lot", type=float, default=7.0,
                    help="Commission per lot per side USD (default 7.0)")
    ap.add_argument("--adx-min",  type=float, default=0.0,
                    help="Override strategy ADX_MIN (0=use default 22)")
    ap.add_argument("--sl-atr",   type=float, default=0.0,
                    help="Override SL ATR multiple (0=use default 1.5)")
    ap.add_argument("--tp-atr",   type=float, default=0.0,
                    help="Override TP ATR multiple (0=auto: sl_atr×2). ใช้คู่กับ --sl-atr")
    ap.add_argument("--no-partial-tp", action="store_true",
                    help="Disable partial take-profit (all-or-nothing)")
    ap.add_argument("--csv", default=None,
                    help="Path to MT5/Exness exported CSV (M15 OHLCV). "
                         "If given, this is the ONLY data source — never falls back.")
    ap.add_argument("--synthetic", action="store_true",
                    help="Explicitly allow synthetic data (generation, or fallback "
                         "if MetaApi fails). Without this flag, MetaApi failure or "
                         "missing data source is a hard error.")
    args = ap.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    # ── Config ──
    cfg = ForexConfig()
    cfg.total_capital_usd  = args.start_cash
    cfg.risk_per_trade_pct = args.risk
    cfg.symbols            = [args.symbol]
    cfg.history_bars       = 900

    # MetaApi credentials (optional — ถ้าไม่มีจะใช้ synthetic)
    cfg.metaapi_token      = os.environ.get("METAAPI_TOKEN",      "")
    cfg.metaapi_account_id = os.environ.get("METAAPI_ACCOUNT_ID", "")

    if args.no_partial_tp:
        cfg.partial_tp_atr   = 999.0   # ไม่ trigger partial TP
        cfg.partial_tp_frac  = 0.0

    # ── Strategy (override params if requested) ──
    strategy = FastHybridTrendPullback()  # O(n) precomputed EMA20 — required for long backtests
    if args.adx_min > 0:
        strategy.ADX_MIN = int(args.adx_min)
        print(f"  Override ADX_MIN → {strategy.ADX_MIN}")
    if args.sl_atr > 0:
        strategy.sl_atr = args.sl_atr
        strategy.tp_atr = args.tp_atr if args.tp_atr > 0 else args.sl_atr * 2.0
        print(f"  Override sl_atr={strategy.sl_atr}  tp_atr={strategy.tp_atr}")

    print()
    print("=" * 65)
    print(f" Backtest: {args.symbol}  |  Hybrid Trend-Pullback")
    print(f" Capital: ${cfg.total_capital_usd:,.0f}  risk={cfg.risk_per_trade_pct}%/trade")
    print(f" Strategy: EMA{strategy.EMA_H1_FAST}/{strategy.EMA_H1_SLOW} H1"
          f" + ADX>{strategy.ADX_MIN}"
          f" + EMA{strategy.EMA_M15} pullback M15")
    print(f" SL={strategy.sl_atr}×ATR  TP={strategy.tp_atr}×ATR"
          f"  Trail={strategy.trail_atr_mult}×ATR")
    print(f" Cost: spread={args.spread_price}  commission=${args.commission_per_lot}/lot/side")
    print("=" * 65)

    # ── Load data ──
    loader = DataLoader()
    df, data_source = loader.load(args.symbol, args.years, cfg,
                                   csv_path=args.csv,
                                   allow_synthetic=args.synthetic)

    if len(df) < strategy.MIN_BARS + 100:
        print(f"[ERROR] ข้อมูลน้อยเกินไป: {len(df)} bars (ต้องการ ≥ {strategy.MIN_BARS + 100})")
        sys.exit(1)

    # ── DATA SOURCE banner — ห้ามอ่านผิดว่าเป็นข้อมูลจริงหรือ synthetic ──────────
    date_start = df["timestamp"].iloc[0]
    date_end   = df["timestamp"].iloc[-1]
    print()
    print("#" * 65)
    print(f"  DATA SOURCE : {data_source}")
    print(f"  Symbol      : {args.symbol}")
    print(f"  Bars        : {len(df):,}")
    print(f"  Date range  : {date_start}  →  {date_end}")
    print("#" * 65)

    # ── Compute indicators ──
    print(f"  Computing indicators on {len(df):,} bars ...")
    d = prepare_data(df)
    if d is None:
        print("[ERROR] compute indicators failed")
        sys.exit(1)
    print(f"  ATR range: {float(np.nanmin(d['atr'])):.4f} – {float(np.nanmax(d['atr'])):.4f}")

    # ── Run simulation ──
    engine = BacktestEngine(
        d                  = d,
        cfg                = cfg,
        strategy           = strategy,
        spread_price       = args.spread_price,
        commission_per_lot = args.commission_per_lot,
        symbol             = args.symbol,
    )
    engine.run()

    # ── Metrics ──
    m = compute_metrics(engine.trades, engine.equity_curve, cfg.total_capital_usd)

    # ── Report ──
    print_report(m, args.symbol, cfg, args.spread_price, args.commission_per_lot)

    # ── Save CSV ──
    if engine.trades:
        tf = save_trades_csv(engine.trades, args.symbol)
        ef = save_equity_csv(engine.equity_curve, args.symbol)
        print(f"\n  Saved: {tf}  ({len(engine.trades)} trades)")
        print(f"  Saved: {ef}  ({len(engine.equity_curve):,} equity points)")
    else:
        print("\n  [WARN] ไม่มี trade — ไม่ได้บันทึก CSV")

    # ── Quick distribution ──
    if engine.trades:
        pnls = [t["net_pnl"] for t in engine.trades]
        reasons = {}
        for t in engine.trades:
            r = t["reason"]
            reasons[r] = reasons.get(r, 0) + 1
        print()
        print("  Exit breakdown:")
        for r, c in sorted(reasons.items(), key=lambda x: -x[1]):
            print(f"    {r:20} {c:5d}  ({c/len(engine.trades):.1%})")

    return m


if __name__ == "__main__":
    main()
