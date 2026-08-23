#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 scaled_exit_test.py — Comprehensive Test Suite: Scaled Exit Strategy
================================================================================
 กลยุทธ์ "Scaled Exit + Adaptive Trail" — รวมข้อดีทุก variant เดิม:
   ✅ SL=2.5 ATR          (Mix_A: best drawdown control)
   ✅ TP1=3.5 ATR, 40%    (C_wider: lock profit early → boost win-rate)
   ✅ Move SL → BE        (protect remaining from loss)
   ✅ Trail 2.5 ATR       (TrendRide: let winner run)
   ✅ Trail activates at 5.5 ATR (give trade room before trailing)
   ✅ Safety TP=9.0 ATR   (Calmar: max reward backstop)

 Spread minimum: 0.10 USD (1 pip floor for XAUUSD)

 Tests (ทำครบทุกรูปแบบ):
   Part 1 — Walk-forward 27 windows: Scaled Exit vs ทุก variant เดิม
   Part 2 — Sensitivity grid: tp1_atr × trail_activation_atr (16 combos)
   Part 3 — Stress test: spread scenarios [0.10, 0.28, 0.50, 0.85, 1.55]
   Part 4 — Detailed 27-window breakdown (per-window PF/DD/regime)
   Part 5 — Heatmap ASCII: Calmar vs tp1_atr × trail_act

 Run:
   python3 scaled_exit_test.py \
       --csv download/xauusd-m15-bid-2013-01-01-2026-06-10.csv
================================================================================
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forex_config import ForexConfig
from backtest_forex import (DataLoader, prepare_data, BacktestEngine,
                             FastHybridTrendPullback, compute_metrics)
from walk_forward_regime import (build_windows, gold_return_pct, regime_tag,
                                  window_metrics)

# ── Constants ─────────────────────────────────────────────────────────────────
START         = 10_000.0
RISK_PCT      = 0.30
BASE_COMM     = 3.50
WINDOW_MONTHS = 6
MIN_SPREAD    = 0.10     # สเปรดขั้นต่ำ 0.1 pip (XAUUSD 1pip=0.10 USD)

# ── Scaled Exit: default params ───────────────────────────────────────────────
SE_SL         = 2.5      # SL (ATR)
SE_TP1        = 3.5      # Partial TP1 (ATR) — close 40%
SE_TP1_FRAC   = 0.40     # 40% ปิดที่ TP1
SE_TRAIL_MULT = 2.5      # chandelier trail distance (ATR)
SE_TRAIL_ACT  = 5.5      # trail activates after +5.5 ATR profit
SE_SAFETY_TP  = 9.0      # safety full-TP backstop (ATR)

# ── Walk-forward: candidates เดิมทั้งหมด + Scaled Exit ───────────────────────
# (label, sl, tp, trail_mult, trail_act, partial_tp_atr, partial_tp_frac, move_be)
CANDIDATES = [
    ("C_wider 2.5/5",   2.5, 5.0,  999., 999., 999., 0.0, False),
    ("TP7  3.0/7",       3.0, 7.0,  999., 999., 999., 0.0, False),
    ("TP8  3.0/8",       3.0, 8.0,  999., 999., 999., 0.0, False),
    ("Calmar 2.0/8",     2.0, 8.0,  999., 999., 999., 0.0, False),
    ("TrendRide 2.5",    2.5, 8.0,  3.0,  3.0,  999., 0.0, False),
    ("Mix_A 2.5/7",      2.5, 7.0,  999., 999., 999., 0.0, False),
    ("Mix_B 3.0/5",      3.0, 5.0,  999., 999., 999., 0.0, False),
    # ── NEW ──
    ("ScaledExit★",      SE_SL, SE_SAFETY_TP, SE_TRAIL_MULT, SE_TRAIL_ACT,
                         SE_TP1, SE_TP1_FRAC, True),
]

# ── Sensitivity grid ──────────────────────────────────────────────────────────
TP1_GRID       = [3.0, 3.5, 4.0, 4.5]
TRAIL_ACT_GRID = [5.0, 5.5, 6.0, 6.5]

# ── Stress test spreads ───────────────────────────────────────────────────────
SPREAD_SCENARIOS = [
    ("Best    sprd=0.10", 0.10),   # floor minimum 0.1 pip
    ("Real    sprd=0.28", 0.28),   # actual observed (MT5 live 2026-06-20)
    ("Normal  sprd=0.50", 0.50),   # conservative realistic
    ("Tough   sprd=0.85", 0.85),   # edge-break threshold ~0.85
    ("Extreme sprd=1.55", 1.55),   # stress ceiling
]


