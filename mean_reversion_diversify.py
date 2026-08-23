#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 mean_reversion_diversify.py — ทดสอบ "signal คนละแบบ" เพื่อ diversification จริง
================================================================================

 คำถามหลัก: signal mean-reversion (fade z-score ในตลาด range) มี
   (A) edge ของตัวเอง (PF>1 หลังหักต้นทุน) ไหม?
   (B) correlation ต่ำกับ C_wider (trend-pullback) จริงไหม?

 ถ้า (A) ✅ และ (B) corr ต่ำ → การ "ผสม" ได้ diversification จริง (ไม่ใช่เจือจาง
 แบบ C+D ที่ corr≈1). ถ้าไม่ → mean-reversion ไม่ใช่คำตอบ, รายงานตามจริง.

 MeanReversion = COUNTER-trend (ตรงข้าม C_wider ทุกมิติ):
   - C_wider เทรด "ตาม" เทรนด์ตอน ADX สูง (trending), เข้า pullback
   - MR เทรด "สวน" ความเหวี่ยง: ราคา ห่างจาก mean มาก (|z|>=Z_ENTRY) → fade กลับ
     + กรองให้เทรดเฉพาะตอนตลาดไม่เทรนด์แรง (|roc| เล็ก)

 วิธีรัน:
   python3 mean_reversion_diversify.py \\
       --csv download/xauusd-m15-bid-2013-01-01-2026-06-10.csv
================================================================================
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from copy import deepcopy
from typing import List, Optional, Tuple

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

# C_wider exit params (baseline เพื่อเทียบ + leg ตอน combine)
CWIDER = dict(sl_atr=2.5, tp_atr=5.0, trail_mult=999.0, trail_act=999.0,
              partial_tp_atr=999.0, partial_tp_frac=0.0, breakeven=False)


# =============================================================================
# Mean-Reversion Strategy — counter-trend z-score fade
# =============================================================================
class MeanReversionStrategy:
    """
    Fade z-score extremes ในตลาด range.
    plug เข้า BacktestEngine ได้ (มี signal/precompute/MIN_BARS/exit attrs).
    """
    name = "MeanReversion (z-score fade, range-filtered)"

    # Entry params
    Z_ENTRY   = 2.0     # |zscore| >= นี้ → ราคา stretched พอจะ fade
    ROC_MAX   = 0.020   # ถ้า |roc(48)| > 2% = เทรนด์แรงเกิน → ไม่ fade (กันสวนเทรนด์ใหญ่)

    # Exit params (อ่านโดย BacktestEngine) — MR: TP เล็กกว่า, R:R ~1:1.2
    sl_atr = 2.0
    tp_atr = 2.5
    trail_atr_mult       = 999.0   # ไม่มี trail
    trail_activation_atr = 999.0

    MIN_BARS = 120   # พอสำหรับ zscore(20) + roc(48) + buffer

    def precompute(self, d: dict):
        # ไม่มีอะไรต้อง precompute เพิ่ม (zscore/roc มากับ data dict แล้ว)
        pass

    def signal(self, d: dict, i: int) -> Signal:
        if i < self.MIN_BARS:
            return Signal()

        z   = d["zscore"][i - 1]   # ใช้แท่งที่ปิดแล้ว (i-1) — no look-ahead
        roc = d["roc"][i - 1]
        if math.isnan(z) or math.isnan(roc):
            return Signal()

        # Range filter: ข้ามถ้าเทรนด์แรงเกินไป (อย่า fade รถไฟ)
        if abs(roc) > self.ROC_MAX:
            return Signal()

        c_cur  = d["c"][i]
        c_prev = d["c"][i - 1]
        o_cur  = d["o"][i]

        # BUY: ราคาต่ำกว่า mean มาก (oversold) + เริ่มเด้งขึ้น
        if z <= -self.Z_ENTRY:
            if c_cur > c_prev and c_cur > o_cur:
                return Signal("BUY", f"MR oversold z={z:.2f}")

        # SELL: ราคาสูงกว่า mean มาก (overbought) + เริ่มย่อลง
        elif z >= self.Z_ENTRY:
            if c_cur < c_prev and c_cur < o_cur:
                return Signal("SELL", f"MR overbought z={z:.2f}")

        return Signal()


# =============================================================================
# Engine helpers
# =============================================================================
def _apply_cwider(strat: FastHybridTrendPullback):
    strat.sl_atr               = CWIDER["sl_atr"]
    strat.tp_atr               = CWIDER["tp_atr"]
    strat.trail_atr_mult       = CWIDER["trail_mult"]
    strat.trail_activation_atr = CWIDER["trail_act"]


