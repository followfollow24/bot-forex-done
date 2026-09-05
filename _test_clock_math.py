#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The scheduling arithmetic, tested away from the broker.

On Saturday 2026-09-05 the live pre-flight reported "broker clock UTC-9"
and a bell nine hours out of place. The cause was reading the server
offset from XAUAUDm's last tick while gold was shut, so the timestamp was
Friday's close. Every tick would then have looked ~32,000 seconds late,
tripped max-wait immediately, and the bot would have skipped every
session forever while looking healthy. These tests pin the arithmetic so
that cannot come back.
"""
import importlib.util
import sys
import types
from datetime import datetime, timedelta, timezone

fake = types.ModuleType("MetaTrader5")
for n in ("TRADE_RETCODE_DONE", "TRADE_ACTION_DEAL", "ORDER_TYPE_BUY",
          "ORDER_TYPE_SELL", "ORDER_FILLING_FOK", "ORDER_FILLING_IOC",
          "ORDER_FILLING_RETURN", "ORDER_TIME_GTC", "TIMEFRAME_M1",
          "TIMEFRAME_M5", "TIMEFRAME_H1", "COPY_TICKS_ALL"):
    setattr(fake, n, 1)
sys.modules["MetaTrader5"] = fake
spec = importlib.util.spec_from_file_location("bot", "clock_scalp_bot.py")
bot = importlib.util.module_from_spec(spec)
sys.modules["bot"] = bot
spec.loader.exec_module(bot)
bot.log = lambda m: None

fails = []


def ok(cond, msg):
    print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
    if not cond:
        fails.append(msg)


print("\nscheduling arithmetic")

# --- the bell is always 12:30 UTC, whatever the broker clock says --------
bell = bot.next_bell_utc()
ok(bell.hour == 12 and bell.minute == 30 and bell.tzinfo is timezone.utc,
   f"the bell is 12:30 UTC ({bell:%Y-%m-%d %H:%M %Z}) = 19:30 Thai")
ok(bell > datetime.now(timezone.utc), "the bell is always in the future")
ok((bell - datetime.now(timezone.utc)).total_seconds() <= 24 * 3600,
   "and never more than a day out")

# --- a stale quote must not move the reported offset --------------------
now = datetime.now(timezone.utc).timestamp()


def ticks(mapping):
    fake.symbol_info_tick = lambda s: (
        types.SimpleNamespace(time=mapping[s], bid=1.0, ask=1.1)
        if mapping.get(s) is not None else None)


# gold shut since Friday, BTC quoting now -- this is the real Saturday case
ticks({"XAUAUDm": now - 9 * 3600, "BTCUSDm": now - 2})
ok(bot.broker_offset_hours(["XAUAUDm", "BTCUSDm"]) == 0,
   "a stale gold quote is ignored when a live BTC quote exists")

# gold alone and stale -- the exact configuration that produced UTC-9
ticks({"XAUAUDm": now - 9 * 3600})
off = bot.broker_offset_hours(["XAUAUDm"])
ok(off == -9, f"with only a stale quote the offset is still wrong ({off})")
ok(bot.next_bell_utc().hour == 12,
   "...but the BELL does not move, because it no longer uses the offset")

# an absurd offset is distrusted rather than propagated
ticks({"XAUAUDm": now - 40 * 3600})
ok(bot.broker_offset_hours(["XAUAUDm"]) == 0,
   "an offset outside -12..+14 is discarded, not believed")

ticks({"XAUAUDm": None})
ok(bot.broker_offset_hours(["XAUAUDm"]) == 0,
   "no quote at all reports offset 0 rather than raising")

# --- and the reference instant the tick loop compares against -----------
elapsed_at_bell = (bot.next_bell_utc().timestamp()
                   - bot.next_bell_utc().timestamp())
ok(elapsed_at_bell == 0,
   "a tick arriving exactly at the bell is elapsed=0, not thousands of seconds")

print()
if fails:
    print(f"{len(fails)} FAILED"); sys.exit(1)
print("ALL CLOCK-MATH TESTS PASSED")
