import sys
import MetaTrader5 as mt5

# 1) Connect to the already-running, logged-in MT5 terminal
ok = mt5.initialize(path=r"C:\Program Files\MetaTrader 5\terminal64.exe", timeout=60000)

if not ok:
    print("FAIL: mt5.initialize() failed, error =", mt5.last_error())
    sys.exit(1)

print("PASS: mt5.initialize() connected to terminal")

# 2) Terminal info
term = mt5.terminal_info()
print("\n--- terminal_info ---")
print(term)

# 3) Account info
acc = mt5.account_info()
print("\n--- account_info ---")
print(acc)

if acc is None:
    print("FAIL: account_info() returned None")
    mt5.shutdown()
    sys.exit(1)

# 4) Assertions: demo account + balance ~10000
if acc.trade_mode == 0:
    print("PASS: account is DEMO (trade_mode == 0)")
else:
    print(f"FAIL: account trade_mode = {acc.trade_mode} (expected 0 = DEMO)")

if 9000 <= acc.balance <= 11000:
    print(f"PASS: balance ~10000 (actual = {acc.balance})")
else:
    print(f"FAIL: balance = {acc.balance}, expected ~10000")

# 5) Resolve gold symbol
symbol = "XAUUSD"
info = mt5.symbol_info(symbol)
if info is None:
    print(f"\n'{symbol}' not found, searching for symbols containing 'XAU'...")
    candidates = [s.name for s in mt5.symbols_get() if "XAU" in s.name]
    print("Candidates:", candidates)
    if candidates:
        symbol = candidates[0]
        print(f"Using symbol: {symbol}")
    else:
        print("FAIL: no XAU symbol found")
        mt5.shutdown()
        sys.exit(1)

selected = mt5.symbol_select(symbol, True)
print(f"\nsymbol_select({symbol}, True) ->", selected)

# 6) Current tick
tick = mt5.symbol_info_tick(symbol)
print("\n--- current tick ---")
if tick is not None:
    spread = tick.ask - tick.bid
    print(f"symbol={symbol} bid={tick.bid} ask={tick.ask} spread={spread}")
else:
    print("FAIL: symbol_info_tick() returned None")

# 7) Last 5 M15 bars
rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 0, 5)
print("\n--- last 5 M15 bars ---")
print(rates)

# 8) Shutdown
mt5.shutdown()
print("\nDone.")
