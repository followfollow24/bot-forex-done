#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_btcusdc_specs_live.py -- Step 1 pre-flight: re-verify live BTCUSDc specs
right before packaging the BTC-HF live bots, rather than trusting a possibly
stale earlier snapshot (same discipline as the gold pip_value bug lesson).
Run on VPS (real MT5 terminal connected).
ASCII-only.
"""
import MetaTrader5 as mt5

print("=" * 78)
print(" BTCUSDc LIVE SPEC CHECK (pre-flight, Step 1)")
print("=" * 78)

if not mt5.initialize():
    print("ERR: MT5 init failed"); raise SystemExit(1)

acc = mt5.account_info()
print(f"\nAccount: login={acc.login}  server={acc.server}  currency={acc.currency}")
print(f"Balance: {acc.balance:,.2f}  Equity: {acc.equity:,.2f}")

# resolve exact symbol name (case-sensitive check)
all_syms = [s.name for s in mt5.symbols_get()]
candidates = [s for s in all_syms if "BTC" in s.upper() and "USD" in s.upper()]
print(f"\nBTC*USD* symbols on this broker: {candidates}")

sym = "BTCUSDc"
if sym not in all_syms:
    print(f"WARNING: exact symbol '{sym}' NOT found in broker symbol list!")
else:
    print(f"CONFIRMED: exact symbol '{sym}' exists.")

info = mt5.symbol_info(sym)
if info is None:
    print(f"ERR: symbol_info('{sym}') returned None")
else:
    print(f"\nsymbol_info('{sym}'):")
    print(f"  visible          : {info.visible}")
    print(f"  trade_mode       : {info.trade_mode}  (0=disabled,4=full is typical 'full' value)")
    print(f"  contract_size    : {info.trade_contract_size}")
    print(f"  volume_min       : {info.volume_min}")
    print(f"  volume_step      : {info.volume_step}")
    print(f"  volume_max       : {info.volume_max}")
    print(f"  trade_tick_size  : {info.trade_tick_size}")
    print(f"  trade_tick_value : {info.trade_tick_value}")
    print(f"  digits           : {info.digits}")

# ensure it's selected/visible so tick data flows
if info is not None and not info.visible:
    mt5.symbol_select(sym, True)
    print(f"\n  [FIX] symbol was not visible -- called symbol_select(True)")

tick = mt5.symbol_info_tick(sym)
if tick is None:
    print(f"\nERR: symbol_info_tick('{sym}') returned None -- no live price!")
else:
    print(f"\nLive tick: bid={tick.bid}  ask={tick.ask}  spread=${tick.ask-tick.bid:.2f}  time={tick.time}")

# derive pip_value_per_lot using the SAME formula as get_pip_value_live()
# (pip_size=1.0 convention -> value per $1 BTC move per lot)
if info is not None and info.trade_tick_size:
    pip_size_convention = 1.0
    pip_value_per_lot = info.trade_tick_value * (pip_size_convention / info.trade_tick_size)
    print(f"\nDerived pip_value_per_lot (pip_size=1.0 convention): {pip_value_per_lot:.6f} "
          f"{acc.currency}/lot per $1 BTC move")
    print(f"  Sanity check: contract_size({info.trade_contract_size}) x $1 = "
          f"{info.trade_contract_size:.4f} USD = {info.trade_contract_size*100:.4f} USC "
          f"(if account currency is USC/cent) -- should roughly match the derived value above")

# Autotrading check
term = mt5.terminal_info()
if term is not None:
    print(f"\nTerminal AutoTrading enabled (terminal_info.trade_allowed): {term.trade_allowed}")

print("\n" + "=" * 78)
mt5.shutdown()
