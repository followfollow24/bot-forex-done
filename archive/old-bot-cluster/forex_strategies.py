#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 forex_strategies.py  —  5 Trading Strategies (Market-Agnostic Logic)
================================================================================
กลยุทธ์เหมือน crypto ทุกประการ — signal logic คำนวณจากพฤติกรรมราคาสากล
ไม่ว่าราคาจะเป็น BTC หรือ EUR/USD, z-score และ Donchian ทำงานเหมือนกัน

ปรับเล็กน้อย:
  - Breakout sl_atr: 1.8 → 2.0  (Forex มี gap weekend → SL กว้างขึ้นเล็กน้อย)
  - Momentum threshold: 0.045 → 0.025  (ROC ของ Forex ต่ำกว่า crypto มาก)
================================================================================
"""
from __future__ import annotations

import math

from forex_indicators import Signal


class MeanReversion:
    """สวนทาง: ราคาเหวี่ยงออกจากค่าเฉลี่ยมาก → เดิมพันว่าจะเด้งกลับ.

    เหมาะกับ Forex มาก: major pairs มี mean-reversion สูงกว่า crypto
    เพราะมีกลไก central bank คานอำนาจ
    """
    name       = "Mean-Reversion (สวนทางค่าเฉลี่ย)"
    short_name = "Mean-Reversion"
    sl_atr, tp_atr, entry_z = 3.2, 2.4, 2.8

    def signal(self, d: dict, i: int) -> Signal:
        if i < 1:
            return Signal()
        z, zp = d["zscore"][i], d["zscore"][i - 1]
        if math.isnan(z) or math.isnan(zp):
            return Signal()
        if z <= -self.entry_z and zp > -self.entry_z:
            return Signal("BUY",  f"oversold z={z:.2f}")
        if z >=  self.entry_z and zp < self.entry_z:
            return Signal("SELL", f"overbought z={z:.2f}")
        return Signal()


class Breakout:
    """ทะลุกรอบ: ราคาทะลุ Donchian N แท่ง ตามทิศ EMA ยาว.

    ปรับ sl_atr 1.8 → 2.0 เผื่อ gap เปิดวันจันทร์
    """
    name       = "Breakout (ทะลุกรอบราคา)"
    short_name = "Breakout"
    sl_atr, tp_atr = 2.0, 4.4

    def signal(self, d: dict, i: int) -> Signal:
        if i < 1:
            return Signal()
        c, cp   = d["c"][i],       d["c"][i - 1]
        hi, hip = d["donch_hi"][i], d["donch_hi"][i - 1]
        lo, lop = d["donch_lo"][i], d["donch_lo"][i - 1]
        if math.isnan(hi) or math.isnan(hip):
            return Signal()
        ema = d["ema"][i]
        if c > hi and cp <= hip and c > ema:
            return Signal("BUY",  "fresh break up")
        if c < lo and cp >= lop and c < ema:
            return Signal("SELL", "fresh break down")
        return Signal()


class TrendFollowing:
    """ตามเทรนด์: EMA เร็วตัด EMA ช้า → เข้าตามทิศ."""
    name       = "Trend-Following (EMA ตัดกัน)"
    short_name = "Trend-Following"
    sl_atr, tp_atr = 2.2, 4.2

    def signal(self, d: dict, i: int) -> Signal:
        if i < 1:
            return Signal()
        ef, es   = d["ema_fast"][i], d["ema_slow"][i]
        efp, esp = d["ema_fast"][i - 1], d["ema_slow"][i - 1]
        if math.isnan(es) or math.isnan(esp):
            return Signal()
        if ef > es and efp <= esp:
            return Signal("BUY",  "ema cross up")
        if ef < es and efp >= esp:
            return Signal("SELL", "ema cross down")
        return Signal()


class Momentum:
    """โมเมนตัม: ROC แรงพอ → เข้าตามแรงส่ง.

    ปรับ threshold 0.045 → 0.025: EUR/USD วิ่ง 4.5% ใน 48 แท่ง (12 ชม.) ไม่ค่อยเกิด
    ค่า 0.025 = ราคาเปลี่ยน 2.5% ใน 12 ชม. — เหมาะกับ Forex มากกว่า
    """
    name       = "Momentum (แรงส่งราคา ROC)"
    short_name = "Momentum"
    sl_atr, tp_atr, thr = 1.9, 3.8, 0.025

    def signal(self, d: dict, i: int) -> Signal:
        if i < 1:
            return Signal()
        r, rp = d["roc"][i], d["roc"][i - 1]
        if math.isnan(r) or math.isnan(rp):
            return Signal()
        if r >=  self.thr and rp < self.thr:
            return Signal("BUY",  f"roc={r:.4f}")
        if r <= -self.thr and rp > -self.thr:
            return Signal("SELL", f"roc={r:.4f}")
        return Signal()


class PullbackTrend:
    """ซื้อย่อในเทรนด์: อยู่ฝั่งเดียวกับ EMA ยาว แล้วย่อสวน → เข้าตามเทรนด์ใหญ่."""
    name       = "Pullback-in-Trend (ซื้อย่อตามเทรนด์)"
    short_name = "Pullback-Trend"
    sl_atr, tp_atr, dip_z = 2.0, 3.4, 1.0

    def signal(self, d: dict, i: int) -> Signal:
        if i < 1:
            return Signal()
        z, zp  = d["zscore"][i], d["zscore"][i - 1]
        c, ema = d["c"][i], d["ema"][i]
        if math.isnan(z) or math.isnan(zp) or math.isnan(ema):
            return Signal()
        if c > ema and z <= -self.dip_z and zp > -self.dip_z:
            return Signal("BUY",  "pullback in uptrend")
        if c < ema and z >=  self.dip_z and zp < self.dip_z:
            return Signal("SELL", "pullback in downtrend")
        return Signal()


from forex_hybrid_strategy import HybridTrendPullback  # noqa: E402

# ── Strategy Portfolio ─────────────────────────────────────────────────────────
# 5 กลยุทธ์เดิม + HybridTrendPullback (multi-timeframe H1+M15)
# live_bot แบ่งทุนเท่ากันทุก strategy ที่ active ใน regime นั้น
# HybridTrendPullback: ต้องการ warm-up ≥ 850 bars → ตั้ง history_bars = 900
STRATEGIES = [MeanReversion, Breakout, TrendFollowing,
              Momentum, PullbackTrend, HybridTrendPullback]
