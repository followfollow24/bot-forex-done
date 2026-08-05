#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tri_timeframe_strategy.py -- 3-timeframe trend-pullback (M15 entry / H1 + H4 trend).

WHY THIS EXISTS
---------------
The live trend-pullback bots already use TWO timeframes, which the startup
banner mislabels. With `--timeframe 1h`, HybridTrendPullback's
_bucket_seconds() = TIMEFRAME_SECONDS(3600) * H1_BARS(4) = 4h, so the actual
structure is H1 entry + H4 trend. This class adds the third: M15 entries
timed inside an H1 trend that must itself agree with the H4 trend.

  H4  : slow bias      -- EMA50/200 (+ADX) on 16-M15-bar buckets
  H1  : confirmation   -- EMA50/200 (+ADX) on 4-M15-bar buckets
  M15 : entry timing   -- pullback to EMA20 + confirmation candle (inherited)

A signal fires only when H4 and H1 point the SAME way. That is strictly more
selective than either alone, so it trades less often -- the point is quality,
not frequency.

CAUSALITY
---------
Both trend arrays are built with the project's timestamp-anchored bucketing
(the post-2026-07-30 fix): a bar only sees a bucket once that bucket has fully
CLOSED, derived from the bar's own timestamp rather than from whether a "next"
row happens to exist in the array. This is the exact bug class that silently
disabled the regime filter before, so the same helper is reused rather than
reimplemented, and the live path (precompute() never called) is asserted to
match the precomputed path in the test harness.

COST NOTE
---------
Gold results in this repo computed at SPREAD=2.85 are invalid -- the real
XAUUSDc spread is $0.24 (MT5-measured 2026-08-05, point 0.001, 240 points).
2.85 is 255% of gold's median M15 ATR, which no broker charges. M15 gold was
previously written off using that bogus number, so it is re-tested here.
"""
from __future__ import annotations

import numpy as np

from forex_indicators import Signal
from forex_hybrid_strategy import HybridTrendPullback


class TriTimeframePullback(HybridTrendPullback):
    """M15 entry, gated by BOTH the H1 and H4 trend agreeing."""

    name = "Tri-Timeframe Pullback (M15 entry / H1+H4 trend)"
    short_name = "Tri3TF"

    # entry bars are M15; H1 bucket = 4 x M15, H4 bucket = 16 x M15
    H1_BARS = 4          # inherited machinery builds the H1 trend from this
    H4_BARS = 16
    REQUIRE_H4 = True    # False -> behaves like the plain 2-TF version

    MIN_BARS = HybridTrendPullback.EMA_H1_SLOW * 16 + 50

    _h4_trend_arr = None
    _h4_built_len = None

    # ---- H4 trend, built with the SAME causal helper as the H1 one --------
    def _build_h4_trend_array(self, d: dict) -> np.ndarray:
        """Identical construction to _build_h1_trend_array but on 16-bar buckets.

        Implemented by temporarily swapping H1_BARS, so there is exactly ONE
        bucketing implementation in the codebase and the H4 array cannot drift
        away from the H1 one if that code is ever fixed again.
        """
        saved = self.H1_BARS
        try:
            self.H1_BARS = self.H4_BARS
            return self.__class__.__mro__[1]._build_h1_trend_array(self, d)
        finally:
            self.H1_BARS = saved

    def precompute(self, d: dict):
        super().precompute(d)
        self._h4_trend_arr = self._build_h4_trend_array(d)
        self._h4_built_len = len(d["c"])

    def _h4_trend(self, d: dict, i: int) -> int:
        """Live path: rebuild over d truncated to i+1 so nothing after i is seen."""
        n = i + 1
        d_slice = {k: (v[:n] if hasattr(v, "__len__") and len(v) > n else v)
                   for k, v in d.items()}
        arr = self._build_h4_trend_array(d_slice)
        return int(arr[i]) if i < len(arr) else 0

    def signal(self, d: dict, i: int) -> Signal:
        if i < self.MIN_BARS:
            return Signal()

        # --- H1 trend (inherited; cached when precompute() was called) ---
        if self._h1_trend_arr is not None and len(self._h1_trend_arr) == len(d["c"]):
            trend_h1 = int(self._h1_trend_arr[i])
        else:
            trend_h1 = self._h1_trend(d, i)
        if trend_h1 == 0:
            return Signal()

        # --- H4 trend must agree ---
        if self.REQUIRE_H4:
            if self._h4_trend_arr is not None and len(self._h4_trend_arr) == len(d["c"]):
                trend_h4 = int(self._h4_trend_arr[i])
            else:
                trend_h4 = self._h4_trend(d, i)
            if trend_h4 != trend_h1:
                return Signal()

        # --- M15 entry timing (inherited pullback + confirmation candle) ---
        return self._m15_entry(d, i, trend_h1)
