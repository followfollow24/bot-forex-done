#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_direction_test.py -- one question, no parameters to choose.

Everything that fell apart in this project fell apart the same way: a
value was picked on the data it was then reported on. So this asks the
single thing left standing after 19:30 was emptied out, in a form that
has nothing to select.

THE QUESTION. When price has moved G points away from the 19:30
reference, does the direction it moved predict where it goes over the
next N minutes?

Measured as the mean of (later move x direction) in points, using MID
prices and NO stop. That is deliberate: a stop is a parameter, and spread
is a cost, and mixing either into the measurement turns "is there a
signal" into "does this particular strategy profit", which is a different
and much easier question to fool yourself on. Cost is printed beside the
result instead, so the comparison stays explicit -- a signal smaller than
the spread is real and useless at the same time, and that distinction is
worth keeping visible.

Fading is not tested as a separate rule. Without a stop it is just the
negative of following, so "follow beats fade" would restate the same
number rather than confirm it.

Nothing here is selected. Every gate and every hold is reported, on each
half of the history, and the only thing looked for is whether a sign is
the SAME in both halves. One cell agreeing is chance; a block agreeing is
worth a second look.

Usage:  python _direction_test.py [symbol] [days] [thai_hour] [thai_min]
        python _direction_test.py XAUAUDm 365 19 30
"""
import sys
from datetime import date, datetime, timedelta, timezone

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)"); sys.exit(1)

import numpy as np

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "XAUAUDm"
DAYS = int(sys.argv[2]) if len(sys.argv) > 2 else 365
TH = int(sys.argv[3]) if len(sys.argv) > 3 else 19
TM = int(sys.argv[4]) if len(sys.argv) > 4 else 30
GATES = [3, 5, 8, 11, 14, 20]
HOLDS = [10, 20, 30, 45, 60, 90]
SPLIT = date(2026, 6, 1)
MIN_WAIT, MAXW, THAI = 1.0, 900, 7


def load(sym):
    out = []
    today = (datetime.now(timezone.utc) + timedelta(hours=THAI)).date()
    uh, um = (TH - THAI) % 24, TM
    for back in range(DAYS, 0, -1):
        d = today - timedelta(days=back)
        if d.weekday() >= 5:
            continue
        s = datetime(d.year, d.month, d.day, uh, um, tzinfo=timezone.utc)
        t = mt5.copy_ticks_range(sym, s, s + timedelta(seconds=MAXW + 60),
                                 mt5.COPY_TICKS_ALL)
        if t is None or len(t) < 20:
            continue
        bars = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M5, s,
                                    s + timedelta(minutes=200))
        if bars is None or len(bars) < 12:
            continue
        t0 = int(s.timestamp())
        sec = (t["time_msc"].astype(np.int64) - t0 * 1000) / 1000.0
        bid, ask = t["bid"].astype(float), t["ask"].astype(float)
        mid = np.where(ask > 0, (bid + ask) / 2.0, bid)
        ref = float(mid[0])
        row = {"date": d, "sig": {}}
        for g in GATES:
            w = np.where((sec >= MIN_WAIT) & (np.abs(mid - ref) >= g))[0]
            if len(w) == 0:
                continue
            i = int(w[0])
            side = 1 if mid[i] > ref else -1
            px0 = float(mid[i])
            srv = t0 + float(sec[i])
            for h in HOLDS:
                end = srv + h * 60
                px1 = None
                for b in bars:
                    if int(b["time"]) + 300 >= end:
                        px1 = float(b["close"]); break
                if px1 is None:
                    continue
                row["sig"][(g, h)] = (px1 - px0) * side
        if row["sig"]:
            out.append(row)
    return out


def cell(rows, key):
    v = [r["sig"][key] for r in rows if key in r["sig"]]
    if len(v) < 8:
        return None
    a = np.array(v)
    sd = float(a.std(ddof=1))
    t = float(a.mean() / (sd / np.sqrt(len(a)))) if sd > 0 else 0.0
    return len(a), float(a.mean()), t, float((a > 0).mean() * 100.0)


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed"); return 2
    si = mt5.symbol_info(SYMBOL)
    if si is None:
        print(f"[ERROR] {SYMBOL} not found"); mt5.shutdown(); return 2
    mt5.symbol_select(SYMBOL, True)
    spread = si.spread * si.point
    print(f"loading {DAYS} days at {TH:02d}:{TM:02d} Thai ...", flush=True)
    rows = load(SYMBOL)
    if len(rows) < 40:
        print(f"[ERROR] only {len(rows)} sessions"); mt5.shutdown(); return 2
    early = [r for r in rows if r["date"] < SPLIT]
    late = [r for r in rows if r["date"] >= SPLIT]

    print("=" * 78)
    print(f" DOES THE DIRECTION PREDICT? -- {SYMBOL} at {TH:02d}:{TM:02d} Thai")
    print(f" {len(rows)} sessions   EARLY {len(early)} | LATE {len(late)}"
          f"   split {SPLIT}")
    print(f" mid prices, no stop, no costs. To be worth trading a cell must")
    print(f" beat spread {spread:.2f} + slippage -- call it {spread+0.25:.2f} pts.")
    print("=" * 78)

    agree = tot = 0
    for g in GATES:
        print(f"\n  gate {g} pts")
        print(f"    {'hold':>6}{'EARLY mean':>13}{'t':>7}{'hit%':>7}{'n':>5}"
              f"{'LATE mean':>13}{'t':>7}{'hit%':>7}{'n':>5}   sign")
        for h in HOLDS:
            a, b = cell(early, (g, h)), cell(late, (g, h))
            if a is None or b is None:
                continue
            tot += 1
            same = (a[1] > 0) == (b[1] > 0)
            agree += same
            mark = ("BOTH +" if same and a[1] > 0 else
                    "BOTH -" if same else "split")
            print(f"    {h:>5}m{a[1]:>+13.2f}{a[2]:>+7.2f}{a[3]:>7.0f}{a[0]:>5}"
                  f"{b[1]:>+13.2f}{b[2]:>+7.2f}{b[3]:>7.0f}{b[0]:>5}   {mark}")

    print("\n" + "=" * 78)
    print(f" {agree} of {tot} cells have the SAME sign in both halves "
          f"(chance gives ~{tot/2:.0f})")
    print("=" * 78)
    print("  A signal that exists would show as a BLOCK of 'BOTH +' with")
    print("  means above the cost line -- not one lucky cell. Scattered")
    print("  agreement at about half the cells is what noise looks like.")
    print("\n  't' near 0 means the mean is indistinguishable from zero at")
    print("  that sample size. |t| above ~2 is the usual bar, and with")
    print(f"  {tot} cells shown, one or two clearing it is expected anyway.")
    print("\n  Positive but below the cost line = real and unprofitable.")
    print("  That is still an answer, and it closes the question honestly.")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
