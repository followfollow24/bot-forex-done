#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Yearly walk-forward for BTC M15 trend-pullback, NO fresh-filter, on the
NOW-FIXED engine -- this was the only survivor of the 2026-07-30 re-validation
(the fresh-trend-maturity filter and gold regime22 filter both turned out to
be largely artifacts of the pre-fix look-ahead bucket bug). Checking whether
this config alone is robust enough (years PF>1, no single dominant year) to
be worth building on, versus just a lucky full-sample average.
"""
from __future__ import annotations
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import DataLoader, prepare_data, BacktestEngine, FastHybridTrendPullback, compute_metrics
from _all_paths import to_monthly, perf, START

BTC_CSV = "download/btcusdt-15m-binance-2017-08-17-2026-06-30.csv"
RISK = 1.00


def cfg(sym, ps, pv, risk=RISK, hold=64):
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = risk
    c.partial_tp_atr = 999.0
    c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False
    c.max_hold_bars = hold
    c.pip_size[sym] = ps
    c.pip_value_usd_approx[sym] = pv
    return c


def run(d, adx=18, spread=10.0, comm=0.0, risk=RISK):
    s = FastHybridTrendPullback()
    s.ADX_MIN = adx
    s.sl_atr, s.tp_atr = 3.0, 999.0
    s.trail_atr_mult = s.trail_activation_atr = 999.0
    s.precompute(d)
    eng = BacktestEngine(d, cfg("BTCUSDc", 1.0, 0.01, risk=risk), s, spread_price=spread,
                          commission_per_lot=comm, symbol="BTCUSDc")
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START), eng.trades


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START
    dfb, _ = loader.load("BTCUSDc", 99.0, c0, csv_path=BTC_CSV, allow_synthetic=False)

    print("=" * 90)
    print(" BTC M15 trend-pullback, NO fresh-filter, adx18, spread $10 -- yearly WF (fixed engine)")
    print("=" * 90)
    years = sorted(dfb["timestamp"].dt.year.unique())
    pf_ok = tot = 0
    for y in years:
        dfy = dfb[dfb["timestamp"].dt.year == y].reset_index(drop=True)
        if len(dfy) < 1500:
            continue
        d = prepare_data(dfy)
        m, tr = run(d)
        if not m or m.get("trades", 0) < 15:
            print(f"  {y}: too few trades ({m.get('trades',0) if m else 0})")
            continue
        tot += 1
        ok = m["profit_factor"] > 1.0
        pf_ok += 1 if ok else 0
        mark = "" if ok else "  <-- PF<1"
        p = perf(to_monthly(tr))
        sh = p["sharpe"] if p else float("nan")
        print(f"  {y}: n={m['trades']:>4}  PF={m['profit_factor']:>5.2f}  "
              f"win%={m['win_rate']*100:>5.1f}  Sharpe={sh:>5.2f}  "
              f"TotRet={m['total_return_pct']:>+7.1f}%  DD={m['max_dd_pct']:>5.1f}%{mark}")
    print(f"\n  -> years PF>1: {pf_ok}/{tot}")

    # full history for reference
    d_full = prepare_data(dfb)
    m, tr = run(d_full)
    years_span = (dfb["timestamp"].iloc[-1] - dfb["timestamp"].iloc[0]).days / 365.25
    p = perf(to_monthly(tr))
    tot_ret = m["total_return_pct"]
    cagr = -100.0 if tot_ret <= -100 else ((1 + tot_ret/100) ** (1/years_span) - 1) * 100
    print(f"\n  FULL HISTORY ({years_span:.1f}y): n={m['trades']}  PF={m['profit_factor']:.2f}  "
          f"Sharpe={p['sharpe']:.2f}  CAGR={cagr:+.2f}%  DD={m['max_dd_pct']:.1f}%")

    # half/half OOS consistency check
    mid = dfb["timestamp"].iloc[len(dfb)//2]
    tr_df = dfb[dfb["timestamp"] <= mid].reset_index(drop=True)
    te_df = dfb[dfb["timestamp"] > mid].reset_index(drop=True)
    d_tr, d_te = prepare_data(tr_df), prepare_data(te_df)
    m_tr, _ = run(d_tr)
    m_te, tr_te = run(d_te)
    y_te = (te_df["timestamp"].iloc[-1] - te_df["timestamp"].iloc[0]).days / 365.25
    p_te = perf(to_monthly(tr_te))
    print(f"\n  1st half: n={m_tr['trades']}  PF={m_tr['profit_factor']:.2f}")
    print(f"  2nd half: n={m_te['trades']}  PF={m_te['profit_factor']:.2f}  Sharpe={p_te['sharpe']:.2f}  "
          f"TotRet={m_te['total_return_pct']:+.1f}%  DD={m_te['max_dd_pct']:.1f}%")


if __name__ == "__main__":
    main()
