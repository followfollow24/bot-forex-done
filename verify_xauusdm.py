import MetaTrader5 as mt5

ok = mt5.initialize(path=r"C:\Program Files\MetaTrader 5\terminal64.exe", timeout=60000)
print("initialize:", ok, mt5.last_error())

for sym in ["XAUUSDm", "XAUUSD"]:
    info = mt5.symbol_info(sym)
    if info is None:
        print(f"{sym}: not found")
        continue
    mt5.symbol_select(sym, True)
    tick = mt5.symbol_info_tick(sym)
    if tick is None:
        print(f"{sym}: tick is None")
        continue
    spread = tick.ask - tick.bid
    print(f"{sym}: bid={tick.bid} ask={tick.ask} spread={spread}")

rates = mt5.copy_rates_from_pos("XAUUSDm", mt5.TIMEFRAME_M15, 0, 3)
print("last 3 M15 bars (XAUUSDm):")
print(rates)

mt5.shutdown()
print("Done.")
