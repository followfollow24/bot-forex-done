#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 forex_hybrid_strategy.py  —  Hybrid Trend-Pullback Strategy
================================================================================
 Multi-timeframe: H1 trend filter  +  M15 pullback entry

 Layer 1 — Trend Filter (H1)
   EMA50 vs EMA200: กำหนดทิศ (bull / bear)
   ADX > 22:        ยืนยันว่ามีเทรนด์จริง ถ้าต่ำกว่า = sideways → ไม่เทรด

 Layer 2 — Entry (M15)
   รอราคา pullback มาแตะ EMA20
   แท่งยืนยันปิดกลับทิศเดียวกับเทรนด์ → เข้า

 Layer 3 — SL / TP / Trailing
   SL = 1.5 × ATR
   TP = 3.0 × ATR  (R:R = 2:1)
   Trailing = chandelier-style: swing_extreme ± 2.5 × ATR
              ขยับไปทางได้เปรียบอย่างเดียว (lock-in profit)

 ข้อควรรู้:
   - ต้องการ warm-up ≥ 850 M15 bars (= H1-EMA200 × 4 + buffer)
   - WIN RATE ปกติอยู่ที่ ~40–55% เป็นเรื่องปกติของ trend-following
   - ดูที่ Expectancy + Profit Factor ไม่ใช่ Win Rate
