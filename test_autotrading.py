#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_autotrading.py -- Verify Algo Trading is actually enabled by placing
a minimum-lot market order and immediately closing it, then reporting the
real retcode. This settles the question empirically instead of relying on
interpreting the MT5 toolbar icon.

SAFETY NOTES:
  - Uses the SMALLEST possible lot size (symbol's volume_min, typically 0.01).
  - Uses magic number 999999 -- never confused with live bots.
  - Closes the position IMMEDIATELY after opening within the same script.
  - Confirms account login=160075275 before doing anything.
  - Real cost if Algo Trading is ON: ~one round-trip spread on min lot.

Run on VPS:
    cd C:/Users/Administrator/Desktop
    python test_autotrading.py
"""
import sys
import time

import MetaTrader5 as mt5

EXPECTED_LOGIN = 160075275
TEST_MAGIC     = 999999


def fail(msg):
    print(f"[FAIL] {msg}")
    mt5.shutdown()
    sys.exit(1)


def main():
    if not mt5.initialize():
        fail(f"mt5.initialize() failed: {mt5.last_error()}")

    info = mt5.account_info()
    if info is None:
        fail(f"mt5.account_info() returned None: {mt5.last_error()}")

    print("=== Account ===")
    print(f"login:      {info.login}")
    print(f"currency:   {info.currency}")
    print(f"balance:    {info.balance}")
    print(f"trade_mode: {info.trade_mode}  (2=REAL)")

    if info.login != EXPECTED_LOGIN:
        fail(f"login={info.login} != expected {EXPECTED_LOGIN} -- ABORTING.")

    # Find gold symbol
    symbol = None
    for candidate in ("XAUUSDc", "XAUUSD"):
        if mt5.symbol_info(candidate) is not None:
            symbol = candidate
            break
    if symbol is None:
        syms = mt5.symbols_get()
        gold = [s.name for s in syms if "XAU" in s.name.upper()] if syms else []
        fail(f"Cannot find XAUUSDc or XAUUSD. Gold-like symbols: {gold}")

    print(f"\nSymbol: {symbol}")
    mt5.symbol_select(symbol, True)

    sym_info = mt5.symbol_info(symbol)
    lot = sym_info.volume_min
    print(f"Min lot: {lot}")

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        fail(f"symbol_info_tick('{symbol}') returned None")

    print(f"Price:  bid={tick.bid}  ask={tick.ask}")

    # --- Attempt to open minimum BUY ---
    print("\n=== Sending BUY order (min lot) ===")
    req = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       symbol,
        "volume":       lot,
        "type":         mt5.ORDER_TYPE_BUY,
        "price":        tick.ask,
        "magic":        TEST_MAGIC,
        "comment":      "AUTOTRADE-TEST",
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    result = mt5.order_send(req)

    if result is None:
        fail(f"order_send() returned None: {mt5.last_error()}")

    print(f"retcode: {result.retcode}")
    print(f"comment: {result.comment}")

    if result.retcode == 10027:
        print()
        print("=" * 60)
        print("[RESULT] retcode=10027 --> ALGO TRADING IS STILL DISABLED")
        print("         No order placed. No cost incurred.")
        print("         Enable the Algo Trading button in MT5 (must be green).")
        print("=" * 60)
        mt5.shutdown()
        return

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        print()
        print("=" * 60)
        print(f"[RESULT] Order failed: retcode={result.retcode} ({result.comment})")
        print("         NOT the AutoTrading-disabled error (10027).")
        print("         Algo Trading may be ON, but something else blocks")
        print("         orders (market closed, invalid volume, etc.).")
        print("=" * 60)
        mt5.shutdown()
        return

    # --- Order succeeded ---
    ticket     = result.order
    fill_price = result.price
    print()
    print("=" * 60)
    print(f"[RESULT] retcode=DONE --> ALGO TRADING IS ENABLED")
    print(f"         ticket={ticket}  fill_price={fill_price}")
    print("=" * 60)

    # --- Close immediately ---
    print("\n=== Closing test position immediately ===")
    time.sleep(1)

    tick2 = mt5.symbol_info_tick(symbol)
    close_req = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       symbol,
        "volume":       lot,
        "type":         mt5.ORDER_TYPE_SELL,
        "position":     ticket,
        "price":        tick2.bid if tick2 else fill_price,
        "magic":        TEST_MAGIC,
        "comment":      "AUTOTRADE-TEST-CLOSE",
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    cr = mt5.order_send(close_req)

    if cr is None or cr.retcode != mt5.TRADE_RETCODE_DONE:
        rc = cr.retcode if cr else mt5.last_error()
        print(f"[WARNING] Auto-close failed! retcode={rc}")
        print(f"          >>> MANUALLY CLOSE ticket={ticket} in MT5 NOW <<<")
    else:
        diff = cr.price - fill_price
        print(f"[OK] Closed at {cr.price}  (opened at {fill_price}, diff={diff:+.3f})")
        print("     Round-trip spread cost confirms Algo Trading works end-to-end.")

    mt5.shutdown()


if __name__ == "__main__":
    main()
