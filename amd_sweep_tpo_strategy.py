#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
amd_sweep_tpo_strategy.py -- LIVE strategy class for the AMD + Liquidity
Sweep + TPO Profile bot (variant tag: btc_amd_sweep).

THE SETUP (ICT / Smart-Money-Concepts style, made fully mechanical)
------------------------------------------------------------------
  A - ACCUMULATION : the Asian session (00:00-ACC_END_H UTC) builds a range.
                     Stops pile up above its high and below its low.
  M - MANIPULATION : during the London window price SWEEPS one side of that
                     range (takes the liquidity) then CLOSES BACK INSIDE.
                     That failed breakout is the manipulation leg.
  D - DISTRIBUTION : the real move goes the OTHER way -- entry is opposite
                     the sweep direction.
  + TPO CONFLUENCE : entry must also sit on the correct side of the PRIOR
                     day's POC, so a sweep is only faded when the profile
                     agrees.

>>> VALIDATION STATUS: FAILED BACKTEST -- DEPLOYED ANYWAY BY EXPLICIT <<<
>>> USER DECISION, AS AN ENTRY SIGNAL ONLY, AT REDUCED RISK.          <<<

Measured 2026-08-01 with real costs (see _amd_sweep_tpo_test.py):
    Gold M15 PF 0.32 | Gold H1 PF 0.50 | BTC M15 PF 0.80
    ETH  M15 PF 0.82 | BTC  H1 PF 0.96  <-- best variant, still < 1.0
BTC H1 + TPO filter is what this class is configured for because it was the
least-bad of the family, NOT because it passed anything.

Inverting the direction (trading WITH the sweep instead of fading it) was
also tested and also loses (BTC H1 0.96 -> 0.92, gold H1 0.50 -> 0.65), so
the failure is not a sign error: the sweep timing carries no usable
directional information in this data and both sides bleed to cost. The TPO
confluence filter did consistently help (BTC H1 0.88 -> 0.96 without/with)
without ever reaching profitability.

The user was shown these numbers, reaffirmed the request, and is running it
to judge the entries by hand. Exits are manual (SL only, no auto-TP), so the
backtested PF above does not describe the live outcome -- the entry timing
it measures, however, does.

"VOLUME PROFILE" CAVEAT
----------------------
No price file in this project has a volume column (verified 2026-08-01), so
a true volume profile is not computable. This uses a genuine TPO
(Time-Price-Opportunity) profile -- Steidlmayer's original Market Profile,
which weights each price level by TIME spent there rather than volume
traded. Real volume would be a one-line swap in _build_day_poc().

LIVE-PATH SAFETY
----------------
The live bot never calls precompute() (confirmed: forex_live_bot_gold_cwider
calls only strategy.signal(d, i)). This class therefore rebuilds its arrays
inside signal() whenever they are missing or stale -- the exact failure mode
that silently disabled the regime filter in production before the 2026-07-30
fix. Rebuilding is causally safe because the live buffer contains only
closed candles up to the current bar, and every level here is derived from
either a completed session window or the PRIOR day.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from forex_indicators import Signal


