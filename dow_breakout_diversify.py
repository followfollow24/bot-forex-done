#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 dow_breakout_diversify.py — Dow-Theory structure breakout (signal คนละแบบ)
================================================================================

 ไอเดีย (Dow Theory / market structure):
   - จำแนก regime: UPTREND (HH/HL) / DOWNTREND (LH/LL) / SIDEWAYS (สะสม)
   - ตอนตลาดสะสม (range แคบ) → รอ "breakout" ออกจากกรอบ Donchian
   - breakout ขึ้น (ทำ new high 48 แท่ง) + อยู่เหนือ EMA → BUY ตามเทรนด์
   - breakout ลง (new low) + อยู่ใต้ EMA → SELL ตามเทรนด์
   - exit แบบ "ปล่อยวิ่งตามเทรนด์" (trailing stop) — ตรงตาม Dow: ถือจนเทรนด์กลับ

 ต่างจาก C_wider:
   C_wider = pullback กลับมาแตะ EMA20 แล้วเด้ง (เข้าตอนย่อ)
   DowBreakout = ทะลุกรอบออกไป (เข้าตอน momentum) ← คนละจังหวะเข้า

 ทดสอบ 3 อย่าง (เหมือน mean_reversion_diversify):
   (A) มี edge ไหม (PF>1 หลังต้นทุน)
   (B) corr กับ C_wider ต่ำไหม
   (C) ถ้าผ่านทั้งคู่ → combined frontier (C_wider + DowBreakout)

 วิธีรัน:
   python3 dow_breakout_diversify.py --csv download/xauusd-m15-bid-2013-01-01-2026-06-10.csv
================================================================================
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from copy import deepcopy
from typing import Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forex_config import ForexConfig
from forex_indicators import Signal
from backtest_forex import (DataLoader, prepare_data, BacktestEngine,
                             FastHybridTrendPullback, compute_metrics)

SPREAD   = 0.10
COMM     = 3.50
START    = 10_000.0
RISK_PCT = 0.30

CWIDER = dict(sl_atr=2.5, tp_atr=5.0, trail_mult=999.0, trail_act=999.0)


# =============================================================================
# Dow-Theory Breakout Strategy
# =============================================================================
class DowBreakoutStrategy:
    """
    Breakout ออกจากกรอบ Donchian + กรอง trend ด้วย EMA + (option) accumulation filter.
    Exit: ปล่อยกำไรวิ่งตามเทรนด์ด้วย trailing stop (อ่านโดย BacktestEngine).
    """
    name = "DowBreakout (Donchian + EMA trend + accumulation)"

    # Entry params
    USE_TREND_FILTER = True    # breakout ต้องอยู่ฝั่งเดียวกับ EMA
    ACCUM_MAX_WIDTH  = 0.0     # 0 = ปิด filter; >0 = กรอบก่อน breakout ต้องแคบ < N×ATR
    CONFIRM_CLOSE    = True    # ต้องปิดเหนือ/ใต้กรอบ (ไม่ใช่แค่ไส้เทียนแตะ)

    # Exit params (อ่านโดย BacktestEngine) — "ride the trend"
    sl_atr = 2.0
    tp_atr = 10.0              # TP ไกลมาก → ปล่อย trailing ทำงานเป็นหลัก
    trail_atr_mult       = 3.0   # trail ห่าง 3 ATR
    trail_activation_atr = 1.0   # เริ่ม trail หลังกำไร 1 ATR

    MIN_BARS = 120

    def precompute(self, d: dict):
        pass

    def signal(self, d: dict, i: int) -> Signal:
        if i < self.MIN_BARS:
            return Signal()

        c   = d["c"][i]
        o   = d["o"][i]
        dh  = d["donch_hi"][i]   # max high ของ 48 แท่งก่อนหน้า (shift(1) แล้ว — no lookahead)
        dl  = d["donch_lo"][i]
        ema = d["ema"][i]
        atr = d["atr"][i]
        if any(math.isnan(x) for x in (dh, dl, ema, atr)) or atr <= 0:
            return Signal()

        # accumulation filter: กรอบก่อน breakout ต้องแคบ (= สะสม) ถ้าเปิดใช้
        if self.ACCUM_MAX_WIDTH > 0:
            width_atr = (dh - dl) / atr
            if width_atr > self.ACCUM_MAX_WIDTH:
                return Signal()   # กรอบกว้างไป = ไม่ใช่ accumulation

        broke_up   = (c > dh and o <= dh) if self.CONFIRM_CLOSE else (d["h"][i] > dh)
        broke_down = (c < dl and o >= dl) if self.CONFIRM_CLOSE else (d["l"][i] < dl)

        if broke_up:
            if not self.USE_TREND_FILTER or c > ema:
                return Signal("BUY", f"breakout↑ donch_hi={dh:.2f}")
        elif broke_down:
            if not self.USE_TREND_FILTER or c < ema:
                return Signal("SELL", f"breakout↓ donch_lo={dl:.2f}")

        return Signal()


