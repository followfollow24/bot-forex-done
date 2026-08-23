#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Apples-to-apples comparison: existing live M15 strategy vs the H4 candidates.

Why this script exists: the flat $2.00 cost used in the idea search is NOT a
fair 13-year assumption. It was measured on 2026 gold (~$4,000/oz). In 2013
gold was ~$1,300/oz and the M15 ATR was a few dollars, so charging a flat $2
per trade back then is a far heavier tax than anything a real broker charged.
That flat cost is exactly the kind of assumption that can manufacture a fake
"H4 beats M15" result -- higher timeframe = bigger ATR = less hurt by a fixed
dollar cost, regardless of whether the signal is any good.

So here we score everything three ways and see whether the ranking survives:
  1. flat $0.10   -- the original repo assumption (too optimistic)
  2. flat $2.00   -- the 2026-measured cost applied to all years (too harsh early)
  3. proportional -- cost scales with price level: spread = price * 2.00/4000
                     i.e. ~$2 when gold is $4,000, ~$0.65 when gold is $1,300.
                     This is the honest middle and the one to judge on.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import (DataLoader, prepare_data, BacktestEngine,
                             FastHybridTrendPullback, compute_metrics)
from _idea_search import DonchianBreakout, resample

GOLD_CSV = "download/xauusd-m15-bid-2013-01-01-2026-06-10.csv"
START = 10_000.0
RISK_PCT = 0.30
COMM = 3.50

# proportional cost anchor: $2.00 of spread+slippage when gold trades at $4,000
COST_RATIO = 2.00 / 4000.0


def gold_cfg(max_hold):
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = RISK_PCT
    c.partial_tp_atr = 999.0
    c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False
    c.max_hold_bars = max_hold
    return c


def run(strat_cls, d, spread, max_hold, sl=3.0, tp=7.0, adx_min=None, **ov):
    strat = strat_cls()
    if adx_min is not None and hasattr(strat, "ADX_MIN"):
        strat.ADX_MIN = adx_min
    for k, v in ov.items():
        setattr(strat, k, v)
    strat.sl_atr, strat.tp_atr = sl, tp
    strat.trail_atr_mult, strat.trail_activation_atr = 999.0, 999.0
    strat.precompute(d)
    eng = BacktestEngine(d, gold_cfg(max_hold), strat, spread_price=spread,
                          commission_per_lot=COMM, symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START), eng.trades


def fmt(m, label):
    if m is None or m.get("trades", 0) == 0:
        return f"  {label:<34} NO TRADES"
    return (f"  {label:<34} trades={m['trades']:>5}  win%={m['win_rate']*100:>5.1f}  "
            f"PF={m['profit_factor']:>5.2f}  Sharpe={m['sharpe']:>6.2f}  "
            f"MaxDD%={m['max_dd_pct']:>5.1f}  TotRet%={m['total_return_pct']:>+9.1f}")


CONFIGS = [
    # label,               class,                    tf,   max_hold, kwargs
    ("LIVE M15 adx20tp7",  FastHybridTrendPullback, "M15", 64, dict(adx_min=20)),
    ("LIVE M15 adx18tp7",  FastHybridTrendPullback, "M15", 64, dict(adx_min=18)),
    ("NEW  H4 pullback18", FastHybridTrendPullback, "H4",  32, dict(adx_min=18)),
    ("NEW  H4 donchian100", DonchianBreakout,       "H4",  32, dict(CHANNEL=100)),
]


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    cfg0 = ForexConfig(); cfg0.total_capital_usd = START
    df_m15, _ = loader.load("XAUUSD", 99.0, cfg0, csv_path=GOLD_CSV, allow_synthetic=True)
    df_h4 = resample(df_m15, "4h")
    data = {"M15": prepare_data(df_m15), "H4": prepare_data(df_h4)}
    dfs  = {"M15": df_m15, "H4": df_h4}

    avg_price = float(df_m15["close"].mean())
    prop_spread = avg_price * COST_RATIO
    print(f"[data] M15={len(df_m15):,} bars, H4={len(df_h4):,} bars")
    print(f"[cost] avg gold price over 13y = ${avg_price:,.0f}"
          f"  ->  proportional spread = ${prop_spread:.2f}\n")

    for cost_label, spread in [("1) flat $0.10 (repo default)", 0.10),
                               ("2) flat $2.00 (2026 cost, all years)", 2.00),
                               (f"3) proportional ~${prop_spread:.2f} (FAIR)", prop_spread)]:
        print("=" * 112)
        print(f" FULL HISTORY 2013-2026  --  cost: {cost_label}")
        print("=" * 112)
        for label, cls, tf, hold, kw in CONFIGS:
            m, _ = run(cls, data[tf], spread, hold, **kw)
            print(fmt(m, label))
        print()

    # recent-era only: here the flat $2 IS the right cost, since gold ~$3-4k
    print("=" * 112)
    print(" RECENT ERA 2024-2026 ONLY  --  cost: flat $2.00 (correct for this price level)")
    print("=" * 112)
    for label, cls, tf, hold, kw in CONFIGS:
        dfw = dfs[tf][dfs[tf]["timestamp"] >= pd.Timestamp("2024-01-01")].reset_index(drop=True)
        m, _ = run(cls, prepare_data(dfw), 2.00, hold, **kw)
        print(fmt(m, label))
    print()

    print("=" * 112)
    print(" RECENT ERA 2024-2026  --  cost: flat $2.85 (adx20tp7's own measured slippage)")
    print("=" * 112)
    for label, cls, tf, hold, kw in CONFIGS:
        dfw = dfs[tf][dfs[tf]["timestamp"] >= pd.Timestamp("2024-01-01")].reset_index(drop=True)
        m, _ = run(cls, prepare_data(dfw), 2.85, hold, **kw)
        print(fmt(m, label))


if __name__ == "__main__":
    main()
