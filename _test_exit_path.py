#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Exercise the exit path against a fake MT5.

This path has never executed against the broker: the bot is live but has
not yet reached a bell with the market open. Everything it does the first
time a trade closes is therefore untested by running, which is exactly
where the positions_get(magic=...) bug was hiding. These tests drive it
with a stand-in module instead of waiting for Monday to find out.
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
bot.log = lambda m: None            # quiet

fails = []


def ok(cond, msg):
    print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
    if not cond:
        fails.append(msg)


class P:
    def __init__(self, ticket, magic, volume=0.05, type_=0):
        self.ticket, self.magic, self.volume, self.type = ticket, magic, volume, type_


print("\nexit path against a fake MT5")

# --- the bug that was there: positions_get has no magic parameter ---------
def strict_positions_get(**kw):
    bad = set(kw) - {"symbol", "group", "ticket"}
    if bad:
        raise TypeError(f"'{bad.pop()}' is an invalid keyword argument")
    return (P(1, bot.MAGIC), P(2, 999), P(3, bot.MAGIC))

fake.positions_get = strict_positions_get
got = bot.positions_of("XAUAUDm")
ok(got is not None and [p.ticket for p in got] == [1, 3],
   "positions_of returns only our own magic, and does not pass magic= to MT5")

# --- an MT5 error must read as UNKNOWN, never as "nothing open" -----------
def exploding(**kw):
    raise RuntimeError("IPC send failed")

fake.positions_get = exploding
ok(bot.positions_of("XAUAUDm") is None,
   "an MT5 failure returns None (unknown), not [] (which would read as closed)")

fake.positions_get = lambda **kw: ()
ok(bot.positions_of("XAUAUDm") == [],
   "a genuinely flat symbol returns an empty list")

# --- close_position must not walk away when the read failed ---------------
sent = []
fake.symbol_info_tick = lambda s: types.SimpleNamespace(bid=100.0, ask=100.2, time=0)
fake.positions_get = exploding
fake.order_send = lambda req: sent.append(req) or types.SimpleNamespace(
    retcode=fake.TRADE_RETCODE_DONE, order=1, price=100.0, comment="")
bot.close_position("XAUAUDm", 1, True, 99.0)
ok(sent == [], "with positions unreadable it does not fire a blind close order")

# --- the normal close ------------------------------------------------------
sent.clear()
fake.positions_get = lambda **kw: (P(7, bot.MAGIC),)
bot.close_position("XAUAUDm", 1, True, 99.0)
ok(len(sent) == 1 and sent[0]["position"] == 7 and sent[0]["magic"] == bot.MAGIC,
   "a normal close sends one order against our own ticket")

# --- a rejected close is retried, not logged once and dropped -------------
sent.clear()
fake.order_send = lambda req: sent.append(dict(req)) or types.SimpleNamespace(
    retcode=10004, order=0, price=0.0, comment="requote")
bot.time.sleep = lambda *_: None
bot.close_position("XAUAUDm", 1, True, 99.0)
ok(len(sent) >= 3, f"a rejected close is retried ({len(sent)} attempts sent)")

# --- filling-mode fallback -------------------------------------------------
tries = []
def fill_picky(req):
    tries.append(req["type_filling"])
    if req["type_filling"] != fake.ORDER_FILLING_FOK:
        return types.SimpleNamespace(retcode=10030, comment="Unsupported filling")
    return types.SimpleNamespace(retcode=fake.TRADE_RETCODE_DONE, order=9,
                                 price=100.0, comment="")
fake.order_send = fill_picky
res = bot.try_send({"type_filling": fake.ORDER_FILLING_IOC})
ok(res is not None and res.retcode == fake.TRADE_RETCODE_DONE
   and fake.ORDER_FILLING_FOK in tries,
   "a broker that rejects IOC with 10030 is retried with another mode")

# --- close_position survives a missing tick -------------------------------
sent.clear()
fake.symbol_info_tick = lambda s: None
fake.positions_get = lambda **kw: (P(8, bot.MAGIC),)
fake.order_send = lambda req: sent.append(req) or types.SimpleNamespace(
    retcode=fake.TRADE_RETCODE_DONE, order=1, price=100.0, comment="")