# =============================================================================
# Helpers
# =============================================================================

def _cfg(partial_tp_atr=999., partial_tp_frac=0.0, move_be=False):
    c = ForexConfig()
    c.total_capital_usd    = START
    c.risk_per_trade_pct   = RISK_PCT
    c.partial_tp_atr       = max(partial_tp_atr, 0.1)
    c.partial_tp_frac      = partial_tp_frac
    c.move_sl_to_breakeven = move_be
    return c


def _run(d, strat, sl, tp, trail_mult, trail_act,
         partial_tp_atr=999., partial_tp_frac=0.0, move_be=False,
         spread=MIN_SPREAD, comm=BASE_COMM):
    spread = max(spread, MIN_SPREAD)          # enforce minimum spread floor
    strat.sl_atr               = sl
    strat.tp_atr               = tp
    strat.trail_atr_mult       = trail_mult
    strat.trail_activation_atr = trail_act
    cfg = _cfg(partial_tp_atr, partial_tp_frac, move_be)
    eng = BacktestEngine(d, cfg, strat,
                         spread_price=spread, commission_per_lot=comm,
                         symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)
    return eng.trades, eng.equity_curve


def _overall(trades, eq):
    m   = compute_metrics(trades, eq, START)
    ret = (m.get("final_equity", START) - START) / START * 100
    dd  = m.get("max_dd_pct", 0) or 0.01
    return dict(
        pf       = m.get("profit_factor", 0),
        ret      = ret,
        dd       = m.get("max_dd_pct", 0),
        calmar   = ret / dd,
        trades   = m.get("trades", 0),
        win_rate = m.get("win_rate", 0),
    )


def _attach_norm_pnl(trades):
    eq_before = START
    for t in trades:
        t["_norm_pnl"] = (t["net_pnl"] * (START / eq_before)) if eq_before > 0 else t["net_pnl"]
        t["_entry_dt"] = pd.Timestamp(t["entry_ts"])
        eq_before = t["equity_after"]
    return trades


def _windows_pf_gt1(trades, win_info):
    trades = _attach_norm_pnl(trades)
    pf_all = pf_ds = n_all = n_ds = 0
    for wi in win_info:
        wt = [t for t in trades if wi["start"] <= t["_entry_dt"] < wi["end"]]
        m  = window_metrics(wt, START)
        if m["trades"] == 0:
            continue
        n_all += 1
        is_ds = wi["regime"] in ("DOWN", "SIDEWAYS")
        if is_ds: n_ds += 1
        pf = m["pf"]
        if pf is not None and (math.isinf(pf) or pf > 1.0):
            pf_all += 1
            if is_ds: pf_ds += 1
    return pf_all, n_all, pf_ds, n_ds


def _hline(w=96): print("=" * w)
def _sep(w=96):   print("-" * w)


# =============================================================================
# Part 1 — Walk-forward 27 windows
# =============================================================================

def part1_walkforward(d, df, strat, win_info):
    _hline()
    print(" PART 1 — Walk-forward 27 windows: Scaled Exit vs ทุก variant เดิม")
    print(f"  spread={MIN_SPREAD} USD (floor), comm={BASE_COMM}/lot, "
          f"{WINDOW_MONTHS}-month windows")
    _hline()
    print(f"  {'Variant':<18} | {'PF':>6} {'Ret%':>7} {'MaxDD%':>6} {'Calmar':>7} "
          f"{'Trades':>7} {'WinR%':>6} | {'Win PF>1':>9} {'D+S PF>1':>9}")
    _sep()

    rows = []
    for (label, sl, tp, tm, ta, ptp, ptf, mbe) in CANDIDATES:
        t0 = time.time()
        trades, eq = _run(d, strat, sl, tp, tm, ta, ptp, ptf, mbe,
                          spread=MIN_SPREAD, comm=BASE_COMM)
        ov = _overall(trades, eq)
        pf_all, n_all, pf_ds, n_ds = _windows_pf_gt1(list(trades), win_info)
        rows.append(dict(label=label, ov=ov, pf_all=pf_all, n_all=n_all,
                         pf_ds=pf_ds, n_ds=n_ds))
        tag = " ★ NEW" if "★" in label else ""
        print(f"  {label:<18} | {ov['pf']:>6.3f} {ov['ret']:>+6.0f}% "
              f"{ov['dd']:>5.1f}% {ov['calmar']:>7.1f} "
              f"{ov['trades']:>7} {ov['win_rate']*100:>5.1f}% "
              f"| {pf_all:>4}/{n_all:<4} {pf_ds:>4}/{n_ds:<3}{tag}  "
              f"({time.time()-t0:.1f}s)")

    # winner summary
    best_cal = max(rows, key=lambda r: r["ov"]["calmar"])
    best_rob = max(rows, key=lambda r: r["pf_all"] / max(r["n_all"], 1))
    _sep()
    print(f"  Best Calmar : {best_cal['label']}  {best_cal['ov']['calmar']:.1f}")
    print(f"  Best Robust : {best_rob['label']}  {best_rob['pf_all']}/{best_rob['n_all']} windows")
    se = next(r for r in rows if "★" in r["label"])
    bench = rows[0]
    print(f"\n  ScaledExit vs C_wider (benchmark):")
    print(f"    Calmar:  {se['ov']['calmar']:+.1f} vs {bench['ov']['calmar']:.1f}  "
          f"(Δ {se['ov']['calmar']-bench['ov']['calmar']:+.1f})")
    print(f"    PF>1:    {se['pf_all']}/{se['n_all']} vs "
          f"{bench['pf_all']}/{bench['n_all']} windows")
    print(f"    D+S PF>1:{se['pf_ds']}/{se['n_ds']} vs "
          f"{bench['pf_ds']}/{bench['n_ds']} windows")
    return rows


