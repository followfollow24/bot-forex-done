#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_error_forensics.py -- what is the error that has silenced several bots?

_why_silent.py found gold_momentum_rsi and btc_lqsweep each carrying
exactly 1,182 errors, and gold_daily_breakout with a log frozen for 892
minutes while its heartbeat kept ticking. Identical counts across two
different strategies point at one shared cause, not three separate bugs.

This pulls the actual error text rather than counting occurrences: the
distinct messages, how often each appears, when it started and whether it
is still happening. It also separates two very different failures that
look alike from outside -- a bot throwing exceptions in a loop, and a bot
whose log simply stopped while the process lives on writing heartbeats.

Every bot in the fleet is checked, not just the ones already suspected,
because a fleet-wide cause would show up in the healthy ones too and the
count of who is affected decides how urgent this is.

Read-only: opens log files, touches nothing.

Usage (on the VPS):  python _error_forensics.py
"""
import glob
import os
import re
from collections import Counter, defaultdict
from datetime import datetime

DESK = os.path.join(os.path.expanduser("~"), "Desktop")
TS = re.compile(r"^(2026-\d\d-\d\d \d\d:\d\d:\d\d)")
ERR = re.compile(r"\[ERROR\]|\[WARNING\]|Traceback|Exception|Error")


def norm(line):
    """Collapse a message to its shape so numbers and symbols do not
    fragment one recurring error into hundreds of unique strings."""
    s = re.sub(r"^\S+ \S+,\d+ ", "", line).strip()
    s = re.sub(r"\d+\.\d+", "<num>", s)
    s = re.sub(r"\b\d+\b", "<n>", s)
    s = re.sub(r"0x[0-9a-fA-F]+", "<addr>", s)
    return s[:150]


def main():
    logs = sorted(glob.glob(os.path.join(DESK, "forex_*.log")))
    print("=" * 92)
    print(" ERROR FORENSICS -- the shared cause behind the silent bots")
    print("=" * 92)
    fleet = Counter()
    for path in logs:
        name = os.path.basename(path)
        try:
            lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
        except OSError:
            continue
        if not lines:
            continue
        tail = lines[-8000:]
        errs = [l for l in tail if ERR.search(l)]
        # last timestamp anywhere in the file = is the log still live?
        last = ""
        for l in reversed(tail):
            m = TS.match(l)
            if m:
                last = m.group(1)
                break
        age = None
        if last:
            try:
                age = (datetime.now() - datetime.strptime(
                    last, "%Y-%m-%d %H:%M:%S")).total_seconds() / 60
            except ValueError:
                pass
        if not errs and (age is None or age < 20):
            continue                      # healthy and live: skip
        print(f"\n  {name}")
        print("  " + "-" * 88)
        print(f"    lines {len(lines)}   last entry {last or '?'}"
              + (f"  ({age:.0f} min ago)" if age is not None else "")
              + ("   <<< LOG FROZEN" if age is not None and age > 20 else ""))
        if not errs:
            print("    no errors -- the log simply STOPPED. The process is alive")
            print("    (heartbeat still written) but its work loop is not running.")
            continue
        shapes = Counter(norm(l) for l in errs)
        first_seen = {}
        for l in errs:
            k = norm(l)
            m = TS.match(l)
            if m and k not in first_seen:
                first_seen[k] = m.group(1)
        for shape, cnt in shapes.most_common(4):
            fleet[shape] += cnt
            print(f"    x{cnt:<6} since {first_seen.get(shape, '?')}")
            print(f"           {shape}")
        # is it still happening, or did it stop?
        recent = [l for l in errs[-40:]]
        if recent:
            m = TS.match(recent[-1])
            print(f"    most recent error: {m.group(1) if m else '?'}")

    print("\n" + "=" * 92)
    print("  SHARED ACROSS THE FLEET (same message shape in several bots)")
    print("=" * 92)
    if not fleet:
        print("  none")
    for shape, cnt in fleet.most_common(6):
        print(f"  x{cnt:<7} {shape}")
    print()
    print("  A frozen log with a live heartbeat is the more dangerous case:")
    print("  every health check this project runs reads the heartbeat, so such")
    print("  a bot reports healthy forever while doing nothing.")


if __name__ == "__main__":
    main()
