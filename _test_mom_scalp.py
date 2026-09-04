#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_test_mom_scalp.py -- the copy-the-operator strategy and --fixed-lot.

--fixed-lot touches _open_position(), the sizing path EVERY live bot in
this repo shares, so the tests that matter most here are the ones proving
the other bots are unaffected and that a fixed lot cannot slip past the
risk cap. This repo has already lost real money once to a sizing change
(2026-08-10, ~100x oversize), so the cap staying in front of the lot is
not a detail.

Runs without MT5.
"""
import sys
import types

class _AnyAttr(types.ModuleType):
    def __getattr__(self, name):
        return 0

sys.modules.setdefault("MetaTrader5", _AnyAttr("MetaTrader5"))

import numpy as np
from momentum_scalp_strategy import MomentumScalp

fails = 0


def check(label, cond, detail=""):
    global fails
    if cond:
        print(f"  OK   {label}")
    else:
        fails += 1
        print(f"  FAIL {label}   {detail}")


def frame(closes, atr=10.0):
    n = len(closes)
    return {"c": np.array(closes, dtype=float),
            "o": np.array(closes, dtype=float),
            "h": np.array(closes, dtype=float),
            "l": np.array(closes, dtype=float),
            "atr": np.array([atr] * n, dtype=float)}


print("=== Case 1: direction follows the recent move, nothing else ===")
s = MomentumScalp()
n = 60
up = frame([100.0] * (n - 3) + [100.0, 102.0, 105.0])
d = s.signal(up, n - 1)
check("rising price -> BUY", d.action == "BUY", getattr(d, "action", None))

dn = frame([100.0] * (n - 3) + [100.0, 98.0, 95.0])
d = s.signal(dn, n - 1)
check("falling price -> SELL", d.action == "SELL", getattr(d, "action", None))

print("\n=== Case 2: drift below the noise floor is not a signal ===")
# MIN_MOVE_ATR 0.15 x atr 10 = 1.5; a 1.0 move must be ignored, or the bot
# trades every bar into the spread
flat = frame([100.0] * (n - 3) + [100.0, 100.4, 101.0])
d = s.signal(flat, n - 1)
check("sub-threshold drift -> no trade", d.action not in ("BUY", "SELL"),
      getattr(d, "action", None))
big = frame([100.0] * (n - 3) + [100.0, 101.0, 102.0])
check("above-threshold move -> trades", s.signal(big, n - 1).action == "BUY")

print("\n=== Case 3: no trend filter (the source trades argue against one) ===")
# 46% of the copied trades were against the H4 trend and still won, so a
# long signal must fire even when the longer history is falling hard
downtrend = frame([200.0 - i * 2 for i in range(n - 3)] + [100.0, 102.0, 105.0])
check("long fires inside a downtrend", s.signal(downtrend, n - 1).action == "BUY")

print("\n=== Case 4: guards ===")
check("too few bars -> no signal", s.signal(up, 3).action not in ("BUY", "SELL"))
nan_atr = frame([100.0] * (n - 3) + [100.0, 102.0, 105.0])
nan_atr["atr"] = np.array([np.nan] * n)
check("NaN ATR -> no signal", s.signal(nan_atr, n - 1).action not in ("BUY", "SELL"))
zero_atr = frame([100.0] * (n - 3) + [100.0, 102.0, 105.0], atr=0.0)
check("zero ATR -> no signal", s.signal(zero_atr, n - 1).action not in ("BUY", "SELL"))

print("\n=== Case 5: defaults are the MEASURED geometry, not invented ===")
check("sl_atr default 0.42 (measured from 10 filled stops)", MomentumScalp.sl_atr == 0.42)
check("tp_atr default 0.51 (measured from 18 filled targets)", MomentumScalp.tp_atr == 0.51)
check("trailing off -- source trades used flat brackets",
      MomentumScalp.trail_atr_mult >= 999)

print("\n=== Case 6: --fixed-lot cannot bypass the risk cap ===")
src = open("forex_live_bot_gold_cwider.py", encoding="utf-8").read()
blk = src[src.index("if getattr(self, \"fixed_lot\", None):"):]
blk = blk[:blk.index("side    = \"long\"")]
check("fixed lot still clamped by max_lot", "min(lot, self.cfg.max_lot)" in blk)
check("risk cap still computed after the lot is chosen",
      "actual_risk_pct" in blk and "max_risk_per_trade_pct" in blk)
# look at the body of the cap's if-statement, not a naive split: the
# f-string in the log line also mentions max_risk_per_trade_pct, so
# splitting on that name lands mid-message and misses the return
_cap = blk[blk.index("if actual_risk_pct >"):]
check("cap still RETURNS (skips the trade) rather than only warning",
      "return" in _cap[:600], _cap[:200])

print("\n=== Case 7: every other bot's sizing is untouched ===")
check("risk-based path still present for bots without --fixed-lot",
      "risk_cash / (sl_pips * pip_value)" in src)
check("fixed lot only applies when the flag is set (falsy -> risk-based)",
      "if getattr(self, \"fixed_lot\", None):" in src and "else:" in blk)
check("flag defaults to None", 'ap.add_argument("--fixed-lot", type=float, default=None' in src)

print()
if fails:
    print(f"FAILED: {fails} check(s)")
    sys.exit(1)
print("ALL MOM-SCALP TESTS PASSED")
