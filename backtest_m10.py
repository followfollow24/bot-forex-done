#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest_m10.py — ADX20_TP7 on M5 / M10 / M15 side-by-side
Load M5 once, resample to M10 (and M15), run walk-forward.

M10 parameters:
  H1_BARS = 6   (6 × 10min = 60min)
  EMA_M15 = 30  (30 × 10min = 300min ≡ EMA20 on M15)
  MIN_BARS = 1300
  MAX_HOLD = 96 bars  (96 × 10min = 960min = 16h)
"""
from __future__ import annotations
import os, sys, time, math
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forex_config import ForexConfig
from backtest_forex import (prepare_data, BacktestEngine,
                             FastHybridTrendPullback, compute_metrics)
from walk_forward_regime import build_windows, gold_return_pct, regime_tag, window_metrics
from backtest_m5 import (FastHybridM5, windows_pf, period_slice,
                          _cfg, _make_strat_m15, _make_strat_m5)

# ── Constants ─────────────────────────────────────────────────────────────────
CSV5     = "download/xauusd-m5-bid-2013-01-01-2026-06-01.csv"

SPREAD   = 0.10
COMM     = 3.50
START    = 10_000.0
RISK_PCT = 0.30

ADX_MIN  = 20
SL_ATR   = 3.0
TP_ATR   = 7.0

MAX_HOLD_M15 = 64   # 64 × 15min = 960min = 16h
MAX_HOLD_M10 = 96   # 96 × 10min = 960min = 16h
MAX_HOLD_M5  = 192  # 192 × 5min = 960min = 16h

WINDOW_MONTHS = 6
PF_THRESHOLD  = 1.05
WIN_THRESHOLD = 15

IS_FROM  = "2013-01-01"; IS_TO  = "2020-01-01"
VAL_FROM = "2020-01-01"; VAL_TO = "2022-01-01"
OOS_FROM = "2022-01-01"; OOS_TO = "2026-06-01"

YEARS = list(range(2013, 2027))


# =============================================================================
# M10 strategy subclass
# =============================================================================
class FastHybridM10(FastHybridTrendPullback):
    """ADX20_TP7 adapted for M10 bars."""
    H1_BARS  = 6    # 6 × 10min = 60min
    EMA_M15  = 30   # 30 × 10min = 300min ≡ EMA20 on M15
    MIN_BARS = 200 * 6 + 100   # = 1300


def _make_strat_m10():
    s = FastHybridM10()
    s.ADX_MIN              = ADX_MIN
    s.sl_atr               = SL_ATR
    s.tp_atr               = TP_ATR
    s.trail_atr_mult       = 999.0
    s.trail_activation_atr = 999.0
    return s


# =============================================================================
# Resample helpers
# =============================================================================
AGG = {"open": "first", "high": "max", "low": "min",
       "close": "last", "volume": "sum"}

def resample_df(df5: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Resample M5 DataFrame to a coarser timeframe."""
    df = (df5.set_index("timestamp")
             .resample(rule)
             .agg(AGG)
             .dropna(subset=["open", "close"])
             .reset_index())
    df = df[df["close"].notna()].copy()
    return df


# =============================================================================
# Backtest runner
# =============================================================================
def run_full(d, strat):
    strat.precompute(d)
    eng = BacktestEngine(d, _cfg(), strat, spread_price=SPREAD,
                         commission_per_lot=COMM, symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)
    ov  = compute_metrics(eng.trades, eng.equity_curve, START)
    avg = (sum(t.get("bars_held", 0) for t in eng.trades) / len(eng.trades)
           if eng.trades else 0)
    return eng.trades, ov, avg


def year_slice(trades, year: int):
    y = str(year)
    ts = [t for t in trades
          if t["entry_ts"][:4] == y or t.get("exit_ts", "")[:4] == y]
    ts_entry = [t for t in trades if t["entry_ts"][:4] == y]
    if not ts_entry:
        return None
    eq = [START]
    for t in ts_entry:
        eq.append(eq[-1] + t["net_pnl"])
    ret  = (eq[-1] - START) / START * 100
    peak = START; dd = 0.0
    for e in eq:
        if e > peak: peak = e
        d2 = (peak - e) / peak * 100
        if d2 > dd: dd = d2
    dd    = dd or 0.001
    cal   = ret / dd
    gw    = sum(t["net_pnl"] for t in ts_entry if t["net_pnl"] > 0)
    gl    = abs(sum(t["net_pnl"] for t in ts_entry if t["net_pnl"] <= 0))
    pf    = gw / gl if gl > 0 else float("inf")
    wins  = sum(1 for t in ts_entry if t["net_pnl"] > 0)
    wp    = wins / len(ts_entry) * 100
    return dict(n=len(ts_entry), pf=pf, ret=ret, dd=dd, calmar=cal, win_pct=wp)


