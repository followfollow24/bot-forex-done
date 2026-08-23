#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wr90_forced_test.py -- Deliberately force win rate toward ~90% by skewing
TP:SL ratio (tiny TP, wide SL), using the BEST entry signal we have
(adx20 trend-pullback) on the real engine. This directly answers "can we
get WR 90%" with real numbers instead of theory.

Kelly breakeven math says: at WR=90%, avg_win/avg_loss must be >= 0.111
just to break even before costs. We test several R:R ratios approaching
and exceeding that skew, to show exactly what happens to PF/expectancy as
WR climbs toward 90% via this mechanism (the only mechanism that can
force WR that high).
"""
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

# (label, sl_atr, tp_atr) -- widening SL / shrinking TP to push WR up
CONFIGS = [
    ("baseline (adx20tp7)",  3.0, 7.0),
    ("mild skew",            5.0, 3.0),
    ("R:R 1:3 (WR~75% target)", 6.0, 2.0),
    ("R:R 1:6 (WR~86% target)", 6.0, 1.0),
    ("R:R 1:9 (WR~90% target)", 9.0, 1.0),
    ("R:R 1:12 (WR~92% target)", 12.0, 1.0),
]


def gold_cfg():
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = RISK_PCT
    c.partial_tp_atr = 999.0
    c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False
    return c


def run(df_full, sl, tp, adx=20):
    d = prepare_data(df_full)
    strat = FastHybridTrendPullback()
    strat.ADX_MIN = adx
    strat.precompute(d)
    strat.sl_atr, strat.tp_atr = sl, tp
    strat.trail_atr_mult, strat.trail_activation_atr = 999.0, 999.0
    eng = BacktestEngine(d, gold_cfg(), strat, spread_price=SPREAD,
                          commission_per_lot=COMM, symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START)


def main():
    print("=" * 100)
    print(" FORCING WR TOWARD 90% -- what actually happens to profitability")
    print(" Same adx20 entry signal throughout, only SL:TP ratio changes")
    print("=" * 100)

    loader = DataLoader(log_fn=lambda *a, **k: None)
    cfg0 = ForexConfig(); cfg0.total_capital_usd = START
    df_full, _ = loader.load("XAUUSD", 99.0, cfg0, csv_path=GOLD_CSV, allow_synthetic=True)
    print(f"[load] {len(df_full):,} bars\n")

    print(f"{'Config':<28}{'SL':>5}{'TP':>5}{'Trades':>8}{'Win%':>8}{'PF':>7}{'MaxDD%':>8}{'TotRet%':>10}{'AvgWin$':>9}{'AvgLoss$':>10}")
    print("-" * 100)
    for label, sl, tp in CONFIGS:
        m = run(df_full, sl, tp)
        if m is None or m.get("trades", 0) == 0:
            print(f"{label:<28} NO TRADES")
            continue
        print(f"{label:<28}{sl:>5.1f}{tp:>5.1f}{m['trades']:>8}{m['win_rate']*100:>7.1f}%"
              f"{m['profit_factor']:>7.2f}{m['max_dd_pct']:>7.1f}%{m['total_return_pct']:>+9.1f}%"
              f"{m['avg_win_usd']:>9.2f}{m['avg_loss_usd']:>10.2f}")

    print("=" * 100)


if __name__ == "__main__":
    main()
