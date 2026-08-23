#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 halt_resume_test.py — "Stop-when-losing, Resume-after-cooldown" Overlay Test
================================================================================
 Variant: C_wider ONLY (SL=2.5xATR, TP=5.0xATR, no partial, no trail)
 Data:    Full 13.4-yr real XAUUSD M15 CSV
 Costs:   spread-price=0.10 (real Exness, 10 pips), commission=$3.50/lot/side

 Entry/exit logic UNCHANGED (FastHybridTrendPullback, verified bit-identical
 to original). No optimization toward any target.

 [1] BASELINE — run C_wider continuously, no halt overlay.

 [2] HALT/RESUME OVERLAY (engine-level toggle in BacktestEngine):
     - Track consecutive calendar DAYS with net realized P&L < 0
       ("consecutive losing days").
     - When consecutive losing days >= N: block NEW entries for the next C
       calendar days (cooldown), then resume tracking. Open positions still
       close per their normal SL/TP/trailing rules during cooldown.
     - Sweep N in {2, 3} x C in {3, 5, 10} days -> 6 configs.

 [3] One table: baseline + 6 configs:
       Total return %, PF, MaxDD %, expectancy $/trade, % weeks green,
       LONGEST consecutive losing-WEEK streak, # days halted, # trades taken

 [4] HONEST RULES:
     (a) did the overlay SHORTEN the longest losing-week streak vs baseline?
     (b) what did it do to TOTAL RETURN vs baseline?
     - reconciliation kept on every run
     - actual numbers, no annualizing/inflating, no tuning to a target
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
from cost_sensitivity_weekly import normalize_trades, weekly_returns, weekly_stats


CSV         = "download/xauusd-m15-bid-2013-01-01-2026-06-10.csv"
SYMBOL      = "XAUUSD"
START_CASH  = 10_000.0
RISK_PCT    = 0.30
SPREAD      = 0.10
COMMISSION  = 3.50
VARIANT     = "C_wider"

N_SWEEP = [2, 3]
C_SWEEP = [3, 5, 10]


# =============================================================================
# Worker — รัน C_wider เดียว ONCE over full history, with optional halt overlay
# =============================================================================
def _run_one(task: dict) -> dict:
    vparams = VARIANTS[task["variant"]]

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
                             halt_after_losing_days=task.get("halt_n"),
                             halt_cooldown_days=task.get("halt_c"))
    engine.run(quiet=True)

    overall = compute_metrics(engine.trades, engine.equity_curve, cfg.total_capital_usd)

    return dict(
        label=task["label"],
        halt_n=task.get("halt_n"),
        halt_c=task.get("halt_c"),
        trades=engine.trades,
        reconcile_diff=engine.reconcile_diff,
        n_trades=len(engine.trades),
        halted_days_count=engine.halted_days_count,
        overall=overall,
    )


