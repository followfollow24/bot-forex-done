#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_test_milestone_alert.py -- the manual-exit alert must speak in R, not xATR.

This guards the account's single largest measured leak. The stop is
sl_atr x ATR (2.5 live), so 1R = 2.5xATR and the FIRST milestone the old
alert fired on (+1xATR) was only +0.40R. It announced that and said
"close whenever you like"; the measured average hand-close on
btc_h1_manual was +0.35R -- right at that first alert.

A take-profit sweep over 591 BTC and 970 gold REAL filtered entries
(8.9y / 13.4y, real spread and swap) priced the two policies:

    close at ~0.35R : EV -0.009R train / -0.032R test
    let the target run: EV +0.167R train / +0.108R test

a gap of 0.14-0.18R per trade with the same sign in all four
train/test x market cells. Across the 19 live BTC trades that is about
-410 USC actual against roughly +400 USC had they been left alone.

Runs without MT5: the milestone method is called against a stub.
"""
import sys
import types


class _AnyAttr(types.ModuleType):
    """Stand-in for MetaTrader5 so this runs off the VPS.

    The import chain reaches forex_executor, which reads module-level
    constants (ORDER_FILLING_IOC and friends) at import time. A bare
    ModuleType raises AttributeError on those, so hand back a benign
    value for anything asked for -- none of it is exercised here, the
    milestone check is called against a stub.
    """
    def __getattr__(self, name):
        return 0


sys.modules.setdefault("MetaTrader5", _AnyAttr("MetaTrader5"))

import gold_manual_exit_bot as gm


class _Pos:
    def __init__(self, entry, entry_atr, side="long", trade_id="t1", lot=0.01):
        self.entry, self.entry_atr, self.side = entry, entry_atr, side
        self.trade_id, self.lot = trade_id, lot


class _Stub:
    """Minimal surface the milestone check touches."""
    def __init__(self, price, sl_atr=2.5, entry=100.0, atr=1.0):
        self.positions = [_Pos(entry, atr)]
        self.buf = types.SimpleNamespace(d={"c": [price]})
        self.variant_tag, self.bsym = "btc_h1_manual", "BTCUSDc"
        self.cfg = types.SimpleNamespace(magic_number=666120)
        self.strategy = types.SimpleNamespace(sl_atr=sl_atr)
        self._alerted_atr_level_max, self._alerted_atr_level_min = {}, {}
        self._price, self.sent = price, []

    def _live_price(self, side):
        return self._price

    def _pip_value_per_lot_approx(self):
        return 1.0

    def _send_telegram_alert(self, msg):
        self.sent.append(msg)


def fire(price, sl_atr=2.5):
    s = _Stub(price, sl_atr=sl_atr)
    gm.GoldManualExitBot._check_atr_milestones(s)
    return s.sent


fails = 0


def check(label, cond, detail=""):
    global fails
    if cond:
        print(f"  OK   {label}")
    else:
        fails += 1
        print(f"  FAIL {label}   {detail}")


print("=== Case 1: below 1R the alert must NOT invite a close ===")
# entry 100, ATR 1.0, sl_atr 2.5 -> 1R = 2.5 price units.
# price 101 = +1.00xATR = +0.40R  <- the exact level the old alert fired on
m = fire(101.0)
check("an alert fires at +1xATR", len(m) == 1)
if m:
    t = m[0]
    check("reports R as the headline number", "+0.40R" in t, t)
    check("tells the operator NOT to close", "อย่าเพิ่งปิด" in t, t)
    check("names the 1R threshold", "+1.00R" in t, t)
    check("does not invite a close", "ปิดได้ถ้าต้องการ" not in t, t)

print("\n=== Case 2: at or above 1R closing is allowed ===")
# price 102.5 = +2.5xATR = +1.00R exactly
m = fire(102.5)
check("an alert fires", len(m) == 1)
if m:
    t = m[0]
    check("reports +1.00R", "+1.00R" in t, t)
    check("permits a close", "ปิดได้ถ้าต้องการ" in t, t)
    check("no longer says wait", "อย่าเพิ่งปิด" not in t, t)

print("\n=== Case 3: 1R tracks the CONFIGURED stop, never a constant ===")
# the old text hardcoded "SL 3.0xATR" while the live bots run --sl-atr 2.5
m = fire(101.0, sl_atr=1.0)      # 1R = 1xATR, so +1xATR IS +1.00R here
check("at sl_atr=1.0, +1xATR is treated as +1.00R",
      m and "+1.00R" in m[0] and "ปิดได้ถ้าต้องการ" in m[0], m[0] if m else "")
m = fire(101.0, sl_atr=2.5)
check("at sl_atr=2.5, the same price is only +0.40R",
      m and "+0.40R" in m[0], m[0] if m else "")
src = open(gm.__file__, encoding="utf-8").read()
check("no hardcoded 3.0xATR stop text survives", "SL อยู่ที่ 3.0xATR" not in src)

print("\n=== Case 4: losing side reports R and defends the stop ===")
m = fire(98.0)                    # -2.00xATR = -0.80R
check("a drawdown alert fires", len(m) == 1)
if m:
    t = m[0]
    check("reports -0.80R", "-0.80R" in t, t)
    check("states the stop in R", "-1.00R" in t, t)
    check("does not invite an early loss-cut", "ตัดสินใจปิดเองได้เลย" not in t, t)

print()
if fails:
    print(f"FAILED: {fails} check(s)")
    sys.exit(1)
print("ALL MILESTONE-ALERT TESTS PASSED")