def _cfg_for(cfg: ForexConfig, risk_pct: float,
             partial=999.0, frac=0.0, breakeven=False) -> ForexConfig:
    c = deepcopy(cfg)
    c.risk_per_trade_pct   = risk_pct
    c.partial_tp_atr       = partial
    c.partial_tp_frac      = frac
    c.move_sl_to_breakeven = breakeven
    return c


def run_cwider(d, cfg, risk_pct=RISK_PCT):
    strat = FastHybridTrendPullback()
    strat.precompute(d)
    _apply_cwider(strat)
    eng = BacktestEngine(d=d, cfg=_cfg_for(cfg, risk_pct), strategy=strat,
                         spread_price=SPREAD, commission_per_lot=COMM, symbol="XAUUSD")
    eng.run(quiet=True)
    return eng.trades, eng.equity_curve


def run_mr(d, cfg, risk_pct=RISK_PCT):
    strat = MeanReversionStrategy()
    eng = BacktestEngine(d=d, cfg=_cfg_for(cfg, risk_pct), strategy=strat,
                         spread_price=SPREAD, commission_per_lot=COMM, symbol="XAUUSD")
    eng.run(quiet=True)
    return eng.trades, eng.equity_curve


def run_combined(d, cfg, risk_per_leg):
    """C_wider + MR พร้อมกัน, 50/50 averaged book."""
    tr_c, eq_c = run_cwider(d, cfg, risk_per_leg)
    tr_m, eq_m = run_mr(d, cfg, risk_per_leg)
    n = min(len(eq_c), len(eq_m))
    comb = [(eq_c[i] + eq_m[i]) / 2.0 for i in range(n)]
    trades = sorted([dict(t, _leg="C") for t in tr_c] +
                    [dict(t, _leg="M") for t in tr_m],
                    key=lambda t: t["entry_ts"])
    return trades, comb


# =============================================================================
# Correlation of monthly returns
# =============================================================================
def monthly_returns(equity_curve: list, ts: np.ndarray) -> pd.Series:
    raw = ts
    try:
        idx = pd.to_datetime(raw.astype("int64"), unit="ms", utc=True)
    except (ValueError, TypeError):
        idx = pd.to_datetime(raw, utc=True)
    n = min(len(equity_curve), len(idx))
    s = pd.Series(equity_curve[:n], index=idx[:n])
    monthly_last = s.resample("ME").last()
    return monthly_last.pct_change().dropna()


def calmar(m):
    fe = m.get("final_equity", START)
    ret = (fe - START) / START * 100
    dd  = m.get("max_dd_pct", 99) or 0.01
    return ret / dd


def _row(label, m):
    if not m or m.get("trades", 0) == 0:
        print(f"  {label:<26}  NO TRADES"); return
    fe = m.get("final_equity", START); ret = (fe - START) / START * 100
    print(f"  {label:<26}  PF={m.get('profit_factor',0):.3f}  "
          f"Win={m.get('win_rate',0):.1%}  Ret={ret:+.1f}%  "
          f"DD={m.get('max_dd_pct',0):.1f}%  Calmar={calmar(m):.1f}  "
          f"#T={m.get('trades',0):,}")


