#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ict_tools_strategies.py -- three ICT/SMC tools, each as its own standalone
LIVE strategy (one bot per tool, as requested 2026-08-02).

  ToolAMD        -- TIME anchored.  Asian session builds the accumulation
                    range; London/NY manipulates it (breaks out, closes back
                    inside); distribution is traded the other way.
  ToolLQSweep    -- LEVEL anchored. Liquidity pools = prior-day high/low and
                    recent confirmed swing highs/lows. Sweep + rejection.
                    No session timing -- fires any hour.
  ToolTPOProfile -- PROFILE anchored. Prior day's TPO profile (POC + 70%
                    value area). Trades price RE-ENTERING the value area
                    from outside, back toward POC.

WHY SEPARATE MATTERS -- measured, not assumed
---------------------------------------------
The earlier combined version (amd_sweep_tpo_strategy.py, all three stacked
as one filter chain) scored PF 0.96 on BTC H1. Splitting the tools apart
scored BETTER on every one of them: the combination was destroying signal,
not adding confluence. Full-history BTC H1, real $10 spread:
    combined  PF 0.96   |   AMD 1.01   |   LQ-Sweep 1.06   |   TPO 1.14

VALIDATION (2026-08-02, BTC H1, real costs, OOS = 2nd half, params frozen)
--------------------------------------------------------------------------
  TPO-Profile : train PF 1.16 -> OOS PF 1.13, Sharpe 0.75 -> 0.57,
                CAGR +12.3% OOS.  BEST RETURNS.
                Caveat: yearly walk-forward only 5/10 years PF>1, and OOS
                DD 42.6% -- the return is real but lumpy.
  LQ-Sweep    : train PF 1.07 -> OOS PF 1.06, Sharpe 0.44 -> 0.44,
                DD 29.9% -> 31.5%.  MOST CONSISTENT train/test agreement of
                the three, though the edge is thin.
  AMD         : train PF 0.89 (FAILS) -> OOS PF 1.09.  Train and test
                disagree in opposite directions, so the positive OOS number
                is not trustworthy. WEAKEST -- deployed at reduced risk
                purely as a live observation, not as a validated edge.

  On ETH all three are marginal (OOS PF 0.97-1.07) and on GOLD H1 all three
  fail badly (PF 0.51-0.63, DD >93%) -- consistent with gold's 17% cost/ATR
  at H1. These classes are therefore configured for BTC H1 only.

VOLUME CAVEAT
-------------
No price file in this project has a volume column (verified 2026-08-01), so
ToolTPOProfile builds a genuine TPO (Time-Price-Opportunity) profile --
Steidlmayer's original Market Profile, weighting each level by TIME spent
there rather than volume traded. It is a real, well-defined profile, not a
stand-in for volume. Real volume would be a one-line change in
_day_profile().

LIVE-PATH SAFETY
----------------
The live bot never calls precompute() (it calls only strategy.signal(d, i)),
which is exactly what silently disabled the regime filter in production
before the 2026-07-30 fix. Every class here therefore rebuilds its arrays
inside signal() via _ensure() whenever they are missing or stale. Causally
safe: the live buffer holds only closed candles up to the current bar, and
every level is derived from a completed prior day, a closed session window,
or a swing confirmed SWING_LOOKBACK bars after the fact.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from forex_indicators import Signal


def _epoch_seconds(ts) -> np.ndarray:
    return pd.to_datetime(pd.Series(ts)).astype("datetime64[s]").astype("int64").to_numpy()


def _day_profile(lows, highs, bins=30, value_frac=0.70):
    """TPO profile for one day -> (poc, vah, val). Each bar adds 1 TPO to
    every bin its range covers. Value area = smallest contiguous band around
    the POC holding `value_frac` of all TPOs. Swap the weight here for
    traded volume if real volume data ever becomes available."""
    lo, hi = float(np.min(lows)), float(np.max(highs))
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        return float("nan"), float("nan"), float("nan")
    edges = np.linspace(lo, hi, bins + 1)
    centers = (edges[:-1] + edges[1:]) / 2.0
    counts = np.zeros(bins)
    for bl, bh in zip(lows, highs):
        i0 = max(np.searchsorted(edges, bl, side="right") - 1, 0)
        i1 = min(np.searchsorted(edges, bh, side="left"), bins)
        if i1 > i0:
            counts[i0:i1] += 1.0
    total = counts.sum()
    if total <= 0:
        return float("nan"), float("nan"), float("nan")
    poc_i = int(np.argmax(counts))
    lo_i = hi_i = poc_i
    acc = counts[poc_i]
    target = total * value_frac
    while acc < target and (lo_i > 0 or hi_i < bins - 1):
        take_lo = counts[lo_i - 1] if lo_i > 0 else -1.0
        take_hi = counts[hi_i + 1] if hi_i < bins - 1 else -1.0
        if take_hi >= take_lo:
            hi_i += 1; acc += take_hi
        else:
            lo_i -= 1; acc += take_lo
    return float(centers[poc_i]), float(centers[hi_i]), float(centers[lo_i])


