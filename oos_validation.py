#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IS/OOS Validation — ADX20_TP7 (SL=3.0, TP=7.0, ADX≥20)
=========================================================
Step 1: IS  2013-2019  — run all 4 variants, confirm ADX20_TP7 wins
Step 2: Lock params → ADX20_TP7
Step 3: VAL 2020-2021  — sanity check (Calmar drop > 50% = overfit signal)
Step 4: OOS 2022-2026  — blind test (real answer)

Usage:
    python3 oos_validation.py --csv download/xauusd-m15-bid-2013-01-01-2026-06-10.csv
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forex_config import ForexConfig
from backtest_forex import (DataLoader, prepare_data, BacktestEngine,
                             FastHybridTrendPullback, compute_metrics)

START    = 10_000.0
RISK_PCT = 0.30
SPREAD   = 0.10
COMM     = 3.50

PERIODS = {
    "IS":   ("2013-01-01", "2020-01-01"),   # 7yr train
    "VAL":  ("2020-01-01", "2022-01-01"),   # 2yr validate
    "OOS":  ("2022-01-01", "2026-06-10"),   # 4yr blind
    "Full": ("2013-01-01", "2026-06-10"),   # full history
}

ALL_VARIANTS = [
    # label,          sl,   tp,   trail_mult, trail_act, adx_min
    ("C_wider",       2.5,  5.0,  999.0, 999.0, 22),
    ("Mix_A",         2.5,  7.0,  999.0, 999.0, 22),
    ("TP7",           3.0,  7.0,  999.0, 999.0, 22),
    ("ADX20_TP7",     3.0,  7.0,  999.0, 999.0, 20),
]
SELECTED = "ADX20_TP7"


def _cfg():
    c = ForexConfig()
    c.total_capital_usd    = START
    c.risk_per_trade_pct   = RISK_PCT
    c.partial_tp_atr       = 999.0
    c.partial_tp_frac      = 0.0
    c.move_sl_to_breakeven = False
    return c


def run_backtest(d_full_df, full_d, strat, label, sl, tp, tm, ta, adx_min,
                 date_from, date_to):
    """Backtest single variant on a date-sliced period. Returns metrics dict."""
    df = d_full_df.copy()
    if date_from:
        df = df[df["timestamp"] >= pd.Timestamp(date_from)]
    if date_to:
        df = df[df["timestamp"] < pd.Timestamp(date_to)]
    df = df.reset_index(drop=True)

    if len(df) < 1000:
        return None

    d = prepare_data(df)
    if d is None:
        return None

    if strat.ADX_MIN != adx_min:
        strat.ADX_MIN = adx_min
        strat.precompute(d)
    else:
        strat.precompute(d)

    strat.sl_atr               = sl
    strat.tp_atr               = tp
    strat.trail_atr_mult       = tm
    strat.trail_activation_atr = ta

    eng = BacktestEngine(d, _cfg(), strat, spread_price=SPREAD,
                         commission_per_lot=COMM, symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)

    ov = compute_metrics(eng.trades, eng.equity_curve, START)
    pf     = ov.get("profit_factor", 0) or 0
    ret    = (ov.get("final_equity", START) - START) / START * 100
    dd     = ov.get("max_dd_pct", 0) or 0.001
    calmar = ret / dd
    trades = eng.trades
    n      = len(trades)
    wins   = sum(1 for t in trades if t.get("net_pnl", 0) > 0)
    win_pct = wins / n * 100 if n > 0 else 0

    start_dt = df["timestamp"].iloc[0].strftime("%Y-%m-%d")
    end_dt   = df["timestamp"].iloc[-1].strftime("%Y-%m-%d")

    return dict(pf=pf, calmar=calmar, dd=dd, ret=ret,
                trades=n, win_pct=win_pct,
                start=start_dt, end=end_dt, bars=len(df))


