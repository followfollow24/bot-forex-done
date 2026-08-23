#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 strategy_zoo.py — 6 Alternative Signal Generators for XAUUSD M15
================================================================================
 ทุก strategy ใช้ interface เดียวกับ HybridTrendPullback:
   strategy.precompute(d)          — O(n) pre-pass (optional)
   strategy.signal(d, i) -> Signal  — คืน BUY/SELL/HOLD ที่ bar i

 No-look-ahead: signal ของ bar i ใช้ข้อมูลถึง close[i] เท่านั้น
                entry ถูก execute ที่ open[i+1]

 d dict keys ที่ใช้ได้:
   o, h, l, c, atr, zscore, donch_hi, donch_lo,
   ema_fast (24), ema_slow (96), ema (96), roc, ts (str "YYYY-MM-DD HH:MM:SS")
================================================================================
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

from forex_indicators import Signal


# =============================================================================
# 1. London Breakout (LON-BO)
# =============================================================================
class LondonBreakoutStrategy:
    """
    Pre-session range  06:00–07:00 UTC (4 M15 bars)
    Entry window       07:00–11:00 UTC
    BUY  if close > range_high + 0.15*ATR
    SELL if close < range_low  - 0.15*ATR
    One-trade-per-day via day tracking.
    """
    name       = "London Breakout"
    short_name = "LON-BO"
    sl_atr             = 2.5
    tp_atr             = 7.0
    trail_atr_mult       = 999.0
    trail_activation_atr = 999.0
    MIN_BARS = 50

    RANGE_H_START = 6   # UTC — range collection start hour
    RANGE_H_END   = 7   # UTC — range collection end hour (exclusive)
    ENTRY_H_START = 7   # UTC — entry window start
    ENTRY_H_END   = 11  # UTC — entry window end (exclusive)

    def __init__(self):
        self._traded_day: Optional[str] = None   # "YYYY-MM-DD"

    def precompute(self, d: dict) -> None:
        self._traded_day = None  # reset on new dataset

    def signal(self, d: dict, i: int) -> Signal:
        if i < self.MIN_BARS:
            return Signal()

        ts = d["ts"][i]
        try:
            h_cur = int(ts[11:13])
            day   = ts[:10]
        except Exception:
            return Signal()

        if h_cur < self.ENTRY_H_START or h_cur >= self.ENTRY_H_END:
            return Signal()
        if self._traded_day == day:
            return Signal()

        atr = d["atr"][i]
        if math.isnan(atr) or atr <= 0:
            return Signal()

        # Collect range bars in 06:00–07:00 UTC today
        rng_hi, rng_lo = -np.inf, np.inf
        found = False
        for j in range(i - 1, max(i - 30, self.MIN_BARS - 1), -1):
            tj = d["ts"][j]
            if tj[:10] != day:
                break
            hj = int(tj[11:13])
            if self.RANGE_H_START <= hj < self.RANGE_H_END:
                rng_hi = max(rng_hi, d["h"][j])
                rng_lo = min(rng_lo, d["l"][j])
                found  = True

        if not found or rng_hi <= rng_lo:
            return Signal()

        buf = 0.15 * atr
        c   = d["c"][i]

        if c > rng_hi + buf:
            self._traded_day = day
            return Signal("BUY",  f"LON-BO break_hi={rng_hi:.2f} atr={atr:.2f}")
        if c < rng_lo - buf:
            self._traded_day = day
            return Signal("SELL", f"LON-BO break_lo={rng_lo:.2f} atr={atr:.2f}")
        return Signal()


