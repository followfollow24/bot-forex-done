#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
compare_m5_adx.py — M5 ADX18 vs ADX20 vs M15 ADX20 baseline
27-window walk-forward + IS/VAL/OOS + year-by-year for M5_ADX18

Usage:
  python3 compare_m5_adx.py \
      --csv5  download/xauusd-m5-bid-2013-01-01-2026-06-01.csv \
      --csv15 download/xauusd-m15-bid-2013-01-01-2026-06-10.csv
"""
from __future__ import annotations
import argparse, math, os, sys, time
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import (DataLoader, prepare_data, BacktestEngine,
                             FastHybridTrendPullback, compute_metrics)
from backtest_m5 import FastHybridM5
from walk_forward_regime import build_windows, gold_return_pct, regime_tag, window_metrics

SPREAD = 0.10; COMM = 3.50; START = 10_000.0; RISK_PCT = 0.30
MAX_HOLD_M15 = 64; MAX_HOLD_M5 = 192
WINDOW_MONTHS = 6
PF_THRESHOLD = 1.05; WIN_THRESHOLD = 15

VARIANTS = [
    # label            sl    tp   adx  tf
    ("M15_ADX20_TP7", 3.0, 7.0, 20, "15m"),
    ("M5_ADX20_TP7",  3.0, 7.0, 20, "5m"),
    ("M5_ADX18_TP7",  3.0, 7.0, 18, "5m"),
]

PERIODS = [
    ("IS",  "2013-01-01", "2020-01-01"),
    ("VAL", "2020-01-01", "2022-01-01"),
    ("OOS", "2022-01-01", "2026-06-01"),
]


def _cfg():
    c = ForexConfig()
    c.total_capital_usd   = START
    c.risk_per_trade_pct  = RISK_PCT
    c.partial_tp_atr      = 999.0
    c.partial_tp_frac     = 0.0
    c.move_sl_to_breakeven = False
    return c


def load(csv):
    loader = DataLoader(log_fn=lambda *a, **k: None)
    cfg0 = ForexConfig(); cfg0.total_capital_usd = START
    df, _ = loader.load("XAUUSD", 99.0, cfg0, csv_path=csv, allow_synthetic=True)
    return df


def run_full(d, strat, max_hold):
    strat.precompute(d)
    eng = BacktestEngine(d, _cfg(), strat, spread_price=SPREAD,
                         commission_per_lot=COMM, symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)
    ov  = compute_metrics(eng.trades, eng.equity_curve, START)
    avg = (sum(t.get("bars_held", 0) for t in eng.trades) / len(eng.trades)
           if eng.trades else 0)
    return eng.trades, ov, avg


def windows_pf(trades, win_info):
    eq = START
    for t in trades:
        t["_norm_pnl"] = t["net_pnl"] * (START / eq) if eq > 0 else t["net_pnl"]
        t["_entry_dt"] = pd.Timestamp(t["entry_ts"])
        eq = t["equity_after"]
    ok = n = 0
    for wi in win_info:
        wt = [t for t in trades if wi["start"] <= t["_entry_dt"] < wi["end"]]
        m  = window_metrics(wt, START)
        if m["trades"] == 0: continue
        n += 1
        pf = m["pf"]
        if pf is not None and (math.isinf(pf) or pf > 1.0): ok += 1
    return ok, n


def period_slice(trades, pfrom, pto):
    ts = [t for t in trades if pfrom <= t["entry_ts"][:10] < pto]
    if not ts: return None
    eq = [START]
    for t in ts: eq.append(eq[-1] + t["net_pnl"])
    ret  = (eq[-1] - START) / START * 100
    peak = START; dd = 0
    for e in eq:
        if e > peak: peak = e
        d2 = (peak - e) / peak * 100
        if d2 > dd: dd = d2
    dd   = dd or 0.001; cal = ret / dd
    gw   = sum(t["net_pnl"] for t in ts if t["net_pnl"] > 0)
    gl   = abs(sum(t["net_pnl"] for t in ts if t["net_pnl"] <= 0))
    pf   = gw / gl if gl > 0 else float("inf")
    wins = sum(1 for t in ts if t["net_pnl"] > 0)
    return dict(n=len(ts), pf=pf, ret=ret, dd=dd, calmar=cal,
                win_pct=wins/len(ts)*100)


def year_stats(trades, year):
    ts = [t for t in trades if t["entry_ts"].startswith(str(year))]
    if not ts: return None
    eq = [START]
    for t in ts: eq.append(eq[-1] + t["net_pnl"])
    ret  = (eq[-1] - START) / START * 100
    peak = START; dd = 0
    for e in eq:
        if e > peak: peak = e
        d2 = (peak - e) / peak * 100
        if d2 > dd: dd = d2
    dd = dd or 0.001
    return dict(n=len(ts), ret=ret, dd=dd, calmar=ret/dd)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv5",  required=True)
    ap.add_argument("--csv15", required=True)
    args = ap.parse_args()
    t0 = time.time()
    W = 100

    print(); print("=" * W)
    print("  M5 ADX18 vs ADX20 vs M15 ADX20  —  27-window + IS/VAL/OOS")
    print("=" * W)

    # ── Load ──────────────────────────────────────────────────────────────────
    print("\n  [load] M15 ...", end="", flush=True); t1=time.time()
    df15 = load(args.csv15); d15 = prepare_data(df15)
    print(f" {len(df15):,} bars  ({time.time()-t1:.1f}s)", flush=True)

    print("  [load] M5  ...", end="", flush=True); t2=time.time()
    df5  = load(args.csv5);  d5  = prepare_data(df5)
    print(f" {len(df5):,} bars  ({time.time()-t2:.1f}s)", flush=True)

    windows  = build_windows(df15["timestamp"].iloc[0], df15["timestamp"].iloc[-1], WINDOW_MONTHS)
    win_info = [dict(start=ws, end=we, gold_ret=gold_return_pct(df15, ws, we),
                     regime=regime_tag(gold_return_pct(df15, ws, we)))
                for ws, we in windows]
    print(f"  {len(windows)} windows × {WINDOW_MONTHS}mo\n")

    # ── Run all variants ──────────────────────────────────────────────────────
    results = {}
    for i, (lbl, sl, tp, adx, tf) in enumerate(VARIANTS, 1):
        d = d5 if tf == "5m" else d15
        mh = MAX_HOLD_M5 if tf == "5m" else MAX_HOLD_M15
        sc = FastHybridM5 if tf == "5m" else FastHybridTrendPullback
        strat = sc()
        strat.ADX_MIN = adx; strat.sl_atr = sl; strat.tp_atr = tp
        strat.trail_atr_mult = 999.0; strat.trail_activation_atr = 999.0
        print(f"  [{i}/{len(VARIANTS)}] {lbl} ...", end="", flush=True); tv=time.time()
        trades, ov, avg = run_full(d, strat, mh)
        ok, na = windows_pf(trades, win_info)
        results[lbl] = dict(trades=trades, ov=ov, avg=avg, ok=ok, na=na)
        print(f" {len(trades):,} trades  ({time.time()-tv:.1f}s)")

    print()

    # ── TABLE 1: 27-window summary ────────────────────────────────────────────
    print("=" * W)
    print("  TABLE 1 — 27-window walk-forward (full 2013-2026)")
    print("=" * W)
    print(f"  {'Variant':<18} {'Trades':>7} {'PF':>6} {'Calmar':>8} {'MaxDD':>6} "
          f"{'Win/N':>7} {'Win%':>6} {'avg_bars':>9}  Verdict")
    print("  " + "-"*(W-2))
    for lbl, _, _, _, _ in VARIANTS:
        r = results[lbl]
        ov = r["ov"]
        ret  = (ov.get("final_equity", START) - START) / START * 100
        dd   = ov.get("max_dd_pct", 0) or 0.001
        pf   = ov.get("profit_factor", 0) or 0
        cal  = ret / dd
        wins = sum(1 for t in r["trades"] if t.get("net_pnl", 0) > 0)
        wp   = wins / len(r["trades"]) * 100 if r["trades"] else 0
        ok, na = r["ok"], r["na"]
        pass_ = pf > PF_THRESHOLD and ok > WIN_THRESHOLD
        flag  = "  ✅ PASS" if pass_ else "  ❌ FAIL"
        print(f"  {lbl:<18} {len(r['trades']):>7,} {pf:>6.3f} {cal:>8.1f} {dd:>5.1f}% "
              f"{ok:>3}/{na:<3} {wp:>5.1f}% {r['avg']:>9.1f}{flag}")

    # ── TABLE 2: IS/VAL/OOS ───────────────────────────────────────────────────
    print()
    print("=" * W)
    print("  TABLE 2 — IS / VAL / OOS breakdown")
    print("=" * W)
    for pname, pfrom, pto in PERIODS:
        print(f"\n  [{pname}  {pfrom[:7]} → {pto[:7]}]")
        print(f"  {'Variant':<18} {'Trades':>7} {'PF':>6} {'Calmar':>8} "
              f"{'MaxDD':>6} {'Win%':>6} {'Ret%':>8}")
        print("  " + "-"*68)
        for lbl, *_ in VARIANTS:
            m = period_slice(results[lbl]["trades"], pfrom, pto)
            if not m: print(f"  {lbl:<18}  —"); continue
            flag = ""
            if pname == "OOS":
                flag = "  ✅" if m["calmar"] >= 20 else ("  🟡" if m["calmar"] >= 10 else "  ❌")
            print(f"  {lbl:<18} {m['n']:>7,} {m['pf']:>6.3f} {m['calmar']:>8.1f} "
                  f"{m['dd']:>5.1f}% {m['win_pct']:>5.1f}% {m['ret']:>+7.0f}%{flag}")

    # ── TABLE 3: Year-by-year M5_ADX18 vs M5_ADX20 vs M15_ADX20 ─────────────
    print()
    print("=" * W)
    print("  TABLE 3 — Year-by-year: M5_ADX18 vs M5_ADX20 vs M15_ADX20")
    print("=" * W)
    t20  = results["M15_ADX20_TP7"]["trades"]
    t5a  = results["M5_ADX20_TP7"]["trades"]
    t5b  = results["M5_ADX18_TP7"]["trades"]
    print(f"\n  {'Year':<6} | {'M15_ADX20':^26} | {'M5_ADX20':^26} | {'M5_ADX18':^26}")
    print(f"  {'':6} | {'Tr':>4} {'Ret%':>7} {'DD%':>6} {'Cal':>7} | "
                   f"{'Tr':>4} {'Ret%':>7} {'DD%':>6} {'Cal':>7} | "
                   f"{'Tr':>4} {'Ret%':>7} {'DD%':>6} {'Cal':>7}")
    print("  " + "-"*(W-2))

    for yr in range(2013, 2026):
        m15 = year_stats(t20, yr)
        m5a = year_stats(t5a, yr)
        m5b = year_stats(t5b, yr)

        def fmt(m):
            if not m: return f"{'—':>4} {'—':>7} {'—':>6} {'—':>7}"
            w = " ⚠" if m["dd"] > 20 else "  "
            return f"{m['n']:>4} {m['ret']:>+6.1f}% {m['dd']:>5.1f}%{w} {m['calmar']:>5.1f}"

        print(f"  {yr:<6} | {fmt(m15)} | {fmt(m5a)} | {fmt(m5b)}")

    # ── ADX18 vs ADX20 on M5 verdict ─────────────────────────────────────────
    print()
    print("=" * W)
    oos20 = period_slice(results["M5_ADX20_TP7"]["trades"], "2022-01-01", "2026-06-01")
    oos18 = period_slice(results["M5_ADX18_TP7"]["trades"], "2022-01-01", "2026-06-01")
    if oos20 and oos18:
        diff  = oos18["calmar"] - oos20["calmar"]
        n_diff = oos18["n"] - oos20["n"]
        print(f"\n  M5_ADX20_TP7 OOS: Calmar={oos20['calmar']:.1f}  PF={oos20['pf']:.3f}  "
              f"Trades={oos20['n']:,}  Win={oos20['win_pct']:.1f}%")
        print(f"  M5_ADX18_TP7 OOS: Calmar={oos18['calmar']:.1f}  PF={oos18['pf']:.3f}  "
              f"Trades={oos18['n']:,} (+{n_diff})  Win={oos18['win_pct']:.1f}%  "
              f"(Calmar diff {diff:+.1f})")
        print()
        if oos18["calmar"] >= 20 and oos18["calmar"] > oos20["calmar"]:
            print("  M5_ADX18 OOS Calmar ≥ 20 AND > ADX20  →  deploy m5adx18 ✅")
        elif oos18["calmar"] >= 20:
            print("  M5_ADX18 OOS Calmar ≥ 20 but ≤ ADX20  →  no gain from lowering ADX on M5 🟡")
        else:
            print("  M5_ADX18 OOS Calmar < 20  →  stick with M5_ADX20 ❌")

        # Check if 2014/2018 problem improved
        p14_20 = year_stats(t5a, 2014); p14_18 = year_stats(t5b, 2014)
        p18_20 = year_stats(t5a, 2018); p18_18 = year_stats(t5b, 2018)
        print(f"\n  2014/2018 problem check:")
        if p14_20 and p14_18:
            print(f"    2014: M5_ADX20={p14_20['ret']:+.1f}% (DD={p14_20['dd']:.1f}%)  "
                  f"M5_ADX18={p14_18['ret']:+.1f}% (DD={p14_18['dd']:.1f}%)", end="")
            print("  → better ✅" if p14_18["ret"] > p14_20["ret"] else "  → worse ❌")
        if p18_20 and p18_18:
            print(f"    2018: M5_ADX20={p18_20['ret']:+.1f}% (DD={p18_20['dd']:.1f}%)  "
                  f"M5_ADX18={p18_18['ret']:+.1f}% (DD={p18_18['dd']:.1f}%)", end="")
            print("  → better ✅" if p18_18["ret"] > p18_20["ret"] else "  → worse ❌")

    print(f"\n  Done in {time.time()-t0:.1f}s")
    print("=" * W)


if __name__ == "__main__":
    main()
