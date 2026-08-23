#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Close the last two gaps before deploying the fresh-trend-filter config:

  2) YEARLY WALK-FORWARD (params frozen, one line per calendar year) --
     stronger than the single train/test split already done, matches the
     discipline used for every other strategy in this project.

  4) COMBINED PORTFOLIO (BTC M15 fresh<=5, ETH M15 fresh<=3 at real spread $1,
     Gold H1 fresh<=10 regime22) -- monthly-return correlation and the actual
     drawdown of running all three together, not just each bot's own DD.

Real costs throughout: BTC spread $10, ETH spread $1 (both MT5-verified this
session), Gold $2.85 (measured from the live fills log).
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import DataLoader, prepare_data, compute_metrics
from _idea_search import resample
from _all_paths import to_monthly, perf
from _fresh_filter_test import FreshPullback, FreshRegime, run, line, START

GOLD_M15 = "download/xauusd-m15-bid-2013-01-01-2026-06-10.csv"
BTC_CSV  = "download/btcusdt-15m-binance-2017-08-17-2026-06-30.csv"
ETH_CSV  = "download/ethusdt-15m-binance-2017-08-17-2026-06-30.csv"

CONFIGS = [
    ("GOLD H1 regime22+fresh10", FreshRegime,   "XAUUSD",  2.85, 22, 3.5, None, None, 10),
    ("BTC  M15 fresh5",          FreshPullback, "BTCUSDc", 10.0, 18, 0.0, 1.0, 0.01, 5),
    ("ETH  M15 fresh3",          FreshPullback, "ETHUSDc",  1.0, 18, 0.0, 1.0, 0.01, 3),
]


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START

    dfg, _ = loader.load("XAUUSD", 99.0, c0, csv_path=GOLD_M15, allow_synthetic=True)
    dfg_h1 = resample(dfg, "1h")
    dfb, _ = loader.load("BTCUSDc", 99.0, c0, csv_path=BTC_CSV, allow_synthetic=False)
    dfe, _ = loader.load("ETHUSDc", 99.0, c0, csv_path=ETH_CSV, allow_synthetic=False)

    dfs = {"GOLD H1 regime22+fresh10": dfg_h1, "BTC  M15 fresh5": dfb, "ETH  M15 fresh3": dfe}

    print("=" * 104)
    print(" (2) YEARLY WALK-FORWARD -- params frozen, one row per calendar year")
    print("=" * 104)

    series = {}
    for label, cls, sym, sp, adx, comm, ps, pv, mm in CONFIGS:
        df = dfs[label]
        print(f"\n  {label}")
        years = sorted(df["timestamp"].dt.year.unique())
        pf_ok = tot = 0
        all_trades = []
        for y in years:
            dfy = df[df["timestamp"].dt.year == y].reset_index(drop=True)
            if len(dfy) < 1500:
                continue
            d = prepare_data(dfy)
            m, tr = run(cls, d, sym, sp, adx, comm, ps, pv, maxmat=mm)
            if not m or m.get("trades", 0) < 5:
                print(f"    {y}: too few trades ({m.get('trades',0) if m else 0})")
                continue
            tot += 1
            ok = m["profit_factor"] > 1.0
            pf_ok += 1 if ok else 0
            mark = "" if ok else "  <-- PF<1"
            print(f"    {y}: n={m['trades']:>4}  PF={m['profit_factor']:>5.2f}  "
                  f"win%={m['win_rate']*100:>5.1f}  TotRet={m['total_return_pct']:>+7.1f}%"
                  f"{mark}")
            for t in tr:
                all_trades.append(t)
        print(f"    -> years PF>1: {pf_ok}/{tot}")

        # full-history monthly series for the portfolio step below
        d_full = prepare_data(df)
        m_full, tr_full = run(cls, d_full, sym, sp, adx, comm, ps, pv, maxmat=mm)
        mr = to_monthly(tr_full)
        if len(mr) >= 12:
            series[label] = mr

    print("\n" + "=" * 104)
    print(" (4) COMBINED PORTFOLIO -- correlation + real drawdown of running all three")
    print("=" * 104)
    if len(series) < 2:
        print("  not enough series."); return

    allm = pd.concat(series, axis=1, sort=True).dropna()
    print(f"\n  overlapping months: {len(allm)}")
    print("\n  correlation matrix (monthly returns):")
    print(allm.corr().round(2).to_string())

    print("\n  individual (over the overlapping window):")
    for c in allm.columns:
        p = perf(allm[c])
        if p:
            print(f"    {c:<28} Sharpe={p['sharpe']:>5.2f}  CAGR={p['cagr']:>+7.2f}%  DD={p['dd']:>5.1f}%")

    port_equal = allm.mean(axis=1)
    p_eq = perf(port_equal)
    print(f"\n  EQUAL-WEIGHT combined:        Sharpe={p_eq['sharpe']:>5.2f}  "
          f"CAGR={p_eq['cagr']:>+7.2f}%  DD={p_eq['dd']:>5.1f}%")

    # capital-weighted: each bot actually risks 1.9% of ITS OWN slice of equity in
    # this deployment, but if all three share one account, a simultaneous drawdown
    # across all three matters most -- show the WORST simultaneous single-month hit
    worst_month = allm.sum(axis=1).min()
    worst_month_date = allm.sum(axis=1).idxmin()
    print(f"\n  worst SINGLE MONTH if all three hit at once: {worst_month:+.2f}% "
          f"(in {worst_month_date.strftime('%Y-%m')})")
    print("  (this is the realistic tail-risk number for one account running all three,")
    print("   since bot drawdowns are not perfectly diversified away by a single month)")


if __name__ == "__main__":
    main()
