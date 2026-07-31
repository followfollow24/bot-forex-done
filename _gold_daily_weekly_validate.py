#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Validate the Daily-entry/Weekly-trend Gold config found in
_gold_new_edge_search.py (PF 1.42-1.88 full-history, but only 63-94 trades
over 13.4y -- low enough that it could just be a few lucky trades). Checks:
  1) OOS split: pick ADX threshold on 1st half only, score on 2nd half.
  2) Multi-year block walk-forward (3-4yr blocks, since per-calendar-year
     would only have ~5-7 trades -- too few to read anything from).
  3) Per-trade R distribution (avg win/loss, biggest single trade's share of
     total profit) -- with only ~70-90 trades, one or two outliers can make
     or break the whole track record.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import DataLoader, prepare_data, BacktestEngine, compute_metrics
from forex_hybrid_strategy import HybridTrendPullback
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


def run(d, adx, risk=0.50):
    s = HybridTrendPullback()
    s.ADX_MIN = adx
    s.H1_BARS = 7
    s.TIMEFRAME_SECONDS = 86400
    s.sl_atr, s.tp_atr = 3.0, 999.0
    s.trail_atr_mult = s.trail_activation_atr = 999.0
    s.precompute(d)
    eng = BacktestEngine(d, cfg(risk, hold=20), s, spread_price=SPREAD,
                          commission_per_lot=COMM, symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START), eng.trades


def line(m, tr, label, yrs):
    if not m or m.get("trades", 0) < 5:
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
    print(f"Daily bars: {len(df_d)}  span={years:.1f}y")

    print("\n" + "=" * 90)
    print(" (1) OOS SPLIT -- pick ADX on 1st half, score on 2nd half")
    print("=" * 90)
    mid = df_d["timestamp"].iloc[len(df_d)//2]
    tr_df = df_d[df_d["timestamp"] <= mid].reset_index(drop=True)
    te_df = df_d[df_d["timestamp"] > mid].reset_index(drop=True)
    d_tr, d_te = prepare_data(tr_df), prepare_data(te_df)
    y_tr = (tr_df["timestamp"].iloc[-1] - tr_df["timestamp"].iloc[0]).days / 365.25
    y_te = (te_df["timestamp"].iloc[-1] - te_df["timestamp"].iloc[0]).days / 365.25

    best_adx, best_pf = None, -1
    for adx in [15, 18, 20, 22, 25]:
        m, tr = run(d_tr, adx)
        line(m, tr, f"TRAIN adx{adx}", y_tr)
        if m and m.get("trades", 0) >= 20 and m["profit_factor"] > best_pf:
            best_pf, best_adx = m["profit_factor"], adx
    print(f"\n  -> picked adx{best_adx} on train (PF={best_pf:.2f})")
    m, tr = run(d_te, best_adx)
    line(m, tr, f"TEST adx{best_adx} (OOS)", y_te)

    print("\n" + "=" * 90)
    print(" (2) MULTI-YEAR BLOCK WALK-FORWARD (adx20, adx22) -- ~3.3yr blocks")
    print("=" * 90)
    for adx in [18, 20, 22]:
        print(f"\n  adx{adx}")
        n_blocks = 4
        block_edges = pd.date_range(df_d["timestamp"].iloc[0], df_d["timestamp"].iloc[-1], periods=n_blocks + 1)
        for i in range(n_blocks):
            dfb = df_d[(df_d["timestamp"] >= block_edges[i]) & (df_d["timestamp"] < block_edges[i+1])].reset_index(drop=True)
            if len(dfb) < 200:
                continue
            d_b = prepare_data(dfb)
            if d_b is None:
                continue
            yb = (dfb["timestamp"].iloc[-1] - dfb["timestamp"].iloc[0]).days / 365.25
            m, tr = run(d_b, adx)
            label = f"{block_edges[i].strftime('%Y-%m')} to {block_edges[i+1].strftime('%Y-%m')}"
            line(m, tr, label, max(yb, 0.5))

    print("\n" + "=" * 90)
    print(" (3) PER-TRADE CONCENTRATION -- adx20, full history")
    print("=" * 90)
    d_full = prepare_data(df_d)
    m, tr = run(d_full, 20)
    pnls = sorted([t["net_pnl"] for t in tr], reverse=True)
    total_profit = sum(p for p in pnls if p > 0)
    total_loss = sum(p for p in pnls if p < 0)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    print(f"  n={len(tr)}  wins={len(wins)}  losses={len(losses)}")
    print(f"  avg win=${np.mean(wins):.1f}  avg loss=${np.mean(losses):.1f}" if wins and losses else "  n/a")
    if pnls:
        top3 = sum(pnls[:3])
        print(f"  top-3 winning trades = ${top3:.1f}  ({top3/total_profit*100:.1f}% of gross profit)" if total_profit else "")
        print(f"  biggest single trade = ${pnls[0]:.1f}")


if __name__ == "__main__":
    main()
