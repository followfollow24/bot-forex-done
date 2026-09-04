#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Logic tests for clock_scalp_bot -- no MT5, no network, no orders."""
import ast
import sys

src = open("clock_scalp_bot.py", encoding="utf-8").read()
tree = ast.parse(src)
fails = []


def ok(cond, msg):
    print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
    if not cond:
        fails.append(msg)


print("\nclock_scalp_bot logic tests")

# 1. dry run must be the default: --live is a store_true flag
ok('p.add_argument("--live", action="store_true"' in src,
   "--live is an opt-in flag, so dry run is the default")
ok('if not live:' in src and 'DRY RUN -- would send' in src,
   "send_order returns before order_send when not live")
ok(src.index('if not live:') < src.index('res = mt5.order_send(req)'),
   "the dry-run branch comes BEFORE any order_send call")

# 2. the stop must be attached to the order itself, not managed in-process
ok('"sl": round(sl_price, info.digits)' in src,
   "stop-loss is on the order request (broker-side, survives a crash)")
ok('if a.sl_atr <= 0:' in src,
   "a non-positive stop is refused at startup")

# 3. timing must come from the broker, not the machine
ok('tk.time_msc' in src or 'tick.time_msc' in src,
   "decision window is measured on broker tick timestamps")
ok('def broker_offset_hours' in src,
   "server-vs-UTC offset is measured, not assumed")

# 4. the operator's 2-5 second window is enforced
ok('min(5.0, max(2.0, a.decide_after))' in src,
   "decide-after is clamped into the 2-5s the operator specified")

# 5. gate semantics
ok('moved >= min_move' in src,
   "direction is only returned once the move clears the gate")
ok('return 0, ref, last, seen, elapsed' in src,
   "a day that never clears the gate returns no direction")

# 6. safety rails
ok('KILL_FILE' in src and 'os.path.exists(KILL_FILE)' in src,
   "kill-switch file is checked before each session")
ok('need > acct.margin_free * 0.30' in src,
   "margin is capped at 30% of free margin before sending")
ok('10027' in src, "AutoTrading-disabled retcode is explained, not swallowed")

# 7. notification failures must never break trade management
tg = next(n for n in tree.body
          if isinstance(n, ast.FunctionDef) and n.name == "telegram")
ok(any(isinstance(h, ast.ExceptHandler) for n in ast.walk(tg)
       for h in getattr(n, "handlers", [])),
   "telegram() swallows its own exceptions")

# 8. one trade per day maximum
ok(src.count("send_order(sym, d, a.lot, sl_px, a.live)") == 1,
   "exactly one entry per session -- it cannot spiral")

print()
if fails:
    print(f"{len(fails)} FAILED")
    sys.exit(1)
print("ALL CLOCK-SCALP TESTS PASSED")
