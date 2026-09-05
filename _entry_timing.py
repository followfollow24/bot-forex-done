#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_entry_timing.py -- what should decide the entry: the clock, or distance?

The operator's question, exactly: enter too early and the direction is
wrong; enter too late and the move is over. Something has to decide, and
the two candidates are a fixed number of seconds and a fixed distance
from the 19:30 price. This measures what each one costs and buys.

Four numbers per setting, because the trade-off has two sides and a
single P&L column hides both:

  FIRED     how often the trigger happens at all. A rule that only fires
            on a quarter of days is not comparable to one that fires
            every day, however good its average looks.
  RIGHT     how often the direction it chose matched where price actually
            was at the M15 close. This is the "too early" cost -- a
            one-second read on 4 Sep said UP a second before a 77 point
            fall.
  GIVEN     how much of the move had already happened before the entry
            went on. This is the "too late" cost, and it is invisible in
            a win rate.
  LEFT      how far price still travelled the chosen way after entry. The
            part you can actually be paid for.

A good trigger is not the one with the best RIGHT -- waiting five minutes
gets the direction right almost every time and leaves nothing to collect.
It is the one where LEFT minus the cost of being wrong is largest.

Usage:  python _entry_timing.py [symbol] [days]
"""
import sys
from datetime import datetime, timedelta, timezone

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)"); sys.exit(1)

import numpy as np

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "XAUAUDm"
DAYS = int(sys.argv[2]) if len(sys.argv) > 2 else 60
THAI, MAXW = 7, 900
DISTS = [2, 4, 6, 8, 11, 15, 20, 25, 30]
TIMES = [1, 3, 5, 10, 20, 30, 60, 120, 300]


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed"); return 2
    info = mt5.symbol_info(SYMBOL)
    if info is None:
        print(f"[ERROR] {SYMBOL} not found"); return 2
    mt5.symbol_select(SYMBOL, True)
    spread = info.spread * info.point

    dist_rows = {d: [] for d in DISTS}
    time_rows = {t: [] for t in TIMES}
    days = 0
    today = (datetime.now(timezone.utc) + timedelta(hours=THAI)).date()

    for back in range(DAYS, 0, -1):
        dt = today - timedelta(days=back)
        if dt.weekday() >= 5:
            continue
        s_utc = datetime(dt.year, dt.month, dt.day, 12, 30, tzinfo=timezone.utc)
        t = mt5.copy_ticks_range(SYMBOL, s_utc,
                                 s_utc + timedelta(seconds=MAXW + 60),
                                 mt5.COPY_TICKS_ALL)
        bars = mt5.copy_rates_range(SYMBOL, mt5.TIMEFRAME_M5, s_utc,
                                    s_utc + timedelta(minutes=60))
        if t is None or len(t) < 30 or bars is None or len(bars) < 4:
            continue
        days += 1
        t0 = int(s_utc.timestamp())
        sec = (t["time_msc"].astype(np.int64) - t0 * 1000) / 1000.0
        px = np.where(t["ask"] > 0,
                      (t["bid"].astype(float) + t["ask"].astype(float)) / 2.0,
                      t["bid"].astype(float))
        ref = float(px[0])

        def outcome(i, s):
            """From tick i in direction s: what was given up, what was left."""
            entry = float(px[i])
            given = (entry - ref) * s          # move already spent
            srv_e = t0 + float(sec[i])
            end = (int(srv_e) // 900 + 1) * 900
            close = None
            best = entry
            for b in bars:
                bt = int(b["time"])
                if bt + 300 <= srv_e:
                    continue
                hi, lo, c = float(b["high"]), float(b["low"]), float(b["close"])
                best = max(best, hi) if s > 0 else min(best, lo)
                if bt + 300 >= end:
                    close = c; break
            if close is None:
                close = float(bars[-1]["close"])
            left = (close - entry) * s
            reach = (best - entry) * s          # best it ever offered
            return given, left, reach

        for d in DISTS:
            w = np.where(np.abs(px - ref) >= d)[0]
            if len(w) == 0:
                dist_rows[d].append(None)
                continue
            i = int(w[0])
            s = 1 if px[i] > ref else -1
            g, l, r = outcome(i, s)
            dist_rows[d].append((float(sec[i]), g, l, r))

        for tt in TIMES:
            m = sec <= tt
            if m.sum() < 2:
                time_rows[tt].append(None)
                continue
            i = int(np.where(m)[0][-1])
            if px[i] == ref:
                time_rows[tt].append(None)
                continue
            s = 1 if px[i] > ref else -1
            g, l, r = outcome(i, s)
            time_rows[tt].append((float(sec[i]), g, l, r))

    print("=" * 88)
    print(f" WHAT SHOULD DECIDE THE ENTRY -- {SYMBOL}, {days} sessions at "
          f"19:30 Thai")
    print(f" spread {spread:.2f}.  GIVEN = move already spent before entry.  "
          f"LEFT = move after it.")
    print(f" exit is the M15 close, as the bot does. All figures in price "
          f"points, median unless said.")
    print("=" * 88)

    def table(title, rows, keys, keylabel, timecol):
        print(f"\n{title}\n")
        print(f"{keylabel:>10}{'fired':>8}{timecol:>10}{'RIGHT':>8}"
              f"{'GIVEN':>8}{'LEFT':>8}{'reach':>8}{'LEFT-cost':>11}")
        print("-" * 72)
        for k in keys:
            r = [x for x in rows[k] if x is not None]
            if len(r) < 5:
                print(f"{k:>10}{len(r):>8}   -- too few --")
                continue
            sc = np.array([x[0] for x in r])
            gv = np.array([x[1] for x in r])
            lf = np.array([x[2] for x in r])
            rc = np.array([x[3] for x in r])
            right = 100.0 * np.mean(lf > 0)
            # what the trigger actually earns: mean of LEFT net of spread
            net = lf.mean() - spread
            print(f"{k:>10}{100.0*len(r)/max(days,1):>7.0f}%"
                  f"{np.median(sc) if timecol == 'at (s)' else np.median(np.abs(gv)):>10.1f}"
                  f"{right:>7.0f}%{np.median(gv):>+8.1f}{np.median(lf):>+8.1f}"
                  f"{np.median(rc):>+8.1f}{net:>+11.2f}")

    table("A. TRIGGER ON DISTANCE  (enter the first time price is N points "
          "from the 19:30 price)",
          dist_rows, DISTS, "distance", "at (s)")
    table("B. TRIGGER ON THE CLOCK  (enter at N seconds, whichever way it "
          "has moved by then)",
          time_rows, TIMES, "seconds", "moved")

    print("\n  RIGHT is the share of sessions where price at the M15 close was")
    print("  still beyond the entry in the chosen direction.")
    print("  LEFT-cost is the MEAN points after entry net of spread -- the")
    print("  column to rank on, since it already contains the losses.")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
