#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 smc_liquidity_strategy.py — Smart Money Concepts: Liquidity Sweep + FVG Entry
================================================================================
 A structurally different signal from HybridTrendPullback (which enters on a
 pullback to EMA20 inside an EMA50/200+ADX trend). This one never looks at a
 moving average for entry -- it enters on a *stop-hunt reversal into an
 imbalance*, which is why it has a chance of being uncorrelated with the
 existing gold bots rather than just another flavour of the same edge.

 Rules (all mechanical, no discretion):

 Layer 1 -- Market Structure (H1 swings)
   Swing high = bar whose high is the max of +/- SWING_LOOKBACK bars.
   Swing low  = mirror. Structure is BULL when the last two confirmed swing
   highs are rising AND the last two swing lows are rising (HH + HL);
   BEAR on LH + LL; 0 otherwise. No EMA, no ADX -- pure price structure.

 Layer 2 -- Liquidity Sweep (M15)
   Price takes out a recent M15 swing extreme (the "liquidity pool" where
   stops sit) and then closes back inside the range within SWEEP_MAX_BARS.
   That failed breakout is the sweep. Only sweeps *against* the H1 structure
   are tradeable (sweep the lows in a bull structure = buy-side setup).

 Layer 3 -- Fair Value Gap (FVG) entry
   After the sweep, find the 3-bar imbalance created by the reversal leg:
   bullish FVG = high[k-1] < low[k+1] (gap between them never traded).
   Enter when price retraces back into that gap. If price never retraces
   within FVG_VALID_BARS, the setup expires -- no chase.

 Layer 4 -- Risk
   SL beyond the sweep extreme (the invalidation point) + SL_BUFFER_ATR.
   TP as an ATR multiple, exposed as tp_atr so the same BacktestEngine /
   live bot plumbing works unchanged.

 NOT YET VALIDATED. This file only defines the signal. It must clear the same
 bar as everything else in this repo before it goes anywhere near real money:
 full-history backtest with real spread/slippage/commission, PF > 1 across
 walk-forward windows, and a minimum trade count -- reported honestly even if
 the answer is "no edge".
