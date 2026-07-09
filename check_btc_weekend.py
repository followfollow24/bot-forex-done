#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
STEP 0 fact-check: does Exness BTCUSDc actually trade on weekends with REAL
range (not a frozen 'technically open' state)? Pull real M15 bars from MT5 and
measure per-bar range% and tick_volume, split by weekday. Also dump the exact
symbol_info fields needed for correct P&L units (trade_tick_value/size/contract)
and the real swap values -- verified from MT5, not hand-derived (per the brief).
ASCII-only. Runs on the VPS.
"""
import MetaTrader5 as mt5
from datetime import datetime, timezone
from collections import defaultdict

if not mt5.initialize():
    print("initialize FAILED:", mt5.last_error()); raise SystemExit(1)

SYM = "BTCUSDc"
si = mt5.symbol_info(SYM)
if si is None:
    print("symbol_info None -- trying to select", SYM)
    mt5.symbol_select(SYM, True)
    si = mt5.symbol_info(SYM)

print("=" * 68)
print("SYMBOL SPECS (from MT5 symbol_info, for correct P&L units):")
print("  name              :", si.name)
print("  trade_tick_value  :", si.trade_tick_value, "  (USD P&L per tick per lot)")
print("  trade_tick_value_profit:", getattr(si, "trade_tick_value_profit", "n/a"))
print("  trade_tick_value_loss  :", getattr(si, "trade_tick_value_loss", "n/a"))
print("  trade_tick_size   :", si.trade_tick_size, "  (price units per tick)")
print("  trade_contract_size:", si.trade_contract_size)
print("  point / digits    :", si.point, "/", si.digits)
print("  volume min/step/max:", si.volume_min, "/", si.volume_step, "/", si.volume_max)
print("  spread (points)   :", si.spread, " = ", si.spread * si.point, "price")
print("  currency base/prof/margin:", si.currency_base, "/", si.currency_profit, "/", si.currency_margin)
print("  swap_long / swap_short (points/lot/day):", si.swap_long, "/", si.swap_short)
print("  swap_mode         :", si.swap_mode, "  swap_rollover3days:", si.swap_rollover3days)
print("=" * 68)

# ---- weekend range/volume analysis: last ~12 days of M15 ----
n = 12 * 96 + 20                       # ~12 days of M15 bars
rates = mt5.copy_rates_from_pos(SYM, mt5.TIMEFRAME_M15, 0, n)
if rates is None or len(rates) == 0:
    print("no M15 rates:", mt5.last_error()); mt5.shutdown(); raise SystemExit(1)

WD = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
agg = defaultdict(lambda: {"bars": 0, "rng_sum": 0.0, "vol_sum": 0.0, "zero_rng": 0})
for r in rates:
    dt = datetime.utcfromtimestamp(int(r["time"]))
    wd = dt.weekday()
    close = r["close"] if r["close"] else 1.0
    rng_pct = (r["high"] - r["low"]) / close * 100.0
    a = agg[wd]
    a["bars"] += 1
    a["rng_sum"] += rng_pct
    a["vol_sum"] += float(r["tick_volume"])
    if r["high"] == r["low"]:
        a["zero_rng"] += 1

print("\nM15 bar activity by weekday (last ~12 days, UTC):")
print("%-5s %7s %14s %14s %12s" % ("day", "#bars", "avg range%", "avg tickVol", "flat bars"))
print("-" * 56)
for wd in range(7):
    a = agg[wd]
    if a["bars"] == 0:
        print("%-5s %7d %14s %14s %12s" % (WD[wd], 0, "-", "-", "-"))
        continue
    print("%-5s %7d %14.3f %14.0f %11d%%"
          % (WD[wd], a["bars"], a["rng_sum"]/a["bars"], a["vol_sum"]/a["bars"],
             round(100*a["zero_rng"]/a["bars"])))

# summary verdict
wk = [agg[i] for i in range(5) if agg[i]["bars"]]
we = [agg[i] for i in (5, 6) if agg[i]["bars"]]
def avg(lst, key):
    tb = sum(x["bars"] for x in lst);
    return sum(x[key] for x in lst)/tb if tb else 0
wk_rng = sum(x["rng_sum"] for x in wk)/max(1, sum(x["bars"] for x in wk))
we_rng = sum(x["rng_sum"] for x in we)/max(1, sum(x["bars"] for x in we))
we_bars = sum(x["bars"] for x in we)
print("-" * 56)
print("weekday avg range%%: %.3f   weekend avg range%%: %.3f   ratio we/wk: %.2f"
      % (wk_rng, we_rng, (we_rng/wk_rng if wk_rng else 0)))
print("weekend bars present: %d  (expect ~%d if fully 24/7 over the window)" % (we_bars, 2*96*int(len(rates)/(7*96)+1)))
if we_bars == 0:
    print("VERDICT: NO weekend bars -> filter weekends OUT of the Binance dataset.")
elif we_rng < 0.2 * wk_rng:
    print("VERDICT: weekend bars exist but near-FROZEN (<20%% of weekday range) -> filter/downweight.")
else:
    print("VERDICT: weekends trade with REAL range -> keep Binance 24/7 bars as-is.")
print("=" * 68)
mt5.shutdown()