# =============================================================================
# Dow regime classification (สำหรับรายงาน — uptrend/downtrend/sideways)
# =============================================================================
def classify_regimes(d: dict) -> dict:
    """นับสัดส่วน bar ที่เป็น UP/DOWN/SIDEWAYS ตามโครงสร้าง (ema slope + donchian width)."""
    c   = d["c"]; ema = d["ema"]; atr = d["atr"]
    dh  = d["donch_hi"]; dl = d["donch_lo"]
    n = len(c)
    up = dn = side = valid = 0
    for i in range(120, n):
        if any(math.isnan(x) for x in (ema[i], atr[i], dh[i], dl[i])) or atr[i] <= 0:
            continue
        valid += 1
        width_atr = (dh[i] - dl[i]) / atr[i]
        if width_atr < 6.0:           # กรอบแคบ = สะสม/ไซด์เวย์
            side += 1
        elif c[i] > ema[i]:
            up += 1
        else:
            dn += 1
    return dict(up=up, dn=dn, side=side, valid=valid)


# =============================================================================
# Engine helpers
# =============================================================================
def _cfg(risk=RISK_PCT):
    c = ForexConfig()
    c.total_capital_usd    = START
    c.risk_per_trade_pct   = risk
    c.partial_tp_atr       = 999.0
    c.partial_tp_frac      = 0.0
    c.move_sl_to_breakeven = False
    return c


def run_cwider(d, risk=RISK_PCT):
    s = FastHybridTrendPullback()
    s.precompute(d)
    s.sl_atr, s.tp_atr = CWIDER["sl_atr"], CWIDER["tp_atr"]
    s.trail_atr_mult, s.trail_activation_atr = CWIDER["trail_mult"], CWIDER["trail_act"]
    eng = BacktestEngine(d, _cfg(risk), s, spread_price=SPREAD,
                         commission_per_lot=COMM, symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)
    return eng.trades, eng.equity_curve


def run_dow(d, risk=RISK_PCT):
    s = DowBreakoutStrategy()
    eng = BacktestEngine(d, _cfg(risk), s, spread_price=SPREAD,
                         commission_per_lot=COMM, symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)
    return eng.trades, eng.equity_curve


def run_combined(d, risk_per_leg):
    tc, ec = run_cwider(d, risk_per_leg)
    td, ed = run_dow(d, risk_per_leg)
    n = min(len(ec), len(ed))
    comb = [(ec[i] + ed[i]) / 2.0 for i in range(n)]
    return tc + td, comb


def monthly_returns(equity_curve, ts) -> pd.Series:
    try:
        idx = pd.to_datetime(ts.astype("int64"), unit="ms", utc=True)
    except (ValueError, TypeError):
        idx = pd.to_datetime(ts, utc=True)
    n = min(len(equity_curve), len(idx))
    s = pd.Series(equity_curve[:n], index=idx[:n])
    return s.resample("ME").last().pct_change().dropna()


def calmar(m):
    ret = (m.get("final_equity", START) - START) / START * 100
    dd  = m.get("max_dd_pct", 99) or 0.01
    return ret / dd


