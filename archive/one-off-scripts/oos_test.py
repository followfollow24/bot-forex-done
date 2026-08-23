#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 oos_test.py — In-Sample / Out-of-Sample Exit-Variant Honesty Test
================================================================================
 จุดประสงค์: ทดสอบว่า exit-rule ไหน (ถ้ามี) ให้ PF > 1.0 ทั้งช่วง
 in-sample (IS) และ out-of-sample (OOS) — ไม่ปรับพารามิเตอร์ไปหาผลลัพธ์
 ที่อยากได้ (no curve-fitting). OOS ถูกประเมิน "ครั้งเดียว" ต่อ variant.

 Entry/trend logic เหมือนกันทุก variant (ใช้ HybridTrendPullback.signal()
 ตัวเดียวกัน — ต่างกันแค่ exit rules):

   A_current : partial TP @1.5ATR (ปิด 50%) + move SL→breakeven + ATR trail
               (ค่า default ของบอทตอนนี้)  SL=1.5ATR  TP=3.0ATR
   B_fixed   : ไม่มี partial, ไม่มี trail.  SL=1.5ATR  TP=3.0ATR  (1:2 สะอาด)
   C_wider   : ไม่มี partial, ไม่มี trail.  SL=2.5ATR  TP=5.0ATR  (1:2 กว้างขึ้น
               กัน wick)
   D_letrun  : ไม่มี partial.  SL=2.0ATR  TP=6.0ATR (1:3)
               ATR trail (2.5×ATR) แต่เริ่มทำงานหลังกำไร ≥ 2.0×ATR เท่านั้น
               (ปล่อยให้วิ่งไกลก่อนเริ่ม lock-in)

 วิธีรัน:
   python3 oos_test.py --csv download/xauusd-m15-bid-2021-06-01-2026-06-10.csv \\
       --start-cash 10000 --spread-price 0.10 --commission-per-lot 3.50

 Verdict rules:
   - PF > 1.0 ทั้ง IS และ OOS  → "POSSIBLE EDGE"
   - PF > 1.0 บน IS แต่ <= 1.0 บน OOS → "OVERFIT" (ไม่ใช่ edge)
   - ไม่มี variant ไหนผ่าน OOS → "NO EDGE FOUND"
