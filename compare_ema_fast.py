#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
compare_ema_fast.py — เทียบ EMA_H1_FAST variants ผ่าน 27-window WF + IS/VAL/OOS

Variants (SL=3.0, TP=7.0, ADX=20, EMA_SLOW=200 fixed):
  EMA50_200  (baseline — ADX20_TP7)
  EMA21_200
  EMA34_200
  EMA75_200

Usage:
  python3 compare_ema_fast.py --csv download/xauusd-m15-bid-2013-01-01-2026-06-10.csv
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import time

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forex_config import ForexConfig
from backtest_forex import (DataLoader, prepare_data, BacktestEngine,
                             FastHybridTrendPullback, compute_metrics)
from walk_forward_regime import (build_windows, gold_return_pct, regime_tag,
                                  window_metrics)

# ── Constants ─────────────────────────────────────────────────────────────────
SPREAD   = 0.10
COMM     = 3.50
START    = 10_000.0
RISK_PCT = 0.30

SL_ATR   = 3.0
TP_ATR   = 7.0
ADX_MIN  = 20
EMA_SLOW = 200
WINDOW_MONTHS = 6

# IS/VAL/OOS periods (same as oos_validation.py)
PERIODS = {
    "IS":  ("2013-01-01", "2020-01-01"),
    "VAL": ("2020-01-01", "2022-01-01"),
    "OOS": ("2022-01-01", "2026-06-10"),
}

# (label, ema_fast)
VARIANTS = [
    ("EMA50_200", 50),   # baseline
    ("EMA21_200", 21),
    ("EMA34_200", 34),
    ("EMA75_200", 75),
]


def _cfg():
    c = ForexConfig()
    c.total_capital_usd    = START
    c.risk_per_trade_pct   = RISK_PCT
    c.partial_tp_atr       = 999.0
    c.partial_tp_frac      = 0.0
    c.move_sl_to_breakeven = False
    return c


def run_on_df(df_slice: pd.DataFrame, strat: FastHybridTrendPullback) -> dict:
    """รัน backtest บน df_slice — คืน metrics dict."""
    if len(df_slice) < strat.MIN_BARS + 10:
        return dict(profit_factor=0, calmar=0, trades=0, final_equity=START, max_dd_pct=0.01)
    d = prepare_data(df_slice.reset_index(drop=True))
    if d is None:
        return dict(profit_factor=0, calmar=0, trades=0, final_equity=START, max_dd_pct=0.01)
    strat.sl_atr               = SL_ATR
    strat.tp_atr               = TP_ATR
    strat.trail_atr_mult       = 999.0
    strat.trail_activation_atr = 999.0
    eng = BacktestEngine(d, _cfg(), strat, spread_price=SPREAD,
                         commission_per_lot=COMM, symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=True)
    m = compute_metrics(eng.trades, eng.equity_curve, START)
    ret = (m.get("final_equity", START) - START) / START * 100
    dd  = m.get("max_dd_pct", 0) or 0.01
    m["calmar"] = ret / dd
    m["trades"] = len(eng.trades)
    return m


