#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Try to thicken SMC's per-trade edge so it can survive the ~$2.0-2.8 real
spread+slippage the live gold bots actually experience. PF 1.03 at $0.25
is not deployable; the question is whether stricter setup quality buys
enough edge per trade, or whether it just shrinks the sample.

Filters tried, one axis at a time (no blind grid over everything at once,
which would overfit 13 years of one symbol):
  A. SWEEP_MIN_ATR   -- how decisively the stop-hunt must exceed the pool
  B. FVG_MIN_ATR     -- how big the imbalance must be
  C. POOL_LOOKBACK   -- how significant the liquidity pool is
  D. session filter  -- London/NY only (tighter real spread than Asia)

Scored at spread=2.00 (the live-measured cost), NOT at 0.25. A filter only
counts as a success if it is profitable at the cost we actually pay.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import DataLoader, prepare_data, BacktestEngine, compute_metrics
from smc_liquidity_strategy import SMCLiquidityFVG

GOLD_CSV = "download/xauusd-m15-bid-2013-01-01-2026-06-10.csv"
START = 10_000.0
RISK_PCT = 0.30
COMM = 3.50
REAL_SPREAD = 2.00      # live-measured spread+slippage for the gold bots
MIN_TRADES_GATE = 200


def gold_cfg():
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = RISK_PCT
    c.partial_tp_atr = 999.0
    c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False
    return c


def run(d, sl=3.0, tp=7.0, spread=REAL_SPREAD, **overrides):
    strat = SMCLiquidityFVG()
    for k, v in overrides.items():
        setattr(strat, k, v)
    strat.sl_atr, strat.tp_atr = sl, tp
    strat.trail_atr_mult, strat.trail_activation_atr = 999.0, 999.0
    strat.precompute(d)
    eng = BacktestEngine(d, gold_cfg(), strat, spread_price=spread,
                          commission_per_lot=COMM, symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START), eng.trades


def fmt(m, label):
    if m is None or m.get("trades", 0) == 0:
        return f"  {label:<34} NO TRADES"
    flag = ""
    if m["trades"] < MIN_TRADES_GATE:
        flag = "  [under-sampled]"
    return (f"  {label:<34} trades={m['trades']:>5}  win%={m['win_rate']*100:>5.1f}  "
            f"PF={m['profit_factor']:>5.2f}  Sharpe={m['sharpe']:>5.2f}  "
            f"MaxDD%={m['max_dd_pct']:>5.1f}  TotRet%={m['total_return_pct']:>+7.1f}{flag}")


class SessionSMC(SMCLiquidityFVG):
    """SMC restricted to London+NY hours, where real gold spread is tightest."""
    SESSION_START_H = 7    # UTC
    SESSION_END_H   = 21
    _hour_arr = None

    def precompute(self, d):
        super().precompute(d)
        ts = d.get("ts")
        if ts is not None:
            self._hour_arr = pd.to_datetime(pd.Series(ts)).dt.hour.to_numpy()

    def signal(self, d, i):
        if self._hour_arr is not None:
            h = int(self._hour_arr[i])
            if not (self.SESSION_START_H <= h < self.SESSION_END_H):
                from forex_indicators import Signal
                return Signal()
        return super().signal(d, i)


def run_cls(cls, d, sl=3.0, tp=7.0, spread=REAL_SPREAD, **overrides):
    strat = cls()
    for k, v in overrides.items():
        setattr(strat, k, v)
    strat.sl_atr, strat.tp_atr = sl, tp
    strat.trail_atr_mult, strat.trail_activation_atr = 999.0, 999.0
    strat.precompute(d)
    eng = BacktestEngine(d, gold_cfg(), strat, spread_price=spread,
                          commission_per_lot=COMM, symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START), eng.trades


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    cfg0 = ForexConfig(); cfg0.total_capital_usd = START
    df, _ = loader.load("XAUUSD", 99.0, cfg0, csv_path=GOLD_CSV, allow_synthetic=True)
    d = prepare_data(df)
    print(f"[load] {len(df):,} bars\n")
    print(f"ALL results below are scored at spread={REAL_SPREAD} (live-measured cost), commission=${COMM}/lot\n")

    print("=" * 108)
    print(" BASELINE (no extra filter)")
    print("=" * 108)
    m0, _ = run(d)
    print(fmt(m0, "baseline SL3/TP7"))

    print("\n" + "=" * 108)
    print(" A. SWEEP_MIN_ATR -- how decisive the stop-hunt must be")
    print("=" * 108)
    for v in [0.15, 0.30, 0.50, 0.75, 1.00]:
        m, _ = run(d, SWEEP_MIN_ATR=v)
        print(fmt(m, f"SWEEP_MIN_ATR={v}"))

    print("\n" + "=" * 108)
    print(" B. FVG_MIN_ATR -- how big the imbalance must be")
    print("=" * 108)
    for v in [0.10, 0.25, 0.40, 0.60, 0.80]:
        m, _ = run(d, FVG_MIN_ATR=v)
        print(fmt(m, f"FVG_MIN_ATR={v}"))

    print("\n" + "=" * 108)
    print(" C. POOL_LOOKBACK -- how significant the liquidity pool is")
    print("=" * 108)
    for v in [20, 40, 60, 100]:
        m, _ = run(d, POOL_LOOKBACK=v)
        print(fmt(m, f"POOL_LOOKBACK={v}"))

    print("\n" + "=" * 108)
    print(" D. session filter (London+NY only)")
    print("=" * 108)
    m, _ = run_cls(SessionSMC, d)
    print(fmt(m, "London+NY only"))

    print("\n" + "=" * 108)
    print(" E. best-of combination (only if individual axes showed real gains)")
    print("=" * 108)
    for sweep, fvg, pool in [(0.50, 0.40, 40), (0.75, 0.40, 60), (0.50, 0.25, 60), (1.00, 0.60, 40)]:
        m, _ = run(d, SWEEP_MIN_ATR=sweep, FVG_MIN_ATR=fvg, POOL_LOOKBACK=pool)
        print(fmt(m, f"sweep={sweep} fvg={fvg} pool={pool}"))
    for sweep, fvg, pool in [(0.50, 0.40, 40), (0.75, 0.40, 60)]:
        m, _ = run_cls(SessionSMC, d, SWEEP_MIN_ATR=sweep, FVG_MIN_ATR=fvg, POOL_LOOKBACK=pool)
        print(fmt(m, f"+session sweep={sweep} fvg={fvg} pool={pool}"))


if __name__ == "__main__":
    main()
