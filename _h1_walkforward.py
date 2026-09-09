#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_h1_walkforward.py -- point the honest tooling at the bots that are
actually running.

The 19:30 hunt is closed: nothing there survived a train/TEST split. But
this project's OTHER family -- the H1 trend-pullback bots that have been
live for months -- was validated years ago and never re-examined with the
same discipline. It has 13 years of gold data behind it instead of eight
months, which is the main reason to expect a different answer.

Two questions, in the order that matters.

1. IS THE LIVE CONFIG STABLE? The parameters are already fixed
   (--adx-min 10 --touch-tolerance 0.012 --sl-atr 2.5 --tp-atr 5.0
   --risk 0.30), so nothing is being chosen here. History is cut into
   contiguous windows and each is run from the same starting capital.
   A real edge shows up in most windows; one that lives in a single
   period is a regime, not an edge -- and the count of profitable
   windows says which.

   Every window is also run at three spreads, because this project has
   shipped a gold spread constant roughly 12x too large, and gold
   backtests computed with it are recorded as suspect. 0.24 is the
   re-confirmed live figure, 2.85 the wrong constant still sitting in
   the older scripts, and 1.00 sits between them.

2. DOES TUNING TRANSFER? The same question that emptied 19:30: ADX_MIN is
   chosen on each window and carried to the next, against simply leaving
   it at 10. If picking it per window cannot beat leaving it alone, the
   sweep that produced it was sorting noise there too.

Each window is run separately from the same starting balance rather than
sliced out of one compounding run: risk is a percentage of CURRENT equity,
so trades from a later window carry the size of a bigger account, and
replaying them from a fixed start would quietly inflate them.

Usage:  python _h1_walkforward.py [folds]
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import (DataLoader, prepare_data, BacktestEngine,
                            FastHybridTrendPullback, compute_metrics)
from _idea_search import resample
from _all_paths import START

GOLD_M15 = "download/xauusd-m15-bid-2013-01-01-2026-06-10.csv"
FOLDS = int(sys.argv[1]) if len(sys.argv) > 1 else 6
SPREADS = [0.24, 1.00, 2.85]
ADX_GRID = [6, 10, 14, 18, 22, 26]
# Read straight off the watchdog's live Args line for gold_h1_manual:
#   --adx-min 10 --touch-tolerance 0.012 --sl-atr 2.5 --tp-atr 15.0
#   --risk 0.30 --block-hours 20-01 --max-positions 1
# The first version of this script used TP 5 (BTC's setting) and omitted
# the hour block entirely, which measured a strategy nobody is running.
LIVE_ADX, LIVE_TOL, LIVE_SL, LIVE_TP, LIVE_RISK = 10, 0.012, 2.5, 15.0, 0.30
BLOCK_LO, BLOCK_HI = 20, 1        # entries blocked in UTC [20:00, 01:00)
COMMISSION = 3.5


def make_cfg():
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = LIVE_RISK
    c.partial_tp_atr = 999.0
    c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False
    c.max_hold_bars = 64
    return c


class HourBlocked(FastHybridTrendPullback):
    """The live bot refuses entries inside a UTC hour window; the backtest
    engine has no such gate, so it goes here. Without it this measures a
    strategy that is not the one running -- and the blocked window is one
    of the three entry gates this project verified separately."""

    _hours = None

    def precompute(self, d):
        super().precompute(d)
        self._hours = np.array([int(str(t)[11:13]) for t in d["ts"]])

    def signal(self, d, i):
        h = self._hours[i]
        if (h >= BLOCK_LO) or (h < BLOCK_HI):
            return super().signal(d, i).__class__()      # empty Signal
        return super().signal(d, i)


def make_strategy(d, adx):
    s = HourBlocked()
    s.ADX_MIN = adx
    s.TOUCH_TOLERANCE = LIVE_TOL
    # H1-spaced bars need H4 buckets; the class default (900s) would make
    # one bar per bucket and silently disable the H4 trend filter.
    s.TIMEFRAME_SECONDS = 3600
    s.sl_atr, s.tp_atr = LIVE_SL, LIVE_TP
    s.trail_atr_mult = s.trail_activation_atr = 999.0
    s.precompute(d)
    return s


def run_slice(d, strat, spread, lo, hi):
    eng = BacktestEngine(d, make_cfg(), strat, spread_price=spread,
                         commission_per_lot=COMMISSION, symbol="XAUUSD")
    eng.run(start_i=lo, end_i=hi, quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START)