def run_wf(df: pd.DataFrame, strat: FastHybridTrendPullback):
    """27-window walk-forward — คืน (pf_wins, n_windows, all_trades)."""
    windows = build_windows(df["timestamp"].iloc[0], df["timestamp"].iloc[-1], WINDOW_MONTHS)
    # full-history run for overall trades
    d_full = prepare_data(df.reset_index(drop=True))
    strat.sl_atr               = SL_ATR
    strat.tp_atr               = TP_ATR
    strat.trail_atr_mult       = 999.0
    strat.trail_activation_atr = 999.0
    strat.precompute(d_full)
    eng = BacktestEngine(d_full, _cfg(), strat, spread_price=SPREAD,
                         commission_per_lot=COMM, symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)
    all_trades = eng.trades
    overall    = compute_metrics(all_trades, eng.equity_curve, START)

    # normalize per-trade for window scoring
    eq_before = START
    for t in all_trades:
        t["_norm_pnl"] = (t["net_pnl"] * (START / eq_before)) if eq_before > 0 else t["net_pnl"]
        t["_entry_dt"] = pd.Timestamp(t["entry_ts"])
        eq_before = t["equity_after"]

    win_info = []
    for ws, we in windows:
        ret = gold_return_pct(df, ws, we)
        win_info.append(dict(start=ws, end=we, regime=regime_tag(ret)))

    pf_wins = 0
    n_valid = 0
    for wi in win_info:
        wt = [t for t in all_trades if wi["start"] <= t["_entry_dt"] < wi["end"]]
        m = window_metrics(wt, START)
        if m["trades"] == 0:
            continue
        n_valid += 1
        pf = m["pf"]
        if pf is not None and (math.isinf(pf) or pf > 1.0):
            pf_wins += 1

    ret_full = (overall.get("final_equity", START) - START) / START * 100
    dd_full  = overall.get("max_dd_pct", 0) or 0.01
    overall["calmar"]     = ret_full / dd_full
    overall["trades"]     = len(all_trades)

    return pf_wins, n_valid, all_trades, overall


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    args = ap.parse_args()

    t0 = time.time()
    print()
    print("=" * 90)
    print(" EMA_H1_FAST COMPARISON — EMA21/34/50/75 vs EMA200 (fixed)")
    print(f" SL={SL_ATR} TP={TP_ATR} ADX≥{ADX_MIN}  spread={SPREAD}  comm={COMM}")
    print("=" * 90)

    # ── โหลด CSV ครั้งเดียว ────────────────────────────────────────────────────
    print("  [load] อ่าน CSV ...", flush=True)
    loader = DataLoader(log_fn=lambda *a, **k: None)
    cfg0 = ForexConfig(); cfg0.total_capital_usd = START
    df, _ = loader.load("XAUUSD", 99.0, cfg0, csv_path=args.csv, allow_synthetic=True)
    print(f"  [load] {len(df):,} bars  {df['timestamp'].iloc[0].date()} → "
          f"{df['timestamp'].iloc[-1].date()}  ({time.time()-t0:.1f}s)", flush=True)

    strat = FastHybridTrendPullback()
    strat.ADX_MIN  = ADX_MIN
    strat.EMA_H1_SLOW = EMA_SLOW

    results = []
    N = len(VARIANTS)
    for idx, (label, ema_fast) in enumerate(VARIANTS, 1):
        strat.EMA_H1_FAST = ema_fast
        strat.MIN_BARS    = EMA_SLOW * strat.H1_BARS + 50

        print(f"  [{idx}/{N}] {label:<14} (EMA{ema_fast}/{EMA_SLOW}) ", end="", flush=True)
        tv = time.time()

        # ── WF ────────────────────────────────────────────────────────────────
        pf_wins, n_win, all_trades, ov_full = run_wf(df, strat)

        # ── IS/VAL/OOS ────────────────────────────────────────────────────────
        period_metrics = {}
        for period, (pfrom, pto) in PERIODS.items():
            df_s = df[(df["timestamp"] >= pd.Timestamp(pfrom)) &
                      (df["timestamp"] <  pd.Timestamp(pto))].copy()
            strat2 = FastHybridTrendPullback()
            strat2.ADX_MIN     = ADX_MIN
            strat2.EMA_H1_FAST = ema_fast
            strat2.EMA_H1_SLOW = EMA_SLOW
            strat2.MIN_BARS    = EMA_SLOW * strat2.H1_BARS + 50
            period_metrics[period] = run_on_df(df_s, strat2)

        results.append(dict(
            label=label, ema_fast=ema_fast,
            pf_wins=pf_wins, n_win=n_win,
            ov=ov_full, trades=ov_full["trades"],
            pm=period_metrics,
        ))
        print(f"trades={ov_full['trades']:,}  WF={pf_wins}/{n_win}  "
              f"OOS_Calmar={period_metrics['OOS']['calmar']:.1f}  "
              f"({time.time()-tv:.1f}s)", flush=True)

    print()

    # ── ตารางสรุป ──────────────────────────────────────────────────────────────
    base = results[0]
    base_ov  = base["ov"]
    base_ret = (base_ov.get("final_equity", START) - START) / START * 100
    base_dd  = base_ov.get("max_dd_pct", 0) or 0.01
    base_cal = base_ret / base_dd

    print("=" * 90)
    print(" WALK-FORWARD SUMMARY (Full History 2013→2026)")
    print("=" * 90)
    print(f"  {'Variant':<14} | {'EMA':>5} | {'PF':>6} {'Calmar':>7} | {'Win/WF':>8} | "
          f"{'Trades':>7} {'vs BASE':>8}")
    print("  " + "-" * 70)
    for r in results:
        ov  = r["ov"]
        ret = (ov.get("final_equity", START) - START) / START * 100
        dd  = ov.get("max_dd_pct", 0) or 0.01
        cal = ret / dd
        pf  = ov.get("profit_factor", 0)
        trades_diff = r["trades"] - base["trades"]
        diff_str = f"{trades_diff:+d}" if r is not base else "—"
        wf_str = f"{r['pf_wins']}/{r['n_win']}"
        base_tag = " ← BASE" if r is base else ""
        print(f"  {r['label']:<14} | {r['ema_fast']:>5} | {pf:>6.3f} {cal:>7.1f} | "
              f"{wf_str:>8} | {r['trades']:>7,} {diff_str:>8}{base_tag}")

    print()
    print("=" * 90)
    print(" IS / VAL / OOS BREAKDOWN")
    print("=" * 90)
    print(f"  {'Variant':<14} | {'IS Calmar':>10} | {'VAL Calmar':>11} | "
          f"{'OOS Calmar':>11} {'vs BASE':>8} | {'IS trades':>10} {'OOS trades':>11}")
    print("  " + "-" * 90)

    base_oos_cal = base["pm"]["OOS"]["calmar"]
    for r in results:
        pm = r["pm"]
        oos_cal = pm["OOS"]["calmar"]
        vs_base = oos_cal - base_oos_cal
        vs_str  = f"{vs_base:+.1f}" if r is not base else "—"
        base_tag = " ← BASE" if r is base else ""
        print(f"  {r['label']:<14} | {pm['IS']['calmar']:>10.1f} | {pm['VAL']['calmar']:>11.1f} | "
              f"{oos_cal:>11.1f} {vs_str:>8} | "
              f"{pm['IS']['trades']:>10,} {pm['OOS']['trades']:>11,}{base_tag}")

    print()
    print("=" * 90)
    print(f" KEY METRICS TABLE (OOS Calmar baseline={base_oos_cal:.1f})")
    print("=" * 90)
    print(f"  {'Variant':<14} | {'PF':>6} {'Calmar':>8} | {'Win/27':>7} | "
          f"{'Trades':>7} | {'OOS Calmar':>11} {'vs BASE':>8}")
    print("  " + "-" * 75)
    for r in results:
        ov  = r["ov"]
        ret = (ov.get("final_equity", START) - START) / START * 100
        dd  = ov.get("max_dd_pct", 0) or 0.01
        cal = ret / dd
        pf  = ov.get("profit_factor", 0)
        oos_cal = r["pm"]["OOS"]["calmar"]
        vs_base = oos_cal - base_oos_cal
        vs_str  = f"{vs_base:+.1f}" if r is not base else "—"
        trades_diff = r["trades"] - base["trades"]
        diff_str = f"({trades_diff:+d})" if r is not base else ""
        wf_str = f"{r['pf_wins']}/{r['n_win']}"
        base_tag = " ← BASE" if r is base else ""
        print(f"  {r['label']:<14} | {pf:>6.3f} {cal:>8.1f} | {wf_str:>7} | "
              f"{r['trades']:>7,} {diff_str:<8} | {oos_cal:>11.1f} {vs_str:>8}{base_tag}")

    print()
    print(f"  Total elapsed: {time.time()-t0:.1f}s")
    print()


if __name__ == "__main__":
    main()
