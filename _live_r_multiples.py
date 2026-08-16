#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_live_r_multiples.py -- what did btc_h1_manual's 13 live trades actually pay?

_tp15_base_rate.py closed the first question: the 15xATR target sits 6R
away and lands about once in 25 trades, so seeing zero of them in 13 is
the MOST LIKELY outcome (69% long / 49% short), not a defect. It also
showed the entry filter earns its keep -- random entries on this geometry
win 32-36%, the bot wins 61.5%.

That leaves an arithmetic problem. At 61.5% wins, with the modelled
payoffs (losers -1R, timeouts averaging +1.3R), the bot should make about
+0.41R a trade. It lost 421.62 instead. Something in the live trades does
not match the model, and the aggregate cannot say which:

  A. the winners are far smaller than modelled -- exits landing near
     break-even rather than at the +1.3R average. A property of the
     strategy, and the far target is then the wrong exit for it.
  B. the losers are BIGGER than 1R -- stops filling past their level.
     That is execution, not strategy, and it would apply to every bot in
     the fleet, not just this one.

Those demand completely different responses, so this reads every real
[OPEN]/[CLOSE-*] pair out of the live log and prices each trade in R
using its own stop distance -- the only unit that makes differently-sized
trades comparable.

R comes from the order the broker actually received (|fill - SL|), not
from an intended distance, so slippage on entry is already inside the
denominator rather than quietly flattering the result.

Usage (on the VPS):  python _live_r_multiples.py [variant] [log_path]
  e.g.               python _live_r_multiples.py btc_h1_manual
