#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 zoo_backtest.py — เปรียบเทียบ 6 Zoo Strategies + cwider/mix_a benchmarks
================================================================================
 รัน:
   python3 zoo_backtest.py --csv download/xauusd-m15-bid-2013-01-01-2026-06-10.csv

 ผลลัพธ์:
   - ตาราง metrics ทุก strategy
   - zoo_results.csv — trade-level data ทุก strategy
   - zoo_equity.png  — equity curves (ถ้ามี matplotlib)
================================================================================
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forex_config import ForexConfig
from backtest_forex import (DataLoader, prepare_data, BacktestEngine,
                            FastHybridTrendPullback, compute_metrics)
from strategy_zoo import ALL_STRATEGIES

# ── Cost model (same as walk_forward_candidates) ─────────────────────────────
SPREAD   = 0.10   # $/price unit (= 0.10 USD on XAUUSD, ~1 pip)
COMM     = 3.50   # commission per lot per side
START    = 10_000.0
RISK_PCT = 0.30
SYMBOL   = "XAUUSD"


def make_cfg() -> ForexConfig:
    c = ForexConfig()
    c.total_capital_usd    = START
    c.risk_per_trade_pct   = RISK_PCT
    c.partial_tp_atr       = 999.0
    c.partial_tp_frac      = 0.0
    c.move_sl_to_breakeven = False
    return c


def run_strategy(d: dict, strategy, label: str,
                 spread: float = SPREAD, comm: float = COMM) -> dict:
    """Run one strategy on full data, return metrics dict."""
    t0 = time.time()

    # precompute once
    if hasattr(strategy, "precompute"):
        strategy.precompute(d)

    cfg = make_cfg()
    eng = BacktestEngine(
        d, cfg, strategy,
        spread_price       = spread,
        commission_per_lot = comm,
        symbol             = SYMBOL,
        reactive_daily_stop = False,
    )
    eng.run()
    trades = eng.trades

    m = compute_metrics(trades, eng.equity_curve, START)
    elapsed = time.time() - t0

    ann_ret = m.get("ann_return_pct", 0)
    mdd     = m.get("max_dd_pct", 0)          # already ×100 in compute_metrics
    calmar  = ann_ret / mdd if mdd > 1e-9 else 0.0

    return {
        "label"    : label,
        "trades"   : m.get("trades", 0),
        "wr_pct"   : m.get("win_rate", 0) * 100,
        "pf"       : m.get("profit_factor", 0),
        "net_usd"  : m.get("final_equity", START) - START,
        "max_dd"   : mdd,
        "calmar"   : calmar,
        "sharpe"   : m.get("sharpe", 0),
        "elapsed"  : elapsed,
        "equity_curve": eng.equity_curve,
        "trade_list"  : trades,
    }


def bench_hybrid(d: dict, sl: float, tp: float, label: str,
                 spread: float = SPREAD, comm: float = COMM) -> dict:
    """Run Hybrid-TPB (fast O(n) variant) with given sl/tp as benchmark."""
    strat = FastHybridTrendPullback()
    strat.sl_atr               = sl
    strat.tp_atr               = tp
    strat.trail_atr_mult       = 999.0
    strat.trail_activation_atr = 999.0
    strat.precompute(d)
    return run_strategy(d, strat, label, spread=spread, comm=comm)


def print_table(rows: list[dict]) -> None:
    hdr = f"{'Strategy':<22} {'Trades':>7} {'WR%':>6} {'PF':>6} {'Net$':>9} {'MaxDD%':>7} {'Calmar':>8} {'Sharpe':>7}"
    sep = "─" * len(hdr)
    print()
    print(sep)
    print(hdr)
    print(sep)
    for r in rows:
        flag = " ★" if r["calmar"] >= 5.0 else ("  " if r["calmar"] >= 1.0 else " ✗")
        print(
            f"{r['label']:<22} {r['trades']:>7,} {r['wr_pct']:>6.1f} "
            f"{r['pf']:>6.3f} {r['net_usd']:>9.0f} {r['max_dd']:>7.1f} "
            f"{r['calmar']:>8.2f} {r['sharpe']:>7.2f}{flag}"
        )
    print(sep)
    print(" ★ Calmar ≥ 5  |  ✗ Calmar < 1\n")


def save_csv(rows: list[dict], path: str) -> None:
    records = []
    for r in rows:
        for t in r["trade_list"]:
            t2 = dict(t)
            t2["strategy"] = r["label"]
            records.append(t2)
    if records:
        pd.DataFrame(records).to_csv(path, index=False)
        print(f"Trades saved → {path}")


def plot_equity(rows: list[dict], path: str) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return

    fig, ax = plt.subplots(figsize=(14, 6))
    for r in rows:
        eq = r["equity_curve"]
        if len(eq) > 10:
            ax.plot(eq, label=r["label"], linewidth=1.2)
    ax.axhline(START, color="gray", linestyle="--", linewidth=0.8)
    ax.set_title("Zoo Backtest — Equity Curves (XAUUSD M15, spread=0.10, comm=3.5/lot)")
    ax.set_xlabel("Bar")
    ax.set_ylabel("Equity (USD)")
    ax.legend(fontsize=8, ncol=2)
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()
    print(f"Equity chart saved → {path}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv",    required=True, help="Path to XAUUSD M15 CSV")
    ap.add_argument("--symbol", default=SYMBOL)
    ap.add_argument("--years",  type=float, default=0.0,
                    help="Limit data to last N years (0=all)")
    ap.add_argument("--spread", type=float, default=SPREAD)
    ap.add_argument("--comm",   type=float, default=COMM)
    args = ap.parse_args()

    spread = args.spread
    comm   = args.comm

    # ── Load data ────────────────────────────────────────────────────────────
    cfg     = make_cfg()
    loader  = DataLoader()
    df, src = loader.load(args.symbol, args.years or 15, cfg, csv_path=args.csv)
    print(f"\nData: {len(df):,} bars  ({df['timestamp'].iloc[0].date()} → "
          f"{df['timestamp'].iloc[-1].date()})  src={src}")
    d = prepare_data(df)

    out_dir = os.path.dirname(args.csv) if os.path.dirname(args.csv) else "."
    results = []

    # ── Benchmarks ───────────────────────────────────────────────────────────
    print("\nRunning benchmarks...")
    results.append(bench_hybrid(d, sl=2.5, tp=5.0, label="[BM] C_wider 2.5/5", spread=spread, comm=comm))
    results.append(bench_hybrid(d, sl=2.5, tp=7.0, label="[BM] Mix_A  2.5/7",  spread=spread, comm=comm))

    # ── Zoo strategies ───────────────────────────────────────────────────────
    print("Running zoo strategies...")
    for StratClass in ALL_STRATEGIES:
        strat = StratClass()
        label = strat.short_name
        print(f"  {label:<10}", end="", flush=True)
        r = run_strategy(d, strat, label, spread=spread, comm=comm)
        results.append(r)
        print(f"  {r['trades']:>5} trades  Calmar={r['calmar']:.2f}  ({r['elapsed']:.1f}s)")

    # ── Print comparison table ────────────────────────────────────────────────
    print_table(results)

    # ── Artefacts ────────────────────────────────────────────────────────────
    csv_path = os.path.join(out_dir, "zoo_results.csv")
    save_csv(results, csv_path)

    png_path = os.path.join(out_dir, "zoo_equity.png")
    plot_equity(results, png_path)


if __name__ == "__main__":
    main()
