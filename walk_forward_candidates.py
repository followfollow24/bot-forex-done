#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 walk_forward_candidates.py — เทียบ candidate exit variants ผ่าน 27 windows
================================================================================

 แก้ปัญหา performance จากเวอร์ชันก่อน:
   ❌ เก่า: wrapper ลวกๆ ไม่มี `if __name__=='__main__'` guard → บน macOS (spawn)
            Pool worker re-import โมดูล → รัน main() ซ้ำ → fork-bomb + โหลด CSV
            ซ้ำเป็นร้อยรอบ ("Date range" spam) → ช้าแบบทวีคูณ
   ✅ ใหม่:
     1. โหลด CSV "ครั้งเดียว" ที่ top-level → prepare_data → d (shared)
     2. precompute H1-trend/EMA20 "ครั้งเดียว" (ไม่ขึ้นกับ exit params) → reuse
        ทุก variant (do_precompute=False)
     3. SERIAL — ไม่ใช้ multiprocessing เลย → ไม่มีทาง fork-bomb, ดู progress ง่าย
     4. slice trades ที่รันแล้วเข้า window — ไม่ re-run/re-read ต่อ window
     5. print progress ทุก variant (i/N, #trades, เวลา)

 วิธีรัน:
   python3 walk_forward_candidates.py \\
       --csv download/xauusd-m15-bid-2013-01-01-2026-06-10.csv
================================================================================
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import time

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forex_config import ForexConfig
from backtest_forex import (DataLoader, prepare_data, BacktestEngine,
                             FastHybridTrendPullback, compute_metrics)
from walk_forward_regime import (build_windows, gold_return_pct, regime_tag,
                                  window_metrics)

SPREAD   = 0.10
COMM     = 3.50
START    = 10_000.0
RISK_PCT = 0.30

# ── Candidates — (label, sl, tp, trail_mult, trail_act, adx_min) ──────────────
#   adx_min=22 คือ baseline; 18/20 คือ variants ใหม่ที่ทดสอบ ADX threshold ต่ำลง
#   Variants ที่มี adx_min เดียวกันจะ share precomputed H1-trend array (re-precompute
#   เฉพาะเมื่อ adx_min เปลี่ยน → ไม่กระทบ performance มาก)
CANDIDATES = [
    # label,              sl,   tp,   trail_mult, trail_act,  adx_min
    ("C_wider",          2.5,  5.0,  999.0, 999.0,  22),   # baseline: SL2.5/TP5.0, ADX22
    ("Mix_A",            2.5,  7.0,  999.0, 999.0,  22),   # baseline: SL2.5/TP7.0, ADX22
    ("TP7",              3.0,  7.0,  999.0, 999.0,  22),   # baseline: SL3.0/TP7.0, ADX22
    ("ADX20_TP7",        3.0,  7.0,  999.0, 999.0,  20),   # NEW: TP7 config, ADX20
]
WINDOW_MONTHS = 6


def _cfg():
    c = ForexConfig()
    c.total_capital_usd    = START
    c.risk_per_trade_pct   = RISK_PCT
    c.partial_tp_atr       = 999.0    # ไม่มี partial-TP ทุก variant
    c.partial_tp_frac      = 0.0
    c.move_sl_to_breakeven = False
    return c


def run_variant(d, shared_strat, sl, tp, trail_mult, trail_act):
    """รัน 1 variant บน d ที่โหลด+precompute แล้ว — คืน trades + overall metrics."""
    shared_strat.sl_atr               = sl
    shared_strat.tp_atr               = tp
    shared_strat.trail_atr_mult       = trail_mult
    shared_strat.trail_activation_atr = trail_act

    eng = BacktestEngine(d, _cfg(), shared_strat,
                         spread_price=SPREAD, commission_per_lot=COMM,
                         symbol="XAUUSD")
    # do_precompute=False → reuse H1-trend/EMA20 ที่ precompute ไว้แล้ว
    eng.run(quiet=True, do_precompute=False)
    overall = compute_metrics(eng.trades, eng.equity_curve, START)
    avg_bars = (sum(t.get("bars_held", 0) for t in eng.trades) / len(eng.trades)
                if eng.trades else 0)
    return eng.trades, overall, avg_bars


def windows_pf_gt1(trades, win_info):
    """นับ windows ที่ PF>1 (รวม + เฉพาะ DOWN/SIDEWAYS) แบบ fixed-notional."""
    # normalize pnl ต่อ trade กลับไป $START notional (ไม่ให้ compounding บิด window)
    eq_before = START
    for t in trades:
        t["_norm_pnl"] = (t["net_pnl"] * (START / eq_before)) if eq_before > 0 else t["net_pnl"]
        t["_entry_dt"] = pd.Timestamp(t["entry_ts"])
        eq_before = t["equity_after"]

    pf_all = pf_ds = n_all = n_ds = 0
    for wi in win_info:
        wt = [t for t in trades if wi["start"] <= t["_entry_dt"] < wi["end"]]
        m = window_metrics(wt, START)
        if m["trades"] == 0:
            continue
        n_all += 1
        is_ds = wi["regime"] in ("DOWN", "SIDEWAYS")
        if is_ds:
            n_ds += 1
        pf = m["pf"]
        if pf is not None and (math.isinf(pf) or pf > 1.0):
            pf_all += 1
            if is_ds:
                pf_ds += 1
    return pf_all, n_all, pf_ds, n_ds


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--spread-price", type=float, default=0.10)
    ap.add_argument("--commission-per-lot", type=float, default=3.50)
    ap.add_argument("--date-from", default=None, help="กรอง bars ตั้งแต่วันนี้ (YYYY-MM-DD)")
    ap.add_argument("--date-to",   default=None, help="กรอง bars ถึงวันนี้ (YYYY-MM-DD, exclusive)")
    args = ap.parse_args()

    global SPREAD, COMM
    SPREAD = args.spread_price
    COMM   = args.commission_per_lot

    t_start = time.time()
    period_label = ""
    if args.date_from or args.date_to:
        period_label = f"  [{args.date_from or ''}–{args.date_to or ''}]"
    print()
    print("=" * 96)
    print(f" WALK-FORWARD CANDIDATES — เทียบ exit variants ผ่าน 27 windows (serial, load-once){period_label}")
    print("=" * 96)

    # ── โหลด CSV ครั้งเดียว ───────────────────────────────────────────────────
    print("  [load] อ่าน CSV ครั้งเดียว ...", flush=True)
    loader = DataLoader(log_fn=lambda *a, **k: None)
    cfg0 = ForexConfig(); cfg0.total_capital_usd = START
    df, _ = loader.load(args.symbol, 99.0, cfg0, csv_path=args.csv, allow_synthetic=True)

    # ── ตัด date range (ถ้าระบุ) ─────────────────────────────────────────────
    if args.date_from:
        df = df[df["timestamp"] >= pd.Timestamp(args.date_from)].reset_index(drop=True)
    if args.date_to:
        df = df[df["timestamp"] < pd.Timestamp(args.date_to)].reset_index(drop=True)

    print(f"  [load] {len(df):,} bars  {df['timestamp'].iloc[0]} → {df['timestamp'].iloc[-1]}  "
          f"({time.time()-t_start:.1f}s)", flush=True)

    d = prepare_data(df)
    if d is None:
        print("[ERROR] prepare_data failed"); sys.exit(1)

    # ── windows + regime ครั้งเดียว ───────────────────────────────────────────
    windows = build_windows(df["timestamp"].iloc[0], df["timestamp"].iloc[-1], WINDOW_MONTHS)
    win_info = []
    for (ws, we) in windows:
        ret = gold_return_pct(df, ws, we)
        win_info.append(dict(start=ws, end=we, gold_ret=ret, regime=regime_tag(ret)))
    print(f"  [windows] {len(windows)} windows × {WINDOW_MONTHS} เดือน", flush=True)

    # ── precompute H1-trend/EMA20 — re-precompute เมื่อ adx_min เปลี่ยน ────────
    strat = FastHybridTrendPullback()
    current_adx_min = None

    # ── รันทีละ candidate (serial) ────────────────────────────────────────────
    rows = []
    N = len(CANDIDATES)
    for i, (label, sl, tp, tm, ta, adx_min) in enumerate(CANDIDATES, 1):
        if adx_min != current_adx_min:
            print(f"  [precompute] H1-trend + EMA20 สำหรับ ADX_MIN={adx_min} ...",
                  end="", flush=True)
            tp0 = time.time()
            strat.ADX_MIN = adx_min
            strat.precompute(d)
            current_adx_min = adx_min
            print(f" เสร็จ ({time.time()-tp0:.1f}s)", flush=True)
        tv = time.time()
        print(f"  [{i}/{N}] running {label:<20} SL{sl} TP{tp} ADX{adx_min} "
              f"{'trail '+str(tm)+'ATR@+'+str(ta) if tm < 900 else 'no-trail':<16} ...",
              end="", flush=True)
        trades, ov, avg_bars = run_variant(d, strat, sl, tp, tm, ta)
        pf_all, n_all, pf_ds, n_ds = windows_pf_gt1(trades, win_info)
        rows.append(dict(label=label, ov=ov, pf_all=pf_all, n_all=n_all,
                         pf_ds=pf_ds, n_ds=n_ds, avg_bars=avg_bars,
                         trades_total=len(trades), adx_min=adx_min))
        print(f" {len(trades):,} trades, PF>1 in {pf_all}/{n_all} windows  "
              f"avg_bars={avg_bars:.1f}  ({time.time()-tv:.1f}s)", flush=True)
    print()

    # ── ตารางสรุป ─────────────────────────────────────────────────────────────
    print("=" * 112)
    print(" RESULTS — full-history + window robustness")
    print("=" * 112)
    print(f"  {'Variant':<22} | {'ADX':>4} {'trades':>7} | {'PF':>6} {'Ret':>8} "
          f"{'MaxDD':>6} {'Calmar':>7} | {'win PF>1':>9} {'D+S PF>1':>9} | {'avg_bars':>9}")
    print("  " + "-" * 112)
    bench = rows[0]
    prev_adx = None
    for r in rows:
        ov  = r["ov"]
        ret = (ov.get("final_equity", START) - START) / START * 100
        dd  = ov.get("max_dd_pct", 0) or 0.01
        cal = ret / dd
        if r is bench:
            tag = " ← benchmark"
        else:
            better = (ov.get("profit_factor", 0) > bench["ov"].get("profit_factor", 0)
                      and r["pf_all"] / max(r["n_all"], 1) >= bench["pf_all"] / max(bench["n_all"], 1))
            tag = " ✅ ดีกว่า" if better else ""
        # separator เมื่อ adx_min กลุ่มเปลี่ยน
        if prev_adx is not None and r["adx_min"] != prev_adx:
            print("  " + "-" * 112)
        prev_adx = r["adx_min"]
        print(f"  {r['label']:<22} | {r['adx_min']:>4} {r['trades_total']:>7,} | "
              f"{ov.get('profit_factor', 0):>6.3f} {ret:>+7.0f}% "
              f"{ov.get('max_dd_pct', 0):>5.1f}% {cal:>7.1f} "
              f"| {r['pf_all']:>4}/{r['n_all']:<4} {r['pf_ds']:>4}/{r['n_ds']:<4}"
              f"| {r['avg_bars']:>7.1f}{tag}")

    # ── Spread Sensitivity Analysis ───────────────────────────────────────────
    SPREAD_SCENARIOS = {
        "raw_0.00": 0.00,
        "raw_0.10": 0.10,
        "demo_0.28": 0.28,
        "demo_0.52": 0.52,
    }
    # (label, sl, tp, trail_mult, trail_act, adx_min)
    SENS_VARIANTS = [
        ("C_wider",      2.5, 5.0, 999.0, 999.0, 22),
        ("Mix_A",        2.5, 7.0, 999.0, 999.0, 22),
        ("TP7",          3.0, 7.0, 999.0, 999.0, 22),
        ("ADX20_TP7",    3.0, 7.0, 999.0, 999.0, 20),
    ]

    print()
    print("=" * 96)
    print(" SPREAD SENSITIVITY — PF | Calmar  ต่อ spread scenario")
    print("=" * 96)

    # header
    col_w  = 16
    lbl_w  = 16
    hdr = f"  {'Variant':<{lbl_w}}"
    for sc in SPREAD_SCENARIOS:
        hdr += f" | {sc:^{col_w}}"
    print(hdr)
    sub = f"  {'':>{lbl_w}}"
    for _ in SPREAD_SCENARIOS:
        sub += f" | {'PF':>6}  {'Calmar':>7} "
    print(sub)
    print("  " + "-" * (lbl_w + (col_w + 3) * len(SPREAD_SCENARIOS)))

    sens_adx_min = None
    for (vlabel, sl, tp, tm, ta, adx_min) in SENS_VARIANTS:
        if adx_min != sens_adx_min:
            strat.ADX_MIN = adx_min
            strat.precompute(d)
            sens_adx_min = adx_min
        row_str = f"  {vlabel:<16}"
        for sc_name, sp in SPREAD_SCENARIOS.items():
            strat.sl_atr               = sl
            strat.tp_atr               = tp
            strat.trail_atr_mult       = tm
            strat.trail_activation_atr = ta
            eng = BacktestEngine(d, _cfg(), strat,
                                 spread_price=sp, commission_per_lot=COMM,
                                 symbol="XAUUSD")
            eng.run(quiet=True, do_precompute=False)
            ov = compute_metrics(eng.trades, eng.equity_curve, START)
            pf  = ov.get("profit_factor", 0) or 0
            ret = (ov.get("final_equity", START) - START) / START * 100
            dd  = ov.get("max_dd_pct", 0) or 0.01
            cal = ret / dd
            row_str += f" | {pf:>6.3f}  {cal:>7.1f} "
        print(row_str)

    print()
    print(f"  เสร็จทั้งหมดใน {time.time()-t_start:.1f}s")
    print("  ⚠️  IN-SAMPLE ทั้งชุด — ของที่ชนะตรงนี้ต้องผ่าน IS/OOS + demo forward-test ก่อนใช้จริง")
    print("=" * 96)
    print()


if __name__ == "__main__":
    main()
