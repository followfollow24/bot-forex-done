#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_1930_moves.py -- what the chart actually did at 19:30, day by day.

No strategy, no entries, no P&L. Just the movement itself, so "does it
really run at 19:30" can be answered by looking rather than inferred from
a backtest's totals.

For each session it prints the signed move from the 19:30 open at one,
five, fifteen and thirty minutes, and the furthest the price got in each
direction during the first thirty minutes. The 11.1-point line is marked
because that is the gate that came out best in the signal comparison --
so the table also shows, session by session, how often that gate would
even have had something to fire on.

Usage:  python _1930_moves.py [symbol] [days]
"""
import sys
from datetime import datetime, timedelta, timezone

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)"); sys.exit(1)

import numpy as np

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "XAUAUDm"
DAYS = int(sys.argv[2]) if len(sys.argv) > 2 else 30
GATE_PTS = 11.1
THAI = 7


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed"); return 2
    info = mt5.symbol_info(SYMBOL)
    if info is None:
        print(f"[ERROR] {SYMBOL} not found"); return 2
    mt5.symbol_select(SYMBOL, True)
    spread = info.spread * info.point

    print("=" * 92)
    print(f" WHAT THE CHART DID AT 19:30 -- {SYMBOL}, last {DAYS} days")
    print(f" moves in PRICE POINTS from the 19:30 open. spread {spread:.2f}, "
          f"the {GATE_PTS} pt line is the best gate found")
    print("=" * 92)
    print(f"\n{'date (Thai)':>16}{'+1m':>9}{'+5m':>9}{'+15m':>9}{'+30m':>9}"
          f"{'max up':>9}{'max dn':>9}   reached {GATE_PTS}pt within 30m")
    print("-" * 92)

    rows, hit, big = [], 0, 0
    today = (datetime.now(timezone.utc) + timedelta(hours=THAI)).date()
    for back in range(DAYS, 0, -1):
        d = today - timedelta(days=back)
        if d.weekday() >= 5:
            continue
        s_utc = datetime(d.year, d.month, d.day, 12, 30, tzinfo=timezone.utc)
        b = mt5.copy_rates_range(SYMBOL, mt5.TIMEFRAME_M1, s_utc,
                                 s_utc + timedelta(minutes=31))
        if b is None or len(b) < 16:
            b = mt5.copy_rates_range(SYMBOL, mt5.TIMEFRAME_M5, s_utc,
                                     s_utc + timedelta(minutes=35))
            if b is None or len(b) < 4:
                print(f"{d:%Y-%m-%d %a':>16}  -- market shut --")
                continue
            step = 5
        else:
            step = 1
        o = float(b[0]["open"])
        highs = np.array([float(x["high"]) for x in b])
        lows = np.array([float(x["low"]) for x in b])

        def at(minutes):
            k = min(minutes // step, len(b) - 1)
            return float(b[k]["close"]) - o

        up = float(highs.max() - o)
        dn = float(lows.min() - o)
        reached = max(up, -dn) >= GATE_PTS
        first = "UP" if up >= GATE_PTS and (
            np.argmax(highs >= o + GATE_PTS) <= np.argmax(lows <= o - GATE_PTS)
            or -dn < GATE_PTS) else ("DOWN" if -dn >= GATE_PTS else "--")
        rows.append((d, at(1), at(5), at(15), at(30), up, dn, reached))
        if reached:
            hit += 1
        if max(up, -dn) >= 40:
            big += 1
        mark = f"YES  {first}" if reached else "no"
        print(f"{d:%Y-%m-%d %a}"[:16].rjust(16)
              + f"{at(1):>+9.1f}{at(5):>+9.1f}{at(15):>+9.1f}{at(30):>+9.1f}"
              f"{up:>+9.1f}{dn:>+9.1f}   {mark}")

    n = len(rows)
    print("-" * 92)
    if n:
        ups = np.array([r[5] for r in rows])
        dns = np.array([-r[6] for r in rows])
        rng = np.maximum(ups, dns)
        print(f"  {n} sessions with data")
        print(f"  reached {GATE_PTS} pts within 30 min: {hit} "
              f"({100.0*hit/n:.0f}% of days)")
        print(f"  reached 40 pts:  {big} ({100.0*big/n:.0f}%)")
        for lim in (5, 10, 11.1, 20, 30, 40, 60):
            k = int(np.sum(rng >= lim))
            print(f"    >= {lim:>5} pts : {k:>3} days ({100.0*k/n:>3.0f}%)")
        print(f"  median furthest move in 30 min: {np.median(rng):.1f} pts")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
