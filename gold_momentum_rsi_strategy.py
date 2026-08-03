#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gold_momentum_rsi_strategy.py -- LIVE strategy class for the Gold H1
momentum+RSI bot (variant tag: gold_momentum_rsi).

>>> VALIDATION STATUS: FAILS BACKTEST -- DEPLOYED ANYWAY BY EXPLICIT   <<<
>>> USER DECISION, AS AN ENTRY SIGNAL ONLY. Exit is manual (SL only,  <<<
>>> no auto-TP/trailing) -- same category as btc_amd_sweep/gold_h1_manual. <<<

THE SPEC (exact, 2026-08-03, checked on H1 candle close)
----------------------------------------------------------------------
  BUY : close > EMA20 and close > EMA50; EMA20 > EMA50; RSI(14) touched
        <=30 at any point in the last 3 closed bars; current candle bullish
        (close > open).
  SELL: mirror (close < both EMAs, EMA20 < EMA50, RSI touched >=70 in the
        last 3 bars, current candle bearish).

This is the 3rd iteration of a gold trend+RSI family tested 2026-08-03:
  round 1 (pullback-touch entry, PDH/PDL TP)      : PF 0.29 real cost,
                                                     PF 0.81 at ZERO cost
  round 2 (same entry, Fixed R:R=1.5 TP)          : PF 0.32 real cost,
                                                     PF 0.88 at ZERO cost
  round 3 (THIS spec: momentum entry, not pullback,
           Fixed R:R=1.5 TP)                      : PF 0.41 real cost,
                                                     PF 0.93 at ZERO cost
                                                     <- best of the three,
                                                        still fails
Each round genuinely improved (lower DD too: 63.9% -> 37.2%), but even with
ZERO transaction cost the entry has never cleared PF 1.0. OOS split for
round 3 is consistent (train PF 0.42, test PF 0.39) -- a real, mild,
persistent negative edge, not overfitting noise. Yearly walk-forward:
1/13 years profitable (2024 only).

Backtested with SL beyond the current bar's low/high (+ buffer) and TP at
a fixed 1.5x that risk. LIVE this bot uses --manual-exit (SL only, no
auto-TP) at the user's explicit request, so the backtest's TP-timing
assumption does not describe live behavior -- only the ENTRY signal
(which is what was actually validated, and what failed) carries over.

LIVE-PATH SAFETY
----------------
The live bot never calls precompute() (confirmed pattern from every prior
strategy this session -- exactly what silently disabled the regime filter
before the 2026-07-30 fix). This class rebuilds its EMA/RSI arrays inside
signal() via _ensure() whenever missing or stale. Verified: precomputed vs
never-precomputed paths give identical signals.
"""
from __future__ import annotations

import numpy as np

from forex_indicators import Signal


def _ema(prices: np.ndarray, span: int) -> np.ndarray:
    out = np.full(len(prices), np.nan)
    if len(prices) < span:
        return out
    alpha = 2.0 / (span + 1)
    out[0] = prices[0]
    for j in range(1, len(prices)):
        out[j] = prices[j] * alpha + out[j - 1] * (1 - alpha)
    return out


def _rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(closes)
    out = np.full(n, np.nan)
    if n < period + 1:
        return out
    delta = np.diff(closes)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = np.zeros(n)
    avg_loss = np.zeros(n)
    avg_gain[period] = gain[:period].mean()
    avg_loss[period] = loss[:period].mean()
    for i in range(period + 1, n):
        avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gain[i - 1]) / period
        avg_loss[i] = (avg_loss[i - 1] * (period - 1) + loss[i - 1]) / period
    rs = np.divide(avg_gain, avg_loss, out=np.full(n, np.inf), where=avg_loss > 0)
    out[period:] = 100 - 100 / (1 + rs[period:])
    out[avg_loss == 0] = 100.0
    out[:period] = np.nan
    return out


class GoldMomentumRSI:
    """Gold H1 momentum + RSI-recent entry. See module docstring for the
    full (failing) validation record -- deployed as entry-signal-only."""

    name = "Gold Momentum + RSI-recent (H1)"
    short_name = "GoldMomRSI"

    EMA_FAST = 20
    EMA_SLOW = 50
    RSI_PERIOD = 14
    RSI_LOOKBACK = 3
    RSI_BUY_TRIGGER = 30
    RSI_SELL_TRIGGER = 70
    SL_BUFFER_ATR = 0.30
    FIXED_RR = 1.5          # backtested exit; live overridden by --manual-exit

    sl_atr = 2.0
    tp_atr = 999.0
    trail_atr_mult = 999.0
    trail_activation_atr = 999.0
    max_spread_atr_ratio = 1.0
    MIN_BARS = 100

    _built_len = None

    def precompute(self, d: dict):
        c = d["c"]
        self._ema_fast = _ema(c, self.EMA_FAST)
        self._ema_slow = _ema(c, self.EMA_SLOW)
        self._rsi = _rsi(c, self.RSI_PERIOD)
        self._built_len = len(c)

    def _ensure(self, d: dict):
        if self._built_len != len(d["c"]):
            self.precompute(d)

    def signal(self, d: dict, i: int) -> Signal:
        if i < self.MIN_BARS:
            return Signal()
        self._ensure(d)
        atr = d["atr"][i]
        if np.isnan(atr) or atr <= 0:
            return Signal()

        ef, es = self._ema_fast[i], self._ema_slow[i]
        if np.isnan(ef) or np.isnan(es):
            return Signal()

        o, h, l, c = d["o"][i], d["h"][i], d["l"][i], d["c"][i]
        lb0 = max(0, i - self.RSI_LOOKBACK + 1)
        rsi_window = self._rsi[lb0:i + 1]
        if np.all(np.isnan(rsi_window)):
            return Signal()

        if c > ef and c > es and ef > es and c > o:
            if np.nanmin(rsi_window) <= self.RSI_BUY_TRIGGER:
                sl_price = l - self.SL_BUFFER_ATR * atr
                risk = c - sl_price
                if risk > 0:
                    self.sl_atr = risk / atr
                    self.tp_atr = risk * self.FIXED_RR / atr
                    return Signal("BUY", f"mom RSI_recent_min={np.nanmin(rsi_window):.0f}")

        if c < ef and c < es and ef < es and c < o:
            if np.nanmax(rsi_window) >= self.RSI_SELL_TRIGGER:
                sl_price = h + self.SL_BUFFER_ATR * atr
                risk = sl_price - c
                if risk > 0:
                    self.sl_atr = risk / atr
                    self.tp_atr = risk * self.FIXED_RR / atr
                    return Signal("SELL", f"mom RSI_recent_max={np.nanmax(rsi_window):.0f}")

        return Signal()
