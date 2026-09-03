#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_test_resolve_symbol.py -- resolve_symbol() must find BTC and ETH the same
way it already finds gold, on a broker whose suffix differs from "c".

Found on 2026-09-04 switching to a real USD account (Exness-MT5Real15):
that broker lists "XAUUSDm" and "BTCUSDm", not "...c". Gold survived
because it has always had a dedicated XAU/GOLD keyword-match branch; BTC
and ETH had none, so a bot launched with --symbol BTCUSDc (which becomes
the canonical string "BTCUSDC" after main()'s .upper()) fell through to
the unresolved fallback and returned "BTCUSDC" -- not an MT5 symbol on
this broker. Every order and every --xasset-short-gate OHLCV read for
ETH would have failed.

The fix generalises the keyword branch instead of touching any CLI arg,
because the configured string ("BTCUSDC") is also the SYMBOL_MAGIC /
pip_size override key (forex_live_bot_gold_cwider.py:186-209) -- changing
it to a plain "BTCUSD" to dodge the suffix mismatch would silently
reassign the magic number and drop the pip_size override instead of
just renaming a variable.

Runs without MT5: MT5Connector.list_broker_symbols is monkeypatched.
"""
import sys
import types

class _AnyAttr(types.ModuleType):
    def __getattr__(self, name):
        return 0

sys.modules.setdefault("MetaTrader5", _AnyAttr("MetaTrader5"))

import forex_executor as fe

fails = 0


def check(label, cond, detail=""):
    global fails
    if cond:
        print(f"  OK   {label}")
    else:
        fails += 1
        print(f"  FAIL {label}   {detail}")


def resolver(broker_symbols):
    conn = fe.MT5Connector.__new__(fe.MT5Connector)
    conn.log = types.SimpleNamespace(info=lambda *a: None, warning=lambda *a: None,
                                     debug=lambda *a: None)
    conn.list_broker_symbols = lambda: broker_symbols
    import unittest.mock as mock
    fe.mt5.symbol_select = mock.Mock()
    return conn


print("=== Case: cent-account broker (baseline -- must not regress) ===")
r = resolver(["XAUUSDc", "BTCUSDc", "ETHUSDc"])
check("XAUUSDC -> XAUUSDc (exact, case-insens via keyword branch)",
      r.resolve_symbol("XAUUSDC") == "XAUUSDc")
check("BTCUSDC -> BTCUSDc", r.resolve_symbol("BTCUSDC") == "BTCUSDc")
check("ETHUSDC -> ETHUSDc", r.resolve_symbol("ETHUSDC") == "ETHUSDc")

print("\n=== Case: the real broker this bug was found on (Exness-MT5Real15) ===")
r = resolver(["XAUUSDm", "BTCUSDm", "ETHUSDm", "EURUSDm"])
check("XAUUSDC -> XAUUSDm (already worked pre-fix; must still work)",
      r.resolve_symbol("XAUUSDC") == "XAUUSDm")
check("BTCUSDC -> BTCUSDm (THE BUG -- used to return 'BTCUSDC' unresolved)",
      r.resolve_symbol("BTCUSDC") == "BTCUSDm")
check("ETHUSDC -> ETHUSDm (xasset-short-gate's symbol)",
      r.resolve_symbol("ETHUSDC") == "ETHUSDm")

print("\n=== Case: a generic 'm'-suffix broker (branch 2 exact +M, unaffected) ===")
r = resolver(["XAUUSD", "BTCUSDm"])
check("XAUUSD -> XAUUSD (branch 1 exact match)",
      r.resolve_symbol("XAUUSDC".replace("C", "")) == "XAUUSD")
check("BTCUSDC -> BTCUSDm via keyword branch (branch 2's +M does not fire: "
      "'BTCUSDC'+'M' = 'BTCUSDCM' != 'BTCUSDM')",
      r.resolve_symbol("BTCUSDC") == "BTCUSDm")

print("\n=== Case: guard against cross-asset false matches ===")
# a BTC-quoted gold pair must never satisfy the BTC branch (needs literal
# "USD" in the broker symbol, matching the pre-existing gold guard)
r = resolver(["BTCXAUm", "XAUUSDm"])
check("XAUUSDC still finds XAUUSDm, not BTCXAUm",
      r.resolve_symbol("XAUUSDC") == "XAUUSDm")
r = resolver(["BTCXAUm"])
check("BTCUSDC with ONLY a BTC/XAU cross pair on the broker resolves to "
      "the unresolved fallback, not the cross pair (no 'USD' substring)",
      r.resolve_symbol("BTCUSDC") == "BTCUSDC")

print("\n=== Case: no keyword match at all falls through safely (unchanged behaviour) ===")
r = resolver(["EURUSDm", "GBPUSDm"])
check("an unrelated configured symbol with no keyword match returns itself",
      r.resolve_symbol("USDJPYC") == "USDJPYC")

print()
if fails:
    print(f"FAILED: {fails} check(s)")
    sys.exit(1)
print("ALL RESOLVE_SYMBOL TESTS PASSED")