# =============================================================================
# 2. EMA Cross (EMA-X) 9/21/50
# =============================================================================
class EMACrossStrategy:
    """
    Triple-EMA system:
      EMA(9) crosses EMA(21) in direction of EMA(50) → BUY/SELL
    Plus ATR expansion filter: atr[i] > median ATR of last 48 bars.
    """
    name       = "EMA Cross 9/21/50"
    short_name = "EMA-X"
    sl_atr             = 2.5
    tp_atr             = 7.0
    trail_atr_mult       = 999.0
    trail_activation_atr = 999.0
    MIN_BARS = 60

    def __init__(self):
        self._e9:  Optional[np.ndarray] = None
        self._e21: Optional[np.ndarray] = None
        self._e50: Optional[np.ndarray] = None

    def precompute(self, d: dict) -> None:
        c = d["c"]
        self._e9  = self._ema(c, 9)
        self._e21 = self._ema(c, 21)
        self._e50 = self._ema(c, 50)

    def signal(self, d: dict, i: int) -> Signal:
        if i < self.MIN_BARS:
            return Signal()
        if self._e9 is None:
            self.precompute(d)

        e9, e21, e50 = self._e9, self._e21, self._e50
        if math.isnan(e9[i]) or math.isnan(e21[i]) or math.isnan(e50[i]):
            return Signal()

        atr = d["atr"][i]
        if math.isnan(atr) or atr <= 0:
            return Signal()

        # ATR expansion: current ATR > median of last 48 bars
        atr_window = d["atr"][max(0, i - 47):i + 1]
        atr_window = atr_window[~np.isnan(atr_window)]
        if len(atr_window) < 10 or atr < np.median(atr_window):
            return Signal()

        # Cross: previous state vs current state
        prev_bull = e9[i - 1] > e21[i - 1]
        curr_bull = e9[i]     > e21[i]

        if not prev_bull and curr_bull and e50[i] > 0:   # cross up
            if e9[i] > e50[i]:                            # trend agrees
                return Signal("BUY",  f"EMA-X cross_up e9={e9[i]:.2f} e50={e50[i]:.2f}")

        if prev_bull and not curr_bull and e50[i] > 0:   # cross down
            if e9[i] < e50[i]:
                return Signal("SELL", f"EMA-X cross_dn e9={e9[i]:.2f} e50={e50[i]:.2f}")

        return Signal()

    @staticmethod
    def _ema(prices: np.ndarray, span: int) -> np.ndarray:
        out   = np.full(len(prices), np.nan)
        alpha = 2.0 / (span + 1)
        out[0] = prices[0]
        for j in range(1, len(prices)):
            out[j] = prices[j] * alpha + out[j - 1] * (1 - alpha)
        return out


# =============================================================================
# 3. RSI Divergence (RSI-DIV)
# =============================================================================
class RSIDivergenceStrategy:
    """
    Detects classic RSI divergence over a 20-bar lookback:
      Bullish: price lower-low  + RSI higher-low  + RSI < 38
      Bearish: price higher-high + RSI lower-high + RSI > 62
    """
    name       = "RSI Divergence"
    short_name = "RSI-DIV"
    sl_atr             = 2.5
    tp_atr             = 7.0
    trail_atr_mult       = 999.0
    trail_activation_atr = 999.0
    MIN_BARS   = 50
    RSI_PERIOD = 14
    LOOKBACK   = 20

    def __init__(self):
        self._rsi: Optional[np.ndarray] = None

    def precompute(self, d: dict) -> None:
        self._rsi = self._compute_rsi(d["c"], self.RSI_PERIOD)

    def signal(self, d: dict, i: int) -> Signal:
        if i < self.MIN_BARS:
            return Signal()
        if self._rsi is None:
            self.precompute(d)

        rsi = self._rsi[i]
        if math.isnan(rsi):
            return Signal()

        lb    = self.LOOKBACK
        start = max(0, i - lb)

        # ── Bullish divergence ────────────────────────────────────────────
        if rsi < 38:
            lows_window   = d["l"][start:i]
            if len(lows_window) > 0:
                prev_low_idx  = int(np.argmin(lows_window)) + start
                prev_low_rsi  = self._rsi[prev_low_idx]
                if (not math.isnan(prev_low_rsi)
                        and d["l"][i] < d["l"][prev_low_idx]   # price: lower low
                        and rsi > prev_low_rsi):                # RSI: higher low
                    return Signal("BUY", f"RSI-DIV bull rsi={rsi:.1f}")

        # ── Bearish divergence ────────────────────────────────────────────
        if rsi > 62:
            highs_window  = d["h"][start:i]
            if len(highs_window) > 0:
                prev_hi_idx   = int(np.argmax(highs_window)) + start
                prev_hi_rsi   = self._rsi[prev_hi_idx]
                if (not math.isnan(prev_hi_rsi)
                        and d["h"][i] > d["h"][prev_hi_idx]    # price: higher high
                        and rsi < prev_hi_rsi):                 # RSI: lower high
                    return Signal("SELL", f"RSI-DIV bear rsi={rsi:.1f}")

        return Signal()

    @staticmethod
    def _compute_rsi(close: np.ndarray, period: int) -> np.ndarray:
        n     = len(close)
        rsi   = np.full(n, np.nan)
        delta = np.diff(close, prepend=close[0])
        gains  = np.where(delta > 0, delta, 0.0)
        losses = np.where(delta < 0, -delta, 0.0)

        avg_g = np.zeros(n)
        avg_l = np.zeros(n)
        if n <= period:
            return rsi

        avg_g[period] = float(np.mean(gains[1:period + 1]))
        avg_l[period] = float(np.mean(losses[1:period + 1]))
        for j in range(period + 1, n):
            avg_g[j] = (avg_g[j - 1] * (period - 1) + gains[j])  / period
            avg_l[j] = (avg_l[j - 1] * (period - 1) + losses[j]) / period

        safe = np.where(avg_l[period:] > 0, avg_l[period:], 1e-10)
        rsi[period:] = 100.0 - 100.0 / (1.0 + avg_g[period:] / safe)
        return rsi


