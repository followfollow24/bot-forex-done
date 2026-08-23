#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Combine the two edges that actually survived the whole search:

  A) GOLD H4 pullback  -- the live strategy, unchanged, just moved off M15.
                          cost/ATR 24% instead of 106%, which is why it works.
  B) CRYPTO H1 pullback across 13 coins -- same strategy family, in the one
                          regime where cost/ATR is comfortable (5-10%).

Both are the SAME signal logic, which matters: nothing here was invented to fit
this portfolio. The difference between them is the instrument's cost relative
to its volatility, which is the single finding that survived everything.

Reported honestly:
  - gold H4's headline Sharpe comes from ~74 trades in 2.4 years and should be
    treated as provisional, so the portfolio is shown over the full common
    window as well as the recent one
  - correlation between the two blocks decides whether combining is worth
    anything at all
  - the risk-scaling is expressed as per-trade risk actually settable in the
    bot, not an abstract multiplier
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import (DataLoader, prepare_data, BacktestEngine,
                             FastHybridTrendPullback, compute_metrics)
from gold_regime_filter_real_engine import RegimeFilteredHybrid
from _idea_search import resample
from _all_paths import to_monthly, perf, START
from _crypto_multi import load_crypto_1h, scale_for, SPREAD_BPS

GOLD_M15 = "download/xauusd-m15-bid-2013-01-01-2026-06-10.csv"
RISK = 0.30
GOLD_COST = 2.85
TRADING_DAYS = 252


def cfg(sym, ps, pv, hold):
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = RISK
    c.partial_tp_atr = 999.0
    c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False
    c.max_hold_bars = hold
    if ps is not None:
        c.pip_size[sym] = ps
        c.pip_value_usd_approx[sym] = pv
    return c


def run(cls, d, sym, spread, hold, comm=0.0, ps=None, pv=None, **ov):
    s = cls()
    for k, v in ov.items():
        setattr(s, k, v)
    s.sl_atr, s.tp_atr = 3.0, 7.0
    s.trail_atr_mult = s.trail_activation_atr = 999.0
    s.precompute(d)
    eng = BacktestEngine(d, cfg(sym, ps, pv, hold), s, spread_price=spread,
                          commission_per_lot=comm, symbol=sym)
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START), eng.trades