# =============================================================================
# Part 2 — Sensitivity grid: tp1_atr × trail_activation_atr
# =============================================================================

def part2_sensitivity(d, strat):
    _hline()
    print(" PART 2 — Sensitivity Grid: tp1_atr × trail_activation_atr")
    print(f"  fixed: SL={SE_SL} ATR, TP1_frac={SE_TP1_FRAC*100:.0f}%, "
          f"trail_mult={SE_TRAIL_MULT} ATR, safety_TP={SE_SAFETY_TP} ATR")
    print(f"  spread={MIN_SPREAD} USD (floor), metric=Calmar")
    _hline()

    results = {}  # (tp1, ta) → overall metrics
    header_lbl = "tp1/trail_act"
    print(f"  {header_lbl:>14} | " +
          "  ".join(f"trail@{ta:.1f}" for ta in TRAIL_ACT_GRID))
    _sep(70)

    for tp1 in TP1_GRID:
        row_vals = []
        for ta in TRAIL_ACT_GRID:
            if ta < tp1:
                # trail must activate AFTER tp1 (otherwise logic nonsensical)
                row_vals.append(None)
                results[(tp1, ta)] = None
                continue
            trades, eq = _run(d, strat,
                               sl=SE_SL, tp=SE_SAFETY_TP,
                               trail_mult=SE_TRAIL_MULT, trail_act=ta,
                               partial_tp_atr=tp1, partial_tp_frac=SE_TP1_FRAC,
                               move_be=True,
                               spread=MIN_SPREAD, comm=BASE_COMM)
            ov = _overall(trades, eq)
            results[(tp1, ta)] = ov
            row_vals.append(ov["calmar"])

        vals_str = "  ".join(
            f"{v:>10.1f}" if v is not None else f"{'N/A':>10}" for v in row_vals
        )
        print(f"  TP1={tp1:.1f} ATR     | {vals_str}")

    # find best
    best_key = max((k for k, v in results.items() if v is not None),
                   key=lambda k: results[k]["calmar"])
    best_ov = results[best_key]
    _sep(70)
    print(f"  Best: TP1={best_key[0]} ATR, trail_act={best_key[1]} ATR → "
          f"Calmar={best_ov['calmar']:.1f}  PF={best_ov['pf']:.3f}  "
          f"Trades={best_ov['trades']}")
    return results, best_key


# =============================================================================
# Part 3 — Stress test: spread scenarios
# =============================================================================

