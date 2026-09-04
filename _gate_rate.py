#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_gate_rate.py -- how many trades would the bot actually have taken?

The clock bot only enters when the move in its decision window clears a
multiple of the spread. That gate decides the entire trade count, and
until now it had been measured on BTC but never on gold. "How often
would this have traded" is a fair thing to know before funding an
account, and it is cheap: only the first ten seconds of each session are
needed, not the whole holding period, so this runs in a fraction of the
time the exit studies took.

Reported per gate setting, and per calendar month, because an average of
"twice a month" made of one busy quarter and three silent ones is a
different proposition from a steady trickle.

Usage:  python _gate_rate.py [symbol] [days] [decide_s] [weekends:0|1]
"""
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)"); sys.exit(1)

import numpy as np

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "XAUAUDm"
DAYS = int(sys.argv[2]) if len(sys.argv) > 2 else 400
DECIDE = float(sys.argv[3]) if len(sys.argv) > 3 else 3.0
WEEKENDS = bool(int(sys.argv[4])) if len(sys.argv) > 4 else False
THAI, TARGET_H, TARGET_M = 7, 19, 30
GATES = [0.0, 1.0, 2.0, 3.0, 5.0]


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed"); return 2
    info = mt5.symbol_info(SYMBOL)
    if info is None:
        print(f"[ERROR] {SYMBOL} not found"); return 2
    mt5.symbol_select(SYMBOL, True)
    spread = info.spread * info.point
    tk = mt5.symbol_info_tick(SYMBOL)
    off = int(round((tk.time - datetime.now(timezone.utc).timestamp()) / 3600.0))

    moves, months, no_ticks = [], [], 0
    today = (datetime.now(timezone.utc) + timedelta(hours=THAI)).date()
    for back in range(DAYS, 0, -1):
        d = today - timedelta(days=back)
        if d.weekday() >= 5 and not WEEKENDS:
            continue
        s_utc = datetime(d.year, d.month, d.day, TARGET_H - THAI, TARGET_M,
                         tzinfo=timezone.utc)
        s_srv = s_utc + timedelta(hours=off)
        t = mt5.copy_ticks_range(SYMBOL, s_srv, s_srv + timedelta(seconds=12),
                                 mt5.COPY_TICKS_ALL)
        if t is None or len(t) < 2:
            no_ticks += 1
            continue
        t0 = int(s_srv.timestamp() * 1000)
        ms = t["time_msc"].astype(np.int64) - t0
        bid, ask = t["bid"].astype(float), t["ask"].astype(float)
        mid = np.where(ask > 0, (bid + ask) / 2.0, bid)
        m = ms <= DECIDE * 1000
        if m.sum() < 2:
            no_ticks += 1
            continue
        moves.append(abs(float(mid[m][-1] - mid[0])))
        months.append(d.strftime("%Y-%m"))

    n = len(moves)
    print("=" * 74)
    print(f" GATE RATE -- {SYMBOL}   decision window +{DECIDE}s   "
          f"spread {spread:.3f}")
    print(f" {n} sessions had tick history"
          + (f"; {no_ticks} had none and are not counted" if no_ticks else ""))
    print("=" * 74)
    if n < 30:
        print(" not enough sessions"); mt5.shutdown(); return 0

    mv = np.array(moves)
    print(f"\n move in the window: median {np.median(mv):.3f} "
          f"({np.median(mv)/spread:.2f}x spread), mean {mv.mean():.3f}\n")
    print(f" {'gate':>10}{'points':>9}{'trades':>9}{'% of days':>11}"
          f"{'per year':>10}{'per month':>11}")
    print(" " + "-" * 58)
    for g in GATES:
        thr = g * spread
        k = int(np.sum(mv >= thr)) if g > 0 else n
        rate = k / n
        label = "no gate" if g == 0 else f"{g:.0f}x spread"
        print(f" {label:>10}{thr:>9.2f}{k:>9}{100.0*rate:>10.0f}%"
              f"{rate*252:>10.0f}{rate*21:>11.1f}")

    live = 3.0
    thr = live * spread
    hit = [mo for mo, v in zip(months, mv) if v >= thr]
    per = Counter(hit)
    allm = sorted(set(months))
    print(f"\n MONTH BY MONTH at the configured {live:.0f}x gate "
          f"({thr:.2f} points)\n")
    line = ""
    for mo in allm:
        c = per.get(mo, 0)
        line += f"   {mo} {c}"
        if len(line) > 60:
            print(line); line = ""
    if line:
        print(line)
    counts = [per.get(mo, 0) for mo in allm]
    print(f"\n   {len(allm)} months: min {min(counts)}, max {max(counts)}, "
          f"median {int(np.median(counts))} trades per month")
    dry = sum(1 for c in counts if c == 0)
    if dry:
        print(f"   {dry} of {len(allm)} months would have had NO trade at all")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
