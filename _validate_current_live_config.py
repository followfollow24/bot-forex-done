#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
What happens if we JUST fix the bug and redeploy with the EXACT SAME flags
currently running live (watchdog_h1.ps1, as of this check):
  btc_h1_manual : --timeframe 1h --adx-min 18                     (no regime, no fresh-filter)
  eth_h1_manual : --timeframe 1h --adx-min 18                     (no regime, no fresh-filter)
  gold_h1_manual: --timeframe 1h --adx-min 22 --regime-filter     (regime ON)

This matters because gold_h1_manual's --regime-filter has NEVER actually been
applied live (bug #3 from the 2026-07-30 fix) -- deploying the bug fix as-is
would, for the FIRST TIME, make it actually filter by regime. Need to know
whether that's an improvement or a regression before touching real money.
"""
from __future__ import annotations
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import DataLoader, prepare_data, BacktestEngine, FastHybridTrendPullback, compute_metrics
from gold_regime_live_strategy import RegimeFilteredHybridLive
from _idea_search import resample
from _all_paths import to_monthly, perf, START

GOLD_M15 = "download/xauusd-m15-bid-2013-01-01-2026-06-10.csv"
BTC_CSV  = "download/btcusdt-15m-binance-2017-08-17-2026-06-30.csv"
ETH_CSV  = "download/ethusdt-15m-binance-2017-08-17-2026-06-30.csv"


class FastRegimeFixed(RegimeFilteredHybridLive, FastHybridTrendPullback):
    pass


def cfg(sym, ps=None, pv=None, hold=64, risk=1.90):
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


def run(cls, d, sym, spread, adx, comm=3.5, ps=None, pv=None, risk=1.90, tf_sec=3600):
    s = cls()
    s.ADX_MIN = adx
    s.TIMEFRAME_SECONDS = tf_sec
    s.sl_atr, s.tp_atr = 3.0, 999.0
    s.trail_atr_mult = s.trail_activation_atr = 999.0
    s.precompute(d)
    eng = BacktestEngine(d, cfg(sym, ps, pv, risk=risk), s, spread_price=spread,
                          commission_per_lot=comm, symbol=sym)
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START), eng.trades


def line(m, tr, label, yrs):
    if not m or m.get("trades", 0) < 15:
        print(f"    {label:<28} n={m.get('trades',0) if m else 0:>5}  too few")
        return
    p = perf(to_monthly(tr))
    sh = p["sharpe"] if p else float("nan")
    tot = m["total_return_pct"]
    cg = -100.0 if tot <= -100 else ((1+tot/100)**(1/yrs)-1)*100
    print(f"    {label:<28} n={m['trades']:>5}  PF={m['profit_factor']:>5.2f}  "
          f"Sharpe={sh:>5.2f}  CAGR={cg:>+7.2f}%  DD={m['max_dd_pct']:>5.1f}%  "
          f"({m['trades']/yrs:>5.0f} trades/yr)")


def yearly(cls, df, sym, spread, adx, comm, ps, pv, risk, label):
    print(f"\n  {label}")
    years = sorted(df["timestamp"].dt.year.unique())
    pf_ok = tot = 0
    for y in years:
        dfy = df[df["timestamp"].dt.year == y].reset_index(drop=True)
        if len(dfy) < 300:
            continue
        d = prepare_data(dfy)
        m, tr = run(cls, d, sym, spread, adx, comm, ps, pv, risk=risk)
        if not m or m.get("trades", 0) < 5:
            print(f"    {y}: too few trades")
            continue
        tot += 1
        ok = m["profit_factor"] > 1.0
        pf_ok += 1 if ok else 0
        mark = "" if ok else "  <-- PF<1"
        print(f"    {y}: n={m['trades']:>4}  PF={m['profit_factor']:>5.2f}  "
              f"TotRet={m['total_return_pct']:>+7.1f}%{mark}")
    print(f"    -> years PF>1: {pf_ok}/{tot}")

    d_full = prepare_data(df)
    yrs = (df["timestamp"].iloc[-1] - df["timestamp"].iloc[0]).days / 365.25
    m, tr = run(cls, d_full, sym, spread, adx, comm, ps, pv, risk=risk)
    line(m, tr, "FULL HISTORY", yrs)


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START

    print("=" * 100)
    print(" CURRENT LIVE CONFIG, bug FIXED, same flags (H1 entry / H4 trend, risk 1.90%)")
    print("=" * 100)

    dfg, _ = loader.load("XAUUSD", 99.0, c0, csv_path=GOLD_M15, allow_synthetic=True)
    dfg_h1 = resample(dfg, "1h")
    yearly(FastHybridTrendPullback, dfg_h1, "XAUUSD", 2.85, 22, 3.5, None, None, 1.90,
           "GOLD (adx22, NO regime-filter -- what has ACTUALLY been running live)")
    yearly(FastRegimeFixed, dfg_h1, "XAUUSD", 2.85, 22, 3.5, None, None, 1.90,
           "GOLD (adx22, regime-filter -- what --regime-filter WOULD do once bug fixed)")

    dfb, _ = loader.load("BTCUSDc", 99.0, c0, csv_path=BTC_CSV, allow_synthetic=False)
    dfb_h1 = resample(dfb, "1h")
    yearly(FastHybridTrendPullback, dfb_h1, "BTCUSDc", 10.0, 18, 0.0, 1.0, 0.01, 1.90,
           "BTC (adx18, H1 entry -- current live config)")

    dfe, _ = loader.load("ETHUSDc", 99.0, c0, csv_path=ETH_CSV, allow_synthetic=False)
    dfe_h1 = resample(dfe, "1h")
    yearly(FastHybridTrendPullback, dfe_h1, "ETHUSDc", 1.0, 18, 0.0, 1.0, 0.01, 1.90,
           "ETH (adx18, H1 entry -- current live config)")


if __name__ == "__main__":
    main()
