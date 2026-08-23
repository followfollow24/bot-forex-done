#!/usr/bin/env python3
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
RISK_PCT = 0.30
COMM = 3.50

def gold_cfg():
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = RISK_PCT
    c.partial_tp_atr = 999.0
    c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False
    return c

def run(strat_cls, d, adx_min, sl, tp, spread_price):
    strat = strat_cls()
    strat.ADX_MIN = adx_min
    strat.sl_atr, strat.tp_atr = sl, tp
    strat.trail_atr_mult, strat.trail_activation_atr = 999.0, 999.0
    strat.precompute(d)
    eng = BacktestEngine(d, gold_cfg(), strat, spread_price=spread_price,
                          commission_per_lot=COMM, symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START)

def fmt(m, label):
    if m is None or m.get("trades", 0) == 0:
        return f"  {label:<28} NO TRADES"
    return (f"  {label:<28} trades={m['trades']:>5}  win%={m['win_rate']*100:>5.1f}  "
            f"PF={m['profit_factor']:>5.2f}  Sharpe={m['sharpe']:>5.2f}  "
            f"MaxDD%={m['max_dd_pct']:>5.1f}  TotRet%={m['total_return_pct']:>+8.1f}")

def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    cfg0 = ForexConfig(); cfg0.total_capital_usd = START
    df_full, _ = loader.load("XAUUSD", 99.0, cfg0, csv_path=GOLD_CSV, allow_synthetic=True)
    # last 6 months only -- closest to live regime
    df_full = df_full[df_full["timestamp"] >= pd.Timestamp("2025-12-01")].reset_index(drop=True)
    d = prepare_data(df_full)
    print(f"[window] last 6mo, {len(df_full):,} bars\n")

    # measured live avg spread + avg abs slippage per bot (from real fills_log)
    configs = [
        ("adx18tp7", FastHybridTrendPullback, 18, 3.0, 7.0, 0.248+1.828),  # spread+slip combined
        ("adx20tp7", FastHybridTrendPullback, 20, 3.0, 7.0, 0.247+2.599),
        ("regime22", RegimeFilteredHybrid,    22, 3.0, 7.0, 0.243+0.132),
    ]
    SPREAD_LEVELS = [0.10, 0.25]  # backtest-assumed, then real-measured-spread-only

    for name, cls, adx_min, sl, tp, realistic_total in configs:
        print("=" * 100)
        print(f" {name} TP={tp} (ADX_MIN={adx_min})")
        print("=" * 100)
        for sp in SPREAD_LEVELS:
            m = run(cls, d, adx_min, sl, tp, sp)
            print(fmt(m, f"spread_price={sp}"))
        m = run(cls, d, adx_min, sl, tp, realistic_total)
        print(fmt(m, f"REALISTIC spread+slip={realistic_total:.3f}"))
        print()

if __name__ == "__main__":
    main()