# =============================================================================
# 4. Fair Value Gap (FVG)
# =============================================================================
class FairValueGapStrategy:
    """
    Bullish FVG:  high[i-2] < low[i]  (3-candle up-gap)
    Bearish FVG:  low[i-2]  > high[i] (3-candle down-gap)
    Signal fires on bar i (gap just formed) with EMA trend alignment.
    Gap must be at least 0.10×ATR wide to filter noise.
    """
    name       = "Fair Value Gap"
    short_name = "FVG"
    sl_atr             = 2.5
    tp_atr             = 7.0
    trail_atr_mult       = 999.0
    trail_activation_atr = 999.0
    MIN_BARS = 30

    def precompute(self, d: dict) -> None:
        pass

    def signal(self, d: dict, i: int) -> Signal:
        if i < self.MIN_BARS:
            return Signal()

        atr = d["atr"][i]
        if math.isnan(atr) or atr <= 0:
            return Signal()

        ef = d["ema_fast"][i]
        es = d["ema_slow"][i]
        if math.isnan(ef) or math.isnan(es):
            return Signal()

        gap_min = 0.10 * atr

        # Bullish FVG: gap between high[i-2] and low[i]
        gap_top = d["l"][i]
        gap_bot = d["h"][i - 2]
        if gap_top > gap_bot + gap_min and ef > es:
            return Signal("BUY",
                          f"FVG-bull gap={gap_bot:.2f}–{gap_top:.2f}")

        # Bearish FVG: gap between low[i-2] and high[i]
        gap_top2 = d["l"][i - 2]
        gap_bot2 = d["h"][i]
        if gap_top2 > gap_bot2 + gap_min and ef < es:
            return Signal("SELL",
                          f"FVG-bear gap={gap_bot2:.2f}–{gap_top2:.2f}")

        return Signal()


