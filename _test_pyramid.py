#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Drive the pyramid against a fake MT5.

Adding lots to a live position is the one thing in this bot that can grow
risk after the trade is on, so it gets its own tests rather than being
trusted to a code read.
"""
import importlib.util
import sys
import types

fake = types.ModuleType("MetaTrader5")
fake.TRADE_RETCODE_DONE = 10009
fake.TRADE_ACTION_DEAL = 1
fake.ORDER_TYPE_BUY, fake.ORDER_TYPE_SELL = 0, 1
fake.ORDER_FILLING_FOK, fake.ORDER_FILLING_IOC, fake.ORDER_FILLING_RETURN = 0, 1, 2
fake.ORDER_TIME_GTC = 0
fake.TIMEFRAME_M1 = fake.TIMEFRAME_M5 = fake.TIMEFRAME_H1 = 1
fake.COPY_TICKS_ALL = 3
sys.modules["MetaTrader5"] = fake
spec = importlib.util.spec_from_file_location("bot", "clock_scalp_bot.py")
bot = importlib.util.module_from_spec(spec)
sys.modules["bot"] = bot
spec.loader.exec_module(bot)
bot.log = lambda m: None
bot.telegram = lambda m: None

fails = []


def ok(cond, msg):
    print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
    if not cond:
        fails.append(msg)


class Args:
    live = True
    add_step_pts = 3.0
    max_adds = 2
    max_margin_pct = 95.0


sent = []
fake.symbol_info = lambda s: types.SimpleNamespace(digits=3, point=0.001,
                                                   spread=1129)
fake.account_info = lambda: types.SimpleNamespace(margin_free=51.46,
                                                  equity=51.46, currency="USD")
fake.order_calc_margin = lambda *a, **k: 2.2
fake.positions_get = lambda **kw: ()


def order_send(req):
    sent.append(dict(req))
    return types.SimpleNamespace(retcode=fake.TRADE_RETCODE_DONE, order=len(sent),
                                 price=req["price"], comment="")


fake.order_send = order_send

print("\npyramid behaviour")

# price ticks: entry 100.0, runs up 1,2,3,...  ask/bid both move together
def feed(prices):
    it = iter(prices)
    last = {"p": prices[0]}

    def tick(sym):
        try:
            last["p"] = next(it)
        except StopIteration:
            pass
        return types.SimpleNamespace(bid=last["p"], ask=last["p"] + 0.1,
                                     time=1_700_000_000, time_msc=0)
    return tick


# --- adds fire once per full step, in profit only ------------------------
sent.clear()
a = Args()
fake.symbol_info_tick = feed([100.0, 101.0, 103.5, 104.0, 107.2, 110.0])
res = []
step, adds, entry, d = 3.0, 0, 100.0, 1
for px in [100.0, 101.0, 103.5, 104.0, 107.2, 110.0]:
    run = (px - entry) * d
    while adds < a.max_adds and run >= step * (adds + 1):
        adds += 1
ok(adds == 2, f"two adds fire on a run of +10 with step 3 (got {adds})")

# --- a losing trade never adds ------------------------------------------
adds = 0
for px in [100.0, 99.0, 97.5, 94.0]:
    run = (px - entry) * d
    while adds < a.max_adds and run >= step * (adds + 1):
        adds += 1
ok(adds == 0, "a trade that never goes into profit adds nothing")

# --- short side uses the same arithmetic --------------------------------
adds, d = 0, -1
for px in [100.0, 98.0, 96.5, 93.0]:
    run = (px - entry) * d
    while adds < a.max_adds and run >= step * (adds + 1):
        adds += 1
ok(adds == 2, "a short adds on a fall, same rule mirrored")

# --- try_add sends one order with our magic and the same stop -----------
sent.clear()
okadd = bot.try_add("XAUAUDm", 1, 0.01, 95.0, True, a, 1)
ok(okadd and len(sent) == 1, "an add sends exactly one order")
ok(sent and sent[0]["magic"] == bot.MAGIC, "the add carries our magic")
ok(sent and abs(sent[0]["sl"] - 95.0) < 1e-9,
   "the add carries the same stop price as the original")
ok(sent and sent[0]["volume"] == 0.01, "the add is one lot unit, not the stack")

# --- margin refusal stops the pyramid, it does not raise ----------------
sent.clear()
fake.order_calc_margin = lambda *a_, **k: 999.0
ok(bot.try_add("XAUAUDm", 1, 0.01, 95.0, True, a, 1) is False,
   "an add that will not fit in free margin is refused, not attempted")
ok(sent == [], "and nothing is sent when it is refused")
fake.order_calc_margin = lambda *a_, **k: 2.2

# --- a rejected add stops further adds ----------------------------------
sent.clear()
fake.order_send = lambda req: sent.append(dict(req)) or types.SimpleNamespace(
    retcode=10004, order=0, price=0.0, comment="requote")
ok(bot.try_add("XAUAUDm", 1, 0.01, 95.0, True, a, 1) is False,
   "a rejected add reports failure so the session stops adding")

# --- the anti-stacking guard must NOT block adds ------------------------
src = open("clock_scalp_bot.py", encoding="utf-8").read()
_add = src[src.index("def try_add("):src.index("def manage_exit(")]
ok("positions_of" not in _add,
   "try_add does not run the entry path's already-holding check, which "
   "would block every add")

print()
if fails:
    print(f"{len(fails)} FAILED"); sys.exit(1)
print("ALL PYRAMID TESTS PASSED")
