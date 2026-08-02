#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test near-1:1 SL:TP (symmetric risk:reward, targeting ~50% win rate) on the
existing, already-validated entry signals -- BTC/ETH HybridTrendPullback
(H1 entry/H4 trend) and Gold GoldDailyDonchianBreakout -- instead of their
current asymmetric exits (wide/trailing TP, lower win rate compensated by
bigger average win). Same discipline as every other test this session:
real costs, no look-ahead.
"""
from __future__ import annotations
import os, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import DataLoader, prepare_data, BacktestEngine, FastHybridTrendPullback, compute_metrics
from gold_daily_breakout_strategy import GoldDailyDonchianBreakout
from _idea_search import resample
from _all_paths import to_monthly, perf, START


def cfg(sym, ps=None, pv=None, risk=1.0, hold=64):
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = risk
    c.partial_tp_atr = 999.0
    c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False
    c.max_hold_bars = hold
    if ps is not None:
        c.pip_size[sym] = ps
        c.pip_value_usd_approx[sym] = pv
    return c


def run_hybrid(d, sym, spread, adx, rr_atr, comm=0.0, ps=1.0, pv=0.01, risk=1.0, hold=64):
    s = FastHybridTrendPullback()
    s.ADX_MIN = adx
    s.sl_atr = rr_atr
    s.tp_atr = rr_atr          # symmetric 1:1
    s.trail_atr_mult = 999.0   # trailing OFF -- fixed TP does the exit
    s.trail_activation_atr = 999.0
    s.precompute(d)
    eng = BacktestEngine(d, cfg(sym, ps, pv, risk=risk, hold=hold), s, spread_price=spread,
                          commission_per_lot=comm, symbol=sym)
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START), eng.trades


def run_breakout(d, rr_atr, risk=0.50):
    s = GoldDailyDonchianBreakout()
    s.sl_atr = rr_atr
    s.tp_atr = rr_atr
    s.trail_atr_mult = 999.0   # trailing OFF -- fixed TP does the exit
    s.trail_activation_atr = 999.0
    s.precompute(d)
    eng = BacktestEngine(d, cfg("XAUUSD", risk=risk, hold=20), s, spread_price=2.85,
                          commission_per_lot=3.5, symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START), eng.trades


def line(m, tr, label, yrs):
    if not m or m.get("trades", 0) < 15:
        n = m.get("trades", 0) if m else 0
        print(f"    {label:<26} n={n:>5}  too few")
        return
    p = perf(to_monthly(tr))
    sh = p["sharpe"] if p else float("nan")
    tot = m["total_return_pct"]
    cg = -100.0 if tot <= -100 else ((1+tot/100)**(1/yrs)-1)*100
    print(f"    {label:<26} n={m['trades']:>5}  win%={m['win_rate']*100:>5.1f}  "
          f"PF={m['profit_factor']:>5.2f}  Sharpe={sh:>5.2f}  CAGR={cg:>+7.2f}%  "
          f"DD={m['max_dd_pct']:>5.1f}%  ({m['trades']/yrs:>5.0f} trades/yr)")


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START

    BTC_CSV = "download/btcusdt-15m-binance-2017-08-17-2026-06-30.csv"
    ETH_CSV = "download/ethusdt-15m-binance-2017-08-17-2026-06-30.csv"
    GOLD_M15 = "download/xauusd-m15-bid-2013-01-01-2026-06-10.csv"

    dfb, _ = loader.load("BTCUSDc", 99.0, c0, csv_path=BTC_CSV, allow_synthetic=False)
    dfb_h1 = resample(dfb, "1h")
    yb = (dfb_h1["timestamp"].iloc[-1] - dfb_h1["timestamp"].iloc[0]).days / 365.25
    db = prepare_data(dfb_h1)

    dfe, _ = loader.load("ETHUSDc", 99.0, c0, csv_path=ETH_CSV, allow_synthetic=False)
    dfe_h1 = resample(dfe, "1h")
    ye = (dfe_h1["timestamp"].iloc[-1] - dfe_h1["timestamp"].iloc[0]).days / 365.25
    de = prepare_data(dfe_h1)

    dfg, _ = loader.load("XAUUSD", 99.0, c0, csv_path=GOLD_M15, allow_synthetic=True)
    df_d = resample(dfg, "1D")
    yg = (df_d["timestamp"].iloc[-1] - df_d["timestamp"].iloc[0]).days / 365.25
    dg = prepare_data(df_d)

    print("=" * 100)
    print(" SYMMETRIC 1:1 SL:TP -- same entry signal, exit swapped to fixed ATR-multiple TP")
    print("=" * 100)

    print(f"\n  BTC H1 (adx18), risk=1.00%, {yb:.1f}y  -- current live: sl=3.0 trailing(no fixed TP)")
    for rr in [1.0, 1.5, 2.0, 2.5, 3.0]:
        m, tr = run_hybrid(db, "BTCUSDc", 10.0, 18, rr, risk=1.0)
        line(m, tr, f"SL=TP={rr}xATR", yb)

    print(f"\n  ETH H1 (adx18), risk=1.00%, {ye:.1f}y  -- current live: sl=3.0 trailing(no fixed TP)")
    for rr in [1.0, 1.5, 2.0, 2.5, 3.0]:
        m, tr = run_hybrid(de, "ETHUSDc", 1.0, 18, rr, risk=1.0)
        line(m, tr, f"SL=TP={rr}xATR", ye)

    print(f"\n  GOLD Daily Donchian, risk=0.50%, {yg:.1f}y  -- current live: sl=2.0 trailing(no fixed TP)")
    for rr in [1.0, 1.5, 2.0, 2.5, 3.0]:
        m, tr = run_breakout(dg, rr, risk=0.50)
        line(m, tr, f"SL=TP={rr}xATR", yg)


if __name__ == "__main__":
    main()