================================================================================
"""
from __future__ import annotations

import math

import numpy as np

from forex_indicators import Signal


class HybridTrendPullback:
    """H1 Trend Filter + M15 Pullback Entry — Chandelier Trailing Stop."""

    name       = "Hybrid Trend-Pullback (H1 EMA/ADX + M15 Pullback)"
    short_name = "Hybrid-TPB"

    # ── SL / TP (ส่งต่อไปให้ live_bot ใช้ใน _do_open) ───────────────────
    sl_atr = 3.0   # SL = 3.0 × ATR  (TP7 champion — Calmar 12.5, Win 21/27)
    tp_atr = 7.0   # TP = 7.0 × ATR → R:R = 2.33:1

    # ── Trailing (chandelier-style — override ค่า config) ────────────────
    trail_atr_mult       = 999.0  # trailing ปิด — TP7 backtest ไม่ใช้ trail
    trail_activation_atr = 999.0

    # ── Spread filter (ATR-based — documented สำหรับ reference) ──────────
    # Live_bot ใช้ cfg.spread_filter_atr_ratio เป็น threshold
    # ค่าแนะนำสำหรับ strategy นี้: ≤ 0.20 (20% ของ ATR)
    # EURUSD: ATR~10p, spread~0.5p → ratio~5%  ✓
    # XAUUSD: ATR~$5, spread~$0.35 → ratio~7%  ✓ (แต่ถ้าข่าวใหญ่ spread→$1.5 → 30% → skip)
    max_spread_atr_ratio = 0.20

    # ── H1 trend filter ──────────────────────────────────────────────────
    H1_BARS    = 4     # M15 bars per 1 H1 bar (4 × 15m = 60m)
    EMA_H1_FAST = 50   # H1 EMA fast
    EMA_H1_SLOW = 200  # H1 EMA slow
    ADX_PERIOD  = 14   # ADX period (H1)
    ADX_MIN     = 22   # ADX ต่ำกว่านี้ = sideways → ไม่เทรด

    # ── M15 entry ────────────────────────────────────────────────────────
    EMA_M15 = 20   # M15 EMA สำหรับ pullback zone
    TOUCH_TOLERANCE = 0.0015  # 0.15% — ราคาเข้าใกล้ EMA นับว่า "แตะ"

    # ── Minimum bars ─────────────────────────────────────────────────────
    # H1-EMA200 × 4 M15-bars/H1 + 50 buffer = 850
    MIN_BARS = EMA_H1_SLOW * H1_BARS + 50

    # ─────────────────────────────────────────────────────────────────────

    # ── precomputed H1 trend array (ใช้ใน backtest) ─────────────────────────
    _h1_trend_arr = None   # np.ndarray of int8 — set by precompute()

    def precompute(self, d: dict):
        """Pre-compute H1 trend array ครั้งเดียว — O(n) แทน O(n²) per-bar.

        เรียกก่อน backtest loop:
            strategy.precompute(d)
            for i in range(WARM, N):
                sig = strategy.signal(d, i)   # O(1) H1 lookup
        """
        self._h1_trend_arr = self._build_h1_trend_array(d)

    def _build_h1_trend_array(self, d: dict) -> np.ndarray:
        n    = len(d["c"])
        n_h1 = n // self.H1_BARS
        out  = np.zeros(n, dtype=np.int8)

        if n_h1 < self.EMA_H1_SLOW + 5:
            return out

        # H1 OHLCV
        idx  = np.arange(n_h1) * self.H1_BARS
        h1_c = np.array([d["c"][j + self.H1_BARS - 1] for j in idx])
        h1_h = np.array([d["h"][j:j + self.H1_BARS].max()  for j in idx])
        h1_l = np.array([d["l"][j:j + self.H1_BARS].min()  for j in idx])

        ema_f = self._ema(h1_c, self.EMA_H1_FAST)
        ema_s = self._ema(h1_c, self.EMA_H1_SLOW)
        adx_a = self._adx_array(h1_h, h1_l, h1_c, self.ADX_PERIOD)

        h1_trend = np.zeros(n_h1, dtype=np.int8)
        for k in range(n_h1):
            ef, es, adx = ema_f[k], ema_s[k], adx_a[k]
            if math.isnan(ef) or math.isnan(es) or math.isnan(adx):
                continue
            if adx < self.ADX_MIN:
                continue
            c = h1_c[k]
            if c > ef > es:   h1_trend[k] = 1
            elif c < ef < es: h1_trend[k] = -1

        # Map H1 → M15
        for i in range(n):
            k = i // self.H1_BARS
            if k < n_h1:
                out[i] = h1_trend[k]
        return out

    def signal(self, d: dict, i: int) -> Signal:
        """คืน Signal สำหรับแท่ง i — ใช้ d['c/h/l/o/atr'] จาก M15.

        ถ้า precompute() ถูกเรียกแล้ว จะใช้ cache → O(1) แทน O(n)
        """
        if i < self.MIN_BARS:
            return Signal()

        # ── 1. H1 trend ──
        if self._h1_trend_arr is not None:
            trend = int(self._h1_trend_arr[i])
        else:
            trend = self._h1_trend(d, i)

        if trend == 0:
            return Signal()

        # ── 2. M15 pullback entry ──
        return self._m15_entry(d, i, trend)

    # ─── H1 Trend ─────────────────────────────────────────────────────────

    def _h1_trend(self, d: dict, i: int) -> int:
        """คืน +1 (bull), -1 (bear), 0 (no trend / sideways)."""
        n = i + 1
        h1_c, h1_h, h1_l = self._resample_h1(
            d["c"][:n], d["h"][:n], d["l"][:n], n)

        if h1_c is None or len(h1_c) < self.EMA_H1_SLOW + 5:
            return 0

        ema_f = self._ema(h1_c, self.EMA_H1_FAST)
        ema_s = self._ema(h1_c, self.EMA_H1_SLOW)
        adx   = self._adx_last(h1_h, h1_l, h1_c, self.ADX_PERIOD)

        if adx < self.ADX_MIN:
            return 0   # ตลาด sideways — งดเทรด

        c, ef, es = h1_c[-1], ema_f[-1], ema_s[-1]
        if math.isnan(ef) or math.isnan(es):
            return 0

        if c > ef > es:   return  1   # bull: ราคา > EMA50 > EMA200
        if c < ef < es:   return -1   # bear: ราคา < EMA50 < EMA200
        return 0

    def _resample_h1(self, closes, highs, lows, n):
        """Resample M15 → H1 (ทุก 4 แท่ง = 1 ชั่วโมง)."""
        n_h1 = n // self.H1_BARS
        if n_h1 < self.EMA_H1_SLOW + 5:
            return None, None, None

        start = n - n_h1 * self.H1_BARS
        idx = [start + k * self.H1_BARS for k in range(n_h1)]

        h1_c = np.array([closes[j + self.H1_BARS - 1] for j in idx])
        h1_h = np.array([np.max(highs[j:j + self.H1_BARS])  for j in idx])
        h1_l = np.array([np.min(lows[j:j + self.H1_BARS])   for j in idx])
        return h1_c, h1_h, h1_l

    # ─── M15 Entry ────────────────────────────────────────────────────────

    def _m15_entry(self, d: dict, i: int, trend: int) -> Signal:
        """pullback เข้า EMA20 + แท่งยืนยัน."""
        if i < self.EMA_M15 + 3:
            return Signal()

        ema20  = self._ema(d["c"][:i + 1], self.EMA_M15)
        e_cur  = ema20[-1]
        e_prev = ema20[-2]

        if math.isnan(e_cur) or math.isnan(e_prev):
            return Signal()

        c_cur  = d["c"][i]
        o_cur  = d["o"][i]
        h_prev = d["h"][i - 1]; l_prev = d["l"][i - 1]

        tol = self.TOUCH_TOLERANCE

        if trend == 1:  # ── LONG: pullback ลงมาแตะ EMA20 แล้วเด้งขึ้น
            # แท่งก่อน: low แตะ EMA20 (±tolerance) หรือ close ต่ำกว่า EMA20
            touched    = l_prev <= e_prev * (1 + tol)
            # แท่งปัจจุบัน: ปิดเหนือ EMA20 และเป็นแท่งขึ้น (close > open)
            confirmed  = c_cur > e_cur and c_cur > o_cur
            if touched and confirmed:
                return Signal("BUY", f"H1-bull pullback e20={e_cur:.5f}")

        elif trend == -1:  # ── SHORT: pullback ขึ้นมาแตะ EMA20 แล้วดิ่งลง
            touched    = h_prev >= e_prev * (1 - tol)
            confirmed  = c_cur < e_cur and c_cur < o_cur
            if touched and confirmed:
                return Signal("SELL", f"H1-bear pullback e20={e_cur:.5f}")

        return Signal()

    # ─── Indicators (self-contained) ──────────────────────────────────────

    @staticmethod
    def _ema(prices: np.ndarray, span: int) -> np.ndarray:
        """Exponential Moving Average (numpy, ไม่ใช้ pandas)."""
        out = np.full(len(prices), np.nan)
        if len(prices) < span:
            return out
        alpha = 2.0 / (span + 1)
        out[0] = prices[0]
        for j in range(1, len(prices)):
            out[j] = prices[j] * alpha + out[j - 1] * (1 - alpha)
        return out

    @staticmethod
    def _wilder_smooth(arr: np.ndarray, period: int) -> np.ndarray:
        """Wilder smoothing — returns full array.

        NaN-safe: ค้นหา window แรกที่มีค่าครบ period ตัว (ไม่ใช่ NaN)
        เพื่อหลีกเลี่ยงกรณี dx[:period] มี NaN จาก pdi/mdi warmup
        ซึ่งจะทำให้ mean() = NaN แล้วกระจายไปทุก bar
        """
        n   = len(arr)
        out = np.full(n, np.nan)
        if n < period:
            return out

        # หา index แรกที่มีข้อมูล period ตัวติดกันโดยไม่มี NaN
        consec = 0
        first_start = -1
        for k in range(n):
            if not math.isnan(float(arr[k])):
                consec += 1
                if consec == period:
                    first_start = k - period + 1
                    break
            else:
                consec = 0
        if first_start < 0:
            return out   # ข้อมูลไม่พอ

        start_idx = first_start + period - 1
        out[start_idx] = float(np.mean(arr[first_start:first_start + period]))
        a = 1.0 / period
        for j in range(start_idx + 1, n):
            v = float(arr[j])
            if math.isnan(v):
                out[j] = out[j - 1]          # carry-forward ถ้า input NaN
            else:
                out[j] = out[j - 1] * (1 - a) + v * a
        return out

    @classmethod
    def _adx_array(cls, high: np.ndarray, low: np.ndarray,
                   close: np.ndarray, period: int = 14) -> np.ndarray:
        """ADX สำหรับทุก bar (array version) — ใช้ใน precompute().

        เร็วกว่า _adx_last() × n เพราะคำนวณทีเดียว O(n) ไม่ใช่ O(n²)
        """
        n = len(close)
        result = np.full(n, np.nan)
        if n < period * 3:
            return result

        prev_c = np.empty(n)
        prev_c[0] = close[0]
        prev_c[1:] = close[:-1]

        tr  = np.maximum(high - low,
                         np.maximum(np.abs(high - prev_c),
                                    np.abs(low  - prev_c)))
        up  = np.diff(high, prepend=high[0])
        dn  = -np.diff(low,  prepend=low[0])
        pdm = np.where((up > dn) & (up > 0), up, 0.0)
        mdm = np.where((dn > up) & (dn > 0), dn, 0.0)

        atr_w = cls._wilder_smooth(tr,  period)
        safe  = np.where(atr_w > 0, atr_w, 1.0)
        pdi   = 100.0 * cls._wilder_smooth(pdm, period) / safe
        mdi   = 100.0 * cls._wilder_smooth(mdm, period) / safe
        dsum  = np.where(pdi + mdi > 0, pdi + mdi, 1.0)
        dx    = 100.0 * np.abs(pdi - mdi) / dsum
        adx   = cls._wilder_smooth(dx, period)

        valid = period * 3
        result[valid:] = adx[valid:]
        return result

    @classmethod
    def _adx_last(cls, high: np.ndarray, low: np.ndarray,
                  close: np.ndarray, period: int = 14) -> float:
        """คืน ADX ค่าสุดท้าย — wrapper around _adx_array."""
        arr = cls._adx_array(high, low, close, period)
        valid = arr[~np.isnan(arr)]
        return float(valid[-1]) if len(valid) > 0 else 0.0

