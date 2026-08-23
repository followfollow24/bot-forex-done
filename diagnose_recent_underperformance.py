#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
diagnose_recent_underperformance.py -- Re-run the EXACT adx20tp7/adx18tp7
signal on fresh real data covering the live-trading window (2026-07-01
onward, with Apr-Jun warm-up for H1 EMA200/ADX to converge properly),
to answer: would the SAME signal have also underperformed on this data,
or does live show something backtest doesn't predict?

If backtest-on-this-window ALSO shows low win rate -> real market regime,
edge intact, just a hard patch. If backtest shows normal ~40% WR but live
got 9-19% -> something in live execution differs from what the signal
should produce, needs a deeper look (not a strategy problem).
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forex_config import ForexConfig
from backtest_forex import (DataLoader, prepare_data, BacktestEngine,
                             FastHybridTrendPullback, compute_metrics)

RECENT_CSV_NPZ = "/Users/follow/Desktop/outputs/duka_cache/XAUUSD_recent_M15.npz"
START = 10_000.0
RISK_PCT = 0.30
SPREAD, COMM = 0.10, 3.50
WINDOW_START = "2026-07-01"


def gold_cfg():
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = RISK_PCT
    c.partial_tp_atr = 999.0
    c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False
    return c


def load_recent():
    import numpy as np
    z = np.load(RECENT_CSV_NPZ)
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(z["ts"], unit="s"),
        "open": z["o"], "high": z["h"], "low": z["l"], "close": z["c"],
    })
    return df


def run(df_full, sl, tp, adx, date_from=None):
    d = prepare_data(df_full)
    strat = FastHybridTrendPullback()
    strat.ADX_MIN = adx
    strat.precompute(d)
    strat.sl_atr, strat.tp_atr = sl, tp
    strat.trail_atr_mult, strat.trail_activation_atr = 999.0, 999.0
    eng = BacktestEngine(d, gold_cfg(), strat, spread_price=SPREAD,
                          commission_per_lot=COMM, symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)

    trades = eng.trades
    if date_from:
        cutoff = pd.Timestamp(date_from)
        trades = [t for t in trades if pd.Timestamp(t["entry_ts"]) >= cutoff]
    if not trades:
        return None, trades
    wins = [t for t in trades if t["net_pnl"] > 0]
    n = len(trades)
    wr = len(wins) / n * 100
    net = sum(t["net_pnl"] for t in trades)
    return dict(n=n, wins=len(wins), wr=wr, net=net), trades


def main():
    print("=" * 100)
    print(" DIAGNOSIS: would adx20tp7/adx18tp7 signal ALSO underperform on real")
    print(f" data for the live window ({WINDOW_START} onward)?")
    print("=" * 100)

    df_full = load_recent()
    print(f"[load] {len(df_full):,} bars, {df_full['timestamp'].iloc[0]} -> {df_full['timestamp'].iloc[-1]}\n")

    for label, sl, tp, adx in [("adx20tp7", 3.0, 7.0, 20), ("adx18tp7", 3.0, 7.0, 18)]:
        m, trades = run(df_full, sl, tp, adx, date_from=WINDOW_START)
        if m is None:
            print(f"{label}: NO TRADES generated in window from this backtest")
            continue
        print(f"{label}: BACKTEST on same window -- n={m['n']}  wins={m['wins']}  "
              f"WR={m['wr']:.1f}%  net=${m['net']:.2f}")
        for t in trades:
            print(f"    {t['entry_ts']}  {t['reason']:<8}  net_pnl=${t['net_pnl']:.2f}")
        print()

    print("=" * 100)
    print(" COMPARE to what LIVE actually showed (from MT5 history, verified earlier):")
    print("   adx20tp7 LIVE: n=22  wins=2   WR=9.1%   net=-$10.10")
    print("   adx18tp7 LIVE: n=26  wins=5   WR=19.2%  net=-$8.84")
    print("=" * 100)


if __name__ == "__main__":
    main()