def main():
    csv_path = CSV
    vparams  = VARIANTS[VARIANT]

    loader = DataLoader(log_fn=print)
    df, data_source = loader.load(SYMBOL, 99.0, ForexConfig(),
                                   csv_path=csv_path, allow_synthetic=True)
    n = len(df)

    print()
    print("=" * 100)
    print(" HALT/RESUME OVERLAY TEST — 'stop-when-losing, resume-after-cooldown'")
    print("=" * 100)
    print(f"  DATA SOURCE : {data_source}")
    print(f"  Symbol      : {SYMBOL}")
    print(f"  Bars        : {n:,}")
    print(f"  Date range  : {df['timestamp'].iloc[0]}  ->  {df['timestamp'].iloc[-1]}")
    print(f"  Variant     : {VARIANT}: {vparams['desc']}")
    print(f"   SL={vparams['sl_atr']}xATR  TP={vparams['tp_atr']}xATR  "
          f"trail_mult={vparams['trail_atr_mult']} trail_act={vparams['trail_activation_atr']}")
    print(f"  Cost model  : spread-price={SPREAD} (real Exness, 10 pips), "
          f"commission=${COMMISSION}/lot/side")
    print(f"  Cash / Risk : ${START_CASH:,.0f}  /  {RISK_PCT}% per trade")
    print(f"  Overlay sweep: N (consec. losing DAYS to trigger) in {N_SWEEP}")
    print(f"                 C (cooldown calendar days) in {C_SWEEP}")
    print("=" * 100)

    # ── Build task list ──
    tasks = [dict(variant=VARIANT, spread_price=SPREAD, csv=csv_path,
                   symbol=SYMBOL, start_cash=START_CASH, risk=RISK_PCT,
                   commission_per_lot=COMMISSION,
                   halt_n=None, halt_c=None, label="baseline")]

    for N in N_SWEEP:
        for C in C_SWEEP:
            tasks.append(dict(variant=VARIANT, spread_price=SPREAD, csv=csv_path,
                               symbol=SYMBOL, start_cash=START_CASH, risk=RISK_PCT,
                               commission_per_lot=COMMISSION,
                               halt_n=N, halt_c=C, label=f"N={N},C={C}"))

    print(f"\n  Running {len(tasks)} backtest combinations "
          f"with {min(8, len(tasks))} workers ...\n")

    with Pool(processes=min(8, len(tasks))) as pool:
        results = pool.map(_run_one, tasks)

    results_by = {r["label"]: r for r in results}

    # =========================================================================
    # [3] REPORT TABLE
    # =========================================================================
    print("=" * 100)
    print(" RESULTS TABLE — baseline vs (N, C) overlay configs")
    print("=" * 100)
    print(f"   {'Config':<14} {'Return%':>9} {'PF':>7} {'MaxDD%':>7} "
          f"{'Expect$':>9} {'%WkGreen':>9} {'LongLoseWkStrk':>15} "
          f"{'#DaysHalted':>12} {'#Trades':>8} {'Reconcile':>10}")

    rows = {}
    order = ["baseline"] + [f"N={N},C={C}" for N in N_SWEEP for C in C_SWEEP]
    for label in order:
        r = results_by[label]
        ov = r["overall"]
        trades = normalize_trades(r["trades"], START_CASH)
        wr = weekly_returns(trades, START_CASH)
        ws = weekly_stats(wr)

        row = dict(
            return_pct=ov["total_return_pct"],
            pf=ov["profit_factor"],
            max_dd_pct=ov["max_dd_pct"],
            expectancy=ov["expectancy_usd"],
            pct_green=ws["pct_green"],
            longest_losing_wk_streak=ws["longest_losing_streak"],
            days_halted=r["halted_days_count"],
            n_trades=r["n_trades"],
            reconcile=r["reconcile_diff"],
        )
        rows[label] = row

        print(f"   {label:<14} {row['return_pct']:8.1f}% {row['pf']:7.3f} "
              f"{row['max_dd_pct']:6.1f}% {row['expectancy']:+8.2f} "
              f"{row['pct_green']:8.1f}% {row['longest_losing_wk_streak']:15d} "
              f"{row['days_halted']:12,d} {row['n_trades']:8,d} "
              f"{row['reconcile']:10.6f}")

    # =========================================================================
    # [4] HONEST SUMMARY
    # =========================================================================
    print()
    print("=" * 100)
    print(" HONEST SUMMARY")
    print("=" * 100)
    base = rows["baseline"]
    print(f"  Baseline (C_wider, spread=0.10, commission=${COMMISSION}):")
    print(f"    Total return = {base['return_pct']:+.1f}%   PF = {base['pf']:.3f}   "
          f"MaxDD = {base['max_dd_pct']:.1f}%   Expectancy = ${base['expectancy']:+.2f}/trade")
    print(f"    Longest consecutive losing-WEEK streak = {base['longest_losing_wk_streak']} weeks")
    print()

    for N in N_SWEEP:
        for C in C_SWEEP:
            label = f"N={N},C={C}"
            row = rows[label]

            d_streak = row["longest_losing_wk_streak"] - base["longest_losing_wk_streak"]
            if d_streak < 0:
                streak_ans = f"YES — shortened from {base['longest_losing_wk_streak']} to " \
                              f"{row['longest_losing_wk_streak']} weeks ({d_streak:+d})"
            elif d_streak > 0:
                streak_ans = f"NO — actually LONGER: {base['longest_losing_wk_streak']} -> " \
                              f"{row['longest_losing_wk_streak']} weeks ({d_streak:+d})"
            else:
                streak_ans = f"NO CHANGE — stayed at {base['longest_losing_wk_streak']} weeks"

            d_ret = row["return_pct"] - base["return_pct"]
            if d_ret > 0:
                ret_ans = f"RAISED total return by {d_ret:+.1f} pts " \
                          f"({base['return_pct']:+.1f}% -> {row['return_pct']:+.1f}%)"
            elif d_ret < 0:
                ret_ans = f"LOWERED total return by {d_ret:.1f} pts " \
                          f"({base['return_pct']:+.1f}% -> {row['return_pct']:+.1f}%)"
            else:
                ret_ans = f"NO CHANGE to total return ({base['return_pct']:+.1f}%)"

            print(f"  --- N={N} (trigger after {N} consecutive losing days), "
                  f"C={C} (cooldown days) ---")
            print(f"    # calendar days halted by overlay : {row['days_halted']:,}")
            print(f"    # trades taken (baseline {base['n_trades']:,}) : {row['n_trades']:,} "
                  f"({row['n_trades'] - base['n_trades']:+,d})")
            print(f"    MaxDD: {base['max_dd_pct']:.1f}% -> {row['max_dd_pct']:.1f}%   "
                  f"PF: {base['pf']:.3f} -> {row['pf']:.3f}")
            print(f"    (a) Longest losing-week streak shortened? {streak_ans}")
            print(f"    (b) Effect on total return: {ret_ans}")
            print()

    print("-" * 100)
    print("  Notes:")
    print("  - All figures are ACTUAL values over the full 13.4-year dataset")
    print("    history — NOT annualized, NOT inflated.")
    print("  - The overlay only gates Phase-3 signal generation (no new entries")
    print("    on halted days, UTC calendar days). Any position already open")
    print("    continues to manage/close per its normal SL/TP rules.")
    print("  - 'Longest consecutive losing-WEEK streak' is a WEEKLY metric (ISO")
    print("    calendar weeks, fixed $10k notional, not compounded) — distinct")
    print("    from the DAILY consecutive-loss counter the overlay itself tracks.")
    print("  - No parameters were tuned toward any target.")
    print("=" * 100)


if __name__ == "__main__":
    main()
