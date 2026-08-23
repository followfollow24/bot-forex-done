#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest_m5.py — ADX20_TP7 on M5 vs M15 side-by-side

Key M5 adaptations:
  H1_BARS = 12   (12 × 5min = 60min = 1 H1 bar)
  EMA_M15 = 60   (60 × 5min = 300min ≡ EMA20 × 15min on M15)
  MIN_BARS = 2500 (200 H1 bars × 12 + 100 buffer)
  MAX_HOLD = 192  (192 × 5min = 960min = 16h — same real time as M15 64 bars)

Usage:
  python3 backtest_m5.py \
      --csv5  download/xauusd-m5-bid-2013-01-01-2026-06-01.csv \
      --csv15 download/xauusd-m15-bid-2013-01-01-2026-06-10.csv
"""
from __future__ import annotations
import argparse, math, os, sys, time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forex_config import ForexConfig
from backtest_forex import (DataLoader, prepare_data, BacktestEngine,
                             FastHybridTrendPullback, compute_metrics)
from walk_forward_regime import build_windows, gold_return_pct, regime_tag, window_metrics

# ── Constants ────────────────────────────────────────────────────────────────
SPREAD       = 0.10
COMM         = 3.50
START        = 10_000.0
RISK_PCT     = 0.30

ADX_MIN      = 20
SL_ATR       = 3.0
TP_ATR       = 7.0

MAX_HOLD_M15 = 64    # 64 × 15min = 960min = 16h
MAX_HOLD_M5  = 192   # 192 × 5min = 960min = 16h  (same real time)

WINDOW_MONTHS = 6
PF_THRESHOLD  = 1.05
WIN_THRESHOLD = 15   # out of 27

IS_FROM  = "2013-01-01"
IS_TO    = "2020-01-01"
VAL_FROM = "2020-01-01"
VAL_TO   = "2022-01-01"
OOS_FROM = "2022-01-01"
OOS_TO   = "2026-06-01"


# =============================================================================
# M5 Strategy subclass
# =============================================================================
class FastHybridM5(FastHybridTrendPullback):
    """ADX20_TP7 logic adapted for M5 entry timeframe.

    H1 trend is resampled from M5 using 12 bars/H1 (unchanged H1 EMA periods).
    Entry EMA is 60-bar on M5 = same 300-minute lookback as EMA20 on M15.
    """

    H1_BARS = 12   # 12 × 5min = 1 H1 bar
    EMA_M15 = 60   # 60 × 5min = 300min ≡ EMA20 on M15

    # MIN_BARS: H1_EMA_SLOW (200) × H1_BARS (12) + buffer = 2500
    MIN_BARS = 200 * 12 + 100   # = 2500


def _cfg():
    c = ForexConfig()
    c.total_capital_usd    = START
    c.risk_per_trade_pct   = RISK_PCT
    c.partial_tp_atr       = 999.0
    c.partial_tp_frac      = 0.0
    c.move_sl_to_breakeven = False
    return c


def _make_strat_m15():
    s = FastHybridTrendPullback()
    s.ADX_MIN               = ADX_MIN
    s.sl_atr                = SL_ATR
    s.tp_atr                = TP_ATR
    s.trail_atr_mult        = 999.0
    s.trail_activation_atr  = 999.0
    return s


def _make_strat_m5():
    s = FastHybridM5()
    s.ADX_MIN               = ADX_MIN
    s.sl_atr                = SL_ATR
    s.tp_atr                = TP_ATR
    s.trail_atr_mult        = 999.0
    s.trail_activation_atr  = 999.0
    return s


def run_full(d, strat, max_hold):
    """Run full backtest, return (trades, metrics, avg_bars)."""
    strat.precompute(d)
    eng = BacktestEngine(d, _cfg(), strat, spread_price=SPREAD,
                         commission_per_lot=COMM, symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)
    ov = compute_metrics(eng.trades, eng.equity_curve, START)
    avg = (sum(t.get("bars_held", 0) for t in eng.trades) / len(eng.trades)
           if eng.trades else 0)
    return eng.trades, ov, avg


def windows_pf(trades, win_info):
    """Count windows with PF > 1 (fixed-notional normalization)."""
    eq = START
    for t in trades:
        t["_norm_pnl"] = t["net_pnl"] * (START / eq) if eq > 0 else t["net_pnl"]
        t["_entry_dt"] = pd.Timestamp(t["entry_ts"])
        eq = t["equity_after"]

    win_ok = n_all = 0
    for wi in win_info:
        wt = [t for t in trades if wi["start"] <= t["_entry_dt"] < wi["end"]]
        m  = window_metrics(wt, START)
        if m["trades"] == 0:
            continue
        n_all += 1
        pf = m["pf"]
        if pf is not None and (math.isinf(pf) or pf > 1.0):
            win_ok += 1
    return win_ok, n_all


def period_slice(trades, pfrom, pto):
    """Metrics for trades in a date sub-period."""
    ts = [t for t in trades if pfrom <= t["entry_ts"][:10] < pto]
    if not ts:
        return None
    eq = [START]
    for t in ts:
        eq.append(eq[-1] + t["net_pnl"])
    ret  = (eq[-1] - START) / START * 100
    peak = START; dd = 0
    for e in eq:
        if e > peak: peak = e
        d2 = (peak - e) / peak * 100
        if d2 > dd: dd = d2
    dd    = dd or 0.001
    cal   = ret / dd
    gw    = sum(t["net_pnl"] for t in ts if t["net_pnl"] > 0)
    gl    = abs(sum(t["net_pnl"] for t in ts if t["net_pnl"] <= 0))
    pf    = gw / gl if gl > 0 else float("inf")
    wins  = sum(1 for t in ts if t["net_pnl"] > 0)
    win_p = wins / len(ts) * 100
    return dict(n=len(ts), pf=pf, ret=ret, dd=dd, calmar=cal, win_pct=win_p)


def load_data(csv_path):
    loader = DataLoader(log_fn=lambda *a, **k: None)
    cfg0 = ForexConfig(); cfg0.total_capital_usd = START
    df, _ = loader.load("XAUUSD", 99.0, cfg0, csv_path=csv_path, allow_synthetic=True)
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv5",  required=True,  help="M5 CSV path")
    ap.add_argument("--csv15", required=True,  help="M15 CSV path")
    args = ap.parse_args()
    t0 = time.time()

    W = 96
    print()
    print("=" * W)
    print(f"  ADX20_TP7  M5 vs M15  —  SL={SL_ATR}×ATR  TP={TP_ATR}×ATR  ADX≥{ADX_MIN}")
    print(f"  M5 config:  H1_BARS=12  EMA_entry=60 bars (=300min same as EMA20 on M15)")
    print(f"  MaxHold: M15={MAX_HOLD_M15} bars (16h)  M5={MAX_HOLD_M5} bars (16h)")
    print("=" * W)

    # ── Load M15 ──────────────────────────────────────────────────────────────
    print(f"\n  [1/2] Loading M15 data ...", flush=True)
    t1 = time.time()
    df15 = load_data(args.csv15)
    d15  = prepare_data(df15)
    print(f"  [1/2] M15: {len(df15):,} bars  "
          f"{df15['timestamp'].iloc[0].date()} → {df15['timestamp'].iloc[-1].date()}  "
          f"({time.time()-t1:.1f}s)", flush=True)

    # ── Load M5 ───────────────────────────────────────────────────────────────
    print(f"  [2/2] Loading M5  data ...", flush=True)
    t2 = time.time()
    df5  = load_data(args.csv5)
    d5   = prepare_data(df5)
    print(f"  [2/2] M5 : {len(df5):,} bars  "
          f"{df5['timestamp'].iloc[0].date()} → {df5['timestamp'].iloc[-1].date()}  "
          f"({time.time()-t2:.1f}s)", flush=True)

    # ── Build windows (using M15 dates as reference) ──────────────────────────
    windows15 = build_windows(df15["timestamp"].iloc[0], df15["timestamp"].iloc[-1], WINDOW_MONTHS)
    win_info  = [dict(start=ws, end=we,
                      gold_ret=gold_return_pct(df15, ws, we),
                      regime=regime_tag(gold_return_pct(df15, ws, we)))
                 for ws, we in windows15]
    print(f"\n  Walk-forward: {len(windows15)} windows × {WINDOW_MONTHS}mo")

    # ── Run M15 ───────────────────────────────────────────────────────────────
    print(f"\n  [M15] Running ADX20_TP7 on M15 ...", end="", flush=True)
    t3 = time.time()
    strat15 = _make_strat_m15()
    trades15, ov15, avg15 = run_full(d15, strat15, MAX_HOLD_M15)
    win_ok15, n_all15 = windows_pf(trades15, win_info)
    print(f"  {len(trades15):,} trades  ({time.time()-t3:.1f}s)", flush=True)

    # ── Run M5 ────────────────────────────────────────────────────────────────
    print(f"  [M5 ] Running ADX20_TP7 on M5  ...", end="", flush=True)
    t4 = time.time()
    strat5 = _make_strat_m5()
    trades5, ov5, avg5 = run_full(d5, strat5, MAX_HOLD_M5)
    win_ok5, n_all5 = windows_pf(trades5, win_info)
    print(f"  {len(trades5):,} trades  ({time.time()-t4:.1f}s)", flush=True)

    # ── Summary: 27-window comparison ─────────────────────────────────────────
    def row(label, trades, ov, avg, win_ok, n_all):
        ret  = (ov.get("final_equity", START) - START) / START * 100
        dd   = ov.get("max_dd_pct", 0) or 0.001
        cal  = ret / dd
        pf   = ov.get("profit_factor", 0) or 0
        wins = sum(1 for t in trades if t.get("net_pnl", 0) > 0)
        wp   = wins / len(trades) * 100 if trades else 0
        pass_ = pf > PF_THRESHOLD and win_ok > WIN_THRESHOLD
        flag  = "  ✅ PASS" if pass_ else "  ❌ FAIL"
        return label, len(trades), pf, cal, dd, win_ok, n_all, wp, avg, flag

    print()
    print("=" * W)
    print(f"  {'Variant':<18} {'Trades':>7} {'PF':>6} {'Calmar':>7} {'MaxDD':>6} "
          f"{'Win/N':>7} {'Win%':>6} {'avg_bars':>9}  Verdict")
    print("  " + "-" * (W-2))

    for label, trades, ov, avg, win_ok, n_all in [
        ("ADX20_TP7 (M15)", trades15, ov15, avg15, win_ok15, n_all15),
        ("ADX20_TP7 (M5)",  trades5,  ov5,  avg5,  win_ok5,  n_all5),
    ]:
        lbl, n, pf, cal, dd, wo, na, wp, avg_, flag = row(
            label, trades, ov, avg, win_ok, n_all)
        print(f"  {lbl:<18} {n:>7,} {pf:>6.3f} {cal:>7.1f} {dd:>5.1f}% "
              f"{wo:>3}/{na:<3} {wp:>5.1f}% {avg_:>9.1f}{flag}")

    # ── Period breakdown ───────────────────────────────────────────────────────
    PERIODS = [
        ("IS  2013-2019", IS_FROM,  IS_TO),
        ("VAL 2020-2021", VAL_FROM, VAL_TO),
        ("OOS 2022-2026", OOS_FROM, OOS_TO),
    ]
    print()
    print("=" * W)
    print("  PERIOD BREAKDOWN")
    print("=" * W)
    for plabel, pfrom, pto in PERIODS:
        print(f"\n  [{plabel}]")
        print(f"  {'Variant':<18} {'Trades':>7} {'PF':>6} {'Calmar':>8} "
              f"{'MaxDD':>6} {'Win%':>6} {'Ret%':>8}")
        print("  " + "-" * 68)
        for label, trades, *_ in [
            ("ADX20_TP7 (M15)", trades15, ov15, avg15, win_ok15, n_all15),
            ("ADX20_TP7 (M5)",  trades5,  ov5,  avg5,  win_ok5,  n_all5),
        ]:
            m = period_slice(trades, pfrom, pto)
            if not m:
                print(f"  {label:<18}  —"); continue
            flag = ""
            if plabel.startswith("OOS"):
                flag = "  ✅" if m["calmar"] >= 20 else ("  🟡" if m["calmar"] >= 10 else "  ❌")
            print(f"  {label:<18} {m['n']:>7,} {m['pf']:>6.3f} {m['calmar']:>8.1f} "
                  f"{m['dd']:>5.1f}% {m['win_pct']:>5.1f}% {m['ret']:>+7.0f}%{flag}")

    # ── Verdict ───────────────────────────────────────────────────────────────
    print()
    print("=" * W)
    pf5  = ov5.get("profit_factor", 0) or 0
    pf15 = ov15.get("profit_factor", 0) or 0
    m5_oos  = period_slice(trades5,  OOS_FROM, OOS_TO)
    m15_oos = period_slice(trades15, OOS_FROM, OOS_TO)

    pass_wf = pf5 > PF_THRESHOLD and win_ok5 > WIN_THRESHOLD
    if pass_wf:
        print(f"\n  M5 walk-forward: PASS (PF={pf5:.3f} > {PF_THRESHOLD}, Win={win_ok5}/{n_all5} > {WIN_THRESHOLD})")
        if m5_oos and m5_oos["calmar"] >= 20:
            print(f"  M5 OOS Calmar = {m5_oos['calmar']:.1f} ≥ 20  →  deploy as separate instance ✅")
        elif m5_oos and m5_oos["calmar"] >= 10:
            print(f"  M5 OOS Calmar = {m5_oos['calmar']:.1f} ≥ 10  →  marginal, monitor closely 🟡")
        else:
            cal_str = f"{m5_oos['calmar']:.1f}" if m5_oos else "N/A"
            print(f"  M5 OOS Calmar = {cal_str} < 10  →  M15 remains better timeframe ❌")
    else:
        print(f"\n  M5 walk-forward: FAIL (PF={pf5:.3f}, Win={win_ok5}/{n_all5})")
        print(f"  M15 remains the production timeframe.")

    if m5_oos and m15_oos:
        diff = m5_oos["calmar"] - m15_oos["calmar"]
        print(f"\n  OOS Calmar: M15={m15_oos['calmar']:.1f}  M5={m5_oos['calmar']:.1f}  "
              f"diff={diff:+.1f}")

    print(f"\n  Total time: {time.time()-t0:.1f}s")
    print("=" * W)


if __name__ == "__main__":
    main()
