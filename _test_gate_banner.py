#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The gate is decided in three places -- they must agree.

run_once arms the real gate, selftest reports it before a live start, and
main's banner announces it at startup. Each grew its own copy of the same
precedence, and the banner was left behind when --gate-pts was added: a
bot trading a fixed 14-point gate announced "gate 2.0x spread". The trade
was right and the line the operator reads was wrong, which is the worse
of the two failures because it is the one that gets believed.
"""
import sys

SRC = open("clock_scalp_bot.py", encoding="utf-8").read()
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


print("all three gate sites use the same precedence")
# run_once and selftest assign the spread multiple as the DEFAULT and then
# override it in an if/elif chain, so the invariant is the chain's order,
# not where each name first appears in the text.
for fn in ("run_once", "selftest"):
    b = body(fn)
    check("gate_pts" in b, f"{fn} knows about --gate-pts")
    default = b.find("gate = a.min_move_spread * spread")
    ip = b.find("if a.gate_pts > 0:")
    im = b.find("a.gate_money >")
    check(default >= 0 and ip > default,
          f"{fn} sets the spread multiple as the fallback, before the chain")
    check(ip >= 0 and im >= 0 and ip < im,
          f"{fn} tests points BEFORE money in the chain")

# main builds one conditional expression, so all three names are in it and
# the order they appear IS the precedence.
m = body("main")
check("gate_pts" in m, "main knows about --gate-pts")
ip, im, isp = (m.find("a.gate_pts"), m.find("a.gate_money"),
               m.find("a.min_move_spread"))
check(0 <= ip < im < isp,
      "main announces points, then money, then the spread multiple")

print("the banner names the gate that will actually be armed")
m = body("main")
check('f"{a.gate_pts:g} pts, fixed"' in m,
      "a fixed-point gate is announced as points, not as a spread multiple")
r = body("run_once")
check('f"{a.gate_pts:g} pts, fixed"' in r,
      "and the session labels it the same way, so log and banner match")

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
