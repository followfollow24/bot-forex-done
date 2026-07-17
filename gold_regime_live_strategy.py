#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gold_regime_live_strategy.py -- LIVE strategy class for the "gold regime
filter" bot (variant tag: regime22, magic 555103). Frozen thresholds proven
on the real M15 engine this session (see gold_regime_filter_real_engine.py):
  ADX(14, H1) > 22  AND  ADX rising  AND  |EMA50-EMA200|(H1) > 1.2 x ATR(14, H1)
Real-engine validation: 2,848 trades (13yr), PF=1.29, MaxDD=10.3%, WF-A yearly
PF>1 in 12/14 years, half-split H1 PF=1.16 -> H2 PF=1.37 (no overfit signature).

NEWS FILTER DELIBERATELY OMITTED from this first live version. Two reasons:
  1. On the real engine it added negligible value (PF 1.29->1.31, TotRet%
     actually went DOWN slightly 219.5->218.9) -- not worth the added risk.
  2. The backtest news filter blocks by UTC hour using Dukascopy timestamps
     (confirmed UTC). The live bot's candle timestamps come from MT5
     copy_rates in BROKER SERVER time (Exness MT5Real20, typically UTC+2/+3,
     not confirmed here) -- wiring an hour-of-day filter without first
     confirming the live server-UTC offset risks silently blocking the WRONG
     hours live vs what was backtested. Given point 1 already shows negligible
     benefit, it is not worth that risk for this first deploy. Can be added
     later if desired, with the offset explicitly verified first.

Everything else (M15 EMA20 pullback entry, SL/TP ATR multiples, cost model)
is untouched -- inherited byte-for-byte from HybridTrendPullback, the exact
class the live bot already runs. Only _build_h1_trend_array is extended.
"""
import math

import numpy as np

from forex_hybrid_strategy import HybridTrendPullback

REGIME_ADX_MIN = 22
REGIME_GAP_MULT = 1.2


class RegimeFilteredHybridLive(HybridTrendPullback):
    """HybridTrendPullback + frozen regime filter on the H1 trend array.
    No other behavior changes -- SL/TP/entry/cost model identical to base."""

    def _build_h1_trend_array(self, d: dict) -> np.ndarray:
        n = len(d["c"])
        n_h1 = n // self.H1_BARS
        out = np.zeros(n, dtype=np.int8)
        if n_h1 < self.EMA_H1_SLOW + 5:
            return out

        idx = np.arange(n_h1) * self.H1_BARS
        h1_c = np.array([d["c"][j + self.H1_BARS - 1] for j in idx])
        h1_h = np.array([d["h"][j:j + self.H1_BARS].max() for j in idx])
        h1_l = np.array([d["l"][j:j + self.H1_BARS].min() for j in idx])

        ema_f = self._ema(h1_c, self.EMA_H1_FAST)
        ema_s = self._ema(h1_c, self.EMA_H1_SLOW)
        adx_a = self._adx_array(h1_h, h1_l, h1_c, self.ADX_PERIOD)

        prev_c = np.empty(n_h1)
        prev_c[0] = h1_c[0]
        prev_c[1:] = h1_c[:-1]
        tr = np.maximum(h1_h - h1_l, np.maximum(np.abs(h1_h - prev_c), np.abs(h1_l - prev_c)))
        atr_h1 = self._wilder_smooth(tr, self.ADX_PERIOD)

        adx_rising = adx_a > np.roll(adx_a, 1)
        adx_rising[0] = False
        gap_ok = np.abs(ema_f - ema_s) > (REGIME_GAP_MULT * atr_h1)

        h1_trend = np.zeros(n_h1, dtype=np.int8)
        for k in range(n_h1):
            ef, es, adx = ema_f[k], ema_s[k], adx_a[k]
            if math.isnan(ef) or math.isnan(es) or math.isnan(adx):
                continue
            if adx < REGIME_ADX_MIN:
                continue
            if not adx_rising[k]:
                continue
            if not gap_ok[k]:
                continue
            c = h1_c[k]
            if c > ef > es:
                h1_trend[k] = 1
            elif c < ef < es:
                h1_trend[k] = -1

        for i in range(n):
            k = i // self.H1_BARS
            if k < n_h1:
                out[i] = h1_trend[k]
        return out