# =============================================================================
# 5. Donchian Breakout + ATR Expansion (DOCH-BO)
# =============================================================================
class DonchianBreakoutStrategy:
    """
    Uses pre-built donch_hi / donch_lo (48-bar, already shifted → no look-ahead).
    Entry: close breaks channel boundary AND ATR is expanding.
    ATR expansion = atr[i] > 1.1 × rolling mean ATR of last 24 bars.
    """
    name       = "Donchian Breakout"
    short_name = "DOCH-BO"
    sl_atr             = 2.5
    tp_atr             = 7.0
    trail_atr_mult       = 999.0
    trail_activation_atr = 999.0
    MIN_BARS = 60

    def precompute(self, d: dict) -> None:
        pass

    def signal(self, d: dict, i: int) -> Signal:
        if i < self.MIN_BARS:
            return Signal()

        c    = d["c"][i]
        dhi  = d["donch_hi"][i]
        dlo  = d["donch_lo"][i]
        atr  = d["atr"][i]

        if math.isnan(dhi) or math.isnan(dlo) or math.isnan(atr) or atr <= 0:
            return Signal()

        # ATR expansion filter
        window = d["atr"][max(0, i - 23):i]
        window = window[~np.isnan(window)]
        if len(window) < 12:
            return Signal()
        mean_atr = float(np.mean(window))
        if atr < 1.1 * mean_atr:
            return Signal()

        if c > dhi:
            return Signal("BUY",  f"DOCH-BO break_hi={dhi:.2f} atr={atr:.2f}")
        if c < dlo:
            return Signal("SELL", f"DOCH-BO break_lo={dlo:.2f} atr={atr:.2f}")
        return Signal()


# =============================================================================
# 6. Asian Range Breakout (ASIA-BO)
# =============================================================================
class AsianRangeBreakoutStrategy:
    """
    Asian session range  00:00–08:00 UTC.
    Entry window         08:00–17:00 UTC.
    BUY  if close > asia_high + 0.15*ATR
    SELL if close < asia_low  - 0.15*ATR
    One-trade-per-day.
    """
    name       = "Asian Range Breakout"
    short_name = "ASIA-BO"
    sl_atr             = 2.5
    tp_atr             = 7.0
    trail_atr_mult       = 999.0
    trail_activation_atr = 999.0
    MIN_BARS = 50

    RANGE_H_START = 0    # UTC range collection
    RANGE_H_END   = 8    # UTC range collection end (exclusive)
    ENTRY_H_START = 8    # UTC entry window start
    ENTRY_H_END   = 17   # UTC entry window end (exclusive)

    def __init__(self):
        self._traded_day: Optional[str] = None

    def precompute(self, d: dict) -> None:
        self._traded_day = None

    def signal(self, d: dict, i: int) -> Signal:
        if i < self.MIN_BARS:
            return Signal()

        ts = d["ts"][i]
        try:
            h_cur = int(ts[11:13])
            day   = ts[:10]
        except Exception:
            return Signal()

        if h_cur < self.ENTRY_H_START or h_cur >= self.ENTRY_H_END:
            return Signal()
        if self._traded_day == day:
            return Signal()

        atr = d["atr"][i]
        if math.isnan(atr) or atr <= 0:
            return Signal()

        # Collect Asian session range bars
        asia_hi, asia_lo = -np.inf, np.inf
        found = False
        for j in range(i - 1, max(i - 40, self.MIN_BARS - 1), -1):
            tj = d["ts"][j]
            if tj[:10] != day:
                break
            hj = int(tj[11:13])
            if self.RANGE_H_START <= hj < self.RANGE_H_END:
                asia_hi = max(asia_hi, d["h"][j])
                asia_lo = min(asia_lo, d["l"][j])
                found   = True

        if not found or asia_hi <= asia_lo:
            return Signal()

        buf = 0.15 * atr
        c   = d["c"][i]

        if c > asia_hi + buf:
            self._traded_day = day
            return Signal("BUY",  f"ASIA-BO break_hi={asia_hi:.2f}")
        if c < asia_lo - buf:
            self._traded_day = day
            return Signal("SELL", f"ASIA-BO break_lo={asia_lo:.2f}")
        return Signal()


# =============================================================================
# Registry — ordered for display
# =============================================================================
ALL_STRATEGIES = [
    LondonBreakoutStrategy,
    EMACrossStrategy,
    RSIDivergenceStrategy,
    FairValueGapStrategy,
    DonchianBreakoutStrategy,
    AsianRangeBreakoutStrategy,
]
