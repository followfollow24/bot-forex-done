#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 forex_regime.py  —  Adaptive Market Regime Detection
================================================================================
ใช้ logic เดียวกับ regime_detector.py ทุกประการ
Hurst + ADX + Vol Ratio เป็นคณิตศาสตร์สากล ใช้ได้ทั้ง crypto และ forex

เพิ่มใหม่สำหรับ Forex:
  is_market_open_utc()  — ตรวจสอบว่าตลาด Forex เปิดทำการอยู่หรือไม่
  (ใช้แทน is_weekend_utc() ของ crypto ซึ่งไม่เกี่ยวกับตลาด 24/7)
================================================================================
"""
from __future__ import annotations

import datetime
from enum import Enum
from typing import Tuple

import numpy as np


# =============================================================================
# 1. REGIME ENUM & MAPPINGS
# =============================================================================
class Regime(Enum):
    TRENDING       = "trending"
    MEAN_REVERTING = "mean_reverting"
    VOLATILE       = "volatile"
    NEUTRAL        = "neutral"


# กลยุทธ์ที่อนุญาตในแต่ละ regime
# Hybrid-TPB: เข้าได้เฉพาะ TRENDING + NEUTRAL (ต้องมีเทรนด์ H1)
# VOLATILE: ไม่เปิด Hybrid เพราะ ADX filter ช่วยกรองอยู่แล้ว
REGIME_STRATEGIES = {
    Regime.TRENDING:       {"Breakout", "Trend-Following", "Momentum",
                            "Hybrid-TPB"},
    Regime.MEAN_REVERTING: {"Mean-Reversion", "Pullback-Trend"},
    Regime.VOLATILE:       {"Mean-Reversion"},
    Regime.NEUTRAL:        {"Mean-Reversion", "Breakout", "Trend-Following",
                            "Momentum", "Pullback-Trend", "Hybrid-TPB"},
}

# ตัวคูณขนาดไม้ตาม regime
REGIME_SIZE_MULT = {
    Regime.TRENDING:       1.0,
    Regime.MEAN_REVERTING: 1.0,
    Regime.VOLATILE:       0.5,
    Regime.NEUTRAL:        0.7,
}


# =============================================================================
# 2. HURST EXPONENT  (R/S method)
# =============================================================================
def hurst_exponent(prices: np.ndarray, max_lag: int = 100) -> float:
    """H > 0.5 → trending, H ≈ 0.5 → random walk, H < 0.5 → mean-reverting."""
    valid = prices[prices > 0]
    if len(valid) < max_lag * 2:
        return 0.5
    log_ret = np.diff(np.log(valid))
    n = len(log_ret)
    if n < max_lag:
        return 0.5

    lags, rs_means = [], []
    for lag in range(10, min(max_lag, n // 4)):
        n_chunks = n // lag
        if n_chunks < 2:
            continue
        rs_chunk = []
        for k in range(n_chunks):
            chunk = log_ret[k * lag:(k + 1) * lag]
            cumdev = np.cumsum(chunk - chunk.mean())
            R = cumdev.max() - cumdev.min()
            S = chunk.std(ddof=1)
            if S > 1e-12:
                rs_chunk.append(R / S)
        if rs_chunk:
            lags.append(lag)
            rs_means.append(np.mean(rs_chunk))

    if len(lags) < 3:
        return 0.5
    H = float(np.polyfit(np.log(np.array(lags, float)),
                         np.log(np.array(rs_means, float)), 1)[0])
    return max(0.0, min(H, 1.0))


# =============================================================================
# 3. ADX  (Wilder's smoothing)
# =============================================================================
def compute_adx(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                period: int = 14) -> float:
    """> 25 → strong trend,  < 20 → ranging."""
    n = len(close)
    if n < period * 3:
        return 20.0

    prev_c = np.empty(n)
    prev_c[0] = close[0]
    prev_c[1:] = close[:-1]
    tr = np.maximum(high - low,
                    np.maximum(np.abs(high - prev_c), np.abs(low - prev_c)))
    up = np.diff(high)
    dn = -np.diff(low)
    plus_dm  = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)

    def wilder(arr: np.ndarray, p: int) -> np.ndarray:
        out = np.empty(len(arr))
        out[0] = arr[:p].mean() if len(arr) >= p else arr[0]
        a = 1.0 / p
        for i in range(1, len(arr)):
            out[i] = out[i - 1] * (1 - a) + arr[i] * a
        return out

    atr_s = wilder(tr[1:], period)
    safe  = np.where(atr_s > 0, atr_s, 1.0)
    plus_di  = 100.0 * wilder(plus_dm, period) / safe
    minus_di = 100.0 * wilder(minus_dm, period) / safe
    di_sum = np.where(plus_di + minus_di > 0, plus_di + minus_di, 1.0)
    dx = 100.0 * np.abs(plus_di - minus_di) / di_sum
    return float(wilder(dx, period)[-1])


# =============================================================================
# 4. VOLATILITY RATIO
# =============================================================================
def volatility_ratio(close: np.ndarray,
                     short_window: int = 8,
                     long_window: int = 96) -> float:
    """vol(short) / vol(long) — > 1.8 = expanding."""
    valid = close[close > 0]
    if len(valid) < long_window + 1:
        return 1.0
    log_ret = np.diff(np.log(valid))
    if len(log_ret) < long_window:
        return 1.0
    vol_l = log_ret[-long_window:].std()
    if vol_l < 1e-12:
        return 1.0
    return float(log_ret[-short_window:].std() / vol_l)


# =============================================================================
# 5. FOREX MARKET HOURS  (แทน is_weekend_utc ของ crypto)
# =============================================================================
def is_market_open_utc() -> bool:
    """ตลาด Forex เปิด: Sun 22:00 UTC → Fri 22:00 UTC.

    ปิด: Fri 22:00 UTC → Sun 22:00 UTC (weekend)
    """
    now = datetime.datetime.utcnow()
    wd  = now.weekday()   # 0=Mon, 4=Fri, 5=Sat, 6=Sun
    h   = now.hour

    if wd == 5:            # วันเสาร์: ปิดทั้งวัน
        return False
    if wd == 4 and h >= 22:  # วันศุกร์หลัง 22:00 UTC: ปิด
        return False
    if wd == 6 and h < 22:   # วันอาทิตย์ก่อน 22:00 UTC: ปิด
        return False
    return True


def minutes_until_friday_close() -> int:
    """นาทีที่เหลือก่อนตลาดปิดวันศุกร์ (-1 = ไม่ใช่วันศุกร์)."""
    now = datetime.datetime.utcnow()
    if now.weekday() != 4:   # ไม่ใช่วันศุกร์
        return -1
    close_time = now.replace(hour=22, minute=0, second=0, microsecond=0)
    delta = (close_time - now).total_seconds() / 60.0
    return int(delta) if delta > 0 else 0


def is_rollover_window() -> bool:
    """21:45–22:15 UTC — spread กว้างมากช่วง daily rollover."""
    h, m = datetime.datetime.utcnow().hour, datetime.datetime.utcnow().minute
    return (h == 21 and m >= 45) or (h == 22 and m <= 15)


def get_active_sessions() -> list:
    """คืนรายชื่อ session ที่กำลัง active ใน UTC ปัจจุบัน."""
    h = datetime.datetime.utcnow().hour
    sessions = []
    if 7 <= h < 17:   sessions.append("london")
    if 12 <= h < 21:  sessions.append("newyork")
    if 23 <= h or h < 8:  sessions.append("tokyo")
    if 21 <= h or h < 6:  sessions.append("sydney")
    return sessions


# =============================================================================
# 6. REGIME DETECTOR
# =============================================================================
class RegimeDetector:
    def __init__(
        self,
        hurst_window: int  = 200,
        adx_period:   int  = 14,
        vol_short:    int  = 8,
        vol_long:     int  = 96,
        h_trend:   float = 0.55,
        h_mr:      float = 0.45,
        adx_trend: float = 25.0,
        adx_range: float = 20.0,
        vol_expand: float = 1.8,
    ):
        self.hurst_window = hurst_window
        self.adx_period   = adx_period
        self.vol_short    = vol_short
        self.vol_long     = vol_long
        self.h_trend   = h_trend
        self.h_mr      = h_mr
        self.adx_trend = adx_trend
        self.adx_range = adx_range
        self.vol_expand = vol_expand

    def detect(self, high: np.ndarray, low: np.ndarray,
               close: np.ndarray) -> Tuple[Regime, dict]:
        tail = close[-self.hurst_window:] if len(close) > self.hurst_window else close
        h  = hurst_exponent(tail, max_lag=min(100, len(tail) // 3))
        a  = compute_adx(high, low, close, self.adx_period)
        vr = volatility_ratio(close, self.vol_short, self.vol_long)

        info = {"hurst": round(h, 3), "adx": round(a, 1), "vol_ratio": round(vr, 2)}

        if vr > self.vol_expand:
            return Regime.VOLATILE, info
        if h > self.h_trend and a > self.adx_trend:
            return Regime.TRENDING, info
        if h < self.h_mr and a < self.adx_range:
            return Regime.MEAN_REVERTING, info
        return Regime.NEUTRAL, info
