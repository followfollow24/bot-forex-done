#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gold_losing_streak.py -- max consecutive-losing-streak distribution for the
LIVE config (ADX20_TP7, partial-TP OFF) on TRAIN 2013-2019 vs TEST 2020-2026,
to answer: is the real account's current 16-loss streak inside or outside what
the 13yr backtest has ever produced?

Real spread 0.28 (as live demo/real conditions), same config as adx20tp7 bot:
sl_atr=3.0, tp_atr=7.0, ADX_MIN=20, partial-TP OFF, risk=0.30%.
ASCII-only.
"""
import sys, os
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import DataLoader, prepare_data, BacktestEngine, FastHybridTrendPullback, compute_metrics

CSV = "download/xauusd-m15-bid-2013-01-01-2026-06-10.csv"
SPREAD = 0.28
COMM = 3.50
START = 10_000.0


def make_cfg():
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = 0.30
    c.partial_tp_atr = 999.0
    c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False
    return c


def streaks(trades):
    """Return list of consecutive-losing-streak lengths (each maximal run of losses)."""
    runs = []
    cur = 0
    for t in trades:
        if t["net_pnl"] <= 0:
            cur += 1
        else:
            if cur > 0:
                runs.append(cur)
            cur = 0
    if cur > 0:
        runs.append(cur)
    return runs


def report(label, trades):
    if not trades:
        print(f"  {label}: no trades"); return
    runs = streaks(trades)
    n = len(trades)
    win = sum(1 for t in trades if t["net_pnl"] > 0)
    print(f"\n  === {label} ===  ({n} trades, {df_date_range(trades)})")
    print(f"    win rate: {win/n*100:.1f}%   longest losing streak: {max(runs) if runs else 0}")
    print(f"    #loss-streaks >=1: {len(runs)}   distribution (len:count): "
          + ", ".join(f"{k}:{v}" for k, v in sorted(pd.Series(runs).value_counts().items())))
    if runs:
        arr = np.array(sorted(runs))
        for target in (11, 16):
            pct = (arr < target).mean() * 100
            ge = (arr >= target).sum()
            print(f"    streaks >= {target} losses: {ge} occurrence(s) historically "
                  f"(percentile of {target}: {pct:.1f}% of streaks are shorter)")
    return runs


def df_date_range(trades):
    return f"{trades[0]['entry_ts'][:10]} .. {trades[-1]['entry_ts'][:10]}"


def find_streak_dates(trades, min_len):
    """Return list of (start_ts, end_ts, length) for streaks >= min_len."""
    out = []
    cur_start = None
    cur_len = 0
    for i, t in enumerate(trades):
        if t["net_pnl"] <= 0:
            if cur_len == 0:
                cur_start = t["entry_ts"]
            cur_len += 1
            cur_end = t["exit_ts"]
        else:
            if cur_len >= min_len:
                out.append((cur_start, cur_end, cur_len))
            cur_len = 0
    if cur_len >= min_len:
        out.append((cur_start, cur_end, cur_len))
    return out


def main():
    print("=" * 78)
    print(" GOLD LIVE CONFIG (ADX20_TP7, partial-TP OFF) -- max losing-streak check")
    print(f" spread={SPREAD}  commission=${COMM}/lot/side  risk=0.30%/trade")
    print("=" * 78)

    loader = DataLoader(log_fn=lambda *a, **k: None)
    cfg0 = ForexConfig(); cfg0.total_capital_usd = START
    df, _ = loader.load("XAUUSD", 99.0, cfg0, csv_path=CSV, allow_synthetic=False)
    d = prepare_data(df)

    strat = FastHybridTrendPullback()
    strat.ADX_MIN = 20
    strat.precompute(d)
    strat.sl_atr = 3.0
    strat.tp_atr = 7.0
    strat.trail_atr_mult = 999.0
    strat.trail_activation_atr = 999.0

    eng = BacktestEngine(d, make_cfg(), strat, spread_price=SPREAD,
                        commission_per_lot=COMM, symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)
    trades = eng.trades
    print(f"\n  full 13.4yr history: {len(trades)} trades")

    train = [t for t in trades if pd.Timestamp(t["entry_ts"]) < pd.Timestamp("2020-01-01")]
    test = [t for t in trades if pd.Timestamp(t["entry_ts"]) >= pd.Timestamp("2020-01-01")]

    report("TRAIN 2013-2019", train)
    report("TEST 2020-2026 (OOS)", test)
    report("FULL 2013-2026", trades)

    print("\n  --- streaks >= 11 losses, with dates (context: current real streak = 16) ---")
    for label, tr in [("TRAIN", train), ("TEST", test), ("FULL", trades)]:
        hits = find_streak_dates(tr, 11)
        for (s, e, ln) in hits:
            print(f"    [{label}] {ln}-loss streak: {s} -> {e}")
        if not hits:
            print(f"    [{label}] (none >= 11)")

    print("\n" + "=" * 78)
    print(" VERDICT: compare current REAL 16-loss streak (all-BUY, 6-7 Jul onward)")
    print(" against the longest historical streak above. If 16 <= historical max,")
    print(" this is inside the strategy's proven tolerance. If 16 > historical max,")
    print(" this is a genuine departure from anything the 13yr backtest has shown.")
    print("=" * 78)


if __name__ == "__main__":
    main()
