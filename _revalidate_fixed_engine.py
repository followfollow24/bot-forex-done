#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Re-validate the fresh-trend-filter configs using the NOW-FIXED production
strategy code (forex_hybrid_strategy.HybridTrendPullback /
gold_regime_live_strategy.RegimeFilteredHybridLive), after the 2026-07-30
H1/H4 bucket-anchoring fix.

Why this exists and not just re-running the earlier scripts: the earlier
validated numbers for GOLD came from gold_regime_filter_real_engine.py's
RegimeFilteredHybrid, which has its OWN COPY of the pre-fix, position-based
`_build_h1_trend_array` (idx = arange(n_h1)*H1_BARS) -- a separate research
file never touched by today's fix to forex_hybrid_strategy.py /
gold_regime_live_strategy.py. Re-running the old scripts unmodified would
silently keep validating the OLD buggy gold logic. BTC/ETH's numbers (via
FastHybridTrendPullback, which inherits _build_h1_trend_array from the base
class rather than overriding it) WERE already picking up the fix -- but this
script re-checks all three from the same, unambiguous, fixed source classes
so there's no doubt about which code path produced which number.

FastXxx classes here = the actual FIXED production classes + FastHybridTrendPullback's
M15-EMA precompute-caching only (O(n) instead of O(n^2) for the M15 entry
check) -- H1/H4 trend logic is untouched, inherited straight from the fixed
base/regime classes via MRO.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import DataLoader, prepare_data, BacktestEngine, FastHybridTrendPullback, compute_metrics
from forex_hybrid_strategy import FreshTrendFilterMixin
from gold_regime_live_strategy import RegimeFilteredHybridLive
from _idea_search import resample
from _all_paths import to_monthly, perf, START

GOLD_M15 = "download/xauusd-m15-bid-2013-01-01-2026-06-10.csv"
BTC_CSV  = "download/btcusdt-15m-binance-2017-08-17-2026-06-30.csv"
ETH_CSV  = "download/ethusdt-15m-binance-2017-08-17-2026-06-30.csv"


# MRO puts RegimeFilteredHybridLive's _build_h1_trend_array (the FIXED one)
# ahead of FastHybridTrendPullback in the lookup order; FastHybridTrendPullback
# still supplies precompute()'s M15-EMA caching and the cached _m15_entry.
class FastRegimeFixed(RegimeFilteredHybridLive, FastHybridTrendPullback):
    pass


class FastPullbackFresh(FreshTrendFilterMixin, FastHybridTrendPullback):
    pass


class FastRegimeFresh(FreshTrendFilterMixin, RegimeFilteredHybridLive, FastHybridTrendPullback):
    pass


# entry-bar spacing in seconds for each config's data -- MUST match, or
# _bucket_seconds() (TIMEFRAME_SECONDS * H1_BARS) silently disables the H4
# aggregation instead of erroring (see run()'s comment).
TF_SECONDS = {
    "GOLD H1 regime22+fresh10": 3600,  # H1-spaced bars -> H1_BARS(4) => H4 buckets
    "BTC  M15 fresh5": 900,            # M15-spaced bars -> H1_BARS(4) => H1 buckets
    "ETH  M15 fresh3": 900,
}

CONFIGS = [
    # label, no-filter class, fresh class, symbol, spread, adx, comm, ps, pv, maxmat
    ("GOLD H1 regime22+fresh10", FastRegimeFixed,   FastRegimeFresh,   "XAUUSD",  2.85, 22, 3.5, None, None, 10),
    ("BTC  M15 fresh5",          FastHybridTrendPullback, FastPullbackFresh, "BTCUSDc", 10.0, 18, 0.0, 1.0, 0.01, 5),
    ("ETH  M15 fresh3",          FastHybridTrendPullback, FastPullbackFresh, "ETHUSDc",  1.0, 18, 0.0, 1.0, 0.01, 3),
]


def cfg(sym, ps=None, pv=None, hold=64, risk=0.30):
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