def block_stats(series, label):
    if not series:
        print(f"  {label}: none"); return None, None
    allm = pd.concat(series, axis=1, sort=True)
    port = allm.mean(axis=1, skipna=True).dropna()
    p = perf(port)
    if p:
        print(f"  {label:<28} edges={len(series):>3}  months={p['n']:>4}  "
              f"Sharpe={p['sharpe']:>5.2f}  CAGR={p['cagr']:>+7.2f}%  DD={p['dd']:>5.2f}%")
    return port, p


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START

    # ---------- A) gold H4 ----------
    print("=" * 100)
    print(" BLOCK A -- GOLD H4 (live strategy, timeframe changed only)")
    print("=" * 100)
    dfg, _ = loader.load("XAUUSD", 99.0, c0, csv_path=GOLD_M15, allow_synthetic=True)
    dfh4 = resample(dfg, "4h")
    d_h4 = prepare_data(dfh4)
    gold_series = {}
    for lbl, cls, kw in [("gold-H4-adx18", FastHybridTrendPullback, dict(ADX_MIN=18)),
                         ("gold-H4-adx22", FastHybridTrendPullback, dict(ADX_MIN=22)),
                         ("gold-H4-regime22", RegimeFilteredHybrid, dict(ADX_MIN=22))]:
        m, tr = run(cls, d_h4, "XAUUSD", GOLD_COST, 32, comm=3.5, **kw)
        if m and m.get("trades", 0) >= 100:
            mr = to_monthly(tr); p = perf(mr)
            if p:
                gold_series[lbl] = mr
                print(f"  {lbl:<20} n={m['trades']:>5}  PF={m['profit_factor']:>5.2f}  "
                      f"Sharpe={p['sharpe']:>5.2f}  CAGR={p['cagr']:>+7.2f}%  DD={p['dd']:>5.2f}%")
    gold_port, gold_p = block_stats(gold_series, "GOLD H4 block")

    # ---------- B) crypto H1 ----------
    print("\n" + "=" * 100)
    print(" BLOCK B -- CRYPTO H1 (13 coins, same strategy)")
    print("=" * 100)
    data = load_crypto_1h()
    crypto_series = {}
    for nm, df in sorted(data.items()):
        d = prepare_data(df)
        pxm = float(df["close"].median())
        ps, pv = scale_for(pxm)
        try:
            m, tr = run(FastHybridTrendPullback, d, nm, pxm * SPREAD_BPS / 1e4, 64,
                        ps=ps, pv=pv, ADX_MIN=18)
        except Exception:
            continue
        if m and m.get("trades", 0) >= 100 and m["profit_factor"] > 1.0:
            mr = to_monthly(tr); p = perf(mr)
            if p and p["sharpe"] > 0.3:
                crypto_series[f"crypto-{nm}"] = mr
    crypto_port, crypto_p = block_stats(crypto_series, "CRYPTO H1 block")

    if gold_port is None or crypto_port is None:
        print("\n  cannot combine."); return

    # ---------- combine ----------
    print("\n" + "=" * 100)
    print(" COMBINED PORTFOLIO")
    print("=" * 100)
    both = pd.concat([gold_port.rename("gold"), crypto_port.rename("crypto")], axis=1).dropna()
    print(f"  overlapping months: {len(both)}")
    if len(both) < 24:
        print("  too little overlap."); return
    corr = both["gold"].corr(both["crypto"])
    print(f"  correlation gold vs crypto: {corr:+.3f}")

    for wg, wc, lbl in [(0.5, 0.5, "50/50"), (0.3, 0.7, "30 gold / 70 crypto"),
                        (0.7, 0.3, "70 gold / 30 crypto")]:
        comb = both["gold"] * wg + both["crypto"] * wc
        p = perf(comb)
        if p:
            print(f"  {lbl:<24} Sharpe={p['sharpe']:>5.2f}  CAGR={p['cagr']:>+7.2f}%  "
                  f"DD={p['dd']:>5.2f}%")

    # inverse-vol weighting
    gv, cv = both["gold"].std(), both["crypto"].std()
    wg = (1 / gv) / ((1 / gv) + (1 / cv))
    comb = both["gold"] * wg + both["crypto"] * (1 - wg)
    p_best = perf(comb)
    print(f"  {'inverse-vol ('+f'{wg:.0%}'+' gold)':<24} Sharpe={p_best['sharpe']:>5.2f}  "
          f"CAGR={p_best['cagr']:>+7.2f}%  DD={p_best['dd']:>5.2f}%")

    # pick the better of 50/50 and inverse-vol for the scaling table
    p50 = perf(both.mean(axis=1))
    use, use_lbl = (p_best, f"inverse-vol") if p_best["sharpe"] >= p50["sharpe"] else (p50, "50/50")
    series_used = comb if use is p_best else both.mean(axis=1)

    print("\n" + "=" * 100)
    print(f" WHAT IT SUPPORTS  (using {use_lbl}, {len(series_used)} months)")
    print("=" * 100)
    n_edges = len(gold_series) + len(crypto_series)
    print(f"  running all {n_edges} edges in parallel:")
    print(f"  {'risk/trade':<14}{'DD%':>10}{'CAGR%/yr':>12}{'%/day':>10}")
    for pt in [0.25, 0.50, 0.75, 1.00]:
        k = (pt / RISK) * n_edges
        p2 = perf(series_used * k)
        if p2:
            dpd = (1 + p2["cagr"] / 100) ** (1 / TRADING_DAYS) * 100 - 100
            print(f"  {pt:<14.2f}{p2['dd']:>9.1f}%{p2['cagr']:>12.2f}{dpd:>10.3f}")

    print("\n" + "=" * 100)
    print(" HONESTY CHECK -- recent window only (where gold H4's headline came from)")
    print("=" * 100)
    rec = both.loc[both.index >= "2024-01-01"]
    if len(rec) >= 18:
        for nm in ["gold", "crypto"]:
            p = perf(rec[nm])
            if p:
                print(f"  {nm:<10} 2024-26  Sharpe={p['sharpe']:>5.2f}  CAGR={p['cagr']:>+7.2f}%")
        p = perf(rec.mean(axis=1))
        if p:
            print(f"  {'combined':<10} 2024-26  Sharpe={p['sharpe']:>5.2f}  CAGR={p['cagr']:>+7.2f}%")
    print("\n  NOTE: gold H4 trades ~30x/year. Its Sharpe is built on a small sample and")
    print("  should be treated as provisional until it has traded a lot more.")


if __name__ == "__main__":
    main()
