#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
compare_sideways.py — BB_MeanRev vs ADX20_TP7 — 27-window walk-forward
=======================================================================
เทียบ 2 strategies ผ่าน 27 windows (6เดือน/window ครอบ 13.5 ปี):
  BB_MeanRev  : Bollinger Band Mean Reversion (ADX<20, sideways)
  ADX20_TP7   : HybridTrendPullback (ADX>=20, trending) — baseline

Usage:
  python3 compare_sideways.py --csv download/xauusd-m15-bid-2013-01-01-2026-06-10.csv
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
from strategy_sideways import BBMeanRev, SessionRange, SessionBreakout
from walk_forward_regime import build_windows, gold_return_pct, regime_tag, window_metrics

SPREAD   = 0.10
COMM     = 3.50
START    = 10_000.0
RISK_PCT = 0.30
MAX_HOLD_TREND    = 64  # bars
MAX_HOLD_SIDEWAYS = 32  # bars
MAX_HOLD_SESSION  = 16  # bars
MAX_HOLD_SESSION6 = 24  # bars — 6-hour window for SB_v2/v3
WINDOW_MONTHS   = 6


def _cfg(max_hold: int):
    c = ForexConfig()
    c.total_capital_usd    = START
    c.risk_per_trade_pct   = RISK_PCT
    c.partial_tp_atr       = 999.0
    c.partial_tp_frac      = 0.0
    c.move_sl_to_breakeven = False
    c.max_hold_bars        = max_hold
    return c


def run_strategy(d, strat, max_hold: int):
    """Full backtest — returns trades, overall metrics, avg_bars."""
    eng = BacktestEngine(d, _cfg(max_hold), strat,
                         spread_price=SPREAD, commission_per_lot=COMM,
                         symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)
    ov       = compute_metrics(eng.trades, eng.equity_curve, START)
    avg_bars = (sum(t.get("bars_held", 0) for t in eng.trades) / len(eng.trades)
                if eng.trades else 0)
    return eng.trades, ov, avg_bars


