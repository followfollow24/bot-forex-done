#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 cost_sensitivity_weekly.py — Weekly Stats + Cost-Sensitivity Sweep
================================================================================
 สำหรับ variants ที่ผ่าน regime walk-forward แล้ว (เริ่มต้น: C_wider, D_letrun
 จาก walk_forward_regime.py — entry/trend logic เหมือนเดิมทุกประการ, ใช้
 FastHybridTrendPullback เพื่อความเร็ว, verified identical กับ original)

 ไม่ปรับพารามิเตอร์ใดๆ ของ strategy. ไม่ tune ไปหา return target — รายงานความจริง.

 [1] WEEKLY STATS (ต่อ variant ต่อ spread level)
     - แบ่ง trades เป็น ISO calendar week ตาม exit date
     - normalize net_pnl ของแต่ละ trade กลับไปที่ momentum ของ $10k คงที่
       (norm_pnl = net_pnl * start_cash / equity_before_trade) เพื่อไม่ให้
       สัปดาห์หลังๆ (equity compound โตแล้ว) ดู "ใหญ่" เกินจริงเทียบสัปดาห์แรกๆ
     - weekly_return% = sum(norm_pnl ในสัปดาห์) / start_cash * 100
     - รายงาน: % weeks green, avg weekly%, median weekly%, best/worst week%,
       longest consecutive losing-week streak (เฉพาะสัปดาห์ที่มี trade ปิด),
       histogram ของ weekly returns

 [2] COST-SENSITIVITY SWEEP
     - รันแต่ละ variant ที่ spread-price = 0.10 / 0.30 / 0.50 / 1.00 (price units)
     - commission-per-lot คงที่ที่ค่า Exness จริง (default 3.50)
     - full-history single run (ไม่ split, ไม่ compound-reset)

 [4] HONEST RULES
     - avg weekly return = ค่าจริง ไม่ annualize ไม่ขยายผล
     - ถ้า PF<=1 ที่ spread ไหน บอกตรงๆ ว่า edge พังที่ spread เท่าไหร่