================================================================================
"""
from __future__ import annotations

import numpy as np

from forex_indicators import Signal


class SMCLiquidityFVG:
    """H1 market structure + M15 liquidity sweep + FVG retrace entry."""

    name       = "SMC Liquidity Sweep + FVG (H1 structure + M15 entry)"
    short_name = "SMC-LiqFVG"

    # ── SL / TP (same interface as HybridTrendPullback) ──────────────────
    sl_atr = 3.0
    tp_atr = 7.0

    trail_atr_mult       = 999.0
    trail_activation_atr = 999.0
    max_spread_atr_ratio = 0.20

    # ── H1 structure ─────────────────────────────────────────────────────
    H1_BARS         = 4    # M15 bars per H1 bar
    SWING_LOOKBACK  = 3    # bars each side to confirm an H1 swing point
    STRUCT_MIN_SWINGS = 2  # need 2 highs + 2 lows to call HH/HL or LH/LL

    # ── M15 liquidity sweep ──────────────────────────────────────────────
    POOL_LOOKBACK   = 20   # M15 bars back to find the liquidity pool extreme
    SWEEP_MAX_BARS  = 3    # bars allowed to close back inside after the take-out
    SWEEP_MIN_ATR   = 0.15 # take-out must exceed the pool by >= this x ATR
                           # (filters noise ticks that aren't real stop-hunts)

    # ── FVG entry ────────────────────────────────────────────────────────
    FVG_VALID_BARS  = 12   # setup expires if price doesn't retrace into the gap
    FVG_MIN_ATR     = 0.10 # gap must be >= this x ATR to be a real imbalance

    # warm-up: H1 structure needs swing history; keep generous like the sibling
    MIN_BARS = 400

    # precomputed
    _h1_struct_arr = None

    # seconds per entry bar; defaults to 900 (M15) matching this file's
    # historical usage. See forex_hybrid_strategy.py for the full rationale.
    TIMEFRAME_SECONDS = 900

    @staticmethod
    def _epoch_seconds(ts: np.ndarray) -> np.ndarray:
        import pandas as pd
        return pd.to_datetime(pd.Series(ts)).astype("datetime64[s]").astype("int64").to_numpy()

    @classmethod
    def _bucket_ids(cls, ts: np.ndarray, bucket_seconds: int) -> np.ndarray:
        return (cls._epoch_seconds(ts) // bucket_seconds).astype(np.int64)

    def _bucket_seconds(self) -> int:
        return getattr(self, "TIMEFRAME_SECONDS", 900) * self.H1_BARS

    # ─────────────────────────────────────────────────────────────────────
    def precompute(self, d: dict):
        self._h1_struct_arr = self._build_h1_structure_array(d)

    def _build_h1_structure_array(self, d: dict) -> np.ndarray:
        """Map each M15 bar -> H1 market structure (+1 bull / -1 bear / 0).

        [FIX 2026-07-30] was position-based (idx = arange(n_h1)*H1_BARS)
        with a look-ahead expansion (out[i] = struct[i // H1_BARS], the
        bucket bar i falls INSIDE rather than the last COMPLETED one --
        every bar in a bucket saw that bucket's final, fully-formed swing
        structure). Rebuilt on the same calendar/timestamp-anchored bucket
        ids as forex_hybrid_strategy.HybridTrendPullback. This strategy is
        marked NOT YET VALIDATED in the module docstring; any prior
        exploratory numbers used the buggy array and should be rerun.
        """
        import pandas as pd
        n    = len(d["c"])
        out  = np.zeros(n, dtype=np.int8)

        bucket_id = self._bucket_ids(d["ts"], self._bucket_seconds())
        uniq, k_of_bar = np.unique(bucket_id, return_inverse=True)
        n_h1 = len(uniq)
        if n_h1 < 50:
            return out

        tmp = pd.DataFrame({"k": k_of_bar, "h": d["h"], "l": d["l"]})
        g = tmp.groupby("k")
        h1_h = g["h"].max().reindex(range(n_h1)).to_numpy()
        h1_l = g["l"].min().reindex(range(n_h1)).to_numpy()

        lb = self.SWING_LOOKBACK
        struct = np.zeros(n_h1, dtype=np.int8)

        # rolling lists of confirmed swing prices
        last_highs: list[float] = []
        last_lows:  list[float] = []

        for k in range(n_h1):
            # a swing at k-lb is confirmed once we're lb bars past it
            c = k - lb
            if c - lb >= 0:
                win_h = h1_h[c - lb:c + lb + 1]
                win_l = h1_l[c - lb:c + lb + 1]
                if h1_h[c] == win_h.max():
                    last_highs.append(float(h1_h[c]))
                    if len(last_highs) > 4:
                        last_highs.pop(0)
                if h1_l[c] == win_l.min():
                    last_lows.append(float(h1_l[c]))
                    if len(last_lows) > 4:
                        last_lows.pop(0)

            if len(last_highs) >= self.STRUCT_MIN_SWINGS and \
               len(last_lows)  >= self.STRUCT_MIN_SWINGS:
                hh = last_highs[-1] > last_highs[-2]
                hl = last_lows[-1]  > last_lows[-2]
                lh = last_highs[-1] < last_highs[-2]
                ll = last_lows[-1]  < last_lows[-2]
                if hh and hl:
                    struct[k] = 1
                elif lh and ll:
                    struct[k] = -1

        entry_bar_seconds = getattr(self, "TIMEFRAME_SECONDS", 900)
        bucket_seconds = self._bucket_seconds()
        epoch = self._epoch_seconds(d["ts"])
        is_last_in_bucket = (epoch // bucket_seconds) != ((epoch + entry_bar_seconds) // bucket_seconds)
        k_complete = np.where(is_last_in_bucket, k_of_bar, k_of_bar - 1)
        valid = k_complete >= 0
        k_complete = np.clip(k_complete, 0, n_h1 - 1)
        out[valid] = struct[k_complete[valid]]
        return out

    # ─────────────────────────────────────────────────────────────────────
    def signal(self, d: dict, i: int) -> Signal:
        if i < self.MIN_BARS:
            return Signal()

        if self._h1_struct_arr is None:
            self.precompute(d)
        struct = int(self._h1_struct_arr[i])
        if struct == 0:
            return Signal()

        atr = float(d["atr"][i])
        if not np.isfinite(atr) or atr <= 0:
            return Signal()

        if struct == 1:
            return self._long_setup(d, i, atr)
        return self._short_setup(d, i, atr)

    # ── LONG: sweep the lows in a bull structure, buy the bullish FVG ────
    def _long_setup(self, d: dict, i: int, atr: float) -> Signal:
        sweep = self._find_sweep(d, i, atr, direction=1)
        if sweep is None:
            return Signal()
        sweep_idx, sweep_extreme = sweep

        fvg = self._find_fvg(d, sweep_idx, i, atr, direction=1)
        if fvg is None:
            return Signal()
        gap_lo, gap_hi = fvg

        # entry: current bar trades back down into the gap and closes bullish
        if d["l"][i] <= gap_hi and d["c"][i] >= gap_lo and d["c"][i] > d["o"][i]:
            return Signal("BUY",
                          f"SMC sweep@{sweep_extreme:.5f} FVG[{gap_lo:.5f},{gap_hi:.5f}]")
        return Signal()

    # ── SHORT: sweep the highs in a bear structure, sell the bearish FVG ─
    def _short_setup(self, d: dict, i: int, atr: float) -> Signal:
        sweep = self._find_sweep(d, i, atr, direction=-1)
        if sweep is None:
            return Signal()
        sweep_idx, sweep_extreme = sweep

        fvg = self._find_fvg(d, sweep_idx, i, atr, direction=-1)
        if fvg is None:
            return Signal()
        gap_lo, gap_hi = fvg

        if d["h"][i] >= gap_lo and d["c"][i] <= gap_hi and d["c"][i] < d["o"][i]:
            return Signal("SELL",
                          f"SMC sweep@{sweep_extreme:.5f} FVG[{gap_lo:.5f},{gap_hi:.5f}]")
        return Signal()

    # ─────────────────────────────────────────────────────────────────────
    def _find_sweep(self, d: dict, i: int, atr: float, direction: int):
        """Most recent completed liquidity sweep within the valid window.

        direction=+1 -> sweep of the LOWS (stops below), sets up a long.
        Returns (bar_index_of_sweep, extreme_price) or None.
        """
        earliest = max(self.POOL_LOOKBACK + 1, i - self.FVG_VALID_BARS)
        for k in range(i - 1, earliest - 1, -1):
            pool_start = k - self.POOL_LOOKBACK
            if pool_start < 0:
                break
            if direction == 1:
                pool = float(d["l"][pool_start:k].min())
                took_out = d["l"][k] < pool - self.SWEEP_MIN_ATR * atr
                if not took_out:
                    continue
                # must close back above the pool within SWEEP_MAX_BARS
                for m in range(k, min(k + self.SWEEP_MAX_BARS + 1, i + 1)):
                    if d["c"][m] > pool:
                        return k, float(d["l"][k])
            else:
                pool = float(d["h"][pool_start:k].max())
                took_out = d["h"][k] > pool + self.SWEEP_MIN_ATR * atr
                if not took_out:
                    continue
                for m in range(k, min(k + self.SWEEP_MAX_BARS + 1, i + 1)):
                    if d["c"][m] < pool:
                        return k, float(d["h"][k])
        return None

    def _find_fvg(self, d: dict, sweep_idx: int, i: int, atr: float, direction: int):
        """Find the imbalance created by the reversal leg after the sweep.

        Bullish FVG (direction=+1): high[k-1] < low[k+1]; the untraded gap is
        [high[k-1], low[k+1]]. Bearish is the mirror.
        Returns (gap_low, gap_high) or None.
        """
        min_gap = self.FVG_MIN_ATR * atr
        for k in range(sweep_idx + 1, i):
            if k - 1 < 0 or k + 1 > i:
                continue
            if direction == 1:
                lo = float(d["h"][k - 1])
                hi = float(d["l"][k + 1])
                if hi - lo >= min_gap:
                    return lo, hi
            else:
                hi = float(d["l"][k - 1])
                lo = float(d["h"][k + 1])
                if hi - lo >= min_gap:
                    return lo, hi
        return None