class _ToolBase:
    """Shared exit config + the live-path rebuild guard."""
    sl_atr = 2.0
    tp_atr = 999.0
    trail_atr_mult = 999.0          # manual exit -- no auto trailing
    trail_activation_atr = 999.0
    max_spread_atr_ratio = 0.5
    MIN_BARS = 200
    _built_len = None

    def _ensure(self, d: dict):
        if self._built_len != len(d["c"]):
            self.precompute(d)


class ToolAMD(_ToolBase):
    """1. AMD -- pure session structure (time anchored). WEAKEST of the
    three: BTC H1 train PF 0.89 / OOS PF 1.09 -- the halves disagree, so
    treat the positive OOS number as unproven."""
    name = "AMD (Accumulation-Manipulation-Distribution)"
    short_name = "AMD"

    ACC_END_H = 7
    HUNT_END_H = 16
    SWEEP_MIN_ATR = 0.10

    def precompute(self, d: dict):
        epoch = _epoch_seconds(d["ts"]); n = len(d["c"])
        day = epoch // 86400
        hour = (epoch % 86400) // 3600
        h, l = d["h"], d["l"]
        in_acc = hour < self.ACC_END_H
        self._ready = hour >= self.ACC_END_H
        self._hunt = (hour >= self.ACC_END_H) & (hour < self.HUNT_END_H)
        df = pd.DataFrame({"day": day, "in_acc": in_acc, "h": h, "l": l})
        acc = df[df["in_acc"]]
        self._hi = df["day"].map(acc.groupby("day")["h"].max()).to_numpy(dtype=float)
        self._lo = df["day"].map(acc.groupby("day")["l"].min()).to_numpy(dtype=float)
        sh = np.zeros(n, bool); sl_ = np.zeros(n, bool)
        cur = None; a = b = False
        for i in range(n):
            if day[i] != cur:
                cur = day[i]; a = b = False
            if self._ready[i]:
                if np.isfinite(self._hi[i]) and h[i] > self._hi[i]: a = True
                if np.isfinite(self._lo[i]) and l[i] < self._lo[i]: b = True
            sh[i] = a; sl_[i] = b
        self._swept_hi, self._swept_lo = sh, sl_
        self._built_len = n

    def signal(self, d: dict, i: int) -> Signal:
        if i < self.MIN_BARS: return Signal()
        self._ensure(d)
        if i >= len(self._hunt) or not self._hunt[i]: return Signal()
        atr = d["atr"][i]
        if np.isnan(atr) or atr <= 0: return Signal()
        ah, al = self._hi[i], self._lo[i]
        if not np.isfinite(ah) or not np.isfinite(al): return Signal()
        h, l, c = d["h"][i], d["l"][i], d["c"][i]
        m = self.SWEEP_MIN_ATR * atr
        if self._swept_hi[i] and h > ah + m and c < ah:
            return Signal("SELL", f"AMD manip-high acc_hi={ah:.2f}")
        if self._swept_lo[i] and l < al - m and c > al:
            return Signal("BUY", f"AMD manip-low acc_lo={al:.2f}")
        return Signal()


