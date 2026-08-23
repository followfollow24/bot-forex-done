#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
"The test always shows 400%/yr" -- where that number comes from, and what
happens to it under an honest cost assumption.

Two things inflate a backtest headline:
  1. cheap spread  ($0.10 vs the $2.85 actually paid)
  2. high risk/trade, which compounds spectacularly in a backtest because the
     backtest cannot blow up -- it has no margin call, no min-lot floor, and
     no broker rejecting the size

This runs the same SL3/TP7 config across risk levels under both cost
assumptions, so the gap between the headline and reality is explicit.
"""
from __future__ import annotations
import os, sys
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import (DataLoader, prepare_data, BacktestEngine,
                             FastHybridTrendPullback, compute_metrics)

GOLD_CSV = "download/xauusd-m15-bid-2013-01-01-2026-06-10.csv"
START = 10_000.0
COMM = 3.50


def cfg(risk):
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = risk
    c.partial_tp_atr = 999.0
    c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False
    c.max_hold_bars = 64
    return c


def run(d, adx, spread, risk):
    s = FastHybridTrendPullback()
    s.ADX_MIN = adx
    s.sl_atr, s.tp_atr = 3.0, 7.0
    s.trail_atr_mult = s.trail_activation_atr = 999.0
    s.precompute(d)
    eng = BacktestEngine(d, cfg(risk), s, spread_price=spread,
                          commission_per_lot=COMM, symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START)


def cagr_of(tot, years):
    if tot <= -100:
        return -100.0
    return ((1 + tot / 100.0) ** (1.0 / years) - 1) * 100


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START
    df, _ = loader.load("XAUUSD", 99.0, c0, csv_path=GOLD_CSV, allow_synthetic=True)
    d = prepare_data(df)
    yrs = (df["timestamp"].iloc[-1] - df["timestamp"].iloc[0]).days / 365.25
    print(f"adx20tp7  SL3/TP7  full history {yrs:.1f} years\n")

    for cost_label, sp in [("CHEAP  $0.10 (repo assumption)", 0.10),
                           ("REAL   $2.85 (measured live)",   2.85)]:
        print("=" * 100)
        print(f" {cost_label}")
        print("=" * 100)
        print(f"  {'risk/trade':<12}{'trades':>8}{'PF':>7}{'TotRet%':>14}{'CAGR%/yr':>12}{'MaxDD%':>10}")
        for risk in [0.30, 1.00, 2.00, 3.00, 5.00]:
            m = run(d, 20, sp, risk)
            if not m or m.get("trades", 0) == 0:
                print(f"  {risk:<12.2f}  NO TRADES"); continue
            tot = m["total_return_pct"]
            print(f"  {risk:<12.2f}{m['trades']:>8}{m['profit_factor']:>7.2f}"
                  f"{tot:>+14.1f}{cagr_of(tot, yrs):>+12.2f}{m['max_dd_pct']:>10.1f}")
        print()


if __name__ == "__main__":
    main()
