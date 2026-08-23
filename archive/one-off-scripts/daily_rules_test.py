#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 daily_rules_test.py — Weekly Stats, Cost-Sensitivity, and Day-Level Rule
                        Comparison Matrix for C_wider / D_letrun
================================================================================
 Variants ที่ผ่าน regime walk-forward แล้ว (จาก walk_forward_regime.py):
   - C_wider  (SL=2.5xATR, TP=5.0xATR, no partial, no trail)
   - D_letrun (SL=2.0xATR, TP=6.0xATR, no partial, trail +2.5xATR after +2ATR)

 Entry/trend logic เหมือนเดิมทุกประการ — ใช้ FastHybridTrendPullback
 (verified bit-identical กับ original). ไม่ปรับพารามิเตอร์ strategy ใดๆ
 ไม่ tune ไปหา return target — รายงานความจริงตามที่เป็น

 [1] WEEKLY STATS (spread=0.10 = real Exness, baseline ไม่มี day-rule)
     - fixed $10k notional (norm_pnl, ไม่ compound) ต่อ trade
     - %weeks green, avg/median/best/worst weekly%, longest losing-week streak
     - histogram ของ weekly returns

 [2] COST-SENSITIVITY SWEEP (spread = 0.10 / 0.30 / 0.50, commission=$3.50/lot/side
     คงที่ = ค่า Exness จริง) — PF, Return%, MaxDD%, Expectancy, weekly stats

 [3] DAY-LEVEL RULE TOGGLES (engine-level, ใน BacktestEngine):
     (a) DAILY LOSS LIMIT 2%: ถ้า realized P&L ของวัน <= -2% ของ equity ณ
         ต้นวัน -> ไม่เปิดไม้ใหม่ที่เหลือของวันนั้น (ไม้ที่เปิดอยู่ปิดตามปกติ)
     (b) REACTIVE DAILY STOP: ติดตาม realized P&L สะสมของวัน (reset ทุกวัน
         ตาม UTC). ถ้าวันนั้นเคยเป็นบวกมาก่อน แล้วมีไม้ปิดขาดทุน (net loss)
         -> ไม่เปิดไม้ใหม่ที่เหลือของวันนั้น (ไม้ที่เปิดอยู่ปิดตามปกติ)

 [4] COMPARISON MATRIX (ที่ spread=0.10, commission=$3.50/lot/side = real Exness)
     ต่อ variant: baseline / +daily_loss_limit / +reactive_daily_stop / +both
     คอลัมน์: Return%, PF, MaxDD%, Expectancy$, %WeeksGreen, WorstWeek%,
              AvgTrades/Day, Reconcile

 [5] HONEST RULES
     - reconciliation ทุกรัน
     - ตัวเลขจริง ไม่ annualize ไม่ขยายผล
     - บอกตรงๆ ว่าแต่ละ day-rule ทำให้ Return RAISED หรือ LOWERED เทียบ baseline
       และทำให้ equity curve "smooth" ขึ้นหรือไม่ (MaxDD ต่ำลง / red weeks น้อยลง)
     - ไม่ tune ไปหา target ใดๆ