================================================================================
"""
from __future__ import annotations

import argparse
import os
import sys
from multiprocessing import Pool

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forex_config import ForexConfig
from forex_hybrid_strategy import HybridTrendPullback
from backtest_forex import DataLoader, prepare_data, BacktestEngine, compute_metrics


# =============================================================================
# Variant definitions — entry/trend IDENTICAL, only exit rules differ
# =============================================================================
VARIANTS = {
    "A_current": dict(
        desc="partial TP@1.5ATR(50%) + breakeven + ATR trail (baseline ปัจจุบัน)",
        sl_atr=1.5, tp_atr=3.0,
        partial_tp_atr=1.5, partial_tp_frac=0.5, move_sl_to_breakeven=True,
        trail_atr_mult=2.5, trail_activation_atr=0.5,
    ),
    "B_fixed": dict(
        desc="ไม่มี partial, ไม่มี trail — SL=1.5ATR TP=3.0ATR (1:2 สะอาด)",
        sl_atr=1.5, tp_atr=3.0,
        partial_tp_atr=999.0, partial_tp_frac=0.0, move_sl_to_breakeven=False,
        trail_atr_mult=999.0, trail_activation_atr=999.0,
    ),
    "C_wider": dict(
        desc="ไม่มี partial, ไม่มี trail — SL=2.5ATR TP=5.0ATR (1:2 กว้างขึ้น)",
        sl_atr=2.5, tp_atr=5.0,
        partial_tp_atr=999.0, partial_tp_frac=0.0, move_sl_to_breakeven=False,
        trail_atr_mult=999.0, trail_activation_atr=999.0,
    ),
    "D_letrun": dict(
        desc="ไม่มี partial — SL=2.0ATR TP=6.0ATR (1:3), trail เริ่มหลัง +2ATR",
        sl_atr=2.0, tp_atr=6.0,
        partial_tp_atr=999.0, partial_tp_frac=0.0, move_sl_to_breakeven=False,
        trail_atr_mult=2.5, trail_activation_atr=2.0,
    ),
}


# =============================================================================
# Worker — runs ONE (variant, period) combination in its own process
# =============================================================================
def _run_one(task: dict) -> dict:
    variant_name = task["variant"]
    vparams      = VARIANTS[variant_name]
    period       = task["period"]          # "IS" or "OOS"

    loader = DataLoader(log_fn=lambda *a, **k: None)
    df, _ = loader.load(task["symbol"], 5.0, ForexConfig(),
                         csv_path=task["csv"], allow_synthetic=True)
    d = prepare_data(df)
    n = len(d["c"])

    cfg = ForexConfig()
    cfg.total_capital_usd     = task["start_cash"]
    cfg.risk_per_trade_pct    = task["risk"]
    cfg.symbols               = [task["symbol"]]
    cfg.partial_tp_atr        = vparams["partial_tp_atr"]
    cfg.partial_tp_frac       = vparams["partial_tp_frac"]
    cfg.move_sl_to_breakeven  = vparams["move_sl_to_breakeven"]

    strategy = HybridTrendPullback()
    strategy.sl_atr               = vparams["sl_atr"]
    strategy.tp_atr               = vparams["tp_atr"]
    strategy.trail_atr_mult       = vparams["trail_atr_mult"]
    strategy.trail_activation_atr = vparams["trail_activation_atr"]
    strategy.precompute(d)

    warm = strategy.MIN_BARS

    split_ts  = pd.Timestamp(task["split_date"]).to_datetime64()
    split_idx = int(np.searchsorted(df["timestamp"].values, split_ts))

    if period == "IS":
        start_i, end_i = warm, split_idx
    else:
        start_i, end_i = max(split_idx, warm), n - 1

    engine = BacktestEngine(d, cfg, strategy,
                             spread_price=task["spread_price"],
                             commission_per_lot=task["commission_per_lot"],
                             symbol=task["symbol"])
    engine.run(start_i=start_i, end_i=end_i, quiet=True)
    m = compute_metrics(engine.trades, engine.equity_curve, cfg.total_capital_usd)
    m["variant"]        = variant_name
    m["period"]         = period
    m["reconcile_diff"] = engine.reconcile_diff
    m["bar_start"]      = start_i
    m["bar_end"]        = end_i
    m["date_start"]     = str(df["timestamp"].iloc[min(start_i, n - 1)])
    m["date_end"]       = str(df["timestamp"].iloc[min(max(end_i, start_i), n - 1)])

    # exit-reason breakdown
    reasons = {}
    for t in engine.trades:
        reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
    m["exit_breakdown"] = reasons

    return m


# =============================================================================
# Main
# =============================================================================
def main():
    ap = argparse.ArgumentParser(
        description="In-sample / Out-of-sample exit-variant honesty test")
    ap.add_argument("--csv", required=True, help="CSV path (M15 OHLCV)")
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--start-cash", type=float, default=10_000.0)
    ap.add_argument("--risk", type=float, default=0.30, help="Risk %% per trade")
    ap.add_argument("--spread-price", type=float, default=0.10)
    ap.add_argument("--commission-per-lot", type=float, default=3.50)
    ap.add_argument("--split-date", default=None,
                    help="YYYY-MM-DD — IS=before, OOS=on/after. "
                         "Default: midpoint of loaded data.")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    # ── Load once in main process to determine date range / split ──
    loader = DataLoader(log_fn=print)
    df, data_source = loader.load(args.symbol, 5.0, ForexConfig(),
                                   csv_path=args.csv, allow_synthetic=True)
    n = len(df)

    if args.split_date:
        split_date = args.split_date
    else:
        split_date = str(df["timestamp"].iloc[n // 2].date())

    print()
    print("=" * 78)
    print(" IN-SAMPLE / OUT-OF-SAMPLE EXIT VARIANT TEST")
    print("=" * 78)
    print(f"  DATA SOURCE : {data_source}")
    print(f"  Symbol      : {args.symbol}")
    print(f"  Bars        : {n:,}")
    print(f"  Date range  : {df['timestamp'].iloc[0]}  ->  {df['timestamp'].iloc[-1]}")
    print(f"  Split date  : {split_date}   (IS = before, OOS = on/after, "
          f"OOS evaluated ONCE)")
    print(f"  Cost model  : spread={args.spread_price}  "
          f"commission=${args.commission_per_lot}/lot/side  "
          f"risk={args.risk}%/trade  cash=${args.start_cash:,.0f}")
    print("=" * 78)

    tasks = []
    for variant_name in VARIANTS:
        for period in ("IS", "OOS"):
            tasks.append(dict(
                variant=variant_name, period=period,
                csv=args.csv, symbol=args.symbol,
                start_cash=args.start_cash, risk=args.risk,
                spread_price=args.spread_price,
                commission_per_lot=args.commission_per_lot,
                split_date=split_date,
            ))

    print(f"\n  Running {len(tasks)} (variant x period) combinations "
          f"with {args.workers} workers ...\n")

    with Pool(processes=args.workers) as pool:
        results = pool.map(_run_one, tasks)

    # organize: results_by[variant][period] = metrics dict
    results_by = {v: {} for v in VARIANTS}
    for r in results:
        results_by[r["variant"]][r["period"]] = r

    # ── Per-variant table ──
    for variant_name, vparams in VARIANTS.items():
        is_m  = results_by[variant_name]["IS"]
        oos_m = results_by[variant_name]["OOS"]

        print("-" * 78)
        print(f" Variant {variant_name}: {vparams['desc']}")
        print(f"   SL={vparams['sl_atr']}xATR  TP={vparams['tp_atr']}xATR  "
              f"partial_tp_atr={vparams['partial_tp_atr']} "
              f"frac={vparams['partial_tp_frac']} "
              f"breakeven={vparams['move_sl_to_breakeven']}  "
              f"trail_mult={vparams['trail_atr_mult']} "
              f"trail_act={vparams['trail_activation_atr']}")
        print("-" * 78)
        print(f"   {'':14} {'IS':>16} {'OOS':>16}")
        print(f"   {'Period':14} {is_m['date_start'][:10]+' ~ '+is_m['date_end'][:10]:>16} "
              f"{oos_m['date_start'][:10]+' ~ '+oos_m['date_end'][:10]:>16}")

        def row(label, key, fmt):
            a = is_m.get(key, 0)
            b = oos_m.get(key, 0)
            print(f"   {label:14} {fmt(a):>16} {fmt(b):>16}")

        if is_m.get("trades", 0) == 0 or oos_m.get("trades", 0) == 0:
            print("   [WARN] ไม่มี trade ในบางช่วง — ข้าม metrics")
        else:
            row("Trades",      "trades",          lambda x: f"{x:,d}")
            row("Win Rate",    "win_rate",        lambda x: f"{x:.1%}")
            row("Profit Fctr", "profit_factor",   lambda x: f"{x:.3f}")
            row("Expectancy",  "expectancy_usd",  lambda x: f"${x:+.2f}")
            row("Avg Win",     "avg_win_usd",     lambda x: f"${x:.2f}")
            row("Avg Loss",    "avg_loss_usd",    lambda x: f"${x:.2f}")
            row("Total Ret%",  "total_return_pct", lambda x: f"{x:+.1f}%")
            row("Max DD",      "max_dd_pct",      lambda x: f"{x:.1f}%")
            row("Reconcile",   "reconcile_diff",  lambda x: f"{x:.6f}")

        print("   Exit breakdown (IS):  " +
              ", ".join(f"{k}={v}" for k, v in
                        sorted(is_m.get("exit_breakdown", {}).items(), key=lambda x: -x[1])))
        print("   Exit breakdown (OOS): " +
              ", ".join(f"{k}={v}" for k, v in
                        sorted(oos_m.get("exit_breakdown", {}).items(), key=lambda x: -x[1])))
        print()

    # ── Verdict ──
    print("=" * 78)
    print(" HONEST VERDICT")
    print("=" * 78)
    any_edge = False
    for variant_name in VARIANTS:
        is_m  = results_by[variant_name]["IS"]
        oos_m = results_by[variant_name]["OOS"]
        pf_is  = is_m.get("profit_factor", 0.0)
        pf_oos = oos_m.get("profit_factor", 0.0)

        if pf_is > 1.0 and pf_oos > 1.0:
            verdict = "✅ POSSIBLE EDGE — PF>1.0 on BOTH IS and OOS"
            any_edge = True
        elif pf_is > 1.0 and pf_oos <= 1.0:
            verdict = "⚠️  OVERFIT — PF>1.0 on IS but <=1.0 on OOS (NOT an edge)"
        elif pf_is <= 1.0 and pf_oos > 1.0:
            verdict = "🟡 PF>1.0 on OOS only (IS<=1.0) — likely noise, not edge"
        else:
            verdict = "❌ NO EDGE — PF<=1.0 on both IS and OOS"

        print(f"  {variant_name:12} IS PF={pf_is:6.3f}   OOS PF={pf_oos:6.3f}   {verdict}")

    print("-" * 78)
    if any_edge:
        print("  >>> At least one variant shows PF>1.0 on BOTH IS and OOS.")
        print("      This is a CANDIDATE edge — still needs walk-forward / live-forward")
        print("      validation before risking real capital.")
    else:
        print("  >>> NO variant achieved PF>1.0 on out-of-sample data.")
        print("      VERDICT: NO EDGE FOUND on real XAUUSD with these exit rules.")
        print("      Changing exits alone (partial/trail/SL-TP width) does not fix")
        print("      the underlying entry signal's lack of edge on this dataset.")
    print("=" * 78)


if __name__ == "__main__":
    main()
