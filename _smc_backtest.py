#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Honest first-pass validation of SMCLiquidityFVG against the same bar every
other strategy in this repo had to clear: real engine, real costs, full
history, plus per-year walk-forward. Reports the result as it comes out.

Gates (decided BEFORE seeing results, so they can't be moved afterwards):
  1. minimum 200 trades over full history -- else the sample is meaningless
  2. full-history PF > 1.0 with realistic costs
  3. PF > 1 in a majority of individual years (not one lucky year carrying it)
  4. correlation check vs the existing gold bot is reported for context
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import (DataLoader, prepare_data, BacktestEngine,
                             FastHybridTrendPullback, compute_metrics)
from smc_liquidity_strategy import SMCLiquidityFVG

GOLD_CSV = "download/xauusd-m15-bid-2013-01-01-2026-06-10.csv"
START = 10_000.0
RISK_PCT = 0.30
SPREAD, COMM = 0.10, 3.50
MIN_TRADES_GATE = 200


def gold_cfg():
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = RISK_PCT
    c.partial_tp_atr = 999.0
    c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False
    return c


def run(strat_cls, d, sl, tp, adx_min=None, spread=SPREAD):
    strat = strat_cls()
    if adx_min is not None and hasattr(strat, "ADX_MIN"):
        strat.ADX_MIN = adx_min
    strat.sl_atr, strat.tp_atr = sl, tp
    strat.trail_atr_mult, strat.trail_activation_atr = 999.0, 999.0
    strat.precompute(d)
    eng = BacktestEngine(d, gold_cfg(), strat, spread_price=spread,
                          commission_per_lot=COMM, symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START), eng.trades


def fmt(m, label):
    if m is None or m.get("trades", 0) == 0:
        return f"  {label:<26} NO TRADES"
    return (f"  {label:<26} trades={m['trades']:>5}  win%={m['win_rate']*100:>5.1f}  "
            f"PF={m['profit_factor']:>5.2f}  Sharpe={m['sharpe']:>5.2f}  "
            f"MaxDD%={m['max_dd_pct']:>5.1f}  TotRet%={m['total_return_pct']:>+8.1f}  "
            f"MaxLoseStreak={m['max_consec_losses']:>3}")


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    cfg0 = ForexConfig(); cfg0.total_capital_usd = START
    df, _ = loader.load("XAUUSD", 99.0, cfg0, csv_path=GOLD_CSV, allow_synthetic=True)
    print(f"[load] {len(df):,} bars  {df['timestamp'].iloc[0].date()} -> {df['timestamp'].iloc[-1].date()}\n")
    d = prepare_data(df)

    print("=" * 104)
    print(" STEP 1 -- SMC full history, several SL/TP combos (cheap costs first, to see if ANY edge exists)")
    print("=" * 104)
    best = None
    for sl, tp in [(3.0, 7.0), (2.0, 6.0), (1.5, 4.5), (2.0, 4.0), (3.0, 3.0)]:
        m, _ = run(SMCLiquidityFVG, d, sl, tp)
        print(fmt(m, f"SMC SL={sl} TP={tp}"))
        if m and m.get("trades", 0) >= MIN_TRADES_GATE:
            if best is None or m["profit_factor"] > best[0]["profit_factor"]:
                best = (m, sl, tp)

    print()
    if best is None:
        print(f"  GATE 1 FAILED: no SL/TP combo reached {MIN_TRADES_GATE} trades.")
        print("  STOPPING -- not going to tune this into significance on a tiny sample.")
        return
    m_best, sl_b, tp_b = best
    print(f"  Best by PF (>= {MIN_TRADES_GATE} trades): SL={sl_b} TP={tp_b}  PF={m_best['profit_factor']:.2f}")

    if m_best["profit_factor"] <= 1.0:
        print(f"\n  GATE 2 FAILED: best PF={m_best['profit_factor']:.2f} <= 1.0 even on cheap costs.")
        print("  STOPPING -- no edge to salvage. Reporting honestly rather than hunting for a lucky window.")
        return

    print("\n" + "=" * 104)
    print(f" STEP 2 -- realistic costs at SL={sl_b} TP={tp_b}")
    print("=" * 104)
    for sp in [0.10, 0.25, 0.50, 2.00]:
        m, _ = run(SMCLiquidityFVG, d, sl_b, tp_b, spread=sp)
        print(fmt(m, f"spread={sp}"))

    print("\n" + "=" * 104)
    print(f" STEP 3 -- per-year walk-forward (params frozen at SL={sl_b} TP={tp_b}, no re-fit)")
    print("=" * 104)
    years = sorted(df["timestamp"].dt.year.unique())
    pf_ok = 0; pf_total = 0
    for y in years:
        dfy = df[df["timestamp"].dt.year == y].reset_index(drop=True)
        if len(dfy) < 2000:
            continue
        dy = prepare_data(dfy)
        m, _ = run(SMCLiquidityFVG, dy, sl_b, tp_b, spread=0.25)
        if m and m.get("trades", 0) > 0:
            pf_total += 1
            if m["profit_factor"] > 1.0:
                pf_ok += 1
        print(fmt(m, f"{y}"))
    print(f"\n  Years with PF>1: {pf_ok}/{pf_total}")

    print("\n" + "=" * 104)
    print(" STEP 4 -- correlation vs existing gold bot (adx20tp7 SL3/TP7)")
    print("=" * 104)
    _, tr_smc = run(SMCLiquidityFVG, d, sl_b, tp_b, spread=0.25)
    _, tr_old = run(FastHybridTrendPullback, d, 3.0, 7.0, adx_min=20, spread=0.25)

    def monthly(trades):
        if not trades:
            return pd.Series(dtype=float)
        rows = [(pd.Timestamp(t["exit_ts"]), t["net_pnl"]) for t in trades if t.get("exit_ts")]
        if not rows:
            return pd.Series(dtype=float)
        s = pd.DataFrame(rows, columns=["ts", "pnl"]).set_index("ts")["pnl"]
        return s.resample("ME").sum()

    ms, mo = monthly(tr_smc), monthly(tr_old)
    both = pd.concat([ms, mo], axis=1, keys=["smc", "old"]).dropna()
    if len(both) >= 12:
        corr = both["smc"].corr(both["old"])
        print(f"  monthly-PnL correlation over {len(both)} months: {corr:+.3f}")
        print("  (near 0 = genuinely diversifying; near +1 = same edge repackaged)")
    else:
        print(f"  not enough overlapping months to judge ({len(both)})")

    print("\n" + "=" * 104)
    print(" VERDICT")
    print("=" * 104)
    m_real, _ = run(SMCLiquidityFVG, d, sl_b, tp_b, spread=0.25)
    gate1 = m_real["trades"] >= MIN_TRADES_GATE
    gate2 = m_real["profit_factor"] > 1.0
    gate3 = pf_total > 0 and pf_ok / pf_total > 0.5
    print(f"  Gate 1 (>={MIN_TRADES_GATE} trades):   {'PASS' if gate1 else 'FAIL'}  ({m_real['trades']})")
    print(f"  Gate 2 (PF>1 realistic):   {'PASS' if gate2 else 'FAIL'}  ({m_real['profit_factor']:.2f})")
    print(f"  Gate 3 (majority yrs PF>1):{'PASS' if gate3 else 'FAIL'}  ({pf_ok}/{pf_total})")
    print(f"\n  -> {'CANDIDATE (proceed to walk-forward OOS)' if (gate1 and gate2 and gate3) else 'REJECTED -- do not deploy'}")


if __name__ == "__main__":
    main()