def part3_stress(d, strat):
    _hline()
    print(" PART 3 — Stress Test: Spread Scenarios")
    print(f"  Scaled Exit params: SL={SE_SL} TP1={SE_TP1}(40%) "
          f"trail_act={SE_TRAIL_ACT} safety_TP={SE_SAFETY_TP}")
    print(f"  Compare vs Mix_A (2.5/7.0 no-partial) and C_wider (2.5/5.0)")
    _hline()

    strategies = [
        ("C_wider 2.5/5",  2.5, 5.0,  999., 999., 999., 0.0, False),
        ("Mix_A 2.5/7",    2.5, 7.0,  999., 999., 999., 0.0, False),
        ("ScaledExit★",    SE_SL, SE_SAFETY_TP, SE_TRAIL_MULT, SE_TRAIL_ACT,
                           SE_TP1, SE_TP1_FRAC, True),
    ]

    print(f"  {'Spread Scenario':<26} | " +
          " | ".join(f"{s[0]:<14}" for s in strategies))
    print(f"  {'':26} | " +
          " | ".join(f"{'PF':>5} {'Cal':>6}" for _ in strategies))
    _sep()

    for (scenario_name, sprd) in SPREAD_SCENARIOS:
        sprd = max(sprd, MIN_SPREAD)
        vals = []
        for (label, sl, tp, tm, ta, ptp, ptf, mbe) in strategies:
            trades, eq = _run(d, strat, sl, tp, tm, ta, ptp, ptf, mbe,
                              spread=sprd, comm=BASE_COMM)
            ov = _overall(trades, eq)
            vals.append(ov)
        row = f"  {scenario_name:<26} | "
        row += " | ".join(
            f"{v['pf']:>5.3f} {v['calmar']:>6.1f}" for v in vals
        )
        print(row)

    _sep()
    print("  Note: ค่าลบใน Calmar = strategy ขาดทุนที่ spread นั้น")


# =============================================================================
# Part 4 — Detailed 27-window breakdown (ScaledExit ★)
# =============================================================================

def part4_detail(d, df, strat, win_info):
    _hline()
    print(f" PART 4 — Detailed 27-window breakdown: ScaledExit★ vs Mix_A")
    print(f"  (spread={MIN_SPREAD} USD floor, fixed-notional per-window)")
    _hline()

    configs = [
        ("ScaledExit★", SE_SL, SE_SAFETY_TP, SE_TRAIL_MULT, SE_TRAIL_ACT,
                        SE_TP1, SE_TP1_FRAC, True),
        ("Mix_A 2.5/7", 2.5, 7.0, 999., 999., 999., 0.0, False),
    ]

    all_trades = {}
    for (label, sl, tp, tm, ta, ptp, ptf, mbe) in configs:
        trades, _ = _run(d, strat, sl, tp, tm, ta, ptp, ptf, mbe,
                         spread=MIN_SPREAD, comm=BASE_COMM)
        all_trades[label] = _attach_norm_pnl(list(trades))

    labels = [c[0] for c in configs]
    hdr = f"  {'#':>2} {'Window':<14} {'Regime':>8} {'GoldRet':>8} | "
    hdr += " | ".join(f"{lb:<14}{'PF':>6} {'DD%':>5}" for lb in labels)
    print(hdr)
    _sep()

    se_wins = mix_wins = tie = 0
    for wi_idx, wi in enumerate(win_info, 1):
        regime = wi["regime"]
        gold   = wi["gold_ret"]
        label_str = f"{wi['start'].strftime('%Y-%m')}–{wi['end'].strftime('%m')}"
        row = f"  {wi_idx:>2} {label_str:<14} {regime:>8} {gold:>+7.1f}% | "

        pf_vals = []
        for lb in labels:
            wt = [t for t in all_trades[lb]
                  if wi["start"] <= t["_entry_dt"] < wi["end"]]
            m = window_metrics(wt, START)
            pf   = m["pf"]   if m["trades"] > 0 else None
            dd   = m["max_dd_pct"] if m["trades"] > 0 else 0.0
            pf_str = f"{pf:.3f}" if pf is not None else " N/A"
            row += f"  {pf_str:>8} {dd:>5.1f}%  | "
            pf_vals.append(pf)

        # who wins this window?
        def safe(v): return v if v is not None else 0.0
        se_pf  = safe(pf_vals[0])
        mix_pf = safe(pf_vals[1])
        if abs(se_pf - mix_pf) < 0.01:
            win_tag = "≈"
            tie += 1
        elif se_pf > mix_pf:
            win_tag = "SE"
            se_wins += 1
        else:
            win_tag = "MX"
            mix_wins += 1
        print(row + win_tag)

    _sep()
    print(f"  Window wins: ScaledExit={se_wins}  Mix_A={mix_wins}  Tie={tie}")


# =============================================================================
# Part 5 — ASCII heatmap: Calmar by tp1_atr × trail_act
# =============================================================================