class ToolLQSweep(_ToolBase):
    """2. Liquidity Sweep -- level anchored, no session timing. MOST
    CONSISTENT of the three: BTC H1 train PF 1.07 -> OOS 1.06, Sharpe
    0.44 -> 0.44, DD 29.9% -> 31.5%. Thin but stable edge."""
    name = "Liquidity Sweep (PDH/PDL + swings)"
    short_name = "LQ-Sweep"

    SWING_LOOKBACK = 5
    SWEEP_MIN_ATR = 0.15

    def precompute(self, d: dict):
        epoch = _epoch_seconds(d["ts"]); n = len(d["c"])
        day = epoch // 86400
        h, l = d["h"], d["l"]
        df = pd.DataFrame({"day": day, "h": h, "l": l})
        dh = df.groupby("day")["h"].max(); dl = df.groupby("day")["l"].min()
        days = sorted(dh.index)
        pdh_map = {days[k]: dh[days[k-1]] for k in range(1, len(days))}
        pdl_map = {days[k]: dl[days[k-1]] for k in range(1, len(days))}
        self._pdh = df["day"].map(pdh_map).to_numpy(dtype=float)
        self._pdl = df["day"].map(pdl_map).to_numpy(dtype=float)

        lb = self.SWING_LOOKBACK
        sw_hi = np.full(n, np.nan); sw_lo = np.full(n, np.nan)
        cur_hi = cur_lo = np.nan
        for i in range(n):
            k = i - lb
            if k >= lb:
                if h[k] == h[k-lb:k+lb+1].max(): cur_hi = h[k]
                if l[k] == l[k-lb:k+lb+1].min(): cur_lo = l[k]
            sw_hi[i] = cur_hi; sw_lo[i] = cur_lo
        self._sw_hi, self._sw_lo = sw_hi, sw_lo
        self._built_len = n

    def signal(self, d: dict, i: int) -> Signal:
        if i < self.MIN_BARS: return Signal()
        self._ensure(d)
        atr = d["atr"][i]
        if np.isnan(atr) or atr <= 0: return Signal()
        h, l, c = d["h"][i], d["l"][i], d["c"][i]
        m = self.SWEEP_MIN_ATR * atr
        for lvl in (self._pdh[i], self._sw_hi[i]):
            if np.isfinite(lvl) and h > lvl + m and c < lvl:
                return Signal("SELL", f"LQ sweep-high lvl={lvl:.2f}")
        for lvl in (self._pdl[i], self._sw_lo[i]):
            if np.isfinite(lvl) and l < lvl - m and c > lvl:
                return Signal("BUY", f"LQ sweep-low lvl={lvl:.2f}")
        return Signal()


class ToolTPOProfile(_ToolBase):
    """3. TPO / 'Volume' Profile -- value-area return trade. BEST RETURNS of
    the three: BTC H1 train PF 1.16 -> OOS 1.13, OOS CAGR +12.3%. Caveat:
    yearly walk-forward only 5/10 years PF>1 and OOS DD 42.6% -- real but
    lumpy. See module docstring for why this is TPO, not volume."""
    name = "TPO Profile (value area / POC)"
    short_name = "TPO-Profile"

    TPO_BINS = 30
    VALUE_FRAC = 0.70

    def precompute(self, d: dict):
        epoch = _epoch_seconds(d["ts"]); n = len(d["c"])
        day = epoch // 86400
        df = pd.DataFrame({"day": day, "h": d["h"], "l": d["l"]})
        poc_m, vah_m, val_m = {}, {}, {}
        for dy, g in df.groupby("day"):
            p, vh, vl = _day_profile(g["l"].to_numpy(), g["h"].to_numpy(),
                                     self.TPO_BINS, self.VALUE_FRAC)
            if np.isfinite(p):
                poc_m[dy], vah_m[dy], val_m[dy] = p, vh, vl
        days = sorted(poc_m)
        pp  = {days[k]: poc_m[days[k-1]] for k in range(1, len(days))}
        pvh = {days[k]: vah_m[days[k-1]] for k in range(1, len(days))}
        pvl = {days[k]: val_m[days[k-1]] for k in range(1, len(days))}
        self._poc = df["day"].map(pp).to_numpy(dtype=float)
        self._vah = df["day"].map(pvh).to_numpy(dtype=float)
        self._val = df["day"].map(pvl).to_numpy(dtype=float)
        self._built_len = n

    def signal(self, d: dict, i: int) -> Signal:
        if i < self.MIN_BARS: return Signal()
        self._ensure(d)
        poc, vah, val = self._poc[i], self._vah[i], self._val[i]
        if not (np.isfinite(poc) and np.isfinite(vah) and np.isfinite(val)):
            return Signal()
        c_prev, c = d["c"][i-1], d["c"][i]
        if c_prev > vah and val <= c <= vah:
            return Signal("SELL", f"VA re-entry from above vah={vah:.2f} poc={poc:.2f}")
        if c_prev < val and val <= c <= vah:
            return Signal("BUY", f"VA re-entry from below val={val:.2f} poc={poc:.2f}")
        return Signal()
