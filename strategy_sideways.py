#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 strategy_sideways.py  —  Sideways / Range Strategies for XAUUSD M15
   1. BBMeanRev    — Bollinger Band Mean Reversion (ADX<20)
   2. SessionRange — Asian Range + London entry (08:00–12:00 UTC)
================================================================================
 Designed as the complement to HybridTrendPullback:
   - Enters when ADX < 20 (sideways/quiet market)
   - BB(20, 2.0) touch at lower/upper band → BUY / SELL
   - TP = midline (BB midband / EMA20)
   - SL = sl_atr_mult × ATR beyond the band
   - H1 EMA50 / EMA200 within range_pct of each other (confirms ranging)

 Interface identical to HybridTrendPullback so BacktestEngine can use either:
   strat.precompute(d)
   strat.signal(d, i)  →  Signal
   strat.sl_atr, strat.tp_atr, strat.trail_atr_mult, strat.trail_activation_atr
   strat.MIN_BARS
================================================================================
"""
from __future__ import annotations

import math

import numpy as np

from forex_indicators import Signal


class BBMeanRev:
    """Bollinger Band Mean Reversion — ADX<20 sideways filter."""

    name       = "BB Mean Reversion (ADX<20 sideways)"
    short_name = "BB-MR"

    # ── SL/TP — set dynamically per signal; defaults used when no position ──
    sl_atr  = 1.5   # SL = 1.5 × ATR beyond the band
    tp_atr  = 2.5   # TP overridden dynamically to (midline - entry) / ATR

    # ── Trailing — disabled (mean-rev exits at midline, not trailing) ───────
    trail_atr_mult       = 999.0
    trail_activation_atr = 999.0

    # ── H1 trend filter ──────────────────────────────────────────────────────
    H1_BARS     = 4     # 4 × M15 = 1 H1 bar
    ADX_PERIOD  = 14
    ADX_MAX     = 20    # ADX BELOW this = sideways → allowed to enter
    EMA_H1_FAST = 50
    EMA_H1_SLOW = 200
    RANGE_PCT   = 0.03  # H1 EMA50/EMA200 must be within 3% of each other

    # ── BB entry (M15) ───────────────────────────────────────────────────────
    BB_PERIOD = 20
    BB_STDS   = 2.0

    # ── Minimum warm-up bars ─────────────────────────────────────────────────
    MIN_BARS = EMA_H1_SLOW * H1_BARS + 50   # 850

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

    # ─────────────────────────────────────────────────────────────────────────
    # Precomputed arrays (set by precompute())
    _h1_sideways_arr = None   # np.ndarray int8: 1=sideways, 0=trending
    _bb_upper_arr    = None   # BB upper band (M15)
    _bb_lower_arr    = None   # BB lower band (M15)
    _bb_mid_arr      = None   # BB midline = EMA20 (M15)

    # ─────────────────────────────────────────────────────────────────────────

    def precompute(self, d: dict):
        """Precompute H1 sideways filter + M15 BB arrays once — O(n)."""
        n = len(d["c"])

        # ── H1 sideways array ────────────────────────────────────────────────
        self._h1_sideways_arr = self._build_h1_sideways(d)

        # ── M15 BB arrays ────────────────────────────────────────────────────
        closes = d["c"]
        sma = self._sma_array(closes, self.BB_PERIOD)
        std = self._std_array(closes, self.BB_PERIOD)

        self._bb_mid_arr   = sma
        self._bb_upper_arr = sma + self.BB_STDS * std
        self._bb_lower_arr = sma - self.BB_STDS * std

    def _build_h1_sideways(self, d: dict) -> np.ndarray:
        """Return array: 1 where ADX<ADX_MAX and EMA range is tight, else 0.

        [FIX 2026-07-30] was position-based (idx = arange(n_h1)*H1_BARS)
        with a look-ahead expansion (out[i] = h1_side[i // H1_BARS], the
        bucket bar i falls INSIDE rather than the last COMPLETED one).
        _h1_sideways_live (the causal fallback) used a SEPARATE, also wrong,
        end-anchored scheme, so the two paths didn't even agree with each
        other. Rebuilt on the same calendar/timestamp-anchored bucket ids as
        forex_hybrid_strategy.HybridTrendPullback, with _h1_sideways_live now
        delegating to this method. Research-only file (not used by any live
        bot); any numbers this script produced before this fix used the
        buggy array and should be rerun before being trusted again.
        """
        import pandas as pd
        n    = len(d["c"])
        out  = np.zeros(n, dtype=np.int8)

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

        ema_f  = self._ema(h1_c, self.EMA_H1_FAST)
        ema_s  = self._ema(h1_c, self.EMA_H1_SLOW)
        adx_a  = self._adx_array(h1_h, h1_l, h1_c, self.ADX_PERIOD)

        h1_side = np.zeros(n_h1, dtype=np.int8)
        for k in range(n_h1):
            ef, es, adx = ema_f[k], ema_s[k], adx_a[k]
            if math.isnan(ef) or math.isnan(es) or math.isnan(adx):
                continue
            if adx >= self.ADX_MAX:
                continue   # trending — skip
            if es == 0:
                continue
            if abs(ef - es) / es > self.RANGE_PCT:
                continue   # EMAs too far apart — strong trend
            h1_side[k] = 1

        entry_bar_seconds = getattr(self, "TIMEFRAME_SECONDS", 900)
        bucket_seconds = self._bucket_seconds()
        epoch = self._epoch_seconds(d["ts"])
        is_last_in_bucket = (epoch // bucket_seconds) != ((epoch + entry_bar_seconds) // bucket_seconds)
        k_complete = np.where(is_last_in_bucket, k_of_bar, k_of_bar - 1)
        valid = k_complete >= 0
        k_complete = np.clip(k_complete, 0, n_h1 - 1)
        out[valid] = h1_side[k_complete[valid]]
        return out

    # ─────────────────────────────────────────────────────────────────────────

    def signal(self, d: dict, i: int) -> Signal:
        if i < self.MIN_BARS:
            return Signal()

        # ── 1. Sideways filter ───────────────────────────────────────────────
        if self._h1_sideways_arr is not None:
            sideways = int(self._h1_sideways_arr[i])
        else:
            sideways = self._h1_sideways_live(d, i)

        if sideways == 0:
            return Signal()

        # ── 2. BB values ─────────────────────────────────────────────────────
        if self._bb_upper_arr is not None:
            mid_cur    = self._bb_mid_arr[i]
            upper_cur  = self._bb_upper_arr[i]
            lower_cur  = self._bb_lower_arr[i]
            mid_prev   = self._bb_mid_arr[i - 1]
            upper_prev = self._bb_upper_arr[i - 1]
            lower_prev = self._bb_lower_arr[i - 1]
        else:
            closes    = d["c"][:i + 1]
            sma       = self._sma_array(closes, self.BB_PERIOD)
            std       = self._std_array(closes, self.BB_PERIOD)
            mid_cur   = sma[-1];  mid_prev   = sma[-2]
            upper_cur = sma[-1] + self.BB_STDS * std[-1]
            lower_cur = sma[-1] - self.BB_STDS * std[-1]
            upper_prev = sma[-2] + self.BB_STDS * std[-2]
            lower_prev = sma[-2] - self.BB_STDS * std[-2]

        if (math.isnan(mid_cur) or math.isnan(upper_cur)
                or math.isnan(lower_cur)):
            return Signal()

        atr = float(d["atr"][i])
        if atr <= 0:
            return Signal()

        c_prev = d["c"][i - 1]
        c_cur  = d["c"][i]
        l_prev = d["l"][i - 1]
        h_prev = d["h"][i - 1]

        # ── 3. Entry signals ─────────────────────────────────────────────────
        # BUY: prev bar's low touches/crosses lower band + current bar bounces up
        touched_lower = l_prev <= lower_prev
        bounce_up     = c_cur > c_prev and c_cur > lower_cur

        if touched_lower and bounce_up and mid_cur > lower_cur:
            dist = mid_cur - lower_cur          # expected profit = midline - entry
            self.tp_atr  = max(dist / atr, 0.5) # TP in ATR units (min 0.5)
            self.sl_atr  = 1.5
            return Signal("BUY",
                          f"BB-low touch lb={lower_prev:.2f} mid={mid_cur:.2f} "
                          f"tp_atr={self.tp_atr:.2f}")

        # SELL: prev bar's high touches/crosses upper band + current bar drops
        touched_upper = h_prev >= upper_prev
        bounce_down   = c_cur < c_prev and c_cur < upper_cur

        if touched_upper and bounce_down and upper_cur > mid_cur:
            dist = upper_cur - mid_cur
            self.tp_atr  = max(dist / atr, 0.5)
            self.sl_atr  = 1.5
            return Signal("SELL",
                          f"BB-high touch ub={upper_prev:.2f} mid={mid_cur:.2f} "
                          f"tp_atr={self.tp_atr:.2f}")

        return Signal()

    # ─── Fallback live computation (no precompute) ────────────────────────────

    def _h1_sideways_live(self, d: dict, i: int) -> int:
        # [FIX 2026-07-30] delegates to _build_h1_sideways (the SAME
        # calendar-bucketed, causally-correct implementation the
        # fast/precomputed path uses) instead of a separate, end-anchored
        # position-based scheme that disagreed with it. See
        # _build_h1_sideways's docstring for the full history.
        n = i + 1
        d_slice = {k: (v[:n] if hasattr(v, "__len__") and len(v) > n else v) for k, v in d.items()}
        arr = self._build_h1_sideways(d_slice)
        return int(arr[i]) if i < len(arr) else 0

    # ─── Indicators (self-contained, copied from HybridTrendPullback) ────────

    @staticmethod
    def _ema(prices: np.ndarray, span: int) -> np.ndarray:
        out = np.full(len(prices), np.nan)
        if len(prices) < span:
            return out
        alpha = 2.0 / (span + 1)
        out[0] = prices[0]
        for j in range(1, len(prices)):
            out[j] = prices[j] * alpha + out[j - 1] * (1 - alpha)
        return out

    @staticmethod
    def _sma_array(prices: np.ndarray, period: int) -> np.ndarray:
        n   = len(prices)
        out = np.full(n, np.nan)
        if n < period:
            return out
        cs = np.cumsum(prices)
        out[period - 1] = cs[period - 1] / period
        out[period:]    = (cs[period:] - cs[:-period]) / period
        return out

    @staticmethod
    def _std_array(prices: np.ndarray, period: int) -> np.ndarray:
        n   = len(prices)
        out = np.full(n, np.nan)
        for j in range(period - 1, n):
            window = prices[j - period + 1: j + 1]
            out[j] = float(np.std(window, ddof=0))
        return out

    @staticmethod
    def _wilder_smooth(arr: np.ndarray, period: int) -> np.ndarray:
        n   = len(arr)
        out = np.full(n, np.nan)
        if n < period:
            return out
        consec = 0; first_start = -1
        for k in range(n):
            if not math.isnan(float(arr[k])):
                consec += 1
                if consec == period:
                    first_start = k - period + 1; break
            else:
                consec = 0
        if first_start < 0:
            return out
        start_idx = first_start + period - 1
        out[start_idx] = float(np.mean(arr[first_start:first_start + period]))
        a = 1.0 / period
        for j in range(start_idx + 1, n):
            v = float(arr[j])
            out[j] = (out[j - 1] * (1 - a) + v * a) if not math.isnan(v) else out[j - 1]
        return out

    @classmethod
    def _adx_array(cls, high, low, close, period=14) -> np.ndarray:
        n      = len(close)
        result = np.full(n, np.nan)
        if n < period * 3:
            return result
        prev_c = np.empty(n); prev_c[0] = close[0]; prev_c[1:] = close[:-1]
        tr  = np.maximum(high - low,
                         np.maximum(np.abs(high - prev_c), np.abs(low - prev_c)))
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
        result[period * 3:] = adx[period * 3:]
        return result

    @classmethod
    def _adx_last(cls, high, low, close, period=14) -> float:
        arr   = cls._adx_array(high, low, close, period)
        valid = arr[~np.isnan(arr)]
        return float(valid[-1]) if len(valid) > 0 else 0.0


# =============================================================================
# 2. SessionRange — Asian Range + London open fade
# =============================================================================

class SessionRange:
    """Asian Range (00:00–08:00 UTC) → fade to midpoint during London (08:00–12:00 UTC).

    Thesis: Gold consolidates in Asian session; London session often tests
    the Asian range boundaries before a breakout, giving a mean-reversion
    trade back to the Asian midpoint.

    Entry:
      - Only 08:00–12:00 UTC (London open window)
      - BUY  if price pulls back to asian_low  and ADX(14) < ADX_MAX
      - SELL if price pulls back to asian_high and ADX(14) < ADX_MAX
      - Asian range width >= MIN_RANGE_ATR × ATR(14) (skip tiny ranges)

    Exit:
      - TP = asian_mid (midpoint of Asian range) → set tp_atr dynamically
      - SL = SL_ATR_MULT × ATR(14) beyond entry   → set sl_atr dynamically
      - Timeout via cfg.max_hold_bars = 16 (4 hours, set in caller)
    """

    name       = "Session Range (Asian→London fade)"
    short_name = "SR"

    # ── SL/TP — overridden dynamically per signal ────────────────────────────
    sl_atr  = 1.0
    tp_atr  = 1.0

    trail_atr_mult       = 999.0
    trail_activation_atr = 999.0

    # ── Parameters ───────────────────────────────────────────────────────────
    ASIAN_START_H  = 0    # UTC hour: start of Asian session (inclusive)
    ASIAN_END_H    = 8    # UTC hour: end of Asian session (exclusive)
    LONDON_START_H = 8    # UTC hour: London open (inclusive)
    LONDON_END_H   = 12   # UTC hour: London close window (exclusive)

    ADX_PERIOD    = 14
    ADX_MAX       = 25    # ADX below this = still ranging → allowed entry
    MIN_RANGE_ATR = 0.5   # range must be ≥ 0.5 × ATR to avoid micro-ranges
    SL_ATR_MULT   = 1.0   # SL = 1.0 × ATR beyond entry level
    TOUCH_TOL_ATR = 0.3   # price within 0.3 × ATR of boundary = "at the level"

    MIN_BARS = 100   # minimal warm-up

    # ── Precomputed arrays ───────────────────────────────────────────────────
    _asian_high_arr = None   # float array — asian_high for each bar's day
    _asian_low_arr  = None   # float array — asian_low for each bar's day
    _asian_mid_arr  = None   # float array — (asian_high + asian_low) / 2
    _hour_arr       = None   # int8 array  — UTC hour of each bar
    _adx_arr        = None   # float array — M15 ADX(14)

    def precompute(self, d: dict):
        """Precompute session ranges and M15 ADX for all bars — O(n)."""
        n   = len(d["c"])
        ts  = d["ts"]          # array of "YYYY-MM-DD HH:MM:SS" strings

        # ── Parse UTC hours and dates ─────────────────────────────────────────
        hours = np.zeros(n, dtype=np.int8)
        dates = []
        for i in range(n):
            s = str(ts[i])     # "2013-01-02 08:15:00" or "2013-01-02T08:15:00"
            s = s.replace("T", " ")
            date_part = s[:10]   # "YYYY-MM-DD"
            time_part = s[11:13] if len(s) > 13 else s[11:13]
            h = int(time_part) if time_part.isdigit() else 0
            hours[i] = h
            dates.append(date_part)

        self._hour_arr = hours

        # ── Build asian_high / asian_low per day ──────────────────────────────
        # First pass: collect Asian session extremes per date
        asian_by_date: dict[str, tuple[float, float]] = {}
        for i in range(n):
            h = int(hours[i])
            if self.ASIAN_START_H <= h < self.ASIAN_END_H:
                date = dates[i]
                hi = float(d["h"][i])
                lo = float(d["l"][i])
                if date in asian_by_date:
                    prev_hi, prev_lo = asian_by_date[date]
                    asian_by_date[date] = (max(prev_hi, hi), min(prev_lo, lo))
                else:
                    asian_by_date[date] = (hi, lo)

        # Second pass: assign asian range values to each bar by its date
        asian_hi_arr  = np.full(n, np.nan)
        asian_lo_arr  = np.full(n, np.nan)
        asian_mid_arr = np.full(n, np.nan)

        for i in range(n):
            date = dates[i]
            if date in asian_by_date:
                ah, al = asian_by_date[date]
                asian_hi_arr[i]  = ah
                asian_lo_arr[i]  = al
                asian_mid_arr[i] = (ah + al) * 0.5

        self._asian_high_arr = asian_hi_arr
        self._asian_low_arr  = asian_lo_arr
        self._asian_mid_arr  = asian_mid_arr

        # ── M15 ADX ───────────────────────────────────────────────────────────
        self._adx_arr = self._adx_array(d["h"], d["l"], d["c"], self.ADX_PERIOD)

    # ─────────────────────────────────────────────────────────────────────────

    def signal(self, d: dict, i: int) -> Signal:
        if i < self.MIN_BARS:
            return Signal()

        # ── 1. London session window only ─────────────────────────────────────
        hour = int(self._hour_arr[i]) if self._hour_arr is not None else self._parse_hour(d["ts"][i])
        if not (self.LONDON_START_H <= hour < self.LONDON_END_H):
            return Signal()

        # ── 2. Asian range for today ──────────────────────────────────────────
        if self._asian_high_arr is None:
            return Signal()

        a_high = self._asian_high_arr[i]
        a_low  = self._asian_low_arr[i]
        a_mid  = self._asian_mid_arr[i]

        if math.isnan(a_high) or math.isnan(a_low) or math.isnan(a_mid):
            return Signal()

        # ── 3. Range quality filter ───────────────────────────────────────────
        atr = float(d["atr"][i])
        if atr <= 0:
            return Signal()

        range_size = a_high - a_low
        if range_size < self.MIN_RANGE_ATR * atr:
            return Signal()   # range too narrow

        # ── 4. ADX filter (still ranging) ─────────────────────────────────────
        adx = float(self._adx_arr[i]) if self._adx_arr is not None else 0.0
        if math.isnan(adx) or adx >= self.ADX_MAX:
            return Signal()

        # ── 5. Entry: price at boundary, confirmed with current bar direction ──
        tol    = self.TOUCH_TOL_ATR * atr
        c_cur  = float(d["c"][i])
        c_prev = float(d["c"][i - 1])
        l_prev = float(d["l"][i - 1])
        h_prev = float(d["h"][i - 1])

        # BUY: previous bar touched asian_low; current bar closes up
        near_low  = l_prev <= a_low + tol
        bounce_up = c_cur > c_prev and c_cur > a_low

        if near_low and bounce_up and a_mid > a_low:
            dist = a_mid - a_low                # expected move to midpoint
            self.tp_atr = max(dist / atr, 0.3)
            self.sl_atr = self.SL_ATR_MULT
            return Signal("BUY",
                          f"SR-low al={a_low:.2f} mid={a_mid:.2f} "
                          f"adx={adx:.1f} tp_atr={self.tp_atr:.2f}")

        # SELL: previous bar touched asian_high; current bar closes down
        near_high  = h_prev >= a_high - tol
        bounce_dn  = c_cur < c_prev and c_cur < a_high

        if near_high and bounce_dn and a_high > a_mid:
            dist = a_high - a_mid
            self.tp_atr = max(dist / atr, 0.3)
            self.sl_atr = self.SL_ATR_MULT
            return Signal("SELL",
                          f"SR-high ah={a_high:.2f} mid={a_mid:.2f} "
                          f"adx={adx:.1f} tp_atr={self.tp_atr:.2f}")

        return Signal()

    # ─── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_hour(ts_str: str) -> int:
        s = str(ts_str).replace("T", " ")
        try:
            return int(s[11:13])
        except Exception:
            return 0

    # ─── Indicators (same as BBMeanRev — self-contained) ─────────────────────

    @staticmethod
    def _wilder_smooth(arr: np.ndarray, period: int) -> np.ndarray:
        n   = len(arr)
        out = np.full(n, np.nan)
        if n < period:
            return out
        consec = 0; first_start = -1
        for k in range(n):
            if not math.isnan(float(arr[k])):
                consec += 1
                if consec == period:
                    first_start = k - period + 1; break
            else:
                consec = 0
        if first_start < 0:
            return out
        start_idx = first_start + period - 1
        out[start_idx] = float(np.mean(arr[first_start:first_start + period]))
        a = 1.0 / period
        for j in range(start_idx + 1, n):
            v = float(arr[j])
            out[j] = (out[j - 1] * (1 - a) + v * a) if not math.isnan(v) else out[j - 1]
        return out

    @classmethod
    def _adx_array(cls, high, low, close, period=14) -> np.ndarray:
        n      = len(close)
        result = np.full(n, np.nan)
        if n < period * 3:
            return result
        prev_c = np.empty(n); prev_c[0] = close[0]; prev_c[1:] = close[:-1]
        tr  = np.maximum(high - low,
                         np.maximum(np.abs(high - prev_c), np.abs(low - prev_c)))
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
        result[period * 3:] = adx[period * 3:]
        return result


# =============================================================================
# 3. SessionBreakout — Asian Range momentum breakout (London session)
#    Parameterised: MOMENTUM_FILTER, TP_ATR_MULT, MIN_RANGE_ATR
# =============================================================================

class SessionBreakout:
    """Asian Range (00:00–08:00 UTC) breakout during London (08:00–12:00 UTC).

    Thesis: Gold consolidates in Asian session; London session often breaks
    out with momentum — trade IN THE DIRECTION of the breakout.

    Configurable params (set on instance before precompute/signal):
      MOMENTUM_FILTER  — close must be MOMENTUM_FILTER×ATR beyond asian boundary
                          (0.0 = any close past boundary, 0.3/0.5 = meaningful break)
      TP_ATR_MULT      — TP distance from fill in ATR units
      MIN_RANGE_ATR    — skip days where Asian range < this × ATR

    SL is always placed at the opposite side of the Asian range + 0.5×ATR.
    Timeout set by the caller via cfg.max_hold_bars.
    """

    name       = "Session Breakout (Asian range → London momentum)"
    short_name = "SBO"

    # ── SL/TP — overridden dynamically per signal ────────────────────────────
    sl_atr  = 1.5
    tp_atr  = 0.5

    trail_atr_mult       = 999.0
    trail_activation_atr = 999.0

    # ── Tunable parameters (override on instance for each variant) ───────────
    MOMENTUM_FILTER = 0.0   # extra ATR buffer beyond asian boundary for entry
    TP_ATR_MULT     = 0.5   # TP = fill + TP_ATR_MULT × ATR
    MIN_RANGE_ATR   = 0.5

    ASIAN_START_H  = 0
    ASIAN_END_H    = 8
    LONDON_START_H = 8
    LONDON_END_H   = 12

    MIN_BARS = 100

    # ── Precomputed arrays ───────────────────────────────────────────────────
    _asian_high_arr = None
    _asian_low_arr  = None
    _hour_arr       = None
    _date_arr       = None

    # ── Sequential daily state (reset in precompute) ─────────────────────────
    _last_bo_date = None
    _bo_buy_done  = False
    _bo_sell_done = False

    def precompute(self, d: dict):
        n  = len(d["c"])
        ts = d["ts"]

        hours = np.zeros(n, dtype=np.int8)
        dates = []
        for i in range(n):
            s = str(ts[i]).replace("T", " ")
            h = int(s[11:13]) if len(s) > 13 and s[11:13].isdigit() else 0
            hours[i] = h
            dates.append(s[:10])

        self._hour_arr = hours
        self._date_arr = dates

        asian_by_date: dict = {}
        for i in range(n):
            h = int(hours[i])
            if self.ASIAN_START_H <= h < self.ASIAN_END_H:
                date = dates[i]
                hi, lo = float(d["h"][i]), float(d["l"][i])
                if date in asian_by_date:
                    ph, pl = asian_by_date[date]
                    asian_by_date[date] = (max(ph, hi), min(pl, lo))
                else:
                    asian_by_date[date] = (hi, lo)

        asian_hi = np.full(n, np.nan)
        asian_lo = np.full(n, np.nan)
        for i in range(n):
            date = dates[i]
            if date in asian_by_date:
                ah, al = asian_by_date[date]
                asian_hi[i] = ah
                asian_lo[i] = al

        self._asian_high_arr = asian_hi
        self._asian_low_arr  = asian_lo

        self._last_bo_date = None
        self._bo_buy_done  = False
        self._bo_sell_done = False

    def signal(self, d: dict, i: int) -> Signal:
        if i < self.MIN_BARS:
            return Signal()

        hour = int(self._hour_arr[i])
        if not (self.LONDON_START_H <= hour < self.LONDON_END_H):
            return Signal()

        a_high = self._asian_high_arr[i]
        a_low  = self._asian_low_arr[i]
        if math.isnan(a_high) or math.isnan(a_low):
            return Signal()

        atr = float(d["atr"][i])
        if atr <= 0:
            return Signal()

        if (a_high - a_low) < self.MIN_RANGE_ATR * atr:
            return Signal()

        date = self._date_arr[i]
        if date != self._last_bo_date:
            self._last_bo_date = date
            self._bo_buy_done  = False
            self._bo_sell_done = False

        c_cur = float(d["c"][i])
        buy_threshold  = a_high + self.MOMENTUM_FILTER * atr
        sell_threshold = a_low  - self.MOMENTUM_FILTER * atr

        # ── BUY breakout ──────────────────────────────────────────────────────
        if not self._bo_buy_done and c_cur > buy_threshold:
            self._bo_buy_done = True
            # SL = asian_low - 0.5×ATR (fixed structural level)
            sl_dist = (c_cur - a_low) + 0.5 * atr
            self.sl_atr = max(sl_dist / atr, 0.3)
            self.tp_atr = max(self.TP_ATR_MULT, 0.2)
            return Signal("BUY",
                          f"SBO mf={self.MOMENTUM_FILTER} ah={a_high:.2f} "
                          f"sl={self.sl_atr:.2f} tp={self.tp_atr:.2f}")

        # ── SELL breakout ─────────────────────────────────────────────────────
        if not self._bo_sell_done and c_cur < sell_threshold:
            self._bo_sell_done = True
            sl_dist = (a_high - c_cur) + 0.5 * atr
            self.sl_atr = max(sl_dist / atr, 0.3)
            self.tp_atr = max(self.TP_ATR_MULT, 0.2)
            return Signal("SELL",
                          f"SBO mf={self.MOMENTUM_FILTER} al={a_low:.2f} "
                          f"sl={self.sl_atr:.2f} tp={self.tp_atr:.2f}")

        return Signal()


# =============================================================================
# 4. RSICross — RSI crossback from extreme zones + H1 sideways filter
# =============================================================================

class RSICross(BBMeanRev):
    """RSI crossback mean reversion.

    Key difference from BB_MeanRev: enters AFTER reversal is confirmed
    (RSI crosses back through 30 from below, or 70 from above).
    Avoids entering against ongoing momentum.

    Regime: same H1 ADX<20 + EMA50/200 convergence filter as BBMeanRev.
    SL/TP symmetric 1:1 — pure mean reversion play.
    """

    name       = "RSI Cross Reversion (H1 ADX<20)"
    short_name = "RSX"

    sl_atr  = 1.5
    tp_atr  = 1.5
    trail_atr_mult       = 999.0
    trail_activation_atr = 999.0

    RSI_PERIOD = 14
    RSI_OS     = 30     # oversold threshold
    RSI_OB     = 70     # overbought threshold
    # Inherit: ADX_MAX=20, RANGE_PCT=0.03, MIN_BARS=850

    _rsi_arr = None

    @classmethod
    def _rsi_array(cls, close, period: int = 14) -> np.ndarray:
        close = np.asarray(close, dtype=float)
        n = len(close)
        result = np.full(n, np.nan)
        if n < period + 2:
            return result
        delta = np.diff(close)
        gains  = np.where(delta > 0, delta, 0.0)
        losses = np.where(delta < 0, -delta, 0.0)
        avg_g = float(np.mean(gains[:period]))
        avg_l = float(np.mean(losses[:period]))
        for j in range(period, n - 1):
            avg_g = (avg_g * (period - 1) + gains[j]) / period
            avg_l = (avg_l * (period - 1) + losses[j]) / period
            rs = avg_g / avg_l if avg_l > 0 else 999.0
            result[j + 1] = 100.0 - 100.0 / (1.0 + rs)
        return result

    def precompute(self, d: dict):
        self._h1_sideways_arr = self._build_h1_sideways(d)
        self._rsi_arr = self._rsi_array(d["c"], self.RSI_PERIOD)

    def signal(self, d: dict, i: int) -> Signal:
        if i < self.MIN_BARS:
            return Signal()
        if self._h1_sideways_arr is None or not int(self._h1_sideways_arr[i]):
            return Signal()

        rsi_cur  = self._rsi_arr[i]
        rsi_prev = self._rsi_arr[i - 1]
        if math.isnan(rsi_cur) or math.isnan(rsi_prev):
            return Signal()

        # BUY: RSI crosses up through oversold line (reversal confirmed)
        if rsi_prev <= self.RSI_OS and rsi_cur > self.RSI_OS:
            return Signal("BUY",
                          f"RSX-cross-OS rsi={rsi_cur:.1f}")

        # SELL: RSI crosses down through overbought line
        if rsi_prev >= self.RSI_OB and rsi_cur < self.RSI_OB:
            return Signal("SELL",
                          f"RSX-cross-OB rsi={rsi_cur:.1f}")

        return Signal()


# =============================================================================
# 5. BBConfirm — BB touch + 2-bar close-back-inside confirmation
# =============================================================================

class BBConfirm(BBMeanRev):
    """Bollinger Band touch with close-back-inside confirmation.

    Entry only when:
      - PREVIOUS bar closed OUTSIDE the BB band (genuine extreme)
      - CURRENT bar closes back INSIDE the band (momentum reversed)

    Stronger signal than simple touch: price must demonstrate a reversal
    candle, eliminating entries on one-wick spikes that continue lower/higher.
    TP = distance to BB midline; SL = 1.5×ATR.
    """

    name       = "BB Close-Back Confirmation (H1 ADX<20)"
    short_name = "BBC"

    sl_atr  = 1.5
    tp_atr  = 1.0
    trail_atr_mult       = 999.0
    trail_activation_atr = 999.0
    # Inherit all BB params and indicator helpers

    def precompute(self, d: dict):
        # Reuse parent precompute — builds H1 sideways + BB arrays
        super().precompute(d)

    def signal(self, d: dict, i: int) -> Signal:
        if i < self.MIN_BARS:
            return Signal()
        if not int(self._h1_sideways_arr[i]):
            return Signal()

        mid   = self._bb_mid_arr[i]
        upper = self._bb_upper_arr[i]
        lower = self._bb_lower_arr[i]
        if math.isnan(mid) or math.isnan(upper) or math.isnan(lower):
            return Signal()

        atr = float(d["atr"][i])
        if atr <= 0:
            return Signal()

        c_cur  = float(d["c"][i])
        c_prev = float(d["c"][i - 1])

        # BUY: previous close was BELOW lower band; current close is ABOVE lower band
        if c_prev < lower and c_cur >= lower:
            self.tp_atr = max((mid - c_cur) / atr, 0.3)
            return Signal("BUY",
                          f"BBC close-back-lower c_prev={c_prev:.2f} lower={lower:.2f} "
                          f"tp_atr={self.tp_atr:.2f}")

        # SELL: previous close was ABOVE upper band; current close is BELOW upper band
        if c_prev > upper and c_cur <= upper:
            self.tp_atr = max((c_cur - mid) / atr, 0.3)
            return Signal("SELL",
                          f"BBC close-back-upper c_prev={c_prev:.2f} upper={upper:.2f} "
                          f"tp_atr={self.tp_atr:.2f}")

        return Signal()


# =============================================================================
# 6. ATRRankRSI — extreme RSI only when market is genuinely quiet
# =============================================================================

class ATRRankRSI(BBMeanRev):
    """ATR-rank regime filter + RSI extreme entry.

    Instead of ADX, uses ATR percentile rank to identify ranging:
    - ATR rank in bottom RANK_THRESHOLD percentile of last RANK_PERIOD bars
      → price is moving less than usual = genuine consolidation
    - Then enter on RSI extreme (< OS or > OB) with 1-bar RSI reversal confirmation

    Rationale: ADX can lag during transitions; ATR rank directly measures
    whether price is moving within a narrow range right now.
    """

    name       = "ATR-Rank RSI Reversion"
    short_name = "ARR"

    sl_atr  = 1.5
    tp_atr  = 1.5
    trail_atr_mult       = 999.0
    trail_activation_atr = 999.0

    RANK_PERIOD    = 200   # lookback bars for ATR percentile
    RANK_THRESHOLD = 30    # only trade when ATR in bottom 30th percentile
    RSI_PERIOD     = 14
    RSI_OS         = 28    # tighter threshold (more extreme = better quality)
    RSI_OB         = 72
    MIN_BARS       = 250

    _rsi_arr      = None
    _atr_rank_arr = None

    def precompute(self, d: dict):
        self._rsi_arr = RSICross._rsi_array(d["c"], self.RSI_PERIOD)
        atr = np.asarray(d["atr"], dtype=float)
        n = len(atr)
        atr_rank = np.full(n, np.nan)
        p = self.RANK_PERIOD
        # Vectorised rolling rank using sorted windows
        for i in range(p, n):
            window = atr[i - p:i]
            valid  = window[~np.isnan(window)]
            if len(valid) > 0 and not math.isnan(atr[i]):
                atr_rank[i] = float(np.sum(valid <= atr[i])) / len(valid) * 100.0
        self._atr_rank_arr = atr_rank

    def signal(self, d: dict, i: int) -> Signal:
        if i < self.MIN_BARS:
            return Signal()

        atr_rank = self._atr_rank_arr[i]
        if math.isnan(atr_rank) or atr_rank > self.RANK_THRESHOLD:
            return Signal()

        rsi_cur  = self._rsi_arr[i]
        rsi_prev = self._rsi_arr[i - 1]
        if math.isnan(rsi_cur) or math.isnan(rsi_prev):
            return Signal()

        # Require RSI to be turning back (1-bar confirmation)
        if rsi_cur < self.RSI_OS and rsi_cur > rsi_prev:
            return Signal("BUY",
                          f"ARR rsi={rsi_cur:.1f} rank={atr_rank:.0f}")
        if rsi_cur > self.RSI_OB and rsi_cur < rsi_prev:
            return Signal("SELL",
                          f"ARR rsi={rsi_cur:.1f} rank={atr_rank:.0f}")

        return Signal()


# =============================================================================
# 7. HybridMR — combines best filters from all tested sideways strategies
# =============================================================================

class HybridMR(BBMeanRev):
    """Hybrid Mean Reversion — best filters stacked.

    Combines:
      ATRRankRSI  → ATR percentile rank regime filter (quiet market only)
      BBMeanRev   → H1 ADX < 20 + EMA convergence + BB structural level
      RSICross    → RSI turning (not just extreme; reversal starting)
      Session     → exclude London open 08:00–11:00 UTC (momentum window)

    Entry: BB lower/upper band touch on PREV bar + RSI turning on CURRENT bar.
           Two independent confirmations must align simultaneously.

    Two variants via REQUIRE_H1_ADX:
      HybridMR_A  REQUIRE_H1_ADX = True  — adds H1 ADX+EMA filter
      HybridMR_B  REQUIRE_H1_ADX = False — ATR rank + BB + RSI only
    """

    name       = "Hybrid MR (ATR-rank + BB + RSI + session)"
    short_name = "HMR"

    sl_atr  = 1.5
    tp_atr  = 1.0
    trail_atr_mult       = 999.0
    trail_activation_atr = 999.0

    # ── Regime params ─────────────────────────────────────────────────────────
    RANK_PERIOD    = 200
    RANK_THRESHOLD = 30    # bottom 30th percentile of ATR = quiet market
    REQUIRE_H1_ADX = True  # flip to False for variant B

    # ── Session exclusion ─────────────────────────────────────────────────────
    EXCL_START_H = 8
    EXCL_END_H   = 11     # skip 08:00–11:00 UTC (London momentum window)

    # ── RSI ───────────────────────────────────────────────────────────────────
    RSI_PERIOD = 14
    RSI_OS     = 32
    RSI_OB     = 68

    # Inherit from BBMeanRev: BB_PERIOD=20, BB_STDS=2.0, ADX_MAX=20, RANGE_PCT=0.03
    MIN_BARS = 850

    _rsi_arr      = None
    _atr_rank_arr = None
    _hour_arr     = None

    def precompute(self, d: dict):
        # H1 sideways + BB arrays from parent
        self._h1_sideways_arr = self._build_h1_sideways(d)
        closes = np.asarray(d["c"], dtype=float)
        sma = self._sma_array(closes, self.BB_PERIOD)
        std = self._std_array(closes, self.BB_PERIOD)
        self._bb_mid_arr   = sma
        self._bb_upper_arr = sma + self.BB_STDS * std
        self._bb_lower_arr = sma - self.BB_STDS * std

        # RSI
        self._rsi_arr = RSICross._rsi_array(d["c"], self.RSI_PERIOD)

        # ATR percentile rank
        atr = np.asarray(d["atr"], dtype=float)
        n = len(atr)
        p = self.RANK_PERIOD
        atr_rank = np.full(n, np.nan)
        for i in range(p, n):
            window = atr[i - p:i]
            valid  = window[~np.isnan(window)]
            if len(valid) > 0 and not math.isnan(atr[i]):
                atr_rank[i] = float(np.sum(valid <= atr[i])) / len(valid) * 100.0
        self._atr_rank_arr = atr_rank

        # UTC hour array
        ts = d["ts"]
        hours = np.zeros(n, dtype=np.int8)
        for i in range(n):
            s = str(ts[i]).replace("T", " ")
            hours[i] = int(s[11:13]) if len(s) > 13 and s[11:13].isdigit() else 0
        self._hour_arr = hours

    def signal(self, d: dict, i: int) -> Signal:
        if i < self.MIN_BARS:
            return Signal()

        # ── 1. ATR rank regime filter ─────────────────────────────────────────
        atr_rank = self._atr_rank_arr[i]
        if math.isnan(atr_rank) or atr_rank > self.RANK_THRESHOLD:
            return Signal()

        # ── 2. Optional H1 ADX + EMA filter ──────────────────────────────────
        if self.REQUIRE_H1_ADX:
            if self._h1_sideways_arr is None or not int(self._h1_sideways_arr[i]):
                return Signal()

        # ── 3. Session exclusion ──────────────────────────────────────────────
        hour = int(self._hour_arr[i])
        if self.EXCL_START_H <= hour < self.EXCL_END_H:
            return Signal()

        # ── 4. BB bands ───────────────────────────────────────────────────────
        mid   = self._bb_mid_arr[i]
        upper = self._bb_upper_arr[i]
        lower = self._bb_lower_arr[i]
        if math.isnan(mid) or math.isnan(lower) or math.isnan(upper):
            return Signal()

        atr = float(d["atr"][i])
        if atr <= 0:
            return Signal()

        # ── 5. RSI ────────────────────────────────────────────────────────────
        rsi_cur  = self._rsi_arr[i]
        rsi_prev = self._rsi_arr[i - 1]
        if math.isnan(rsi_cur) or math.isnan(rsi_prev):
            return Signal()

        l_prev = float(d["l"][i - 1])
        h_prev = float(d["h"][i - 1])
        c_cur  = float(d["c"][i])

        # BUY: wick tested lower band prev bar + RSI turning up this bar
        at_lower       = l_prev <= lower
        rsi_turning_up = rsi_cur < self.RSI_OS and rsi_cur > rsi_prev
        if at_lower and rsi_turning_up:
            self.tp_atr = max((mid - c_cur) / atr, 0.3)
            self.sl_atr = 1.5
            return Signal("BUY",
                          f"HMR rank={atr_rank:.0f} rsi={rsi_cur:.1f}")

        # SELL: wick tested upper band prev bar + RSI turning down this bar
        at_upper        = h_prev >= upper
        rsi_turning_dn  = rsi_cur > self.RSI_OB and rsi_cur < rsi_prev
        if at_upper and rsi_turning_dn:
            self.tp_atr = max((c_cur - mid) / atr, 0.3)
            self.sl_atr = 1.5
            return Signal("SELL",
                          f"HMR rank={atr_rank:.0f} rsi={rsi_cur:.1f}")

        return Signal()
