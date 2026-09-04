#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Replay synthetic tick streams through the bot's real entry rule.

Answers, in code rather than prose, the question "does it actually watch
from 19:30:00 and enter from one second onward, or does it sit out the
first N seconds?"
"""
import importlib.util
import sys
import types

# The bot exits at import when MetaTrader5 is missing, which is correct
# for a trading process and useless for a logic test. A stand-in module
# lets the pure decision rule be imported and replayed off the VPS; it is
# never called, because entry_decision touches no broker state.
sys.modules.setdefault("MetaTrader5", types.ModuleType("MetaTrader5"))

spec = importlib.util.spec_from_file_location("bot", "clock_scalp_bot.py")
bot = importlib.util.module_from_spec(spec)
sys.modules["bot"] = bot
spec.loader.exec_module(bot)

GATE, MINW, MAXW = 2.26, 1.0, 900.0
fails = []


def replay(ticks, label, expect_dir, expect_at):
    """ticks: [(seconds, price)]; price[0] is the 19:30:00.000 reference."""
    ref = ticks[0][1]
    fired_at = fired_dir = None
    for t, px in ticks:
        d = bot.entry_decision(t, px, ref, GATE, MINW, MAXW)
        if d is None:
            break
        if d:
            fired_at, fired_dir = t, d
            break
    ok = (fired_dir == expect_dir) and (
        fired_at == expect_at if expect_at is None else
        abs((fired_at or -1) - expect_at) < 1e-9)
    print(f"  {'OK  ' if ok else 'FAIL'} {label}"
          f"  -> {'no entry' if not fired_dir else f'{fired_dir:+d} at {fired_at}s'}")
    if not ok:
        fails.append(label)


print("\nentry rule replay  (gate 2.26, min wait 1.0s)")

# the move clears the gate at 0.4s -- too early to act, but watching does
# not stop: it must fire at the first tick at or after 1.0s
replay([(0.0, 6200.00), (0.2, 6200.50), (0.4, 6197.00), (0.7, 6196.00),
        (1.0, 6195.50), (2.0, 6195.00)],
       "gate cleared at 0.4s, entry permitted from 1.0s", -1, 1.0)

# a 4 Sep style collapse: 30 points inside 1.4s
replay([(0.0, 6203.10), (0.5, 6202.00), (1.1, 6190.00), (1.4, 6173.00)],
       "fast collapse -- fires on the first tick past 1.0s", -1, 1.1)

# a slow day: nothing clears until 34 seconds
replay([(0.0, 6200.0), (1.0, 6200.4), (5.0, 6200.9), (12.0, 6201.5),
        (34.0, 6202.4), (60.0, 6203.0)],
       "slow day -- keeps watching, enters at 34s", +1, 34.0)

# noise inside the gate all session -> no trade
replay([(0.0, 6200.0), (1.0, 6200.9), (60.0, 6199.2), (400.0, 6201.0),
        (900.0, 6200.5)],
       "never clears the gate -- no trade", None, None)

# exactly at the gate counts as cleared
replay([(0.0, 6200.0), (1.0, 6200.0), (2.0, 6202.26)],
       "a move exactly equal to the gate is taken", +1, 2.0)

# an unchanged price is not a direction, even once the wait has elapsed
replay([(0.0, 6200.0), (0.5, 6200.0), (1.2, 6200.0), (2.0, 6202.30)],
       "flat price yields no direction; entry waits for a real move",
       +1, 2.0)

print()
if fails:
    print(f"{len(fails)} FAILED"); sys.exit(1)
print("ALL ENTRY-RULE REPLAYS PASSED")
