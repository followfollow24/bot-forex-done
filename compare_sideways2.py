#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
compare_sideways2.py — RSICross / BBConfirm / ATRRankRSI vs ADX20_TP7
27-window walk-forward  |  threshold: PF > 1.05 AND Win/27 > 15
Usage:
  python3 compare_sideways2.py --csv download/xauusd-m15-bid-2013-01-01-2026-06-10.csv
"""
from __future__ import annotations

import argparse, math, os, sys, time
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forex_config import ForexConfig
from backtest_forex import (DataLoader, prepare_data, BacktestEngine,
                             FastHybridTrendPullback, compute_metrics)
from strategy_sideways import RSICross, BBConfirm, ATRRankRSI
from walk_forward_regime import build_windows, gold_return_pct, regime_tag, window_metrics

SPREAD        = 0.10
COMM          = 3.50
START         = 10_000.0
RISK_PCT      = 0.30
WINDOW_MONTHS = 6

# Timeouts
MAX_HOLD_TREND = 64   # ADX20_TP7
MAX_HOLD_MR    = 32   # mean-reversion strategies (8 h)

PF_THRESHOLD   = 1.05
WIN_THRESHOLD  = 15   # out of 27


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
    eng = BacktestEngine(d, _cfg(max_hold), strat,
                         spread_price=SPREAD, commission_per_lot=COMM,
                         symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)
    ov       = compute_metrics(eng.trades, eng.equity_curve, START)
    avg_bars = (sum(t.get("bars_held", 0) for t in eng.trades) / len(eng.trades)
                if eng.trades else 0)
    return eng.trades, ov, avg_bars


def windows_stats(trades, win_info):
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


def period_stats(trades, pfrom, pto):
    tslice = [t for t in trades if pfrom <= t["entry_ts"][:10] < pto]
    if not tslice:
        return None
    eq = [START]
    for t in tslice:
        eq.append(eq[-1] + t["net_pnl"])
    ret    = (eq[-1] - START) / START * 100
    peak   = START; dd_max = 0
    for e in eq:
        if e > peak: peak = e
        dd = (peak - e) / peak * 100
        if dd > dd_max: dd_max = dd
    dd_max  = dd_max or 0.01
    calmar  = ret / dd_max
    gross_w = sum(t["net_pnl"] for t in tslice if t["net_pnl"] > 0)
    gross_l = abs(sum(t["net_pnl"] for t in tslice if t["net_pnl"] <= 0))
    pf      = gross_w / gross_l if gross_l > 0 else float("inf")
    return dict(n=len(tslice), pf=pf, ret=ret, dd=dd_max, calmar=calmar)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    args = ap.parse_args()
    t0 = time.time()

    W = 100
    print()
    print("=" * W)
    print(" RSICross | BBConfirm | ATRRankRSI  vs  ADX20_TP7 — 27-window walk-forward")
    print(f" Threshold: PF > {PF_THRESHOLD}  AND  Win/27 > {WIN_THRESHOLD}/27")
    print("=" * W)

    # ── Load ──────────────────────────────────────────────────────────────────
    print("  [load] อ่าน CSV ...", flush=True)
    loader = DataLoader(log_fn=lambda *a, **k: None)
    cfg0 = ForexConfig(); cfg0.total_capital_usd = START
    df, _ = loader.load("XAUUSD", 99.0, cfg0, csv_path=args.csv, allow_synthetic=True)
    print(f"  [load] {len(df):,} bars  "
          f"{df['timestamp'].iloc[0].date()} → {df['timestamp'].iloc[-1].date()}  "
          f"({time.time()-t0:.1f}s)", flush=True)

    d = prepare_data(df)
    if d is None:
        print("[ERROR] prepare_data failed"); sys.exit(1)

    windows  = build_windows(df["timestamp"].iloc[0], df["timestamp"].iloc[-1], WINDOW_MONTHS)
    win_info = [dict(start=ws, end=we, gold_ret=gold_return_pct(df, ws, we),
                     regime=regime_tag(gold_return_pct(df, ws, we)))
                for (ws, we) in windows]
    print(f"  [windows] {len(windows)} windows × {WINDOW_MONTHS}mo\n", flush=True)

    results = []

    # ── 1. ADX20_TP7 baseline ─────────────────────────────────────────────────
    print("  [1/4] ADX20_TP7 (trend baseline) ...", end="", flush=True)
    t1 = time.time()
    tr = FastHybridTrendPullback()
    tr.ADX_MIN = 20; tr.sl_atr = 3.0; tr.tp_atr = 7.0
    tr.trail_atr_mult = 999.0; tr.trail_activation_atr = 999.0
    tr.precompute(d)
    trd, ov, avg = run_strategy(d, tr, MAX_HOLD_TREND)
    pfa, na, pfd, nd = windows_stats(trd, win_info)
    results.append(dict(label="ADX20_TP7", trades=trd, ov=ov, avg=avg,
                        pfa=pfa, na=na, pfd=pfd, nd=nd))
    print(f" {len(trd):,} trades  ({time.time()-t1:.1f}s)", flush=True)

    # ── 2–4. New sideways candidates ─────────────────────────────────────────
    CANDIDATES = [
        ("RSICross",   RSICross(),   MAX_HOLD_MR),
        ("BBConfirm",  BBConfirm(),  MAX_HOLD_MR),
        ("ATRRankRSI", ATRRankRSI(), MAX_HOLD_MR),
    ]
    for idx, (lbl, strat, timeout) in enumerate(CANDIDATES, 2):
        print(f"  [{idx}/4] {lbl} ...", end="", flush=True)
        tv = time.time()
        strat.precompute(d)
        trd, ov, avg = run_strategy(d, strat, timeout)
        pfa, na, pfd, nd = windows_stats(trd, win_info)
        results.append(dict(label=lbl, trades=trd, ov=ov, avg=avg,
                            pfa=pfa, na=na, pfd=pfd, nd=nd))
        print(f" {len(trd):,} trades  ({time.time()-tv:.1f}s)", flush=True)

    # ── Summary table ─────────────────────────────────────────────────────────
    print()
    print("=" * W)
    print(" RESULTS — full 13yr + 27-window robustness")
    print("=" * W)
    print(f"  {'Strategy':<14} {'trades':>7} {'PF':>6} {'Ret%':>8} {'MaxDD':>6} "
          f"{'Calmar':>7} {'Win/27':>7} {'D+S/DS':>7} {'avg_b':>6}  Verdict")
    print("  " + "-" * W)

    for r in results:
        ov  = r["ov"]
        ret = (ov.get("final_equity", START) - START) / START * 100
        dd  = ov.get("max_dd_pct", 0) or 0.01
        cal = ret / dd
        pf  = ov.get("profit_factor", 0) or 0
        if r["label"] == "ADX20_TP7":
            verdict = "  BASELINE"
        else:
            pass_pf  = pf > PF_THRESHOLD
            pass_win = r["pfa"] > WIN_THRESHOLD
            if pass_pf and pass_win:
                verdict = "  ✅ PASS — proceed to IS/OOS"
            elif pass_pf or pass_win:
                verdict = "  🟡 PARTIAL"
            else:
                verdict = "  ❌ FAIL"
        print(f"  {r['label']:<14} {len(r['trades']):>7,} {pf:>6.3f} {ret:>+7.0f}% "
              f"{dd:>5.1f}% {cal:>7.1f} {r['pfa']:>3}/{r['na']:<3} "
              f"{r['pfd']:>3}/{r['nd']:<3} {r['avg']:>5.1f}{verdict}")

    # ── Period breakdown ──────────────────────────────────────────────────────
    PERIODS = [
        ("IS  2013-2020", "2013-01-01", "2020-01-01"),
        ("VAL 2020-2022", "2020-01-01", "2022-01-01"),
        ("OOS 2022-2026", "2022-01-01", "2026-06-10"),
    ]
    print()
    print("=" * W)
    print(" PERIOD BREAKDOWN")
    print("=" * W)
    for (plabel, pfrom, pto) in PERIODS:
        print(f"\n  [{plabel}]")
        print(f"  {'Strategy':<14} {'trades':>7} {'PF':>6} {'Ret%':>8} "
              f"{'MaxDD':>6} {'Calmar':>7}")
        print("  " + "-" * 55)
        for r in results:
            m = period_stats(r["trades"], pfrom, pto)
            if not m:
                print(f"  {r['label']:<14}  —")
                continue
            flag = ""
            if plabel.startswith("OOS"):
                flag = "  ✅" if m["calmar"] >= 20 else ("  🟡" if m["calmar"] >= 10 else "  ❌")
            print(f"  {r['label']:<14} {m['n']:>7,} {m['pf']:>6.3f} "
                  f"{m['ret']:>+7.0f}% {m['dd']:>5.1f}% {m['calmar']:>7.1f}{flag}")

    print()
    print(f"  เสร็จใน {time.time()-t0:.1f}s")
    print("=" * W)


if __name__ == "__main__":
    main()
