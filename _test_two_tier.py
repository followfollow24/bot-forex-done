#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the two-tier size: bigger lot when the gate falls fast.

The rule is worth testing because it changes real position size on exactly
the sessions that move hardest, and because it is easy to get backwards --
a gate priced off the fast lot would trip on a nearer distance than the one
printed in the banner, and a fast lot smaller than the ordinary one would
read as sizing up while sizing down.
"""
import io
import sys

sys.modules.setdefault("MetaTrader5", type(sys)("MetaTrader5"))
import clock_scalp_bot as B

SRC = io.open("clock_scalp_bot.py", encoding="utf-8").read()
ok = fail = 0


def check(cond, what):
    global ok, fail
    if cond:
        ok += 1
    else:
        fail += 1
        print(f"  FAILED: {what}")


def body(fn):
    i = SRC.index(f"def {fn}(")
    j = SRC.find("\ndef ", i + 1)
    return SRC[i:] if j < 0 else SRC[i:j]


F, O, H = "fast", "ordinary", "held-back"

print("size_for -- the choice itself")
check(B.size_for(1.4, 0.01, 0.05, 10) == (0.05, F), "1.4s is fast")
check(B.size_for(6.3, 0.01, 0.05, 10) == (0.05, F), "6.3s is fast at 10s")
check(B.size_for(6.3, 0.01, 0.05, 5) == (0.01, O), "6.3s is slow at 5s")
check(B.size_for(10.0, 0.01, 0.05, 10) == (0.05, F), "boundary is inclusive")
check(B.size_for(10.001, 0.01, 0.05, 10) == (0.01, O), "just past is slow")
check(B.size_for(0.0, 0.01, 0.05, 10) == (0.05, F), "instant is fast")

print("size_for -- the tier stays off unless it is configured")
check(B.size_for(1.4, 0.01, 0.05, 0) == (0.01, O), "fast_sec 0 disables")
check(B.size_for(1.4, 0.01, None, 10) == (0.01, O), "no fast lot given")
check(B.size_for(1.4, 0.01, 0.0, 10) == (0.01, O), "zero fast lot")

print("size_for -- the equity floor, which is why the tier is affordable")
# The account this was written for: 43.38 today, 200 after the top-up.
# At 0.05 lot the fast sessions measured over three months went 71 to 121
# in account currency against the entry before paying.
check(B.size_for(1.4, 0.01, 0.05, 10, 43.38, 180) == (0.01, H),
      "today's equity cannot hold the fast size")
check(B.size_for(1.4, 0.01, 0.05, 10, 200.0, 180) == (0.05, F),
      "after the top-up it can")
check(B.size_for(1.4, 0.01, 0.05, 10, 179.99, 180) == (0.01, H),
      "just under the floor is held back")
check(B.size_for(1.4, 0.01, 0.05, 10, 180.0, 180) == (0.05, F),
      "exactly at the floor arms")
check(B.size_for(1.4, 0.01, 0.05, 10, None, 180) == (0.01, H),
      "unreadable equity is not permission to size up")
check(B.size_for(1.4, 0.01, 0.05, 10, 43.38, 0) == (0.05, F),
      "floor 0 means no floor, as documented")
check(B.size_for(30.0, 0.01, 0.05, 10, 200.0, 180) == (0.01, O),
      "a slow day stays ordinary however rich the account")
# after one bad fast day the floor disarms the tier by itself
check(B.size_for(1.4, 0.01, 0.05, 10, 200.0 - 121.0, 180) == (0.01, H),
      "a 121 loss drops equity under the floor, disarming the next one")

print("the gate is priced before the size is chosen")
b = body("run_once")
check(b.index("gate = a.gate_money / float(per_pt)") < b.index("size_for("),
      "the money gate is computed at the ordinary lot, ahead of the choice")
check(b.index("size_for(") < b.index("order_calc_margin"),
      "margin is tested on the size actually being sent")
check(b.index("size_for(") < b.index("a.max_risk_pct"),
      "the risk cap is tested on the size actually being sent")
check(b.index("size_for(") < b.index("res = send_order"),
      "the size is settled before the order goes out")

print("a fixed-point gate overrides the money gate")
for fn in ("run_once", "selftest"):
    f = body(fn)
    check("if a.gate_pts > 0:" in f, f"{fn} honours --gate-pts")
    check(f.index("if a.gate_pts > 0:") < f.index("a.gate_money >"),
          f"{fn} checks points before money")

print("bad configurations are refused at startup, not mid-session")
m = body("main")
check("--fast-sec given without --fast-lot" in m, "fast-sec alone refused")
check("--fast-lot given without --fast-sec" in m, "fast-lot alone refused")
check("is SMALLER than --lot" in m, "an inverted pair is refused")
check(m.index("a.fast_lots = {}") < m.index("if a.fast_sec > 0:"),
      "fast_lots always exists, so run_once never reads a missing attribute")

print("the preflight reports the room the fast lot leaves")
c = body("selftest")
check("runs out" in c and "pts against the entry" in c,
      "check prints points-to-liquidation at the fast lot")
check("20-33 pts against the entry" in c,
      "check names the measured excursion the tier has to survive")

print("the floor is read at entry, not once at startup")
b2 = body("run_once")
check(b2.index("mt5.account_info()") < b2.index("size_for("),
      "equity is re-read in the session, so a drawdown disarms the tier")
check("fast_min_equity" in b2, "run_once passes the floor through")
check('"held-back"' in b2 or "'held-back'" in b2,
      "run_once reports a held-back day rather than silently sizing down")

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