# =============================================================================
# main
# =============================================================================
def main():
    t0  = time.time()
    W   = 100
    DIR = os.path.dirname(os.path.abspath(__file__))
    csv = os.path.join(DIR, CSV5)

    print()
    print("=" * W)
    print(f"  ADX20_TP7  M5 / M10 / M15  —  SL={SL_ATR}×ATR  TP={TP_ATR}×ATR  ADX≥{ADX_MIN}")
    print(f"  MaxHold: M15={MAX_HOLD_M15} bars  M10={MAX_HOLD_M10} bars  M5={MAX_HOLD_M5} bars  (all = 16h real time)")
    print("=" * W)

    # ── 1. Load M5 once ───────────────────────────────────────────────────────
    print(f"\n  [1/3] Loading M5 CSV ...", flush=True)
    t1 = time.time()
    df5 = pd.read_csv(csv, parse_dates=["timestamp"])
    if "volume" not in df5.columns:
        df5["volume"] = 1.0
    print(f"  [1/3] M5 raw: {len(df5):,} bars  "
          f"{df5['timestamp'].iloc[0].date()} → {df5['timestamp'].iloc[-1].date()}  "
          f"({time.time()-t1:.1f}s)", flush=True)

    # ── 2. Resample to M10 and M15 ────────────────────────────────────────────
    print(f"  [2/3] Resampling M5 → M10 & M15 ...", end="", flush=True)
    t2 = time.time()
    df10 = resample_df(df5, "10min")
    df15 = resample_df(df5, "15min")
    print(f"  M10: {len(df10):,}  M15: {len(df15):,}  ({time.time()-t2:.1f}s)")
    print(f"        M5→M10 ratio: {len(df5)/len(df10):.2f}x  "
          f"M5→M15 ratio: {len(df5)/len(df15):.2f}x  (expected ~2.0×, ~3.0×)")

    # ── 3. Prepare data dicts ─────────────────────────────────────────────────
    print(f"  [3/3] Building indicator arrays ...", end="", flush=True)
    t3 = time.time()
    d5  = prepare_data(df5)
    d10 = prepare_data(df10)
    d15 = prepare_data(df15)
    print(f"  ({time.time()-t3:.1f}s)")

    # ── 4. Build walk-forward windows ─────────────────────────────────────────
    windows_list = build_windows(df5["timestamp"].iloc[0], df5["timestamp"].iloc[-1], WINDOW_MONTHS)
    win_info = [dict(start=ws, end=we,
                     gold_ret=gold_return_pct(df5, ws, we),
                     regime=regime_tag(gold_return_pct(df5, ws, we)))
                for ws, we in windows_list]
    print(f"\n  Walk-forward: {len(windows_list)} windows × {WINDOW_MONTHS}mo")

    # ── 5. Run backtests ──────────────────────────────────────────────────────
    variants = [
        ("M15_ADX20", d15, _make_strat_m15(), MAX_HOLD_M15, len(df15)),
        ("M10_ADX20", d10, _make_strat_m10(), MAX_HOLD_M10, len(df10)),
        ("M5_ADX20",  d5,  _make_strat_m5(),  MAX_HOLD_M5,  len(df5)),
    ]
    results = {}
    for name, d, strat, max_hold, n_bars in variants:
        strat.MAX_HOLD = max_hold
        print(f"  [{name}] Running on {n_bars:,} bars ...", end="", flush=True)
        t_ = time.time()
        trades, ov, avg = run_full(d, strat)
        win_ok, n_all = windows_pf(trades, win_info)
        results[name] = dict(trades=trades, ov=ov, avg=avg, win_ok=win_ok, n_all=n_all)
        print(f"  {len(trades):,} trades  ({time.time()-t_:.1f}s)", flush=True)

    # ── 6. Summary table ──────────────────────────────────────────────────────
    print()
    print("=" * W)
    print("  FULL-PERIOD SUMMARY  (2013–2026)")
    print(f"  {'Variant':<14} {'Trades':>7} {'PF':>6} {'Calmar':>8} {'MaxDD%':>7} "
          f"{'Win/N':>7} {'Win%':>6} {'avg_bars':>9}")
    print("  " + "-" * (W - 2))
    for name, _ , __, max_hold, ___ in variants:
        r = results[name]
        trades, ov, avg, win_ok, n_all = (r["trades"], r["ov"], r["avg"],
                                           r["win_ok"], r["n_all"])
        ret = (ov.get("final_equity", START) - START) / START * 100
        dd  = ov.get("max_dd_pct", 0) or 0.001
        cal = ret / dd
        pf  = ov.get("profit_factor", 0) or 0
        wins = sum(1 for t in trades if t.get("net_pnl", 0) > 0)
        wp   = wins / len(trades) * 100 if trades else 0
        pass_ = pf > PF_THRESHOLD and win_ok > WIN_THRESHOLD
        flag  = "✅" if pass_ else "❌"
        print(f"  {name:<14} {len(trades):>7,} {pf:>6.3f} {cal:>8.1f} {dd:>6.1f}% "
              f"{win_ok:>3}/{n_all:<3} {wp:>5.1f}% {avg:>9.1f}  {flag}")

    # ── 7. Period breakdown ───────────────────────────────────────────────────
    PERIODS = [
        ("IS  2013–2019", IS_FROM,  IS_TO),
        ("VAL 2020–2021", VAL_FROM, VAL_TO),
        ("OOS 2022–2026", OOS_FROM, OOS_TO),
    ]
    print()
    print("=" * W)
    print("  PERIOD BREAKDOWN")
    for plabel, pfrom, pto in PERIODS:
        print(f"\n  [{plabel}]")
        print(f"  {'Variant':<14} {'Trades':>7} {'PF':>6} {'Calmar':>8} {'MaxDD%':>7} "
              f"{'Win%':>6} {'Ret%':>8}")
        print("  " + "-" * 62)
        for name, *_ in variants:
            m = period_slice(results[name]["trades"], pfrom, pto)
            if not m:
                print(f"  {name:<14}  —"); continue
            flag = ""
            if plabel.startswith("OOS"):
                flag = " ✅" if m["calmar"] >= 20 else (" 🟡" if m["calmar"] >= 10 else " ❌")
            print(f"  {name:<14} {m['n']:>7,} {m['pf']:>6.3f} {m['calmar']:>8.1f} "
                  f"{m['dd']:>6.1f}% {m['win_pct']:>5.1f}% {m['ret']:>+7.0f}%{flag}")

    # ── 8. Year-by-year (focus on 2014 and 2018) ─────────────────────────────
    print()
    print("=" * W)
    print("  YEAR-BY-YEAR  (MaxDD% / Ret%)")
    FOCUS_YEARS = [2013, 2014, 2015, 2016, 2017, 2018, 2019,
                   2020, 2021, 2022, 2023, 2024, 2025]
    hdr = f"  {'Year':<6}"
    for name, *_ in variants:
        hdr += f"  {name:<22}"
    print(hdr)
    sep = f"  {'':6}" + "  " + ("-" * 22 + "  ") * len(variants)
    print("  " + "-" * (W - 2))
    for yr in FOCUS_YEARS:
        row = f"  {yr:<6}"
        for name, *_ in variants:
            m = year_slice(results[name]["trades"], yr)
            if not m:
                row += f"  {'—':^22}"; continue
            flag = ""
            if yr in (2014, 2018):
                flag = "⚠️ " if m["dd"] >= 20 else "  "
            row += f"  {flag}DD={m['dd']:5.1f}% ret={m['ret']:+6.0f}%   "
        print(row)

    # ── 9. Final comparison table ─────────────────────────────────────────────
    print()
    print("=" * W)
    print("  DEPLOYMENT COMPARISON TABLE")
    print(f"  {'Variant':<14} {'PF':>6} {'Calmar':>8} {'Win/27':>8} {'Trades':>7} "
          f"{'OOS Calmar':>11}  Decision")
    print("  " + "-" * (W - 2))
    for name, *_ in variants:
        r = results[name]
        trades, ov, win_ok, n_all = r["trades"], r["ov"], r["win_ok"], r["n_all"]
        ret = (ov.get("final_equity", START) - START) / START * 100
        dd  = ov.get("max_dd_pct", 0) or 0.001
        cal = ret / dd
        pf  = ov.get("profit_factor", 0) or 0
        m_oos = period_slice(trades, OOS_FROM, OOS_TO)
        oos_cal = m_oos["calmar"] if m_oos else 0
        if oos_cal >= 20:
            decision = "Deploy ✅"
        elif oos_cal >= 10:
            decision = "Monitor 🟡"
        else:
            decision = "Skip ❌"
        m14 = year_slice(trades, 2014)
        m14_dd = m14["dd"] if m14 else 0
        safety = "  DD2014={:.0f}%".format(m14_dd)
        print(f"  {name:<14} {pf:>6.3f} {cal:>8.1f} {win_ok:>4}/{n_all:<3} "
              f"{len(trades):>7,} {oos_cal:>11.1f}  {decision}{safety}")

    print()
    m14_names = {}
    for name, *_ in variants:
        m14 = year_slice(results[name]["trades"], 2014)
        m14_names[name] = m14["dd"] if m14 else 0
    best_2014 = min(m14_names, key=m14_names.get)
    print(f"  2014 MaxDD:  " + "   ".join(f"{n}={d:.0f}%" for n, d in m14_names.items()))
    print(f"  Safest 2014: {best_2014} (DD={m14_names[best_2014]:.0f}%)")

    print(f"\n  Total elapsed: {time.time()-t0:.1f}s")
    print("=" * W)


if __name__ == "__main__":
    main()