"""
import glob
import os
import re
import sys
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
VARIANT = sys.argv[1] if len(sys.argv) > 1 else "btc_h1_manual"

OPEN_RE = re.compile(
    r"^(?P<ts>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ \[INFO\]\s+\[OPEN\] "
    r"(?P<side>LONG|SHORT) (?P<sym>\S+) lot=(?P<lot>[\d.]+)\s+"
    r"fill=(?P<fill>[\d.]+)\s+SL=(?P<sl>[\d.]+) TP=(?P<tp>[\d.]+)\s+"
    r"spread=(?P<spread>[\d.]+)\s+slippage=(?P<slip>[+-][\d.]+)\s+"
    r"commission=(?P<comm>[\d.-]+)")

CLOSE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ \[INFO\]\s+\[CLOSE-(?P<reason>[A-Z/ ]+)\] "
    r"(?P<side>LONG|SHORT) (?P<sym>\S+) lot=(?P<lot>[\d.]+)\s+"
    r"fill=(?P<fill>[\d.]+)\s+net_pnl=(?P<pnl>[+-][\d.]+)\s+"
    r"spread=(?P<spread>[\d.]+)\s+slippage=(?P<slip>[+-][\d.]+)\s+"
    r"commission=(?P<comm>[\d.-]+)")


def find_log():
    if len(sys.argv) > 2:
        return sys.argv[2]
    pats = [os.path.join(d, f"*{VARIANT}*.log")
            for d in (BASE, os.path.join(os.path.expanduser("~"), "Desktop"))]
    hits = []
    for p in pats:
        hits += glob.glob(p)
    if not hits:
        print(f"[ERROR] no *{VARIANT}*.log found beside this script or on the Desktop")
        sys.exit(1)
    hits.sort(key=lambda p: os.path.getsize(p), reverse=True)
    return hits[0]


def main():
    log = find_log()
    events = []
    with open(log, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.rstrip("\n")
            m = OPEN_RE.match(s)
            if m:
                events.append(("OPEN", m.groupdict()))
                continue
            m = CLOSE_RE.match(s)
            if m:
                events.append(("CLOSE", m.groupdict()))

    # max-positions is 1 for this bot, so opens and closes strictly alternate.
    # Pair them in order and report anything left dangling rather than
    # silently dropping it -- an unpaired OPEN is usually a broker-side stop
    # the bot never logged, which is exactly the case that matters here.
    trades, pending, orphans = [], None, 0
    for kind, d in events:
        if kind == "OPEN":
            if pending is not None:
                orphans += 1
            pending = d
        else:
            if pending is None:
                orphans += 1
                continue
            trades.append((pending, d))
            pending = None

    print("=" * 100)
    print(f" LIVE R-MULTIPLES -- {VARIANT}")
    print(f" log: {log}")
    print(f" parsed {len(events)} events -> {len(trades)} paired trades"
          + (f", {orphans} UNPAIRED" if orphans else ""))
    if pending is not None:
        print(" (one position still open, excluded)")
    print("=" * 100)
    if not trades:
        print("  no paired trades -- broker-side stops may not be logged as")
        print("  [CLOSE-*]; cross-check against MT5 history_deals if so.")
        return

    print(f"  {'opened':<13}{'side':<7}{'lot':>6}{'R (pts)':>10}  {'exit':<12}"
          f"{'R mult':>8}{'net $':>10}{'slip in':>9}{'slip out':>9}")
    print("  " + "-" * 96)

    rows = []
    for o, c in trades:
        fill = float(o["fill"])
        R = abs(fill - float(o["sl"]))
        if R <= 0:
            continue
        long_ = o["side"] == "LONG"
        move = (float(c["fill"]) - fill) if long_ else (fill - float(c["fill"]))
        rmult = move / R
        rows.append({
            "ts": o["ts"], "side": o["side"], "lot": float(o["lot"]),
            "R": R, "reason": c["reason"].strip(), "rmult": rmult,
            "pnl": float(c["pnl"]),
            "slip_in": float(o["slip"]), "slip_out": float(c["slip"]),
        })
        print(f"  {datetime.strptime(o['ts'], '%Y-%m-%d %H:%M:%S'):%m-%d %H:%M}  "
              f"{o['side']:<7}{float(o['lot']):>6.2f}{R:>10.1f}  "
              f"{c['reason'].strip():<12}{rmult:>+8.2f}{float(c['pnl']):>+10.2f}"
              f"{float(o['slip']):>+9.2f}{float(c['slip']):>+9.2f}")

    print("  " + "-" * 96)
    n = len(rows)
    wins = [r for r in rows if r["pnl"] > 0]
    losses = [r for r in rows if r["pnl"] <= 0]
    aw = sum(r["rmult"] for r in wins) / len(wins) if wins else 0.0
    al = sum(r["rmult"] for r in losses) / len(losses) if losses else 0.0
    print(f"  {n} trades   wins {len(wins)} ({100*len(wins)/n:.1f}%)   "
          f"net ${sum(r['pnl'] for r in rows):+.2f}   total {sum(r['rmult'] for r in rows):+.2f}R")
    print(f"  average WIN  {aw:+.2f}R      average LOSS {al:+.2f}R")

    print("\n" + "=" * 100)
    print("  WHICH EXPLANATION HOLDS?")
    print("=" * 100)
    # B: are stops filling past their level?
    worse = [r for r in losses if r["rmult"] < -1.05]
    print(f"  B. losers worse than -1R : {len(worse)}/{len(losses)}"
          + (f"   worst {min(r['rmult'] for r in losses):+.2f}R" if losses else ""))
    tot_slip = sum(abs(r["slip_in"]) + abs(r["slip_out"]) for r in rows)
    print(f"     total |slippage| across {n} trades: {tot_slip:.2f} price units")
    if worse:
        print("     -> stops ARE filling past their level. That is EXECUTION,")
        print("        and it would affect every bot in the fleet, not just this one.")
    else:
        print("     -> stops are landing at or inside 1R. Execution is not the leak.")
    # A: are the winners simply small?
    print(f"\n  A. average win {aw:+.2f}R vs the +1.30R the geometry models")
    if wins and aw < 0.8:
        print("     -> winners are far smaller than modelled. The far 15xATR target")
        print("        is the wrong exit for what these trades actually do: they")
        print("        are being closed near break-even while losers pay in full.")
    elif wins:
        print("     -> winners are close to the model; the shortfall is elsewhere.")
    print()
    by_reason = {}
    for r in rows:
        by_reason.setdefault(r["reason"], []).append(r)
    print("  exits by reason:")
    for k, v in sorted(by_reason.items(), key=lambda kv: -len(kv[1])):
        print(f"    {k:<14}{len(v):>3}   avg {sum(x['rmult'] for x in v)/len(v):+.2f}R"
              f"   net ${sum(x['pnl'] for x in v):+.2f}")
    print()
    print("  n is small. Treat this as locating the leak, not sizing it.")


if __name__ == "__main__":
    main()
