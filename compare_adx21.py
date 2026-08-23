#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
C_wider / Mix_A / ADX20_TP7 / ADX18_TP7  — IS / VAL / OOS side-by-side
Usage: python3 compare_adx21.py --csv download/xauusd-m15-bid-2013-01-01-2026-06-10.csv
"""
from __future__ import annotations
import argparse, os, sys, time
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import DataLoader, prepare_data, BacktestEngine, FastHybridTrendPullback, compute_metrics

START    = 10_000.0
RISK_PCT = 0.30
SPREAD   = 0.10
COMM     = 3.50

PERIODS = {
    "IS":   ("2013-01-01", "2020-01-01"),
    "VAL":  ("2020-01-01", "2022-01-01"),
    "OOS":  ("2022-01-01", "2026-06-10"),
}

VARIANTS = [
    # label         sl    tp    adx
    ("C_wider",    2.5,  5.0,  22),
    ("Mix_A",      2.5,  7.0,  22),
    ("ADX20_TP7",  3.0,  7.0,  20),
    ("ADX18_TP7",  3.0,  7.0,  18),
]


def _cfg():
    c = ForexConfig()
    c.total_capital_usd    = START
    c.risk_per_trade_pct   = RISK_PCT
    c.partial_tp_atr       = 999.0
    c.partial_tp_frac      = 0.0
    c.move_sl_to_breakeven = False
    return c


def run_one(df_full, strat, sl, tp, adx, date_from, date_to):
    df = df_full.copy()
    if date_from: df = df[df["timestamp"] >= pd.Timestamp(date_from)]
    if date_to:   df = df[df["timestamp"] <  pd.Timestamp(date_to)]
    df = df.reset_index(drop=True)
    if len(df) < 1000: return None

    d = prepare_data(df)
    if d is None: return None

    strat.ADX_MIN = adx
    strat.sl_atr  = sl
    strat.tp_atr  = tp
    strat.trail_atr_mult       = 999.0
    strat.trail_activation_atr = 999.0
    strat.precompute(d)

    eng = BacktestEngine(d, _cfg(), strat, spread_price=SPREAD,
                         commission_per_lot=COMM, symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)

    ov     = compute_metrics(eng.trades, eng.equity_curve, START)
    pf     = ov.get("profit_factor", 0) or 0
    ret    = (ov.get("final_equity", START) - START) / START * 100
    dd     = ov.get("max_dd_pct", 0) or 0.001
    calmar = ret / dd
    n      = len(eng.trades)
    wins   = sum(1 for t in eng.trades if t.get("net_pnl", 0) > 0)
    win_pct = wins / n * 100 if n > 0 else 0.0
    return dict(pf=pf, calmar=calmar, dd=dd, ret=ret, trades=n, win_pct=win_pct)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    args = ap.parse_args()

    t0 = time.time()
    loader = DataLoader(log_fn=lambda *a, **k: None)
    cfg0 = ForexConfig(); cfg0.total_capital_usd = START
    df_full, _ = loader.load("XAUUSD", 99.0, cfg0, csv_path=args.csv, allow_synthetic=True)
    print(f"\n  Loaded {len(df_full):,} bars  ({time.time()-t0:.1f}s)\n")

    strat = FastHybridTrendPullback()

    # collect results
    results = {}
    for (lbl, sl, tp, adx) in VARIANTS:
        results[lbl] = {}
        for period, (d_from, d_to) in PERIODS.items():
            results[lbl][period] = run_one(df_full, strat, sl, tp, adx, d_from, d_to)

    # ── Period-by-period side-by-side table ───────────────────────────────────
    sep = "=" * 88
    period_labels = {"IS": "IS  2013-2019", "VAL": "VAL 2020-2021", "OOS": "OOS 2022-2026"}
    for period, plabel in period_labels.items():
        print(sep)
        print(f" {plabel}")
        print(sep)
        print(f"  {'Variant':<13} {'PF':>6} {'Calmar':>8} {'MaxDD':>6} {'Trades':>7} {'Win%':>6} {'Ret':>8}")
        print("  " + "-" * 68)
        for (lbl, sl, tp, adx) in VARIANTS:
            m = results[lbl].get(period)
            if not m:
                print(f"  {lbl:<13}  —")
                continue
            flag = ""
            if period == "OOS":
                if m["calmar"] >= 20:   flag = "  ✅"
                elif m["calmar"] >= 10: flag = "  🟡"
                else:                   flag = "  ❌"
            print(f"  {lbl:<13} {m['pf']:>6.3f} {m['calmar']:>8.1f} "
                  f"{m['dd']:>5.1f}% {m['trades']:>7,} {m['win_pct']:>5.1f}% "
                  f"{m['ret']:>+7.0f}%{flag}")
        print()

    # ── ADX20 vs ADX18 verdict ────────────────────────────────────────────────
    print(sep)
    adx20 = results.get("ADX20_TP7", {}).get("OOS")
    adx18 = results.get("ADX18_TP7", {}).get("OOS")
    if adx20 and adx18:
        diff = adx18["calmar"] - adx20["calmar"]
        pct  = diff / abs(adx20["calmar"]) * 100 if adx20["calmar"] else 0
        print(f"\n  ADX20_TP7  OOS Calmar = {adx20['calmar']:>6.1f}  PF={adx20['pf']:.3f}  Win={adx20['win_pct']:.1f}%")
        print(f"  ADX18_TP7  OOS Calmar = {adx18['calmar']:>6.1f}  PF={adx18['pf']:.3f}  Win={adx18['win_pct']:.1f}%  (diff {diff:+.1f}, {pct:+.0f}%)")
        print()
        if adx18["calmar"] >= 20 and adx18["calmar"] > adx20["calmar"]:
            print("  VERDICT: ADX18 OOS Calmar ≥ 20 AND > ADX20  →  worth deploying ✅")
        elif adx18["calmar"] >= 20:
            print("  VERDICT: ADX18 OOS Calmar ≥ 20 but not better than ADX20  →  no gain from switching 🟡")
        else:
            print("  VERDICT: ADX18 OOS Calmar < 20  →  ADX20 remains best threshold ❌")
    print(f"\n  Done in {time.time()-t0:.1f}s\n")
    print(sep)


if __name__ == "__main__":
    main()
