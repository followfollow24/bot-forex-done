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
    H1_BARS    = 4     # entry-bars per 1 trend-bucket (4 x 15m = H1, or 4 x 1h = H4)
    TIMEFRAME_SECONDS = 900   # seconds per entry bar; live bot sets this from --timeframe
                              # (900=15m default here; 3600 for --timeframe 1h)
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

    # ── Calendar-anchored bucket ids (fixes the two bugs below) ──────────
    #
    # BUG 1 (position-based, this method's old form): buckets were built as
    # `idx = arange(n_h1) * H1_BARS`, i.e. anchored to INDEX 0 OF WHATEVER
    # ARRAY WAS PASSED IN. A full backtest array and a live bot's shrinking/
    # growing rolling buffer are different lengths at different times, so the
    # SAME real hour of data could fall in different buckets depending on
    # which array was passed. Confirmed empirically: sign disagreed with the
    # (correct) causal method on 6.2% of bars in a live-style replay.
    #
    # BUG 2 (_h1_trend, the causal method previously used only as the live
    # fallback): used `start = n - n_h1 * H1_BARS` -- buckets anchored to the
    # END of whatever slice was passed, which drifts with buffer length too,
    # and is a SEPARATE scheme from BUG 1's, so the two methods didn't even
    # agree with EACH OTHER, let alone give a stable real-world hour boundary.
    #
    # FIX: bucket id = epoch_seconds // bucket_seconds, straight from each
    # bar's own timestamp. This is a pure function of wall-clock time -- given
    # the same bar, it always maps to the same bucket, independent of array
    # length, position, or how much history happens to be in the buffer. Both
    # _h1_trend (below) and _build_h1_trend_array now go through this ONE
    # implementation, so a live bot and a backtest fed the same bars always
    # agree, and any subclass that overrides _build_h1_trend_array (e.g.
    # RegimeFilteredHybridLive) is picked up by BOTH the live and backtest
    # code paths automatically (previously the live path silently used the
    # BASE class's _h1_trend instead of the subclass's regime-filtered logic,
    # since _h1_trend was never overridden -- meaning regime22's live bot was
    # not actually applying its regime filter at all).
    @staticmethod
    def _epoch_seconds(ts: np.ndarray) -> np.ndarray:
        import pandas as pd
        # Force seconds-since-epoch explicitly rather than assuming a unit --
        # pandas' datetime64 storage unit is version-dependent (ns in older
        # pandas, us as of 2.x/3.x here per `pd.to_datetime(...).dtype`), and
        # a wrong hardcoded divisor (e.g. //10**9 against a us-unit array)
        # silently produces near-constant bucket ids instead of an error.
        # Confirmed live: that exact mistake collapsed 20,000 bars into a
        # single bucket, which would have made every trend check evaluate
        # as "no trend" forever.
        return pd.to_datetime(pd.Series(ts)).astype("datetime64[s]").astype("int64").to_numpy()

    @classmethod
    def _bucket_ids(cls, ts: np.ndarray, bucket_seconds: int) -> np.ndarray:
        return (cls._epoch_seconds(ts) // bucket_seconds).astype(np.int64)

    def _bucket_seconds(self) -> int:
        # TIMEFRAME_SECONDS is set by the live bot at construction time to
        # match --timeframe (900 for 15m, 3600 for 1h); defaults to 15m so
        # any code that never sets it keeps the historical M15 behaviour.
        return getattr(self, "TIMEFRAME_SECONDS", 900) * self.H1_BARS

    def _build_h1_trend_array(self, d: dict) -> np.ndarray:
        import pandas as pd
        n = len(d["c"])
        out = np.zeros(n, dtype=np.int8)

        bucket_id = self._bucket_ids(d["ts"], self._bucket_seconds())
        uniq, k_of_bar = np.unique(bucket_id, return_inverse=True)
        n_h1 = len(uniq)
        if n_h1 < self.EMA_H1_SLOW + 5:
            return out

        tmp = pd.DataFrame({"k": k_of_bar, "c": d["c"], "h": d["h"], "l": d["l"]})
        g = tmp.groupby("k")
        h1_c = g["c"].last().reindex(range(n_h1)).to_numpy()
        h1_h = g["h"].max().reindex(range(n_h1)).to_numpy()
        h1_l = g["l"].min().reindex(range(n_h1)).to_numpy()

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

        # Causal expansion: bar i only "knows" a bucket once that bucket has
        # fully finished. Whether bar i is the LAST bar of its bucket must be
        # a pure function of bar i's OWN timestamp -- NOT of whether a "next"
        # row happens to exist in this (possibly truncated/live-buffer) array.
        #
        # RESIDUAL BUG this replaces: the old code used
        # `is_last_in_bucket[:-1] = bucket_id[:-1] != bucket_id[1:]` with a
        # `np.ones(n)` default, so the array's LAST row was always treated as
        # "bucket complete" even when the live buffer had simply been cut off
        # mid-bucket (e.g. only the 1st or 2nd M15 bar of the current H1 hour
        # had arrived so far). That inflated live signal counts vs the fast/
        # precomputed path by ~0.6-1.1% (confirmed via direct comparison).
        #
        # FIX: bar i is the last bar of its bucket iff advancing by one
        # entry-bar's duration from bar i's own epoch would land in the NEXT
        # bucket -- independent of whether bar i+1 is actually present here.
        entry_bar_seconds = getattr(self, "TIMEFRAME_SECONDS", 900)
        bucket_seconds = self._bucket_seconds()
        epoch = self._epoch_seconds(d["ts"])
        is_last_in_bucket = (epoch // bucket_seconds) != ((epoch + entry_bar_seconds) // bucket_seconds)
        k_complete = np.where(is_last_in_bucket, k_of_bar, k_of_bar - 1)
        valid = k_complete >= 0
        k_complete = np.clip(k_complete, 0, n_h1 - 1)
        out[valid] = h1_trend[k_complete[valid]]
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
        """คืน +1 (bull), -1 (bear), 0 (no trend / sideways).

        Delegates to self._build_h1_trend_array(d)[i] -- the SAME calendar-
        bucketed, causally-correct implementation the fast/precomputed path
        uses, rebuilt fresh over whatever slice of d the caller passes (live:
        the current bounded buffer; here: d truncated to i+1 so this never
        sees bars after i even if the caller's d happens to contain more).
        Going through self. (not a hardcoded class) means a subclass that
        overrides _build_h1_trend_array (e.g. RegimeFilteredHybridLive) is
        honoured here too -- previously this method was a separate,
        non-overridden implementation, so subclasses' regime/extra
        conditions were silently skipped whenever precompute() hadn't been
        called (i.e. always, in live trading).
        """
        n = i + 1
        d_slice = {k: (v[:n] if hasattr(v, "__len__") and len(v) > n else v)
                  for k, v in d.items()}
        arr = self._build_h1_trend_array(d_slice)
        return int(arr[i]) if i < len(arr) else 0

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


class FreshTrendFilterMixin:
    """Skip the signal unless the trend alignment is still 'fresh' -- at most
    MAX_MATURITY entry-bars old, continuously, at the moment of entry.

    Validated 2026-07-30 on the current M15/H1 configs, real costs, OOS split
    (train picks the threshold, test never sees it):
        BTC  M15 ADX18   TEST no-filter PF=1.37            -> +fresh<=5  PF=1.84
        ETH  M15 ADX18   TEST no-filter PF=1.08            -> +fresh<=3  PF=1.36
        GOLD H1  regime22 full-history  PF=0.87 DD=39.2%   -> +fresh<=10 PF=1.12 DD=16.1%
    and yearly walk-forward with the threshold frozen: BTC 10/10 years PF>1,
    ETH 10/10, gold 8/14 (gold stays the weak leg but improves vs no filter).

    Root cause this addresses: raw MFE/PnL analysis (both on 2,934 backtest
    entries AND separately on 183 real closed trades) showed entries taken
    once the H4/H1 trend has run a long time already do systematically worse
    than fresh ones -- the pullback signal still fires, but the move it is
    chasing is more likely already exhausted.

    Works in both call patterns this codebase uses:
      - backtest (precompute() called once): reuses the cached
        self._h1_trend_arr, O(1) per bar like the rest of FastHybridTrendPullback.
      - live (precompute() never called): rebuilds the trend array from
        d TRUNCATED TO i+1 each call. This matters: _build_h1_trend_array(d)
        buckets bars into H1/H4 groups sized off len(d["c"]) and reads each
        bucket's OWN LAST bar as that bucket's close -- if d still contains
        bars after i (as it does in a backtest loop, where d is the full
        historical array reused every iteration), a bucket straddling i can
        read 1-3 bars of future data. Live's own rolling buffer never
        contains bars after "now" so this would be a no-op there anyway, but
        truncating explicitly makes this method's behaviour independent of
        what the caller happens to pass, and makes it consistent with the
        base class's own causal _h1_trend(d, i) fallback (which already
        truncates the same way) rather than silently disagreeing with it.
        Confirmed by test: without truncation, a live-style loop (same d
        reused, i advancing) disagreed with the precomputed array on ~13%
        of bars; with it, they match exactly.
    """
    MAX_MATURITY = 5

    def signal(self, d: dict, i: int) -> Signal:
        sig = super().signal(d, i)
        if sig.action not in ("BUY", "SELL"):
            return sig
        if self._trend_maturity(d, i) > self.MAX_MATURITY:
            return Signal()
        return sig

    def _trend_maturity(self, d: dict, i: int) -> int:
        arr = self._h1_trend_arr
        if arr is None or i >= len(arr):
            n = i + 1
            d_causal = {k: (v[:n] if hasattr(v, "__len__") and len(v) > n else v)
                       for k, v in d.items()}
            arr = self._build_h1_trend_array(d_causal)
        cur = int(arr[i]) if i < len(arr) else 0
        if cur == 0:
            return 0
        maturity = 0
        k = i
        while k >= 0 and int(arr[k]) == cur:
            maturity += 1
            k -= 1
        return maturity


class FreshHybridTrendPullback(FreshTrendFilterMixin, HybridTrendPullback):
    """HybridTrendPullback + fresh-trend filter. Live-bot default for
    btc_h1_manual/eth_h1_manual style variants once --fresh-maturity is set."""
    pass