def label(d, lo, hi):
    return f"{str(d['ts'][lo])[:7]}..{str(d['ts'][hi-1])[:7]}"


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START
    print("loading 13 years of gold ...", flush=True)
    df, _ = loader.load("XAUUSD", 99.0, c0, csv_path=GOLD_M15,
                        allow_synthetic=False)
    d = prepare_data(resample(df, "1h"))
    if d is None:
        print("[ERROR] prepare_data returned nothing"); return 2
    n = len(d["ts"])
    edges = [round(i * n / FOLDS) for i in range(FOLDS + 1)]

    print("=" * 78)
    print(f" H1 TREND-PULLBACK, THE LIVE CONFIG -- XAUUSD  {n:,} H1 bars")
    print(f" adx {LIVE_ADX}  touch {LIVE_TOL}  SL {LIVE_SL}xATR  TP {LIVE_TP}xATR"
          f"  risk {LIVE_RISK}%  from {START:,.0f}")
    print(f" {FOLDS} windows, each run from the same starting balance")
    print("=" * 78)

    strat = make_strategy(d, LIVE_ADX)
    for sp in SPREADS:
        note = ("re-confirmed live figure" if sp == 0.24 else
                "the ~12x-too-large constant still in older scripts"
                if sp == 2.85 else "in between")
        print(f"\n  SPREAD {sp:.2f}   ({note})")
        print(f"    {'window':>16}{'trades':>8}{'PF':>7}{'return':>10}"
              f"{'maxDD':>8}{'win%':>7}")
        wins = 0
        for k in range(FOLDS):
            m = run_slice(d, strat, sp, edges[k], edges[k + 1])
            if not m or m.get("trades", 0) < 5:
                print(f"    {label(d, edges[k], edges[k+1]):>16}"
                      f"{m.get('trades', 0):>8}   too few to judge")
                continue
            ret = m["total_return_pct"]
            wins += ret > 0
            print(f"    {label(d, edges[k], edges[k+1]):>16}{m['trades']:>8}"
                  f"{m['profit_factor']:>7.2f}{ret:>+9.1f}%"
                  f"{m['max_dd_pct']:>7.1f}%{m['win_rate']*100:>6.0f}%")
        print(f"    {'PROFITABLE WINDOWS':>16}{wins:>8} of {FOLDS}")

    # ---- does choosing ADX per window beat leaving it alone? ------------
    print("\n" + "=" * 78)
    print(f" DOES TUNING TRANSFER?  ADX chosen on each window, used on the next")
    print(f" (spread {SPREADS[0]:.2f}; the live setting is a fixed {LIVE_ADX})")
    print("=" * 78)
    cache = {}
    for adx in ADX_GRID:
        s = make_strategy(d, adx)
        for k in range(FOLDS):
            m = run_slice(d, s, SPREADS[0], edges[k], edges[k + 1])
            cache[(adx, k)] = (m.get("total_return_pct", 0.0)
                               if m and m.get("trades", 0) >= 5 else None)
    print(f"\n    {'window':>16}{'chosen':>8}{'its return':>12}"
          f"{'fixed 10':>10}   better?")
    tuned = fixed = 0.0
    better = 0
    for k in range(1, FOLDS):
        prev = [(a, cache[(a, k - 1)]) for a in ADX_GRID
                if cache[(a, k - 1)] is not None]
        if not prev:
            continue
        pick = max(prev, key=lambda t: t[1])[0]
        got, base = cache[(pick, k)], cache[(LIVE_ADX, k)]
        if got is None or base is None:
            continue
        tuned += got; fixed += base
        better += got > base
        print(f"    {label(d, edges[k], edges[k+1]):>16}{pick:>8}"
              f"{got:>+11.1f}%{base:>+9.1f}%   {'yes' if got > base else 'no'}")
    print(f"\n    tuning won {better} of {FOLDS-1} windows;"
          f" total {tuned:+.1f}% vs {fixed:+.1f}% for leaving it at {LIVE_ADX}")
    print("    Choosing the parameter per window has to BEAT leaving it")
    print("    alone. At 19:30 it lost -174 points doing this.")
    print("\n  Every window starts from the same balance, so returns are")
    print("  comparable across windows but do not compound into a total.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