def print_divider(w=90):
    print("=" * w)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    args = ap.parse_args()

    t0 = time.time()
    print()
    print_divider()
    print(" IS/OOS VALIDATION — ADX20_TP7 (SL=3.0, TP=7.0, ADX≥20)")
    print_divider()

    # ── load CSV once ──────────────────────────────────────────────────────────
    print("  [load] อ่าน CSV ...", flush=True)
    loader = DataLoader(log_fn=lambda *a, **k: None)
    cfg0 = ForexConfig(); cfg0.total_capital_usd = START
    df_full, _ = loader.load("XAUUSD", 99.0, cfg0, csv_path=args.csv,
                              allow_synthetic=True)
    print(f"  [load] {len(df_full):,} bars  "
          f"{df_full['timestamp'].iloc[0].date()} → "
          f"{df_full['timestamp'].iloc[-1].date()}  ({time.time()-t0:.1f}s)")
    print()

    strat = FastHybridTrendPullback()

    # ══════════════════════════════════════════════════════════════════════════
    # STEP 1 — IS period: รัน all 4 variants, confirm selection
    # ══════════════════════════════════════════════════════════════════════════
    IS_FROM, IS_TO = PERIODS["IS"]
    print(f"  STEP 1 — IS ({IS_FROM} → {IS_TO}): เลือก variant ดีสุด")
    print(f"  {'Variant':<14} {'PF':>6} {'Ret':>8} {'MaxDD':>6} {'Calmar':>8} "
          f"{'Trades':>7} {'Win%':>6}")
    print("  " + "-" * 65)

    is_results = {}
    for (lbl, sl, tp, tm, ta, adx) in ALL_VARIANTS:
        m = run_backtest(df_full, None, strat, lbl, sl, tp, tm, ta, adx,
                         IS_FROM, IS_TO)
        is_results[lbl] = m
        if m:
            sel = " ← selected" if lbl == SELECTED else ""
            print(f"  {lbl:<14} {m['pf']:>6.3f} {m['ret']:>+7.0f}% "
                  f"{m['dd']:>5.1f}% {m['calmar']:>8.1f} "
                  f"{m['trades']:>7,} {m['win_pct']:>5.1f}%{sel}")

    is_calmar = is_results[SELECTED]["calmar"] if is_results.get(SELECTED) else 0
    print()

    # ══════════════════════════════════════════════════════════════════════════
    # STEP 2 — Lock params
    # ══════════════════════════════════════════════════════════════════════════
    print(f"  STEP 2 — Params LOCKED: {SELECTED}  (ห้าม re-optimize หลังจากนี้)")
    print()

    # ══════════════════════════════════════════════════════════════════════════
    # STEP 3 — VAL
    # ══════════════════════════════════════════════════════════════════════════
    sel_var = next(v for v in ALL_VARIANTS if v[0] == SELECTED)
    lbl, sl, tp, tm, ta, adx = sel_var

    VAL_FROM, VAL_TO = PERIODS["VAL"]
    print(f"  STEP 3 — VAL ({VAL_FROM} → {VAL_TO}): sanity check")
    val_m = run_backtest(df_full, None, strat, lbl, sl, tp, tm, ta, adx,
                         VAL_FROM, VAL_TO)
    val_calmar = val_m["calmar"] if val_m else 0
    calmar_drop = (1 - val_calmar / is_calmar) * 100 if is_calmar > 0 else 0
    overfit_flag = "⚠️  overfit signal" if calmar_drop > 50 else "✅ pass"
    print(f"  Calmar IS={is_calmar:.1f} → VAL={val_calmar:.1f}  "
          f"(drop {calmar_drop:+.0f}%)  {overfit_flag}")
    print()

    # ══════════════════════════════════════════════════════════════════════════
    # STEP 4 — OOS (blind test)
    # ══════════════════════════════════════════════════════════════════════════
    OOS_FROM, OOS_TO = PERIODS["OOS"]
    print(f"  STEP 4 — OOS ({OOS_FROM} → {OOS_TO}): blind test  ← real answer")
    oos_m = run_backtest(df_full, None, strat, lbl, sl, tp, tm, ta, adx,
                         OOS_FROM, OOS_TO)

    FULL_FROM, FULL_TO = PERIODS["Full"]
    full_m = run_backtest(df_full, None, strat, lbl, sl, tp, tm, ta, adx,
                          FULL_FROM, FULL_TO)

    # ══════════════════════════════════════════════════════════════════════════
    # SUMMARY TABLE
    # ══════════════════════════════════════════════════════════════════════════
    print()
    print_divider()
    print(f" SUMMARY — {SELECTED}")
    print_divider()
    print(f"  {'Period':<8} {'Years':<7} {'PF':>6} {'Calmar':>8} "
          f"{'MaxDD':>6} {'Trades':>7} {'Win%':>6} {'Ret':>8}")
    print("  " + "-" * 68)

    rows = [
        ("IS",   "7yr",  is_results.get(SELECTED)),
        ("VAL",  "2yr",  val_m),
        ("OOS",  "4yr",  oos_m),
        ("Full", "13yr", full_m),
    ]
    for period, yrs, m in rows:
        if m:
            print(f"  {period:<8} {yrs:<7} {m['pf']:>6.3f} {m['calmar']:>8.1f} "
                  f"{m['dd']:>5.1f}% {m['trades']:>7,} {m['win_pct']:>5.1f}% "
                  f"{m['ret']:>+7.0f}%")

    # ── decision ──────────────────────────────────────────────────────────────
    if oos_m:
        oc = oos_m["calmar"]
        print()
        if oc >= 20:
            verdict = f"✅  OOS Calmar={oc:.1f} ≥ 20 → edge likely real, consider deploy"
        elif oc >= 10:
            verdict = f"🟡  OOS Calmar={oc:.1f} 10–20 → marginal, proceed with caution"
        else:
            verdict = f"❌  OOS Calmar={oc:.1f} < 10 → overfit, do not deploy"
        print(f"  VERDICT: {verdict}")

    print()
    print(f"  เสร็จใน {time.time()-t0:.1f}s")
    print_divider()


if __name__ == "__main__":
    main()
