#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Straight answer to "what % does H4 actually return", with the numbers that
matter rather than just headline total return:
  - total return over the tested span
  - CAGR (annualised) -- the honest way to read a 13-year total
  - MaxDD and return/DD, so the return is judged against the risk taken
  - the same at several risk-per-trade settings, since 0.30% is a choice,
    not a property of the strategy

Costs: the fair proportional cost for full history, and the live-measured
$2.85 for the recent era.
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
COMM = 3.50
PROP_SPREAD = 0.90     # price-proportional fair cost over full history
LIVE_SPREAD = 2.85     # measured live cost, valid for the recent era


def cfg(risk, max_hold=32):
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = risk
    c.partial_tp_atr = 999.0
    c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False
    c.max_hold_bars = max_hold
    return c


def run(strat_cls, d, spread, risk, adx_min=None, **ov):
    s = strat_cls()
    if adx_min is not None and hasattr(s, "ADX_MIN"):
        s.ADX_MIN = adx_min
    for k, v in ov.items():
        setattr(s, k, v)
    s.sl_atr, s.tp_atr = 3.0, 7.0
    s.trail_atr_mult = s.trail_activation_atr = 999.0
    s.precompute(d)
    eng = BacktestEngine(d, cfg(risk), s, spread_price=spread,
                          commission_per_lot=COMM, symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START)


def report(m, years, label):
    if m is None or m.get("trades", 0) == 0:
        print(f"  {label:<30} NO TRADES"); return
    tot = m["total_return_pct"]
    cagr = ((1 + tot / 100.0) ** (1.0 / years) - 1) * 100 if tot > -100 else float("nan")
    dd = m["max_dd_pct"]
    rdd = (cagr / dd) if dd > 0 else float("nan")
    print(f"  {label:<30} trades={m['trades']:>5}  TotRet={tot:>+8.1f}%  "
          f"CAGR={cagr:>+6.2f}%/yr  MaxDD={dd:>5.1f}%  CAGR/DD={rdd:>5.2f}  "
          f"PF={m['profit_factor']:.2f}")


CANDS = [
    ("H4 pullback adx18", FastHybridTrendPullback, dict(adx_min=18)),
    ("H4 donchian ch100", DonchianBreakout,        dict(CHANNEL=100)),
]


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START
    df_m15, _ = loader.load("XAUUSD", 99.0, c0, csv_path=GOLD_CSV, allow_synthetic=True)
    df_h4 = resample(df_m15, "4h")
    d_full = prepare_data(df_h4)

    df_rec = df_h4[df_h4["timestamp"] >= pd.Timestamp("2024-01-01")].reset_index(drop=True)
    d_rec = prepare_data(df_rec)

    yrs_full = (df_h4["timestamp"].iloc[-1] - df_h4["timestamp"].iloc[0]).days / 365.25
    yrs_rec  = (df_rec["timestamp"].iloc[-1] - df_rec["timestamp"].iloc[0]).days / 365.25
    print(f"[span] full = {yrs_full:.1f} years   recent = {yrs_rec:.1f} years\n")

    print("=" * 108)
    print(f" FULL HISTORY ({yrs_full:.1f}y), fair proportional cost ${PROP_SPREAD}")
    print("=" * 108)
    for risk in [0.30, 0.50, 1.00, 2.00]:
        print(f" risk/trade = {risk}%")
        for name, cls, kw in CANDS:
            report(run(cls, d_full, PROP_SPREAD, risk, **kw), yrs_full, name)
        print()

    print("=" * 108)
    print(f" RECENT ERA 2024-2026 ({yrs_rec:.1f}y), live-measured cost ${LIVE_SPREAD}")
    print("=" * 108)
    for risk in [0.30, 0.50, 1.00, 2.00]:
        print(f" risk/trade = {risk}%")
        for name, cls, kw in CANDS:
            report(run(cls, d_rec, LIVE_SPREAD, risk, **kw), yrs_rec, name)
        print()


if __name__ == "__main__":
    main()