================================================================================
"""
from __future__ import annotations

import os
import sys
from multiprocessing import Pool

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forex_config import ForexConfig
from backtest_forex import (DataLoader, prepare_data, BacktestEngine,
                             FastHybridTrendPullback, compute_metrics)
from walk_forward_regime import VARIANTS
from cost_sensitivity_weekly import (normalize_trades, weekly_returns,
                                      weekly_stats, histogram_lines, HIST_BINS)


CSV          = "download/xauusd-m15-bid-2013-01-01-2026-06-10.csv"
SYMBOL       = "XAUUSD"
START_CASH   = 10_000.0
RISK_PCT     = 0.30
COMMISSION   = 3.50          # real Exness, fixed
SPREADS_SWEEP = [0.10, 0.30, 0.50]
VARIANT_NAMES = ["C_wider", "D_letrun"]
DLL_PCT      = 2.0           # daily loss limit %


# =============================================================================
# Worker — รัน (variant, spread, day-rule config) เดียว ONCE over full history
# =============================================================================
def _run_one(task: dict) -> dict:
    variant_name = task["variant"]
    vparams      = VARIANTS[variant_name]

    loader = DataLoader(log_fn=lambda *a, **k: None)
    df, _ = loader.load(task["symbol"], 99.0, ForexConfig(),
                         csv_path=task["csv"], allow_synthetic=True)
    d = prepare_data(df)

    cfg = ForexConfig()
    cfg.total_capital_usd    = task["start_cash"]
    cfg.risk_per_trade_pct   = task["risk"]
    cfg.symbols              = [task["symbol"]]
    cfg.partial_tp_atr       = vparams["partial_tp_atr"]
    cfg.partial_tp_frac      = vparams["partial_tp_frac"]
    cfg.move_sl_to_breakeven = vparams["move_sl_to_breakeven"]

    strategy = FastHybridTrendPullback()
    strategy.sl_atr               = vparams["sl_atr"]
    strategy.tp_atr               = vparams["tp_atr"]
    strategy.trail_atr_mult       = vparams["trail_atr_mult"]
    strategy.trail_activation_atr = vparams["trail_activation_atr"]

    engine = BacktestEngine(d, cfg, strategy,
                             spread_price=task["spread_price"],
                             commission_per_lot=task["commission_per_lot"],
                             symbol=task["symbol"],
                             daily_loss_limit_pct=task.get("daily_loss_limit_pct"),
                             reactive_daily_stop=task.get("reactive_daily_stop", False))
    engine.run(quiet=True)   # full history, ONE pass

    overall = compute_metrics(engine.trades, engine.equity_curve, cfg.total_capital_usd)

    return dict(
        variant=variant_name,
        spread=task["spread_price"],
        commission=task["commission_per_lot"],
        label=task["label"],
        trades=engine.trades,
        reconcile_diff=engine.reconcile_diff,
        n_trades=len(engine.trades),
        overall=overall,
    )


# =============================================================================
# Main
# =============================================================================
def main():
    csv_path = CSV

    # ── Load once for banner / date range / day-count ──
    loader = DataLoader(log_fn=print)
    df, data_source = loader.load(SYMBOL, 99.0, ForexConfig(),
                                   csv_path=csv_path, allow_synthetic=True)
    n = len(df)

    # number of unique calendar days actually IN the simulated range
    # (range starts at MIN_BARS warm-up, same constant for all variants)
    warm = FastHybridTrendPullback().MIN_BARS
    sim_days = df["timestamp"].iloc[warm:n - 1].dt.date.nunique()

    print()
    print("=" * 100)
    print(" DAILY-RULE TEST — WEEKLY STATS / COST-SENSITIVITY / DAY-LEVEL RULE COMPARISON")
    print("=" * 100)
    print(f"  DATA SOURCE   : {data_source}")
    print(f"  Symbol        : {SYMBOL}")
    print(f"  Bars          : {n:,}")
    print(f"  Date range    : {df['timestamp'].iloc[0]}  ->  {df['timestamp'].iloc[-1]}")
    print(f"  Sim. days     : {sim_days:,} unique calendar days "
          f"(after MIN_BARS={warm:,} warm-up)")
    print(f"  Variants      : {', '.join(VARIANT_NAMES)}")
    print(f"  Cash / Risk   : ${START_CASH:,.0f}  /  {RISK_PCT}% per trade")
    print(f"  Commission    : ${COMMISSION}/lot/side (FIXED — real Exness value)")
    print(f"  Cost-sens spreads : {SPREADS_SWEEP}  (price units; 0.10=10pips real)")
    print(f"  Comparison matrix : at spread=0.10 (real Exness), commission=${COMMISSION}")
    print(f"  Daily loss limit  : {DLL_PCT}% of day-start equity")
    print(f"  Reactive daily stop: block new entries rest-of-day after first net-loss")
    print(f"                       trade IF the day had been net-positive at some point")
    print("=" * 100)

    # ── Build task list ──────────────────────────────────────────────────────
    # Cost-sensitivity sweep: baseline (no day rules) at spreads 0.10/0.30/0.50
    tasks = []
    for v in VARIANT_NAMES:
        for sp in SPREADS_SWEEP:
            tasks.append(dict(variant=v, spread_price=sp, csv=csv_path,
                               symbol=SYMBOL, start_cash=START_CASH, risk=RISK_PCT,
                               commission_per_lot=COMMISSION,
                               daily_loss_limit_pct=None, reactive_daily_stop=False,
                               label="baseline"))

    # Comparison matrix extra configs at spread=0.10 (baseline already covered above)
    extra_configs = [
        ("dll_only",  dict(daily_loss_limit_pct=DLL_PCT, reactive_daily_stop=False)),
        ("rds_only",  dict(daily_loss_limit_pct=None,    reactive_daily_stop=True)),
        ("both",      dict(daily_loss_limit_pct=DLL_PCT, reactive_daily_stop=True)),
    ]
    for v in VARIANT_NAMES:
        for label, kw in extra_configs:
            tasks.append(dict(variant=v, spread_price=0.10, csv=csv_path,
                               symbol=SYMBOL, start_cash=START_CASH, risk=RISK_PCT,
                               commission_per_lot=COMMISSION,
                               label=label, **kw))

    print(f"\n  Running {len(tasks)} backtest combinations "
          f"with {min(8, len(tasks))} workers ...\n")

    with Pool(processes=min(8, len(tasks))) as pool:
        results = pool.map(_run_one, tasks)

    # results_by[variant][(spread, label)] = result
    results_by: dict = {}
    for r in results:
        results_by.setdefault(r["variant"], {})[(r["spread"], r["label"])] = r

    # =========================================================================
    # [1] WEEKLY STATS @ spread=0.10, baseline (real Exness, no day-rules)
    # =========================================================================
    print("=" * 100)
    print(" [1] WEEKLY STATS — spread=0.10 (real Exness), baseline (no day-rules)")
    print("     Fixed $10k notional per trade (norm_pnl, NOT compounded)")
    print("=" * 100)

    baseline_ws = {}   # variant -> weekly_stats dict (spread=0.10, baseline)
    for v in VARIANT_NAMES:
        vparams = VARIANTS[v]
        r = results_by[v][(0.10, "baseline")]
        ov = r["overall"]
        trades = normalize_trades(r["trades"], START_CASH)
        wr = weekly_returns(trades, START_CASH)
        ws = weekly_stats(wr)
        baseline_ws[v] = ws

        print("-" * 100)
        print(f" Variant {v}: {vparams['desc']}")
        print(f"   SL={vparams['sl_atr']}xATR  TP={vparams['tp_atr']}xATR  "
              f"trail_mult={vparams['trail_atr_mult']} "
              f"trail_act={vparams['trail_activation_atr']}")
        print("-" * 100)
        print(f"   Total trades : {r['n_trades']:,}   Reconcile diff: {r['reconcile_diff']:.6f}")
        print(f"   Overall      : PF={ov['profit_factor']:.3f}  "
              f"Return={ov['total_return_pct']:+.1f}%  MaxDD={ov['max_dd_pct']:.1f}%  "
              f"Expectancy=${ov['expectancy_usd']:+.2f}/trade")
        print(f"   Weeks-with-trades : {ws['n']}")
        print(f"   % Weeks profitable : {ws['pct_green']:.1f}%")
        print(f"   Avg weekly return  : {ws['avg']:+.3f}%")
        print(f"   Median weekly ret. : {ws['median']:+.3f}%")
        print(f"   Best week          : {ws['best']:+.3f}%")
        print(f"   Worst week         : {ws['worst']:+.3f}%")
        print(f"   Longest consecutive losing-week streak : {ws['longest_losing_streak']}")
        print(f"   Histogram of weekly returns (bins in %):")
        for line in histogram_lines(ws["rets"]):
            print(line)
        print()

    # =========================================================================
    # [2] COST-SENSITIVITY SWEEP — spreads 0.10 / 0.30 / 0.50, commission fixed
    # =========================================================================
    print("=" * 100)
    print(f" [2] COST-SENSITIVITY SWEEP — spreads {SPREADS_SWEEP}, "
          f"commission=${COMMISSION}/lot/side (FIXED)")
    print("=" * 100)

    breaks_at = {}
    for v in VARIANT_NAMES:
        vparams = VARIANTS[v]
        print("-" * 100)
        print(f" Variant {v}: {vparams['desc']}")
        print("-" * 100)
        print(f"   {'Spread':>7} {'PF':>7} {'Return%':>9} {'MaxDD%':>7} "
              f"{'Expect$':>9} {'%Green':>7} {'AvgWk%':>8} {'MedWk%':>8} "
              f"{'BestWk%':>8} {'WorstWk%':>9} {'LoseStrk':>8} {'#Wks':>5} "
              f"{'#Trades':>8} {'Reconcile':>10}")

        per_spread_ws = {}
        for sp in SPREADS_SWEEP:
            r = results_by[v][(sp, "baseline")]
            ov = r["overall"]
            trades = normalize_trades(r["trades"], START_CASH)
            wr = weekly_returns(trades, START_CASH)
            ws = weekly_stats(wr)
            per_spread_ws[sp] = ws

            if ov.get("trades", 0) == 0:
                print(f"   {sp:7.2f}   [no trades]")
                continue

            pf = ov["profit_factor"]
            print(f"   {sp:7.2f} {pf:7.3f} {ov['total_return_pct']:8.1f}% "
                  f"{ov['max_dd_pct']:6.1f}% {ov['expectancy_usd']:+8.2f} "
                  f"{ws['pct_green']:6.1f}% {ws['avg']:+7.2f}% {ws['median']:+7.2f}% "
                  f"{ws['best']:+7.2f}% {ws['worst']:+8.2f}% "
                  f"{ws['longest_losing_streak']:8d} {ws['n']:5d} "
                  f"{r['n_trades']:8,d} {r['reconcile_diff']:10.6f}")

            if v not in breaks_at and pf <= 1.0:
                breaks_at[v] = sp

        print()
        print(f"   Weekly-return histograms ({len(SPREADS_SWEEP)} spread levels, bins in %):")
        for sp in SPREADS_SWEEP:
            ws = per_spread_ws[sp]
            if ws.get("n", 0) == 0:
                print(f"     spread={sp:.2f}: [no weeks with trades]")
                continue
            print(f"     spread={sp:.2f}  ({ws['n']} weeks-with-trades):")
            for line in histogram_lines(ws["rets"]):
                print(line)
        print()

    # =========================================================================
    # [3]/[4] DAY-LEVEL RULE COMPARISON MATRIX — at spread=0.10, real Exness
    # =========================================================================
    print("=" * 100)
    print(" [3]/[4] DAY-LEVEL RULE COMPARISON MATRIX — spread=0.10 (real Exness), "
          f"commission=${COMMISSION}/lot/side")
    print("=" * 100)

    matrix_labels = [
        ("baseline", "baseline (no day rules)"),
        ("dll_only", f"+ daily loss limit ({DLL_PCT}% of day-start equity)"),
        ("rds_only", "+ reactive daily stop"),
        ("both",     "+ both"),
    ]

    matrix_results = {}   # variant -> label -> dict(metrics)
    for v in VARIANT_NAMES:
        vparams = VARIANTS[v]
        print("-" * 100)
        print(f" Variant {v}: {vparams['desc']}")
        print("-" * 100)
        print(f"   {'Config':<38} {'Return%':>9} {'PF':>7} {'MaxDD%':>7} "
              f"{'Expect$':>9} {'%WkGreen':>9} {'WorstWk%':>9} "
              f"{'Trades/Day':>11} {'#Trades':>8} {'Reconcile':>10}")

        matrix_results[v] = {}
        for label, desc in matrix_labels:
            r = results_by[v][(0.10, label)]
            ov = r["overall"]
            trades = normalize_trades(r["trades"], START_CASH)
            wr = weekly_returns(trades, START_CASH)
            ws = weekly_stats(wr)

            trades_per_day = r["n_trades"] / sim_days if sim_days > 0 else float("nan")

            row = dict(
                return_pct=ov["total_return_pct"],
                pf=ov["profit_factor"],
                max_dd_pct=ov["max_dd_pct"],
                expectancy=ov["expectancy_usd"],
                pct_green=ws["pct_green"],
                worst_week=ws["worst"],
                trades_per_day=trades_per_day,
                n_trades=r["n_trades"],
                reconcile=r["reconcile_diff"],
                ws=ws,
            )
            matrix_results[v][label] = row

            print(f"   {desc:<38} {row['return_pct']:8.1f}% {row['pf']:7.3f} "
                  f"{row['max_dd_pct']:6.1f}% {row['expectancy']:+8.2f} "
                  f"{row['pct_green']:8.1f}% {row['worst_week']:+8.2f}% "
                  f"{row['trades_per_day']:11.2f} {row['n_trades']:8,d} "
                  f"{row['reconcile']:10.6f}")
        print()

    # =========================================================================
    # [5] HONEST SUMMARY
    # =========================================================================
    print("=" * 100)
    print(" [5] HONEST SUMMARY")
    print("=" * 100)

    print("  Cost sensitivity (commission held fixed at "
          f"${COMMISSION}/lot/side):")
    for v in VARIANT_NAMES:
        if v in breaks_at:
            print(f"    {v:12} -> EDGE BREAKS (PF<=1.0) at spread={breaks_at[v]:.2f}")
        else:
            print(f"    {v:12} -> PF>1.0 held across all tested spreads {SPREADS_SWEEP}")

    print()
    print("  Day-level rules vs. baseline (spread=0.10, real Exness):")
    for v in VARIANT_NAMES:
        base = matrix_results[v]["baseline"]
        print(f"    --- {v} ---")
        for label, desc in matrix_labels[1:]:
            row = matrix_results[v][label]
            d_ret = row["return_pct"] - base["return_pct"]
            ret_dir = "RAISED" if d_ret > 0 else ("LOWERED" if d_ret < 0 else "UNCHANGED")

            d_dd = row["max_dd_pct"] - base["max_dd_pct"]
            dd_dir = "lower (better)" if d_dd < 0 else (
                "higher (worse)" if d_dd > 0 else "unchanged")

            d_green = row["pct_green"] - base["pct_green"]
            d_worst = row["worst_week"] - base["worst_week"]
            smoothed = (d_dd < 0) or (d_green > 0) or (d_worst > 0)
            smooth_str = "YES" if smoothed else "NO"

            print(f"      {desc}")
            print(f"        Total return: {base['return_pct']:+.1f}% -> "
                  f"{row['return_pct']:+.1f}%  ({ret_dir} by {abs(d_ret):.1f} pts)")
            print(f"        MaxDD:        {base['max_dd_pct']:.1f}% -> "
                  f"{row['max_dd_pct']:.1f}%  ({dd_dir}, delta {d_dd:+.1f} pts)")
            print(f"        %Weeks green: {base['pct_green']:.1f}% -> "
                  f"{row['pct_green']:.1f}%  (delta {d_green:+.1f} pts)")
            print(f"        Worst week:   {base['worst_week']:+.2f}% -> "
                  f"{row['worst_week']:+.2f}%  (delta {d_worst:+.2f} pts)")
            print(f"        Trades/day:   {base['trades_per_day']:.2f} -> "
                  f"{row['trades_per_day']:.2f}")
            print(f"        Equity curve smoothed (lower MaxDD and/or fewer "
                  f"red weeks)?  {smooth_str}")
            print()

    print("-" * 100)
    print("  Notes:")
    print("  - All weekly/return figures above are ACTUAL values over the full")
    print("    13.4-year dataset history — NOT annualized, NOT inflated.")
    print("  - Day-level rules ONLY gate Phase-3 signal generation (no new")
    print("    entries for the rest of the calendar day, UTC). Any position")
    print("    already open continues to manage/close per its normal SL/TP/")
    print("    trailing rules.")
    print("  - 'AvgTrades/Day' = total trades / "
          f"{sim_days:,} unique calendar days in the simulated range.")
    print("  - No parameters were tuned toward any target.")
    print("=" * 100)


if __name__ == "__main__":
    main()
