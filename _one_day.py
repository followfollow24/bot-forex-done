#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_one_day.py -- every entry x exit on one session, against the perfect trade.

The operator asks how 4 Sep should have been traded from 19:30 for the
most profit. In hindsight that has an exact answer, and the answer is
useful precisely because it is unreachable: it says how much of the day
each REAL rule threw away, and whether the loss was at the entry or the
exit. Those are different problems with different fixes.

Entries are distance triggers from the 19:30 price -- the study of entry
timing found distance beats the clock, and that a one-second read is
worse than a coin flip. Exits are the ones available to the bot plus the
obvious alternatives it does not have.

Prints the price path first, so the numbers can be checked against the
chart rather than taken on trust.

Usage:  python _one_day.py [symbol] [YYYY-MM-DD] [hours]
"""
import sys
from datetime import datetime, timedelta, timezone

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)"); sys.exit(1)

import numpy as np

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "XAUAUDm"
DAY = sys.argv[2] if len(sys.argv) > 2 else "2026-09-04"
HOURS = float(sys.argv[3]) if len(sys.argv) > 3 else 2.0
DISTS = [2, 5, 11, 15, 20, 25, 30]
FIXED = [15, 30, 45, 60, 90]
TRAILS = [10, 20, 30]
THAI = 7


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed"); return 2
    info = mt5.symbol_info(SYMBOL)
    if info is None:
        print(f"[ERROR] {SYMBOL} not found"); return 2
    mt5.symbol_select(SYMBOL, True)
    spread = info.spread * info.point
    tkn = mt5.symbol_info_tick(SYMBOL)
    pp05 = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, SYMBOL, 0.05,
                                 tkn.ask, tkn.ask + 1.0) or 0.0
    d = datetime.strptime(DAY, "%Y-%m-%d").date()
    s_utc = datetime(d.year, d.month, d.day, 12, 30, tzinfo=timezone.utc)
    b = mt5.copy_rates_range(SYMBOL, mt5.TIMEFRAME_M1, s_utc,
                             s_utc + timedelta(hours=HOURS))
    if b is None or len(b) < 20:
        print(f"[ERROR] no M1 data ({mt5.last_error()})"); return 2
    t0 = int(s_utc.timestamp())
    mins = (b["time"].astype(np.int64) - t0) / 60.0
    o = float(b[0]["open"])
    hi = b["high"].astype(float)
    lo = b["low"].astype(float)
    cl = b["close"].astype(float)

    print("=" * 88)
    print(f" {DAY} FROM 19:30 THAI -- {SYMBOL}   open {o:.2f}   "
          f"spread {spread:.2f}   1 pt = {pp05:.2f} USD at 0.05 lot")
    print("=" * 88)

    print("\nTHE PATH  (minutes after 19:30, price relative to the 19:30 open)\n")
    print(f"  {'+min':>6}{'price':>11}{'move':>9}{'low so far':>12}"
          f"{'high so far':>13}")
    for m in (1, 2, 3, 5, 10, 15, 20, 25, 30, 45, 60, 90, 120):
        k = np.where(mins <= m)[0]
        if len(k) == 0:
            continue
        j = int(k[-1])
        print(f"  {m:>6}{cl[j]:>11.2f}{cl[j]-o:>+9.1f}"
              f"{lo[:j+1].min()-o:>+12.1f}{hi[:j+1].max()-o:>+13.1f}")

    best_short = o - lo.min()
    best_long = hi.max() - o
    print(f"\n  PERFECT SHORT: sell the high {hi.max():.2f}, buy the low "
          f"{lo.min():.2f} = {best_short + (hi.max()-o):.1f} pts")
    print(f"  perfect long : {best_long:.1f} pts     "
          f"from the 19:30 price, short captures {best_short:.1f} pts")

    def simulate(trigger, exitmode, arg):
        """Enter on the first bar reaching `trigger` from the open."""
        s = None
        ei = None
        for i in range(len(b)):
            if hi[i] - o >= trigger:
                s, ei = 1, i; break
            if o - lo[i] >= trigger:
                s, ei = -1, i; break
        if s is None:
            return None
        entry = o + s * trigger
        best = entry
        for j in range(ei, len(b)):
            ext = hi[j] if s > 0 else lo[j]
            best = max(best, ext) if s > 0 else min(best, ext)
            run = (best - entry) * s
            if exitmode == "m15":
                if (t0 + mins[j] * 60) >= (int(t0 + mins[ei] * 60) // 900 + 1) * 900:
                    return ((cl[j] - entry) * s - spread), mins[j], s
            elif exitmode == "fixed":
                if mins[j] - mins[ei] >= arg:
                    return ((cl[j] - entry) * s - spread), mins[j], s
            elif exitmode == "trail":
                back = (best - (lo[j] if s > 0 else hi[j])) * s
                if run > 0 and back >= arg:
                    return ((best - s * arg - entry) * s - spread), mins[j], s
        return ((cl[-1] - entry) * s - spread), mins[-1], s

    print(f"\nEVERY ENTRY x EXIT  (points captured, and USD at 0.05 lot)\n")
    exits = ([("M15 close", "m15", 0)]
             + [(f"hold {f}min", "fixed", f) for f in FIXED]
             + [(f"trail {t}pt", "trail", t) for t in TRAILS])
    head = f"{'entry':>12}" + "".join(f"{e[0]:>12}" for e in exits)
    print(head)
    print("-" * len(head))
    best_cell = (None, -1e9)
    for trg in DISTS:
        line = f"{str(trg) + ' pts':>12}"
        for name, mode, arg in exits:
            r = simulate(trg, mode, arg)
            if r is None:
                line += f"{'--':>12}"
                continue
            pts, _, _ = r
            line += f"{pts:>+12.1f}"
            if pts > best_cell[1]:
                best_cell = ((trg, name), pts)
        print(line)
    print("-" * len(head))
    if best_cell[0]:
        trg, name = best_cell[0]
        r = simulate(trg, *[(m, a) for n, m, a in exits if n == name][0])
        print(f"\n  BEST COMBINATION: enter at {trg} pts, exit '{name}'"
              f"  ->  {best_cell[1]:+.1f} pts"
              f"  =  {best_cell[1]*pp05:+.0f} USD at 0.05 lot"
              f"  /  {best_cell[1]*pp05/5:+.0f} at 0.01")
        print(f"  the perfect trade was {best_short + (hi.max()-o):.1f} pts, "
              f"so this captures {100.0*best_cell[1]/(best_short+(hi.max()-o)):.0f}% of it")
    print(f"\n  The bot as configured (2.778 pt gate, M15 close) is the top-left")
    print(f"  region of this table. What it left on the table is the difference")
    print(f"  between that cell and the best one -- and which axis that")
    print(f"  difference sits on says whether the entry or the exit is at fault.")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