bot.close_position("XAUAUDm", 1, True, 99.0)
ok(len(sent) == 1, "a missing tick does not stop the close from being sent")

print()
if fails:
    print(f"{len(fails)} FAILED"); sys.exit(1)
# --- MT5 signals an error by RETURNING None, not only by raising ----------
# Every guard in this bot is built on positions_of distinguishing "unknown"
# from "flat". Only the raising case was covered, and `or []` turned a
# plain None straight into an empty list -- so the distinction never
# existed on the path MT5 actually uses.
print("\npositions_get returning None is unknown, not flat")
fake.positions_get = lambda **kw: None
fake.last_error = lambda: (-10004, "no IPC connection")
ok(bot.positions_of("XAUAUDm") is None,
   "a None from positions_get reads as UNKNOWN, not as an empty account")
fake.positions_get = lambda **kw: ()
ok(bot.positions_of("XAUAUDm") == [],
   "an empty tuple still reads as genuinely flat")
fake.positions_get = lambda **kw: (P(4, bot.MAGIC), P(5, 111))
ok([p.ticket for p in bot.positions_of("XAUAUDm")] == [4],
   "and a real list is still filtered to our own magic")

# --- close_position must not read "cannot tell" as "nothing to close" -----
# The retry used `if not held`, and None is falsy, so a broker that would
# not answer twice was reported as flat and a live position was abandoned.
print("\nclose_position when the broker will not answer")

fake.symbol_info_tick = lambda s: types.SimpleNamespace(bid=100.0, ask=100.2,
                                                        time=1_700_000_000)
sent = []
fake.order_send = lambda req: (sent.append(req),
                               types.SimpleNamespace(retcode=10009,
                                                     order=1, price=100.0))[1]
notes = []
bot.log = lambda m: notes.append(m)
bot.telegram = lambda m: notes.append("TG " + m)
bot.time.sleep = lambda s: None

fake.positions_get = lambda **kw: None            # never answers
del sent[:], notes[:]
bot.close_position("XAUAUDm", 1, True, 100.0)
ok(not sent, "no close order is invented when positions cannot be read")
ok(any("STILL CANNOT READ" in n for n in notes),
   "it says plainly that it could not tell, instead of 'nothing to close'")
ok(any(n.startswith("TG ") and "may still be open" in n for n in notes),
   "and it escalates to Telegram rather than logging once and stopping")

fake.positions_get = lambda **kw: ()              # genuinely flat
del sent[:], notes[:]
bot.close_position("XAUAUDm", 1, True, 100.0)
ok(not sent, "a genuinely empty account still sends nothing")
ok(any("nothing of ours open" in n for n in notes),
   "and an empty list is still reported as empty, not as an error")

calls = {"n": 0}
def flaky(**kw):
    calls["n"] += 1
    return None if calls["n"] < 3 else (P(7, bot.MAGIC),)
fake.positions_get = flaky
del sent[:], notes[:]
bot.close_position("XAUAUDm", 1, True, 100.0)
ok(len(sent) == 1 and sent[0]["position"] == 7,
   "a read that recovers on the third try still closes the position")

# --- the pyramid was wired to nothing -------------------------------------
print("\nmanage_exit gets what it needs to pyramid")
src = open("clock_scalp_bot.py", encoding="utf-8").read()
i0 = src.index("def run_once(")
body = src[i0:src.index("\ndef ", i0 + 1)]
call = body[body.index("manage_exit("):]
ok("a.exit_mode, a, lot_used, sl_used" in call,
   "run_once passes a, lot and sl_price -- without them --add-step-pts "
   "could never fire")
ok("lot_used, sl_used in open_trades" in body,
   "the size actually sent is carried to the exit, not re-derived")
ok("WARNING" in body and "managed in sequence" in body,
   "two open positions are called out, since the second runs unwatched")


print("ALL EXIT-PATH TESTS PASSED")