# =============================================================================
def main():
    global SPREAD, COMM
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--z-entry", type=float, default=None,
                    help="override MR z-score entry threshold")
    ap.add_argument("--spread-price", type=float, default=0.10)
    ap.add_argument("--commission-per-lot", type=float, default=3.50)
    args = ap.parse_args()

    SPREAD = args.spread_price
    COMM   = args.commission_per_lot
    if args.z_entry is not None:
        MeanReversionStrategy.Z_ENTRY = args.z_entry

    print()
    print("=" * 92)
    print(" MEAN-REVERSION DIVERSIFICATION TEST — different SIGNAL, not different exit")
    print("=" * 92)
    print(f"  CSV    : {args.csv}")
    print(f"  Cost   : spread={SPREAD}  comm=${COMM}/lot/side  risk={RISK_PCT}%/trade")
    print(f"  MR     : z-entry=±{MeanReversionStrategy.Z_ENTRY}  "
          f"roc-filter=±{MeanReversionStrategy.ROC_MAX:.1%}  "
          f"SL={MeanReversionStrategy.sl_atr}ATR TP={MeanReversionStrategy.tp_atr}ATR")
    print("=" * 92)

    loader = DataLoader(log_fn=print)
    cfg    = ForexConfig(); cfg.total_capital_usd = START
    df, src = loader.load(args.symbol, 99.0, cfg, csv_path=args.csv, allow_synthetic=True)
    d = prepare_data(df)
    if d is None:
        print("[ERROR] prepare_data failed"); sys.exit(1)
    ts = d["ts"]
    print(f"  Data   : {len(df):,} bars  {df['timestamp'].iloc[0]} → {df['timestamp'].iloc[-1]}")
    print()

    # ── Standalone runs ───────────────────────────────────────────────────────
    print("Running C_wider (trend) ...")
    tr_c, eq_c = run_cwider(d, cfg)
    m_c = compute_metrics(tr_c, eq_c, START)

    print("Running MeanReversion (fade) ...")
    tr_m, eq_m = run_mr(d, cfg)
    m_m = compute_metrics(tr_m, eq_m, START)

    print()
    print("=" * 92)
    print(" STANDALONE EDGE")
    print("=" * 92)
    _row("C_wider (trend-pullback)", m_c)
    _row("MeanReversion (z-fade)",   m_m)
    print()

    # ── (A) ตรวจ MR มี edge ไหม ───────────────────────────────────────────────
    mr_pf  = m_m.get("profit_factor", 0)
    mr_trades = m_m.get("trades", 0)
    print("-" * 92)
    if mr_trades < 50:
        print(f"  ⚠️  MR เทรดน้อยเกินไป ({mr_trades} ไม้) — สรุป edge ไม่ได้")
        edge_ok = False
    elif mr_pf > 1.0:
        print(f"  ✅ (A) MR มี edge: PF={mr_pf:.3f} > 1.0  ({mr_trades:,} ไม้)")
        edge_ok = True
    else:
        print(f"  ❌ (A) MR ไม่มี edge: PF={mr_pf:.3f} ≤ 1.0  ({mr_trades:,} ไม้)")
        edge_ok = False

    # ── (B) Correlation ของ monthly returns ──────────────────────────────────
    r_c = monthly_returns(eq_c, ts)
    r_m = monthly_returns(eq_m, ts)
    joined = pd.concat([r_c.rename("cwider"), r_m.rename("mr")], axis=1).dropna()
    corr = joined["cwider"].corr(joined["mr"]) if len(joined) > 3 else float("nan")

    print(f"  Correlation (monthly returns, n={len(joined)}): {corr:+.3f}")
    if not math.isnan(corr):
        if corr < 0.3:
            print(f"  ✅ (B) corr ต่ำ ({corr:+.2f}) — diversify ได้จริง (ต่างจาก C+D ที่ ~1.0)")
            corr_ok = True
        elif corr < 0.6:
            print(f"  ◐ (B) corr ปานกลาง ({corr:+.2f}) — diversify ได้บ้าง")
            corr_ok = True
        else:
            print(f"  ❌ (B) corr สูง ({corr:+.2f}) — ไม่ได้ diversify จริง")
            corr_ok = False
    else:
        corr_ok = False
    print("-" * 92)

    # ── ถ้าผ่านทั้งสอง → ทดสอบ combined frontier ──────────────────────────────
    if edge_ok and corr_ok:
        print()
        print("=" * 92)
        print(" COMBINED FRONTIER — C_wider + MeanReversion (เทียบ DD เท่ากัน)")
        print("=" * 92)
        print(f"     {'risk/leg':>9} | {'C_wider เดี่ยว':^26} | {'C_wider + MR':^26}")
        print(f"     {'':>9} | {'Ret':>9} {'DD':>6} {'Calmar':>7} | {'Ret':>9} {'DD':>6} {'Calmar':>7}")
        for rpl in (0.15, 0.20, 0.25, 0.30, 0.375, 0.45):
            tc, ec = run_cwider(d, cfg, rpl)
            mc = compute_metrics(tc, ec, START)
            tcomb, ecomb = run_combined(d, cfg, rpl)
            mco = compute_metrics(tcomb, ecomb, START)
            rc  = (mc.get("final_equity", START)  - START) / START * 100
            rco = (mco.get("final_equity", START) - START) / START * 100
            print(f"     {rpl:>8.3f}% | {rc:>+8.1f}% {mc.get('max_dd_pct',0):>5.1f}% "
                  f"{calmar(mc):>7.1f} | {rco:>+8.1f}% {mco.get('max_dd_pct',0):>5.1f}% "
                  f"{calmar(mco):>7.1f}")
        print("=" * 92)
    else:
        print()
        print("  → ไม่ผ่านเกณฑ์ (A) หรือ (B) — ยังไม่คุ้มที่จะ combine.")
        print("    mean-reversion บน gold M15 หลังหักต้นทุนจริง มัก edge หาย (คาดไว้แล้ว).")
        print("    ลองปรับ z-entry / timeframe / asset อื่นเป็นก้าวถัดไป.")

    print()
    print("  ⚠️  IN-SAMPLE — ต้อง walk-forward + demo forward-test ก่อนเชื่อ")
    print()


if __name__ == "__main__":
    main()