def part5_heatmap(results):
    _hline()
    print(" PART 5 — Calmar Heatmap: TP1 (rows) × TrailAct (cols)")
    _hline()

    all_vals = [v["calmar"] for v in results.values() if v is not None]
    if not all_vals:
        print("  (no data)")
        return
    lo, hi = min(all_vals), max(all_vals)
    rng = hi - lo if hi > lo else 1.0

    blocks = [" ", "░", "▒", "▓", "█"]

    print(f"  {'':10} | " + "  ".join(f"trail@{ta:.1f}" for ta in TRAIL_ACT_GRID))
    _sep(70)
    for tp1 in TP1_GRID:
        cells = []
        for ta in TRAIL_ACT_GRID:
            v = results.get((tp1, ta))
            if v is None:
                cells.append(f"  {'---':^8}")
            else:
                cal = v["calmar"]
                lvl = int((cal - lo) / rng * (len(blocks) - 1))
                lvl = max(0, min(len(blocks) - 1, lvl))
                cells.append(f"  {blocks[lvl]}{cal:>5.1f}   ")
        print(f"  TP1={tp1:.1f}  | " + "".join(cells))

    _sep(70)
    print(f"  Range: {lo:.1f} – {hi:.1f}  (█=high, ░=low)")
    best = max((k for k, v in results.items() if v is not None),
               key=lambda k: results[k]["calmar"])
    print(f"  Best cell: TP1={best[0]}, trail_act={best[1]}  "
          f"Calmar={results[best]['calmar']:.1f}")


# =============================================================================
# Main
# =============================================================================

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv",    required=True, help="M15 XAUUSD CSV path")
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--spread", type=float, default=MIN_SPREAD,
                    help=f"base spread (floor {MIN_SPREAD} USD)")
    ap.add_argument("--comm",   type=float, default=BASE_COMM)
    args = ap.parse_args()

    t0 = time.time()
    _hline()
    print(" SCALED EXIT TEST SUITE — Comprehensive Analysis")
    print(f" Strategy: SL={SE_SL}ATR | TP1={SE_TP1}ATR({SE_TP1_FRAC*100:.0f}%)→BE | "
          f"Trail={SE_TRAIL_MULT}ATR@+{SE_TRAIL_ACT}ATR | SafeTP={SE_SAFETY_TP}ATR")
    print(f" Min spread: {MIN_SPREAD} USD (0.1 pip floor) | comm={args.comm}/lot")
    _hline()

    # ── Load CSV once ─────────────────────────────────────────────────────────
    print("  [load] อ่าน CSV ...", flush=True)
    loader = DataLoader(log_fn=lambda *a, **k: None)
    cfg0 = ForexConfig(); cfg0.total_capital_usd = START
    df, _ = loader.load(args.symbol, 99.0, cfg0,
                        csv_path=args.csv, allow_synthetic=True)
    print(f"  [load] {len(df):,} bars  "
          f"{df['timestamp'].iloc[0]} → {df['timestamp'].iloc[-1]}  "
          f"({time.time()-t0:.1f}s)", flush=True)

    d = prepare_data(df)
    if d is None:
        print("[ERROR] prepare_data failed"); sys.exit(1)

    # ── Windows + regime once ─────────────────────────────────────────────────
    windows  = build_windows(df["timestamp"].iloc[0], df["timestamp"].iloc[-1], WINDOW_MONTHS)
    win_info = []
    for (ws, we) in windows:
        ret = gold_return_pct(df, ws, we)
        win_info.append(dict(start=ws, end=we, gold_ret=ret, regime=regime_tag(ret)))
    print(f"  [windows] {len(windows)} windows × {WINDOW_MONTHS} เดือน")

    # ── Precompute H1-trend/EMA20 once ───────────────────────────────────────
    print("  [precompute] H1-trend + EMA20 ...", flush=True)
    strat = FastHybridTrendPullback()
    strat.precompute(d)
    print(f"  [precompute] เสร็จ ({time.time()-t0:.1f}s)", flush=True)
    print()

    # ── Run all parts ─────────────────────────────────────────────────────────
    part1_walkforward(d, df, strat, win_info)
    print()
    sensitivity_results, best_key = part2_sensitivity(d, strat)
    print()
    part3_stress(d, strat)
    print()
    part4_detail(d, df, strat, win_info)
    print()
    part5_heatmap(sensitivity_results)

    _hline()
    print(f" ✅ เสร็จทุก part ใน {time.time()-t0:.0f}s")
    print(f" Best sensitivity params: TP1={best_key[0]} ATR, trail_act={best_key[1]} ATR")
    _hline()


if __name__ == "__main__":
    main()