def windows_stats(trades, win_info):
    """Count windows where PF>1 (all + DOWN/SIDEWAYS regime only)."""
    eq = START
    for t in trades:
        t["_norm_pnl"] = t["net_pnl"] * (START / eq) if eq > 0 else t["net_pnl"]
        t["_entry_dt"] = pd.Timestamp(t["entry_ts"])
        eq = t["equity_after"]

    pf_all = pf_ds = n_all = n_ds = 0
    for wi in win_info:
        wt = [t for t in trades if wi["start"] <= t["_entry_dt"] < wi["end"]]
        m  = window_metrics(wt, START)
        if m["trades"] == 0:
            continue
        n_all += 1
        is_ds = wi["regime"] in ("DOWN", "SIDEWAYS")
        if is_ds:
            n_ds += 1
        pf = m["pf"]
        if pf is not None and (math.isinf(pf) or pf > 1.0):
            pf_all += 1
            if is_ds:
                pf_ds += 1
    return pf_all, n_all, pf_ds, n_ds


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    args = ap.parse_args()

    t0 = time.time()
    print()
    print("=" * 100)
    print(" ADX20_TP7 | BB_MeanRev | SessionRange | SB_v1/v2/v3 — 27-window walk-forward")
    print("=" * 100)

    # ── Load CSV once ─────────────────────────────────────────────────────────
    print("  [load] อ่าน CSV ...", flush=True)
    loader = DataLoader(log_fn=lambda *a, **k: None)
    cfg0 = ForexConfig(); cfg0.total_capital_usd = START
    df, _  = loader.load("XAUUSD", 99.0, cfg0, csv_path=args.csv, allow_synthetic=True)
    print(f"  [load] {len(df):,} bars  "
          f"{df['timestamp'].iloc[0].date()} → {df['timestamp'].iloc[-1].date()}  "
          f"({time.time()-t0:.1f}s)", flush=True)

    d = prepare_data(df)
    if d is None:
        print("[ERROR] prepare_data failed"); sys.exit(1)

    # ── Windows + regime ──────────────────────────────────────────────────────
    windows  = build_windows(df["timestamp"].iloc[0], df["timestamp"].iloc[-1],
                             WINDOW_MONTHS)
    win_info = [dict(start=ws, end=we, gold_ret=gold_return_pct(df, ws, we),
                     regime=regime_tag(gold_return_pct(df, ws, we)))
                for (ws, we) in windows]
    print(f"  [windows] {len(windows)} windows × {WINDOW_MONTHS}mo", flush=True)

    # ── Precompute + run strategies ───────────────────────────────────────────
    results = []

    # 1. ADX20_TP7 (trend baseline)
    print("  [1/6] ADX20_TP7 (trend baseline) ...", end="", flush=True)
    t1 = time.time()
    trend_strat = FastHybridTrendPullback()
    trend_strat.ADX_MIN  = 20
    trend_strat.sl_atr   = 3.0
    trend_strat.tp_atr   = 7.0
    trend_strat.trail_atr_mult       = 999.0
    trend_strat.trail_activation_atr = 999.0
    trend_strat.precompute(d)
    trades_t, ov_t, avg_t = run_strategy(d, trend_strat, MAX_HOLD_TREND)
    pf_all_t, n_all_t, pf_ds_t, n_ds_t = windows_stats(trades_t, win_info)
    results.append(dict(label="ADX20_TP7", trades=trades_t, ov=ov_t,
                        avg_bars=avg_t, pf_all=pf_all_t, n_all=n_all_t,
                        pf_ds=pf_ds_t, n_ds=n_ds_t))
    print(f" {len(trades_t):,} trades  ({time.time()-t1:.1f}s)", flush=True)

    # 2. BB_MeanRev (sideways)
    print("  [2/6] BB_MeanRev (sideways) ...", end="", flush=True)
    t2 = time.time()
    bb_strat = BBMeanRev()
    bb_strat.precompute(d)
    trades_b, ov_b, avg_b = run_strategy(d, bb_strat, MAX_HOLD_SIDEWAYS)
    pf_all_b, n_all_b, pf_ds_b, n_ds_b = windows_stats(trades_b, win_info)
    results.append(dict(label="BB_MeanRev", trades=trades_b, ov=ov_b,
                        avg_bars=avg_b, pf_all=pf_all_b, n_all=n_all_b,
                        pf_ds=pf_ds_b, n_ds=n_ds_b))
    print(f" {len(trades_b):,} trades  ({time.time()-t2:.1f}s)", flush=True)

    # 3. SessionRange
    print("  [3/6] SessionRange (Asian→London fade) ...", end="", flush=True)
    t3 = time.time()
    sr_strat = SessionRange()
    sr_strat.precompute(d)
    trades_s, ov_s, avg_s = run_strategy(d, sr_strat, MAX_HOLD_SESSION)
    pf_all_s, n_all_s, pf_ds_s, n_ds_s = windows_stats(trades_s, win_info)
    results.append(dict(label="SessionRange", trades=trades_s, ov=ov_s,
                        avg_bars=avg_s, pf_all=pf_all_s, n_all=n_all_s,
                        pf_ds=pf_ds_s, n_ds=n_ds_s))
    print(f" {len(trades_s):,} trades  ({time.time()-t3:.1f}s)", flush=True)

    # 4–6. SessionBreakout variants
    # Precompute once — all variants share the same Asian range arrays.
    # Re-set params on instance before each run (precompute is cheap enough to repeat;
    # but since MOMENTUM_FILTER only changes entry logic, not arrays, skip repeat precompute).
    SB_VARIANTS = [
        # (label,    MOMENTUM_FILTER, TP_ATR_MULT, timeout_bars)
        ("SB_v1",   0.3,             0.5,          MAX_HOLD_SESSION),   # mf0.3 tp0.5 4h
        ("SB_v2",   0.3,             0.5,          MAX_HOLD_SESSION6),  # mf0.3 tp0.5 6h
        ("SB_v3",   0.5,             0.5,          MAX_HOLD_SESSION6),  # mf0.5 tp0.5 6h
    ]
    sbo_base = SessionBreakout()
    sbo_base.precompute(d)   # builds Asian range arrays once

    for idx, (vlabel, mf, tp_mult, timeout) in enumerate(SB_VARIANTS, 4):
        print(f"  [{idx}/6] {vlabel} (mf={mf} tp={tp_mult} t/o={timeout}bars) ...",
              end="", flush=True)
        tv = time.time()
        # Re-instantiate to avoid state bleed between runs, but copy precomputed arrays
        sbo = SessionBreakout()
        sbo.MOMENTUM_FILTER = mf
        sbo.TP_ATR_MULT     = tp_mult
        # Reuse precomputed arrays from sbo_base
        sbo._asian_high_arr = sbo_base._asian_high_arr
        sbo._asian_low_arr  = sbo_base._asian_low_arr
        sbo._hour_arr       = sbo_base._hour_arr
        sbo._date_arr       = sbo_base._date_arr
        # Sequential state fresh for this run
        sbo._last_bo_date = None
        sbo._bo_buy_done  = False
        sbo._bo_sell_done = False
        trades_v, ov_v, avg_v = run_strategy(d, sbo, timeout)
        pf_all_v, n_all_v, pf_ds_v, n_ds_v = windows_stats(trades_v, win_info)
        results.append(dict(label=vlabel, trades=trades_v, ov=ov_v,
                            avg_bars=avg_v, pf_all=pf_all_v, n_all=n_all_v,
                            pf_ds=pf_ds_v, n_ds=n_ds_v))
        print(f" {len(trades_v):,} trades  ({time.time()-tv:.1f}s)", flush=True)

    # ── Summary table ─────────────────────────────────────────────────────────
    print()
    print("=" * 100)
    print(" RESULTS — full 13yr history + 27-window robustness")
    print("=" * 100)
    print(f"  {'Strategy':<18} {'trades':>7} {'PF':>6} {'Ret%':>8} {'MaxDD':>6} "
          f"{'Calmar':>7} {'Win/Win':>9} {'D+S/D+S':>9} {'avg_bars':>9}")
    print("  " + "-" * 100)

    for r in results:
        ov  = r["ov"]
        ret = (ov.get("final_equity", START) - START) / START * 100
        dd  = ov.get("max_dd_pct", 0) or 0.01
        cal = ret / dd
        pf  = ov.get("profit_factor", 0) or 0
        wins_str = f"{r['pf_all']}/{r['n_all']}"
        ds_str   = f"{r['pf_ds']}/{r['n_ds']}"
        print(f"  {r['label']:<18} {len(r['trades']):>7,} {pf:>6.3f} {ret:>+7.0f}% "
              f"{dd:>5.1f}% {cal:>7.1f} {wins_str:>9} {ds_str:>9} {r['avg_bars']:>7.1f}")

    print()
    print("=" * 100)
    print(" PERIOD BREAKDOWN — IS / VAL / OOS")
    print("=" * 100)

    periods = [
        ("IS  2013-2020", "2013-01-01", "2020-01-01"),
        ("VAL 2020-2022", "2020-01-01", "2022-01-01"),
        ("OOS 2022-2026", "2022-01-01", "2026-06-10"),
    ]

    for (plabel, pfrom, pto) in periods:
        print(f"\n  [{plabel}]")
        print(f"  {'Strategy':<18} {'trades':>7} {'PF':>6} {'Ret%':>8} "
              f"{'MaxDD':>6} {'Calmar':>7}")
        print("  " + "-" * 60)
        for r in results:
            tslice = [t for t in r["trades"]
                      if pfrom <= t["entry_ts"][:10] < pto]
            if not tslice:
                print(f"  {r['label']:<18} {'—':>7}")
                continue
            eq_curve = [START]
            for t in tslice:
                eq_curve.append(eq_curve[-1] + t["net_pnl"])
            eq_arr = eq_curve
            pnl_total = eq_arr[-1] - START
            ret_pct   = pnl_total / START * 100
            peak = START; dd_max = 0
            for eq in eq_arr:
                if eq > peak: peak = eq
                dd = (peak - eq) / peak * 100
                if dd > dd_max: dd_max = dd
            dd_max = dd_max or 0.01
            calmar = ret_pct / dd_max
            gross_win  = sum(t["net_pnl"] for t in tslice if t["net_pnl"] > 0)
            gross_loss = abs(sum(t["net_pnl"] for t in tslice if t["net_pnl"] <= 0))
            pf2 = gross_win / gross_loss if gross_loss > 0 else float("inf")
            flag = ""
            if plabel.startswith("OOS"):
                if calmar >= 20: flag = "  ✅"
                elif calmar >= 10: flag = "  🟡"
                else: flag = "  ❌"
            print(f"  {r['label']:<18} {len(tslice):>7,} {pf2:>6.3f} "
                  f"{ret_pct:>+7.0f}% {dd_max:>5.1f}% {calmar:>7.1f}{flag}")

    print()
    print(f"  เสร็จใน {time.time()-t0:.1f}s")
    print("=" * 100)


if __name__ == "__main__":
    main()
