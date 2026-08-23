#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Direct answer to: "SL3/TP7 per the backtest -- what % per year?"

The answer is entirely a function of the cost assumption, so all three are
shown side by side with CAGR (not 13-year totals, which flatter the number):

  $0.10  -- the assumption baked into this repo's original backtests, and the
            basis on which these bots were deployed to a real account
  $0.90  -- price-proportional: ~$2 when gold is $4,000, scaled down for the
            years when gold was cheaper. The fair full-history number.
  $2.85  -- what adx20tp7 actually pays now, measured from its own fills log
"""
from __future__ import annotations
import os, sys
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import (DataLoader, prepare_data, BacktestEngine,
                             FastHybridTrendPullback, compute_metrics)
from gold_regime_filter_real_engine import RegimeFilteredHybrid

GOLD_CSV = "download/xauusd-m15-bid-2013-01-01-2026-06-10.csv"
START = 10_000.0
RISK = 0.30
COMM = 3.50


def cfg():
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = RISK
    c.partial_tp_atr = 999.0
    c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False
    c.max_hold_bars = 64
    return c


def run(cls, d, adx, spread):
    s = cls()
    s.ADX_MIN = adx
    s.sl_atr, s.tp_atr = 3.0, 7.0
    s.trail_atr_mult = s.trail_activation_atr = 999.0
    s.precompute(d)
    eng = BacktestEngine(d, cfg(), s, spread_price=spread,
                          commission_per_lot=COMM, symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START)


def cagr_of(tot, years):
    if tot <= -100:
        return -100.0
    return ((1 + tot / 100.0) ** (1.0 / years) - 1) * 100


BOTS = [
    ("adx20tp7", FastHybridTrendPullback, 20),
    ("adx18tp7", FastHybridTrendPullback, 18),
    ("regime22", RegimeFilteredHybrid,    22),
]
COSTS = [("$0.10  (repo assumption)", 0.10),
         ("$0.90  (fair proportional)", 0.90),
         ("$2.85  (real measured)", 2.85)]


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START
    df, _ = loader.load("XAUUSD", 99.0, c0, csv_path=GOLD_CSV, allow_synthetic=True)
    d_full = prepare_data(df)
    yrs = (df["timestamp"].iloc[-1] - df["timestamp"].iloc[0]).days / 365.25
    print(f"SL=3.0xATR  TP=7.0xATR   risk/trade={RISK}%   full history {yrs:.1f} years\n")

    for cost_label, sp in COSTS:
        print("=" * 96)
        print(f" cost = {cost_label}")
        print("=" * 96)
        for name, cls, adx in BOTS:
            m = run(cls, d_full, adx, sp)
            if not m or m.get("trades", 0) == 0:
                print(f"  {name:<12} NO TRADES"); continue
            tot = m["total_return_pct"]
            print(f"  {name:<12} trades={m['trades']:>5}  PF={m['profit_factor']:>5.2f}  "
                  f"TotRet={tot:>+9.1f}%  ->  CAGR={cagr_of(tot, yrs):>+7.2f}%/yr   "
                  f"MaxDD={m['max_dd_pct']:>5.1f}%")
        print()


if __name__ == "__main__":
    main()
