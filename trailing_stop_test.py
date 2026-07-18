#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
trailing_stop_test.py -- Test replacing the fixed TP=7xATR with a
Chandelier-style trailing stop, on the adx20tp7 config. Uses the REAL
engine's already-built trailing mechanism (BacktestEngine._update_trail),
just never exercised in any live config so far (all configs set
trail_atr_mult=999 to disable it).

Mechanics (structurally chosen, not tuned to results):
  SL = 3.0xATR (unchanged from adx20tp7)
  TP = 999xATR (effectively disabled -- let the trailing stop do the exit)
  Trail activation = 3.0xATR (once price has moved 3xATR in favor, same
                     distance as the initial SL -- natural "give the trade
                     room to prove itself" threshold)
  Trail distance = 2.0xATR behind the peak/trough once active (tighter
                     than the initial 3xATR SL, since price has already
                     shown direction -- lets winners run while locking in
                     more profit as the trend extends)

Compares against the baseline (fixed TP=7xATR) on the real engine, full
history + train/test + WF-A yearly, same discipline as every other test.
"""
from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forex_config import ForexConfig
from backtest_forex import (DataLoader, prepare_data, BacktestEngine,
                             FastHybridTrendPullback, compute_metrics)

GOLD_CSV = "download/xauusd-m15-bid-2013-01-01-2026-06-10.csv"
START = 10_000.0
RISK_PCT = 0.30
SPREAD, COMM = 0.10, 3.50
SPLIT = ("2013-01-01", "2020-01-01")

TRAIL_ACTIVATION = 3.0
TRAIL_MULT = 2.0


def gold_cfg():
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = RISK_PCT
    c.partial_tp_atr = 999.0
    c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False
    return c


def run(df_full, sl, tp, adx, trail_mult, trail_act, date_from=None, date_to=None):
    df = df_full
    if date_from:
        df = df[df["timestamp"] >= pd.Timestamp(date_from)]
    if date_to:
        df = df[df["timestamp"] < pd.Timestamp(date_to)]
    df = df.reset_index(drop=True)
    if len(df) < 1000:
        return None
    d = prepare_data(df)
    strat = FastHybridTrendPullback()
    strat.ADX_MIN = adx
    strat.precompute(d)
    strat.sl_atr, strat.tp_atr = sl, tp
    strat.trail_atr_mult, strat.trail_activation_atr = trail_mult, trail_act
    eng = BacktestEngine(d, gold_cfg(), strat, spread_price=SPREAD,
                          commission_per_lot=COMM, symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START)


def fmt(m, label):
    if m is None or m.get("trades", 0) == 0:
        return f"  {label:<10} NO TRADES"
    return (f"  {label:<10} trades={m['trades']:>5}  win%={m['win_rate']*100:>5.1f}  "
            f"PF={m['profit_factor']:>5.2f}  Sharpe={m['sharpe']:>5.2f}  "
            f"MaxDD%={m['max_dd_pct']:>5.1f}  TotRet%={m['total_return_pct']:>+7.1f}  "
            f"AvgWin$={m['avg_win_usd']:>7.2f}  AvgLoss$={m['avg_loss_usd']:>7.2f}  "
            f"MaxLoseStreak={m['max_consec_losses']:>3}")


def main():
    print("=" * 100)
    print(" TRAILING STOP TEST -- adx20tp7, fixed TP7 vs Chandelier trailing")
    print(f" Trail: activation={TRAIL_ACTIVATION}xATR, distance={TRAIL_MULT}xATR behind peak")
    print("=" * 100)

    loader = DataLoader(log_fn=lambda *a, **k: None)
    cfg0 = ForexConfig(); cfg0.total_capital_usd = START
    df_full, _ = loader.load("XAUUSD", 99.0, cfg0, csv_path=GOLD_CSV, allow_synthetic=True)
    print(f"[load] {len(df_full):,} bars\n")

    tr_from, tr_to = SPLIT
    sl, adx = 3.0, 20

    print("--- BASELINE (fixed TP=7xATR) ---")
    for label, date_from, date_to in [("FULL", None, None), ("TRAIN", None, tr_to), ("TEST", tr_to, None)]:
        m = run(df_full, sl, 7.0, adx, 999.0, 999.0, date_from, date_to)
        print(fmt(m, label))

    print("\n--- TRAILING STOP (TP effectively off, Chandelier exit) ---")
    for label, date_from, date_to in [("FULL", None, None), ("TRAIN", None, tr_to), ("TEST", tr_to, None)]:
        m = run(df_full, sl, 999.0, adx, TRAIL_MULT, TRAIL_ACTIVATION, date_from, date_to)
        print(fmt(m, label))

    print("\n--- WF-A YEARLY -- baseline vs trailing ---")
    years = range(df_full["timestamp"].min().year, df_full["timestamp"].max().year + 1)
    pf_gt1_base = pf_gt1_trail = n_years = 0
    for y in years:
        m_base = run(df_full, sl, 7.0, adx, 999.0, 999.0, f"{y}-01-01", f"{y+1}-01-01")
        m_trail = run(df_full, sl, 999.0, adx, TRAIL_MULT, TRAIL_ACTIVATION, f"{y}-01-01", f"{y+1}-01-01")
        if m_base is None or m_base.get("trades", 0) == 0:
            continue
        n_years += 1
        if m_base["profit_factor"] > 1.0:
            pf_gt1_base += 1
        if m_trail and m_trail.get("profit_factor", 0) > 1.0:
            pf_gt1_trail += 1
        pf_b = f"{m_base['profit_factor']:.2f}"
        pf_t = f"{m_trail['profit_factor']:.2f}" if m_trail and m_trail.get('trades',0) else "n/a"
        print(f"  {y}: BASELINE PF={pf_b} DD={m_base['max_dd_pct']:.1f}%  |  "
              f"TRAILING PF={pf_t} DD={m_trail['max_dd_pct']:.1f}%" if m_trail and m_trail.get('trades',0)
              else f"  {y}: BASELINE PF={pf_b}  |  TRAILING NO TRADES")
    print(f"\n  PF>1 years: BASELINE {pf_gt1_base}/{n_years}  |  TRAILING {pf_gt1_trail}/{n_years}")
    print("=" * 100)


if __name__ == "__main__":
    main()
