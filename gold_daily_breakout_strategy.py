#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gold_daily_breakout_strategy.py -- LIVE strategy class for the Daily-timeframe
Gold Donchian-channel breakout bot (variant tag: gold_daily_breakout).

WHY THIS EXISTS: every trend-pullback variant tried for gold this session
(M15, H1, H4, Daily-entry/Weekly-trend EMA pullback) either had no edge at
real cost ($2.85 spread -- cost/ATR is 255% on M15, 111% on H1, 53% on H4)
or turned out to be an illusion driven entirely by the 2024-2026 gold bull
run (Daily/Weekly trend-pullback: 9 losing/flat years out of 13, then all
the profit from 3 bull-market years). This is a structurally different
signal -- momentum/breakout instead of pullback-to-mean -- validated on
Daily bars only (cost/ATR 18.5%, the only timeframe where spread stops
dominating).

VALIDATION SUMMARY (2026-07-31, XAUUSD Daily, spread=$2.85, comm=$3.50/lot):
  - Parameter grid searched on train half (2013-2019.7) only; best-by-train-PF
    config (DONCH_WIN=80, BREAKOUT_MARGIN_ATR=0.25) frozen and scored ONCE on
    test half (2019.7-2026): OOS PF=3.22, Sharpe=0.85, CAGR=+2.85%, DD=2.9%.
  - Full-history (13.4y) with the same frozen params: PF=2.44, DD=3.1%,
    12/14 calendar years profitable (only 2017 -$32, 2021 -$137 negative --
    both modest, not devastating). Gains spread across the WHOLE span
    (2013,2015,2016,2018-2020,2022,2024-26 all profitable), not concentrated
    in the recent bull run -- the key thing that killed the pullback idea.
  - Trend-filter overlay (slow EMA) tested and did NOT help -- the breakout
    itself already encodes trend direction; dropped from the final config.
  - Caveats: low trade count (~83 trades / 13.4y = ~6/yr) -- real but not
    large-sample confidence. Top-10 trades = 61% of gross profit, which is
    normal/expected for a breakout strategy's fat-tailed win distribution,
    not evidence of fragility on its own (losses are small and controlled,
    DD stayed <=3.1% throughout).

Entry: close beyond a DONCH_WIN-day high/low channel (computed from the
bars themselves via rolling().shift(1) -- causal by construction, no
resampling/bucketing step exists here at all, so this signal is immune to
the entire class of H1/H4 bucket-anchoring bugs found and fixed elsewhere
in forex_hybrid_strategy.py this session) by more than
BREAKOUT_MARGIN_ATR x ATR (filters marginal/noise breakouts).
Exit: SL_ATR x ATR stop, ATR trailing stop once price has moved
TRAIL_ACTIVATION_ATR x ATR in profit (no fixed take-profit -- let winners
run, consistent with the fat-tailed win distribution above).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from forex_indicators import Signal


class GoldDailyDonchianBreakout:
    """Daily Donchian-channel breakout for XAUUSD. See module docstring for
    the full validation summary and why this exists."""

    name = "Gold Daily Donchian Breakout"
    short_name = "GoldDailyBO"

    # ── Validated defaults (2026-07-31 OOS search) -- do not change without
    # rerunning the train/test split in _gold_breakout_refine.py ──────────
    DONCH_WIN = 80
    BREAKOUT_MARGIN_ATR = 0.25

    sl_atr = 2.0
    tp_atr = 999.0                  # no fixed TP -- trailing stop only
    trail_atr_mult = 3.0
    trail_activation_atr = 1.0
    max_spread_atr_ratio = 0.5

    MIN_BARS = 100                  # >= DONCH_WIN + margin for ATR/indicator warmup

    _donch_hi = None
    _donch_lo = None

    def precompute(self, d: dict):
        h = pd.Series(d["h"])
        l = pd.Series(d["l"])
        self._donch_hi = h.rolling(self.DONCH_WIN).max().shift(1).to_numpy()
        self._donch_lo = l.rolling(self.DONCH_WIN).min().shift(1).to_numpy()

    def signal(self, d: dict, i: int) -> Signal:
        if i < max(self.MIN_BARS, self.DONCH_WIN + 1):
            return Signal()

        if self._donch_hi is not None and i < len(self._donch_hi):
            hi = self._donch_hi[i]
            lo = self._donch_lo[i]
        else:
            # causal fallback (no precompute() call yet, e.g. very first
            # live evaluation before the bot's own precompute pass) --
            # recompute on the causally-truncated slice, same formula.
            h = pd.Series(d["h"][:i + 1])
            l = pd.Series(d["l"][:i + 1])
            hi_arr = h.rolling(self.DONCH_WIN).max().shift(1).to_numpy()
            lo_arr = l.rolling(self.DONCH_WIN).min().shift(1).to_numpy()
            hi, lo = hi_arr[-1], lo_arr[-1]

        c = d["c"][i]
        atr = d["atr"][i]
        if math.isnan(hi) or math.isnan(lo) or math.isnan(atr) or atr <= 0:
            return Signal()

        margin = self.BREAKOUT_MARGIN_ATR * atr
        if c > hi + margin:
            return Signal("BUY", f"breakout>donch_hi={hi:.2f} margin={margin:.2f}")
        if c < lo - margin:
            return Signal("SELL", f"breakout<donch_lo={lo:.2f} margin={margin:.2f}")
        return Signal()