class AMDSweepTPO:
    """AMD + liquidity sweep + TPO-profile confluence. See module docstring
    for the full (failing) validation record and why it is deployed anyway."""

    name = "AMD + Liquidity Sweep + TPO Profile"
    short_name = "AMD-Sweep-TPO"

    ACC_END_H = 7           # accumulation window 00:00..07:00 UTC (Asian session)
    HUNT_END_H = 16         # sweep must occur before this hour (London window)
    SWEEP_MIN_ATR = 0.10    # sweep must clear the range edge by >= this x ATR
    TPO_BINS = 30
    USE_TPO_FILTER = True

    sl_atr = 2.0
    tp_atr = 999.0
    trail_atr_mult = 999.0          # manual exit -- no auto trailing
    trail_activation_atr = 999.0
    max_spread_atr_ratio = 0.5
    MIN_BARS = 200

    _acc_hi = None
    _acc_lo = None
    _acc_ready = None
    _in_hunt = None
    _prev_poc = None
    _swept_hi = None
    _swept_lo = None
    _built_len = None

    # ── helpers ──────────────────────────────────────────────────────────
    @staticmethod
    def _epoch_seconds(ts) -> np.ndarray:
        return pd.to_datetime(pd.Series(ts)).astype("datetime64[s]").astype("int64").to_numpy()

    def _build_day_poc(self, lows: np.ndarray, highs: np.ndarray) -> float:
        """TPO point-of-control for one day. Each bar contributes 1 TPO to
        every price bin its range covers (time at price). Swap this weight
        for traded volume if real volume data ever becomes available."""
        lo, hi = float(np.min(lows)), float(np.max(highs))
        if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
            return float("nan")
        edges = np.linspace(lo, hi, self.TPO_BINS + 1)
        centers = (edges[:-1] + edges[1:]) / 2.0
        counts = np.zeros(self.TPO_BINS)
        for bl, bh in zip(lows, highs):
            i0 = np.searchsorted(edges, bl, side="right") - 1
            i1 = np.searchsorted(edges, bh, side="left")
            i0 = max(i0, 0)
            i1 = min(i1, self.TPO_BINS)
            if i1 > i0:
                counts[i0:i1] += 1.0
        if counts.sum() <= 0:
            return float("nan")
        return float(centers[int(np.argmax(counts))])

    # ── precompute ───────────────────────────────────────────────────────
    def precompute(self, d: dict):
        epoch = self._epoch_seconds(d["ts"])
        n = len(d["c"])
        day_id = epoch // 86400
        hour = (epoch % 86400) // 3600

        h, l = d["h"], d["l"]
        in_acc = hour < self.ACC_END_H
        self._acc_ready = hour >= self.ACC_END_H
        self._in_hunt = (hour >= self.ACC_END_H) & (hour < self.HUNT_END_H)

        df = pd.DataFrame({"day": day_id, "in_acc": in_acc, "h": h, "l": l})

        # A: this day's own Asian-session range (complete before it is used)
        acc = df[df["in_acc"]]
        self._acc_hi = df["day"].map(acc.groupby("day")["h"].max()).to_numpy(dtype=float)
        self._acc_lo = df["day"].map(acc.groupby("day")["l"].min()).to_numpy(dtype=float)

        # TPO POC of the PRIOR day (causal by construction)
        poc_by_day = {}
        for day, grp in df.groupby("day"):
            poc = self._build_day_poc(grp["l"].to_numpy(), grp["h"].to_numpy())
            if np.isfinite(poc):
                poc_by_day[day] = poc
        days_sorted = sorted(poc_by_day)
        prev_poc_map = {days_sorted[k]: poc_by_day[days_sorted[k - 1]]
                        for k in range(1, len(days_sorted))}
        self._prev_poc = df["day"].map(prev_poc_map).to_numpy(dtype=float)

        # M: running "has this day already swept a side" flags
        swept_hi = np.zeros(n, dtype=bool)
        swept_lo = np.zeros(n, dtype=bool)
        cur_day = None
        hi_done = lo_done = False
        for i in range(n):
            if day_id[i] != cur_day:
                cur_day = day_id[i]
                hi_done = lo_done = False
            if self._acc_ready[i]:
                ah, al = self._acc_hi[i], self._acc_lo[i]
                if np.isfinite(ah) and h[i] > ah:
                    hi_done = True
                if np.isfinite(al) and l[i] < al:
                    lo_done = True
            swept_hi[i] = hi_done
            swept_lo[i] = lo_done
        self._swept_hi = swept_hi
        self._swept_lo = swept_lo
        self._built_len = n

    def _ensure_built(self, d: dict):
        """Live path never calls precompute() -- rebuild on demand whenever
        the cached arrays are missing or do not match the current buffer."""
        if self._built_len != len(d["c"]) or self._acc_hi is None:
            self.precompute(d)

    # ── signal ───────────────────────────────────────────────────────────
    def signal(self, d: dict, i: int) -> Signal:
        if i < self.MIN_BARS:
            return Signal()
        self._ensure_built(d)
        if i >= len(self._in_hunt) or not self._in_hunt[i]:
            return Signal()

        atr = d["atr"][i]
        if np.isnan(atr) or atr <= 0:
            return Signal()
        ah, al = self._acc_hi[i], self._acc_lo[i]
        if not np.isfinite(ah) or not np.isfinite(al):
            return Signal()

        h, l, c = d["h"][i], d["l"][i], d["c"][i]
        margin = self.SWEEP_MIN_ATR * atr
        poc = self._prev_poc[i]
        if self.USE_TPO_FILTER and not np.isfinite(poc):
            return Signal()

        # swept the HIGH (buy-side liquidity taken) + closed back inside -> SELL
        if self._swept_hi[i] and h > ah + margin and c < ah:
            if (not self.USE_TPO_FILTER) or (c < poc):
                return Signal("SELL", f"AMD sweep-high acc_hi={ah:.2f} poc={poc:.2f}")

        # swept the LOW (sell-side liquidity taken) + closed back inside -> BUY
        if self._swept_lo[i] and l < al - margin and c > al:
            if (not self.USE_TPO_FILTER) or (c > poc):
                return Signal("BUY", f"AMD sweep-low acc_lo={al:.2f} poc={poc:.2f}")

        return Signal()
