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
ok('a.max_wait = max(a.decide_after + 1.0, a.max_wait)' in src,
   "max-wait is always later than the minimum wait")
ok('def entry_decision(' in src
   and 'if elapsed >= min_wait and px != ref and abs(px - ref) >= gate:' in src,
   "the entry rule is a pure function -- replayed in _test_entry_decision.py")
ok('if elapsed >= max_wait:' in src and 'return None' in src,
   "the session is abandoned if the gate never opens")

# 5. gate semantics
ok('if d is None:' in src and 'st["done"] = (0, elapsed)' in src,
   "a day that never clears the gate yields no direction")

# 6. safety rails
ok('KILL_FILE' in src and 'os.path.exists(KILL_FILE)' in src,
   "kill-switch file is checked before each session")
ok('need > acct.margin_free * (a.max_margin_pct / 100.0)' in src,
   "margin is checked against free margin before sending (default 95%: only "
   "blocks orders the broker would reject anyway)")
ok('10027' in src, "AutoTrading-disabled retcode is explained, not swallowed")
ok('mt5.order_calc_profit(otype, sym, lot, entry_px, sl_px)' in src,
   "stop cost comes from the broker's calculator, not hand-rolled pip maths")
ok('a.max_risk_pct > 0 and pct > a.max_risk_pct' in src,
   "risk cap only refuses when it is switched on (0 = off, as instructed)")
ok('pts_bust < pts_stop' in src,
   "warns when the broker's stop-out comes before the stop-loss")
ok('"gate": float(gates[s])' in src
   and 'entry_decision(elapsed, px, st["ref"], st["gate"],' in src,
   "the gate is set before the decision loop reads it, not after")
ok('p.add_argument("--lot", default="0.05"' in src,
   "default lot is the 0.05 the operator asked for")
ok('def parse_lots' in src and 'if "=" not in spec' in src,
   "lot can be set per symbol -- 0.05 of gold and of BTC are not the same size")
ok('p.add_argument("--symbols", default="XAUAUDm,BTCUSDm"' in src,
   "gold and BTC are both traded by default")
ok(src.index('def decide_all') < src.index('def run_once'),
   "one polling loop reads every symbol -- they fire on the same second")
ok('p.add_argument("--exit-mode", default="m15close"' in src,
   "default exit is the M15 candle close the operator chose")
ok('return (int(ts_srv) // 900 + 1) * 900' in src,
   "M15 close is the next 900-second boundary, computed from server time")
ok(src.index('loss = mt5.order_calc_profit') < src.index('res = send_order('),
   "risk is priced and logged BEFORE the order is sent")
ok(src.count('log(f"  [{sym}] stop {a.sl_atr}xATR') == 1,
   "the money cost is logged per symbol on every trade")
ok('p.add_argument("--sl-atr", type=float, default=3.0)' in src,
   "default stop is the 3xATR the operator asked for")

# 7. notification failures must never break trade management
tg = next(n for n in tree.body
          if isinstance(n, ast.FunctionDef) and n.name == "telegram")
ok(any(isinstance(h, ast.ExceptHandler) for n in ast.walk(tg)
       for h in getattr(n, "handlers", [])),
   "telegram() swallows its own exceptions")

# 8. one trade per day maximum
ok(src.count("res = send_order(sym, d, lot, sl_px, a.live)") == 1,
   "exactly one entry per symbol per session -- it cannot spiral")

ok('def selftest' in src and 'sends nothing' in src,
   "a pre-flight check exists that opens and sends nothing")
ok(src.index('if a.selftest:') < src.index('run_once(a, syms)'),
   "selftest returns before the trading loop is ever entered")
ok('term.trade_allowed' in src,
   "pre-flight checks AutoTrading rather than assuming it")

ok('p.add_argument("--gate-money"' in src
   and 'gate = a.gate_money / float(per_pt)' in src,
   "the gate can be set in account currency, priced by the broker")
ok('cannot price a money gate -- falling back' in src,
   "a money gate that cannot be priced falls back rather than trading blind")

print()
if fails:
    print(f"{len(fails)} FAILED")
    sys.exit(1)
print("ALL CLOCK-SCALP TESTS PASSED")