def run(cls, d, sym, spread, adx, comm=3.5, ps=None, pv=None, maxmat=None, risk=0.30, entry_tf_seconds=900):
    s = cls()
    s.ADX_MIN = adx
    # [FIX] TIMEFRAME_SECONDS must match the actual spacing of the bars in d,
    # or _bucket_seconds() (= TIMEFRAME_SECONDS * H1_BARS) silently mismatches
    # the data -- e.g. Gold here uses H1-spaced bars (entry_tf_seconds=3600)
    # needing H4 buckets (3600*4=14400s), but the class default (900) would
    # instead compute 3600s buckets == the entry bar spacing itself, i.e. ONE
    # bar per bucket, disabling H4 aggregation entirely without erroring.
    s.TIMEFRAME_SECONDS = entry_tf_seconds
    if maxmat is not None and hasattr(s, "MAX_MATURITY"):
        s.MAX_MATURITY = maxmat
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
        return None
    p = perf(to_monthly(tr))
    sh = p["sharpe"] if p else float("nan")
    tot = m["total_return_pct"]
    cg = -100.0 if tot <= -100 else ((1+tot/100)**(1/yrs)-1)*100
    print(f"    {label:<28} n={m['trades']:>5}  PF={m['profit_factor']:>5.2f}  "
          f"Sharpe={sh:>5.2f}  CAGR={cg:>+7.2f}%  DD={m['max_dd_pct']:>5.1f}%  "
          f"({m['trades']/yrs:>5.0f} trades/yr)")
    return dict(pf=m["profit_factor"], sharpe=sh, cagr=cg, dd=m["max_dd_pct"], n=m["trades"])


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START

    dfg, _ = loader.load("XAUUSD", 99.0, c0, csv_path=GOLD_M15, allow_synthetic=True)
    dfg_h1 = resample(dfg, "1h")
    dfb, _ = loader.load("BTCUSDc", 99.0, c0, csv_path=BTC_CSV, allow_synthetic=False)
    dfe, _ = loader.load("ETHUSDc", 99.0, c0, csv_path=ETH_CSV, allow_synthetic=False)
    dfs = {"GOLD H1 regime22+fresh10": dfg_h1, "BTC  M15 fresh5": dfb, "ETH  M15 fresh3": dfe}
    risks = {"GOLD H1 regime22+fresh10": 0.30, "BTC  M15 fresh5": 1.00, "ETH  M15 fresh3": 1.00}

    print("=" * 104)
    print(" (1) FULL HISTORY -- fixed engine, real costs, baseline vs fresh-filter")
    print("=" * 104)
    for label, base_cls, fresh_cls, sym, sp, adx, comm, ps, pv, mm in CONFIGS:
        df = dfs[label]
        years = (df["timestamp"].iloc[-1] - df["timestamp"].iloc[0]).days / 365.25
        d = prepare_data(df)
        risk = risks[label]
        print(f"\n  {label}  (risk={risk:.2f}%, {years:.1f}y)")
        m, tr = run(base_cls, d, sym, sp, adx, comm, ps, pv, risk=risk, entry_tf_seconds=TF_SECONDS[label])
        line(m, tr, "no filter (baseline)", years)
        m, tr = run(fresh_cls, d, sym, sp, adx, comm, ps, pv, maxmat=mm, risk=risk, entry_tf_seconds=TF_SECONDS[label])
        line(m, tr, f"fresh<={mm}", years)

    print("\n" + "=" * 104)
    print(" (2) YEARLY WALK-FORWARD -- params frozen, one row per calendar year (fresh-filter config)")
    print("=" * 104)
    series = {}
    for label, base_cls, fresh_cls, sym, sp, adx, comm, ps, pv, mm in CONFIGS:
        df = dfs[label]
        risk = risks[label]
        print(f"\n  {label}")
        yrs_list = sorted(df["timestamp"].dt.year.unique())
        pf_ok = tot = 0
        for y in yrs_list:
            dfy = df[df["timestamp"].dt.year == y].reset_index(drop=True)
            if len(dfy) < 1500:
                continue
            d = prepare_data(dfy)
            m, tr = run(fresh_cls, d, sym, sp, adx, comm, ps, pv, maxmat=mm, risk=risk, entry_tf_seconds=TF_SECONDS[label])
            if not m or m.get("trades", 0) < 5:
                print(f"    {y}: too few trades ({m.get('trades',0) if m else 0})")
                continue
            tot += 1
            ok = m["profit_factor"] > 1.0
            pf_ok += 1 if ok else 0
            mark = "" if ok else "  <-- PF<1"
            print(f"    {y}: n={m['trades']:>4}  PF={m['profit_factor']:>5.2f}  "
                  f"win%={m['win_rate']*100:>5.1f}  TotRet={m['total_return_pct']:>+7.1f}%{mark}")
        print(f"    -> years PF>1: {pf_ok}/{tot}")

        d_full = prepare_data(df)
        m_full, tr_full = run(fresh_cls, d_full, sym, sp, adx, comm, ps, pv, maxmat=mm, risk=risk, entry_tf_seconds=TF_SECONDS[label])
        mr = to_monthly(tr_full)
        if len(mr) >= 12:
            series[label] = mr

    print("\n" + "=" * 104)
    print(" (3) OUT-OF-SAMPLE -- threshold chosen on 1st half only, scored on 2nd half")
    print("=" * 104)
    for label, base_cls, fresh_cls, sym, sp, adx, comm, ps, pv, mm in CONFIGS:
        df = dfs[label]
        risk = risks[label]
        mid = df["timestamp"].iloc[len(df)//2]
        tr_df = df[df["timestamp"] <= mid].reset_index(drop=True)
        te_df = df[df["timestamp"] > mid].reset_index(drop=True)
        if len(tr_df) < 1500 or len(te_df) < 1500:
            continue
        d_tr, d_te = prepare_data(tr_df), prepare_data(te_df)
        y_te = (te_df["timestamp"].iloc[-1] - te_df["timestamp"].iloc[0]).days / 365.25

        best_th, best_pf = None, -1
        for th in [3, 5, 10, 20]:
            m, _ = run(fresh_cls, d_tr, sym, sp, adx, comm, ps, pv, maxmat=th, risk=risk, entry_tf_seconds=TF_SECONDS[label])
            if m and m.get("trades", 0) >= 30 and m["profit_factor"] > best_pf:
                best_pf, best_th = m["profit_factor"], th
        print(f"\n  {label}: train picked maturity<={best_th} (train PF={best_pf:.2f})")
        mb, trb = run(base_cls, d_te, sym, sp, adx, comm, ps, pv, risk=risk, entry_tf_seconds=TF_SECONDS[label])
        line(mb, trb, "TEST baseline", y_te)
        if best_th:
            mf, trf = run(fresh_cls, d_te, sym, sp, adx, comm, ps, pv, maxmat=best_th, risk=risk, entry_tf_seconds=TF_SECONDS[label])
            line(mf, trf, f"TEST maturity<={best_th}", y_te)

    print("\n" + "=" * 104)
    print(" (4) COMBINED PORTFOLIO -- differentiated risk (Gold 0.30%, BTC 1.00%, ETH 1.00%)")
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

    worst_month = allm.sum(axis=1).min()
    worst_month_date = allm.sum(axis=1).idxmin()
    print(f"\n  worst SINGLE MONTH if all three hit at once: {worst_month:+.2f}% "
          f"(in {worst_month_date.strftime('%Y-%m')})")


if __name__ == "__main__":
    main()
