#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Validate Gold Daily Donchian breakout (sl2.0, trail 3.0x@1.0) -- the
candidate from _gold_breakout_search.py whose per-year breakdown looked
genuinely distributed (11/14 years PF>1, wins spread across the whole
2013-2026 span, not concentrated in the 2024-26 bull run like the
trend-pullback dead end). Checks:
  1) OOS split: pick SL/trail params on 1st half only, score on 2nd half.
  2) Per-trade concentration: how much of the profit comes from a few
     outlier trades (low trade count = fragile to survivorship in a few
     lucky trades).
  3) Risk-scaling: DD looked very low (3.6% at risk=0.50%) -- check what
     CAGR looks like at higher risk before concluding this is attractive.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import DataLoader, prepare_data, BacktestEngine, compute_metrics
from _gold_breakout_search import DonchianBreakout
from _idea_search import resample
from _all_paths import to_monthly, perf, START

GOLD_M15 = "download/xauusd-m15-bid-2013-01-01-2026-06-10.csv"
SPREAD, COMM = 2.85, 3.50


def cfg(risk=0.50, hold=20):
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = risk
    c.partial_tp_atr = 999.0
    c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False
    c.max_hold_bars = hold
    return c


def run(d, sl_atr, trail_mult, trail_act, risk=0.50, hold=20):
    s = DonchianBreakout()
    s.sl_atr, s.tp_atr = sl_atr, 999.0
    s.trail_atr_mult = trail_mult
    s.trail_activation_atr = trail_act
    eng = BacktestEngine(d, cfg(risk, hold), s, spread_price=SPREAD,
                          commission_per_lot=COMM, symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START), eng.trades


def line(m, tr, label, yrs):
    if not m or m.get("trades", 0) < 10:
        print(f"    {label:<30} n={m.get('trades',0) if m else 0:>5}  too few")
        return
    p = perf(to_monthly(tr))
    sh = p["sharpe"] if p else float("nan")
    tot = m["total_return_pct"]
    cg = -100.0 if tot <= -100 else ((1+tot/100)**(1/yrs)-1)*100
    print(f"    {label:<30} n={m['trades']:>5}  PF={m['profit_factor']:>5.2f}  "
          f"Sharpe={sh:>5.2f}  CAGR={cg:>+7.2f}%  DD={m['max_dd_pct']:>5.1f}%")


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START
    dfg, _ = loader.load("XAUUSD", 99.0, c0, csv_path=GOLD_M15, allow_synthetic=True)
    df_d = resample(dfg, "1D")
    years = (df_d["timestamp"].iloc[-1] - df_d["timestamp"].iloc[0]).days / 365.25

    print("=" * 90)
    print(" (1) OOS SPLIT -- pick SL/trail on 1st half, score on 2nd half")
    print("=" * 90)
    mid = df_d["timestamp"].iloc[len(df_d)//2]
    tr_df = df_d[df_d["timestamp"] <= mid].reset_index(drop=True)
    te_df = df_d[df_d["timestamp"] > mid].reset_index(drop=True)
    d_tr, d_te = prepare_data(tr_df), prepare_data(te_df)
    y_tr = (tr_df["timestamp"].iloc[-1] - tr_df["timestamp"].iloc[0]).days / 365.25
    y_te = (te_df["timestamp"].iloc[-1] - te_df["timestamp"].iloc[0]).days / 365.25

    grid = [(1.5, 2.5, 0.75), (2.0, 3.0, 1.0), (2.5, 3.0, 1.0), (2.0, 4.0, 1.5), (2.5, 4.0, 1.0)]
    best_cfg, best_pf = None, -1
    for sl, tm, ta in grid:
        m, tr = run(d_tr, sl, tm, ta)
        line(m, tr, f"TRAIN sl{sl} trail{tm}@{ta}", y_tr)
        if m and m.get("trades", 0) >= 30 and m["profit_factor"] > best_pf:
            best_pf, best_cfg = m["profit_factor"], (sl, tm, ta)
    print(f"\n  -> picked {best_cfg} on train (PF={best_pf:.2f})")
    sl, tm, ta = best_cfg
    m, tr = run(d_te, sl, tm, ta)
    line(m, tr, f"TEST sl{sl} trail{tm}@{ta} (OOS)", y_te)

    print("\n" + "=" * 90)
    print(" (2) PER-TRADE CONCENTRATION -- full history, sl2.0 trail3.0@1.0")
    print("=" * 90)
    d_full = prepare_data(df_d)
    m, tr = run(d_full, 2.0, 3.0, 1.0)
    pnls = sorted([t["net_pnl"] for t in tr], reverse=True)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    total_profit = sum(wins)
    print(f"  n={len(tr)}  wins={len(wins)}  losses={len(losses)}  win%={len(wins)/len(tr)*100:.1f}%")
    print(f"  avg win=${np.mean(wins):.1f}  avg loss=${np.mean(losses):.1f}")
    for k in [3, 5, 10]:
        topk = sum(pnls[:k])
        print(f"  top-{k} winning trades = ${topk:.1f}  ({topk/total_profit*100:.1f}% of gross profit)")

    print("\n" + "=" * 90)
    print(" (3) RISK SCALING -- full history, sl2.0 trail3.0@1.0")
    print("=" * 90)
    for risk in [0.50, 1.0, 1.5, 2.0, 3.0]:
        m, tr = run(d_full, 2.0, 3.0, 1.0, risk=risk)
        line(m, tr, f"risk={risk:.2f}%", years)


if __name__ == "__main__":
    main()