def _row(label, m):
    if not m or m.get("trades", 0) == 0:
        print(f"  {label:<24}  NO TRADES"); return
    ret = (m.get("final_equity", START) - START) / START * 100
    print(f"  {label:<24}  PF={m.get('profit_factor',0):.3f}  "
          f"Win={m.get('win_rate',0):.1%}  Ret={ret:+.1f}%  "
          f"DD={m.get('max_dd_pct',0):.1f}%  Calmar={calmar(m):.1f}  #T={m.get('trades',0):,}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--accum-width", type=float, default=0.0,
                    help=">0 เปิด accumulation filter: กรอบก่อน breakout ต้อง < N×ATR")
    ap.add_argument("--no-trend-filter", action="store_true")
    ap.add_argument("--spread-price", type=float, default=0.10)
    ap.add_argument("--commission-per-lot", type=float, default=3.50)
    args = ap.parse_args()

    global SPREAD, COMM
    SPREAD = args.spread_price
    COMM   = args.commission_per_lot
    DowBreakoutStrategy.ACCUM_MAX_WIDTH = args.accum_width
    DowBreakoutStrategy.USE_TREND_FILTER = not args.no_trend_filter

    print()
    print("=" * 92)
    print(" DOW-BREAKOUT DIVERSIFICATION — structure breakout vs C_wider pullback")
    print("=" * 92)
    print(f"  Cost : spread={SPREAD} comm=${COMM}/lot/side risk={RISK_PCT}%")
    print(f"  Dow  : trend-filter={DowBreakoutStrategy.USE_TREND_FILTER}  "
          f"accum-filter={'OFF' if args.accum_width==0 else f'<{args.accum_width}ATR'}  "
          f"exit=trail {DowBreakoutStrategy.trail_atr_mult}ATR@+{DowBreakoutStrategy.trail_activation_atr}")
    print("=" * 92)

    loader = DataLoader(log_fn=lambda *a, **k: None)
    cfg0 = ForexConfig(); cfg0.total_capital_usd = START
    df, _ = loader.load(args.symbol, 99.0, cfg0, csv_path=args.csv, allow_synthetic=True)
    d = prepare_data(df)
    if d is None:
        print("[ERROR] prepare_data failed"); sys.exit(1)
    ts = d["ts"]
    print(f"  Data : {len(df):,} bars  {df['timestamp'].iloc[0]} → {df['timestamp'].iloc[-1]}")

    # regime breakdown (Dow structure)
    rg = classify_regimes(d)
    if rg["valid"]:
        print(f"  Structure: UPTREND {rg['up']/rg['valid']:.0%}  "
              f"DOWNTREND {rg['dn']/rg['valid']:.0%}  "
              f"SIDEWAYS/accum {rg['side']/rg['valid']:.0%}")
    print()

    print("Running C_wider (pullback) ...", flush=True)
    tc, ec = run_cwider(d)
    m_c = compute_metrics(tc, ec, START)
    print("Running DowBreakout (breakout) ...", flush=True)
    td, ed = run_dow(d)
    m_d = compute_metrics(td, ed, START)

    print()
    print("=" * 92); print(" STANDALONE EDGE"); print("=" * 92)
    _row("C_wider (pullback)", m_c)
    _row("DowBreakout", m_d)
    print()

    # (A) edge?
    d_pf, d_n = m_d.get("profit_factor", 0), m_d.get("trades", 0)
    print("-" * 92)
    if d_n < 50:
        print(f"  ⚠️  DowBreakout เทรดน้อย ({d_n}) — สรุปไม่ได้"); edge_ok = False
    elif d_pf > 1.0:
        print(f"  ✅ (A) DowBreakout มี edge: PF={d_pf:.3f} > 1.0  ({d_n:,} ไม้)"); edge_ok = True
    else:
        print(f"  ❌ (A) DowBreakout ไม่มี edge: PF={d_pf:.3f} ≤ 1.0  ({d_n:,} ไม้)"); edge_ok = False

    # (B) correlation
    r_c = monthly_returns(ec, ts); r_d = monthly_returns(ed, ts)
    j = pd.concat([r_c.rename("c"), r_d.rename("d")], axis=1).dropna()
    corr = j["c"].corr(j["d"]) if len(j) > 3 else float("nan")
    print(f"  Correlation (monthly, n={len(j)}): {corr:+.3f}")
    if not math.isnan(corr) and corr < 0.5:
        print(f"  ✅ (B) corr ต่ำ-กลาง ({corr:+.2f}) — diversify ได้"); corr_ok = True
    elif not math.isnan(corr):
        print(f"  ❌ (B) corr สูง ({corr:+.2f}) — ไม่ diversify"); corr_ok = False
    else:
        corr_ok = False
    print("-" * 92)

    # (C) combined frontier
    if edge_ok and corr_ok:
        print()
        print("=" * 92)
        print(" COMBINED FRONTIER — C_wider + DowBreakout (เทียบ DD เท่ากัน)")
        print("=" * 92)
        print(f"     {'risk/leg':>9} | {'C_wider เดี่ยว':^26} | {'C_wider + DowBreakout':^28}")
        print(f"     {'':>9} | {'Ret':>9} {'DD':>6} {'Calmar':>7} | {'Ret':>9} {'DD':>6} {'Calmar':>7}")
        for rpl in (0.15, 0.20, 0.25, 0.30, 0.375, 0.45):
            tcx, ecx = run_cwider(d, rpl); mc = compute_metrics(tcx, ecx, START)
            tco, eco = run_combined(d, rpl); mo = compute_metrics(tco, eco, START)
            rc  = (mc.get("final_equity", START) - START) / START * 100
            rco = (mo.get("final_equity", START) - START) / START * 100
            print(f"     {rpl:>8.3f}% | {rc:>+8.1f}% {mc.get('max_dd_pct',0):>5.1f}% "
                  f"{calmar(mc):>7.1f} | {rco:>+8.1f}% {mo.get('max_dd_pct',0):>5.1f}% "
                  f"{calmar(mo):>7.1f}")
        print("=" * 92)
    else:
        print()
        print("  → ไม่ผ่าน (A) หรือ (B) — ลองปรับ accum-width / trend-filter / trailing")
    print()
    print("  ⚠️  IN-SAMPLE — ต้อง IS/OOS + walk-forward + demo ก่อนเชื่อ")
    print()


if __name__ == "__main__":
    main()
