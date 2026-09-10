#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_day_path.py -- where price actually went after the bell, hour by hour.

The operator is looking at the chart and saying the move ran a long way.
The 30-minute replay cannot answer that, because it stops at 30 minutes.
If price kept falling afterwards then the exit is too SHORT and the move
was captured badly; if it came back and stayed back, the exit is fine and
the move simply reversed. Those are opposite conclusions and the only
thing separating them is looking past the end of the hold.

So this walks the whole session out to a chosen number of hours and prints
the path relative to BOTH the 19:30 reference and the entry the rule took,
with the running best and worst, so where the 30-minute exit falls is
visible against everything that came after it.

Uses M5 bars rather than ticks: the question here is shape over hours, not
the millisecond the gate was crossed.

Usage:  python _day_path.py [symbol] [YYYY-MM-DD] [hours]
"""
import sys
from datetime import datetime, timedelta, timezone

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)"); sys.exit(1)

import numpy as np

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "XAUAUDm"
DAY = sys.argv[2] if len(sys.argv) > 2 else None
HOURS = float(sys.argv[3]) if len(sys.argv) > 3 else 8.0
GATE, MIN_WAIT, MAXW, HOLD_MIN, THAI = 14.0, 1.0, 900, 30.0, 7


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed"); return 2
    if mt5.symbol_info(SYMBOL) is None:
        print(f"[ERROR] {SYMBOL} not found"); mt5.shutdown(); return 2
    mt5.symbol_select(SYMBOL, True)

    d = (datetime.strptime(DAY, "%Y-%m-%d").date() if DAY else
         (datetime.now(timezone.utc) + timedelta(hours=THAI)).date())
    bell = datetime(d.year, d.month, d.day, 12, 30, tzinfo=timezone.utc)

    t = mt5.copy_ticks_range(SYMBOL, bell, bell + timedelta(seconds=MAXW + 60),
                             mt5.COPY_TICKS_ALL)
    if t is None or len(t) < 20:
        print(f"[ERROR] no ticks for {d}"); mt5.shutdown(); return 2
    t0 = int(bell.timestamp())
    sec = (t["time_msc"].astype(np.int64) - t0 * 1000) / 1000.0
    bid, ask = t["bid"].astype(float), t["ask"].astype(float)
    mid = np.where(ask > 0, (bid + ask) / 2.0, bid)
    ref = float(mid[0])
    w = np.where((sec >= MIN_WAIT) & (sec <= MAXW)
                 & (np.abs(mid - ref) >= GATE))[0]
    if len(w) == 0:
        print(f"gate never cleared on {d} -- nothing to trace")
        mt5.shutdown(); return 0
    i = int(w[0])
    side = 1 if mid[i] > ref else -1
    entry = float(ask[i]) if side > 0 else float(bid[i])
    t_in = float(sec[i])

    bars = mt5.copy_rates_range(SYMBOL, mt5.TIMEFRAME_M5, bell,
                                bell + timedelta(hours=HOURS + 1))
    if bars is None or len(bars) < 6:
        print("[ERROR] no M5 bars"); mt5.shutdown(); return 2

    print("=" * 72)
    print(f" WHERE IT WENT AFTER 19:30 -- {SYMBOL} {d}   next {HOURS:.0f} hours")
    print(f" reference {ref:.3f}   {'SELL' if side < 0 else 'BUY'} entry "
          f"{entry:.3f} at +{t_in/60:.1f} min   30-min exit marked <<<")
    print("=" * 72)
    print(f"\n{'Thai':>8}{'+min':>7}{'price':>11}{'vs ref':>9}"
          f"{'vs entry':>10}{'best':>8}{'worst':>8}")
    best = worst = 0.0
    exit_min = t_in / 60.0 + HOLD_MIN
    shown_exit = False
    for b in bars:
        m = (int(b["time"]) - t0) / 60.0
        if m < 0 or m > HOURS * 60:
            continue
        c = float(b["close"])
        pnl = (c - entry) * side
        best, worst = max(best, pnl), min(worst, pnl)
        mark = ""
        if not shown_exit and m >= exit_min:
            mark = "  <<< the rule leaves here"; shown_exit = True
        if m % 15 < 5 or mark:
            thai = (bell + timedelta(minutes=m) + timedelta(hours=THAI))
            print(f"{thai:%H:%M}{m:>7.0f}{c:>11.3f}{c-ref:>+9.2f}"
                  f"{pnl:>+10.2f}{best:>+8.2f}{worst:>+8.2f}{mark}")
    print(f"\n  vs ref   = distance from the 19:30 reference")
    print(f"  vs entry = what the position was worth, sign-corrected")
    print(f"\n  If 'vs entry' keeps RISING long after the exit line, the hold")
    print(f"  is too short and the move was captured badly. If it comes back")
    print(f"  and stays back, the exit is fine and the move simply reversed.")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