================================================================================
"""
from __future__ import annotations

import argparse
import os
import sys
from multiprocessing import Pool

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forex_config import ForexConfig
from backtest_forex import (DataLoader, prepare_data, BacktestEngine,
                             FastHybridTrendPullback, compute_metrics)
from walk_forward_regime import VARIANTS


# =============================================================================
# Worker — รัน (variant, spread) เดียว ONCE over full history
# =============================================================================
def _run_one(task: dict) -> dict:
    variant_name = task["variant"]
    vparams      = VARIANTS[variant_name]

    loader = DataLoader(log_fn=lambda *a, **k: None)
    df, _ = loader.load(task["symbol"], 99.0, ForexConfig(),
                         csv_path=task["csv"], allow_synthetic=True)
    d = prepare_data(df)

    cfg = ForexConfig()
    cfg.total_capital_usd    = task["start_cash"]
    cfg.risk_per_trade_pct   = task["risk"]
    cfg.symbols              = [task["symbol"]]
    cfg.partial_tp_atr       = vparams["partial_tp_atr"]
    cfg.partial_tp_frac      = vparams["partial_tp_frac"]
    cfg.move_sl_to_breakeven = vparams["move_sl_to_breakeven"]

    strategy = FastHybridTrendPullback()
    strategy.sl_atr               = vparams["sl_atr"]
    strategy.tp_atr               = vparams["tp_atr"]
    strategy.trail_atr_mult       = vparams["trail_atr_mult"]
    strategy.trail_activation_atr = vparams["trail_activation_atr"]

    engine = BacktestEngine(d, cfg, strategy,
                             spread_price=task["spread_price"],
                             commission_per_lot=task["commission_per_lot"],
                             symbol=task["symbol"])
    engine.run(quiet=True)   # full history, ONE pass

    overall = compute_metrics(engine.trades, engine.equity_curve, cfg.total_capital_usd)

    return dict(
        variant=variant_name,
        spread=task["spread_price"],
        commission=task["commission_per_lot"],
        trades=engine.trades,
        reconcile_diff=engine.reconcile_diff,
        n_trades=len(engine.trades),
        overall=overall,
    )


# =============================================================================
# Weekly helpers
# =============================================================================
def normalize_trades(trades: list[dict], start_cash: float) -> list[dict]:
    """แนบ _norm_pnl (fixed-notional $start_cash, ไม่ compound) ให้แต่ละ trade —
    ใช้ equity_before (จาก single full compounded run) ในการ rescale กลับ."""
    equity_before = start_cash
    for t in trades:
        npnl = (t["net_pnl"] * (start_cash / equity_before)
                if equity_before > 0 else t["net_pnl"])
        t["_norm_pnl"] = npnl
        equity_before = t["equity_after"]
    return trades


def weekly_returns(trades: list[dict], start_cash: float) -> list[tuple]:
    """Group by ISO calendar week (exit date) -> weekly return % on fixed $start_cash."""
    weekly: dict = {}
    for t in trades:
        ts = pd.Timestamp(t["exit_ts"])
        iso_year, iso_week, _ = ts.isocalendar()
        key = (int(iso_year), int(iso_week))
        weekly[key] = weekly.get(key, 0.0) + t["_norm_pnl"]
    out = []
    for key in sorted(weekly.keys()):
        out.append((key[0], key[1], weekly[key] / start_cash * 100.0))
    return out


def weekly_stats(wr: list[tuple]) -> dict:
    if not wr:
        return dict(n=0)
    rets = np.array([r for *_, r in wr], dtype=float)
    pct_green = float(np.mean(rets > 0)) * 100.0

    longest = cur = 0
    for r in rets:
        if r <= 0:
            cur += 1
            longest = max(longest, cur)
        else:
            cur = 0

    return dict(
        n=len(rets),
        pct_green=pct_green,
        avg=float(np.mean(rets)),
        median=float(np.median(rets)),
        best=float(np.max(rets)),
        worst=float(np.min(rets)),
        longest_losing_streak=longest,
        rets=rets,
    )


HIST_BINS = [-np.inf, -3, -2, -1, 0, 1, 2, 3, np.inf]


def histogram_lines(rets: np.ndarray, bins=HIST_BINS) -> list[str]:
    lines = []
    for i in range(len(bins) - 1):
        lo, hi = bins[i], bins[i + 1]
        if i == 0:
            mask = rets <= hi
            lbl = f"<= {hi:g}%"
        elif i == len(bins) - 2:
            mask = rets > lo
            lbl = f"> {lo:g}%"
        else:
            mask = (rets > lo) & (rets <= hi)
            lbl = f"({lo:g}, {hi:g}]%"
        cnt = int(np.sum(mask))
        bar = "#" * cnt
        lines.append(f"        {lbl:>10}: {cnt:4d}  {bar}")
    return lines


# =============================================================================
# Main
# =============================================================================
def main():
    ap = argparse.ArgumentParser(
        description="Weekly performance stats + cost-sensitivity sweep "
                     "(C_wider / D_letrun by default)")
    ap.add_argument("--csv", required=True, help="CSV path (M15 OHLCV, real data)")
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--start-cash", type=float, default=10_000.0)
    ap.add_argument("--risk", type=float, default=0.30, help="Risk %% per trade")
    ap.add_argument("--commission-per-lot", type=float, default=3.50,
                    help="Real Exness commission per lot per side ($) — "
                         "held FIXED across the spread sweep")
    ap.add_argument("--spreads", type=float, nargs="+",
                    default=[0.10, 0.30, 0.50, 1.00],
                    help="Spread levels to sweep (price units, e.g. XAUUSD pip=0.01 "
                         "so 0.10=10pips, 1.00=100pips)")
    ap.add_argument("--variants", nargs="+", default=["C_wider", "D_letrun"],
                    choices=list(VARIANTS.keys()))
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    # ── Load once for banner / date range ──
    loader = DataLoader(log_fn=print)
    df, data_source = loader.load(args.symbol, 99.0, ForexConfig(),
                                   csv_path=args.csv, allow_synthetic=True)
    n = len(df)

    print()
    print("=" * 96)
    print(" WEEKLY PERFORMANCE + COST-SENSITIVITY SWEEP")
    print("=" * 96)
    print(f"  DATA SOURCE : {data_source}")
    print(f"  Symbol      : {args.symbol}")
    print(f"  Bars        : {n:,}")
    print(f"  Date range  : {df['timestamp'].iloc[0]}  ->  {df['timestamp'].iloc[-1]}")
    print(f"  Variants    : {', '.join(args.variants)}")
    print(f"  Spreads     : {args.spreads}  (price units)")
    print(f"  Commission  : ${args.commission_per_lot}/lot/side  (FIXED — real Exness value)")
    print(f"  Risk/trade  : {args.risk}%   Cash: ${args.start_cash:,.0f}")
    print(f"  Weekly base : fixed $10k (norm_pnl, NOT compounded) — ISO calendar weeks "
          f"by exit date")
    print("=" * 96)

    tasks = []
    for v in args.variants:
        for sp in args.spreads:
            tasks.append(dict(variant=v, spread_price=sp, csv=args.csv,
                               symbol=args.symbol, start_cash=args.start_cash,
                               risk=args.risk,
                               commission_per_lot=args.commission_per_lot))

    print(f"\n  Running {len(tasks)} (variant x spread) combinations "
          f"with {min(args.workers, len(tasks))} workers ...\n")

    with Pool(processes=min(args.workers, len(tasks))) as pool:
        results = pool.map(_run_one, tasks)

    results_by: dict = {}
    for r in results:
        results_by.setdefault(r["variant"], {})[r["spread"]] = r

    breaks_at = {}   # variant -> spread where PF first <= 1.0 (or None)

    for v in args.variants:
        vparams = VARIANTS[v]
        print("-" * 96)
        print(f" Variant {v}: {vparams['desc']}")
        print(f"   SL={vparams['sl_atr']}xATR  TP={vparams['tp_atr']}xATR  "
              f"trail_mult={vparams['trail_atr_mult']} "
              f"trail_act={vparams['trail_activation_atr']}")
        print("-" * 96)
        print(f"   {'Spread':>7} {'PF':>7} {'Return%':>9} {'MaxDD%':>7} "
              f"{'Expect$':>9} {'%Green':>7} {'AvgWk%':>8} {'MedWk%':>8} "
              f"{'BestWk%':>8} {'WorstWk%':>9} {'LoseStrk':>8} {'#Wks':>5} "
              f"{'#Trades':>8} {'Reconcile':>10}")

        per_spread_ws = {}
        for sp in args.spreads:
            r = results_by[v][sp]
            ov = r["overall"]
            trades = normalize_trades(r["trades"], args.start_cash)
            wr = weekly_returns(trades, args.start_cash)
            ws = weekly_stats(wr)
            per_spread_ws[sp] = ws

            if ov.get("trades", 0) == 0:
                print(f"   {sp:7.2f}   [no trades]")
                continue

            pf = ov["profit_factor"]
            print(f"   {sp:7.2f} {pf:7.3f} {ov['total_return_pct']:8.1f}% "
                  f"{ov['max_dd_pct']:6.1f}% {ov['expectancy_usd']:+8.2f} "
                  f"{ws['pct_green']:6.1f}% {ws['avg']:+7.2f}% {ws['median']:+7.2f}% "
                  f"{ws['best']:+7.2f}% {ws['worst']:+8.2f}% "
                  f"{ws['longest_losing_streak']:8d} {ws['n']:5d} "
                  f"{r['n_trades']:8,d} {r['reconcile_diff']:10.6f}")

            if v not in breaks_at and pf <= 1.0:
                breaks_at[v] = sp

        # ── Weekly histograms — one per spread level ──
        print()
        print(f"   Weekly-return histograms (norm $10k, {len(args.spreads)} spread "
              f"levels, bins in %):")
        for sp in args.spreads:
            ws = per_spread_ws[sp]
            if ws.get("n", 0) == 0:
                print(f"     spread={sp:.2f}: [no weeks with trades]")
                continue
            print(f"     spread={sp:.2f}  ({ws['n']} weeks-with-trades):")
            for line in histogram_lines(ws["rets"]):
                print(line)
        print()

    # ── Honest rules summary ──
    print("=" * 96)
    print(" HONEST SUMMARY — COST SENSITIVITY")
    print("=" * 96)
    for v in args.variants:
        if v in breaks_at:
            print(f"  {v:12} -> EDGE BREAKS (PF<=1.0) at spread={breaks_at[v]:.2f} "
                  f"(commission held fixed at ${args.commission_per_lot}/lot/side)")
        else:
            print(f"  {v:12} -> PF>1.0 held across ALL tested spreads "
                  f"{args.spreads} (commission=${args.commission_per_lot}/lot/side)")
    print("-" * 96)
    print("  Avg weekly return % reported above is the ACTUAL value over the full")
    print("  dataset history — NOT annualized, NOT inflated. A small positive avg")
    print("  weekly return with PF only slightly >1.0 means the edge is thin and")
    print("  sensitive to execution quality (real spread/commission/slippage).")
    print("=" * 96)


if __name__ == "__main__":
    main()
