#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
h1_trend_diagnostic.py -- show H1 trend state at each trade entry
Pulls H1 bars Jul 1-10 from MT5, computes EMA50/EMA200 + ADX(14),
then maps each real trade to its trend state at entry.
ASCII-only. Run on VPS.
"""
import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from datetime import datetime

def ema(s, n): return s.ewm(span=n, adjust=False).mean()

def adx_series(df, n=14):
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([h-l, (h-pc).abs(), (l-pc).abs()], axis=1).max(axis=1)
    up = h.diff(); dn = -l.diff()
    pdm = ((up > dn) & (up > 0)) * up
    mdm = ((dn > up) & (dn > 0)) * dn
    atr = tr.ewm(alpha=1/n, adjust=False).mean()
    pdi = 100*pdm.ewm(alpha=1/n, adjust=False).mean()/atr
    mdi = 100*mdm.ewm(alpha=1/n, adjust=False).mean()/atr
    dx  = 100*(pdi-mdi).abs()/(pdi+mdi).replace(0, np.nan)
    return dx.ewm(alpha=1/n, adjust=False).mean(), pdi, mdi

print("=" * 80)
print(" H1 TREND DIAGNOSTIC -- XAUUSDc Jul 1-10 2026")
print("=" * 80)

if not mt5.initialize():
    print("ERR: MT5 init failed"); exit(1)

# Get H1 bars Jul 1 - Jul 10
rates = mt5.copy_rates_range(
    "XAUUSDc",
    mt5.TIMEFRAME_H1,
    datetime(2026, 7, 1),
    datetime(2026, 7, 11)
)
mt5.shutdown()

df = pd.DataFrame(rates)
df["time"] = pd.to_datetime(df["time"], unit="s")
df = df.set_index("time").sort_index()
print(f"\nH1 bars loaded: {len(df)} bars ({df.index[0].date()} to {df.index[-1].date()})")

# Compute indicators
df["ema50"]  = ema(df["close"], 50)
df["ema200"] = ema(df["close"], 200)
df["trend_ema"] = (df["ema50"] > df["ema200"]).map({True: "BULL", False: "BEAR"})
df["adx"], df["pdi"], df["mdi"] = adx_series(df)
df["trend_adx"] = (df["pdi"] > df["mdi"]).map({True: "BULL", False: "BEAR"})

# Print daily summary
print("\nDAILY TREND SUMMARY (07:00 bar = session open reference):")
print(f"  {'Date':<12} {'Close':<8} {'EMA50':<8} {'EMA200':<8} {'EMA-trend':<10} {'ADX':<6} {'PDI':<6} {'MDI':<6} {'ADX-trend'}")
print("  " + "-" * 75)
for date in sorted(df.index.date):
    day_bars = df[df.index.date == date]
    if len(day_bars) == 0: continue
    ref = day_bars.iloc[min(7, len(day_bars)-1)]  # ~07:00 bar
    last = day_bars.iloc[-1]
    print(f"  {str(date):<12} {last['close']:<8.2f} {ref['ema50']:<8.2f} {ref['ema200']:<8.2f} "
          f"{ref['trend_ema']:<10} {ref['adx']:<6.1f} {ref['pdi']:<6.1f} {ref['mdi']:<6.1f} {ref['trend_adx']}")

# Trade entries (from fetch_real_trades output, real trades only)
trades = [
    # (open_time_str, entry_price, result, magic)
    ("2026-07-06 11:30", 4155.47, "LOSS", 555083),
    ("2026-07-06 11:30", 4155.67, "LOSS", 555053),
    ("2026-07-06 10:45", 4154.54, "LOSS", 555083),
    ("2026-07-06 10:45", 4154.33, "LOSS", 555053),
    ("2026-07-06 17:45", 4157.08, "LOSS", 555053),
    ("2026-07-06 17:30", 4155.76, "LOSS", 555083),
    ("2026-07-07 13:45", 4149.27, "LOSS", 555053),
    ("2026-07-07 13:45", 4149.25, "LOSS", 555083),
    ("2026-07-06 16:45", 4149.18, "LOSS", 555053),
    ("2026-07-06 15:30", 4150.24, "LOSS", 555083),
    ("2026-07-07 14:30", 4158.80, "LOSS", 555083),
    ("2026-07-07 11:30", 4143.39, "LOSS", 555083),
    ("2026-07-09 17:30", 4133.95, "LOSS", 555053),
    ("2026-07-09 20:45", 4124.07, "LOSS", 555083),
    ("2026-07-09 20:45", 4124.07, "LOSS", 555053),
    ("2026-07-09 11:45", 4106.79, "WIN",  555083),
    ("2026-07-09 13:15", 4120.75, "LOSS", 555083),
    ("2026-07-09 13:30", 4123.40, "LOSS", 555053),
    ("2026-07-10 02:30", 4125.59, "LOSS", 555053),
]

print("\n\nPER-TRADE TREND STATE AT ENTRY:")
print(f"  {'#':<3} {'OpenTime':<18} {'Entry':<8} {'H1-EMA-trend':<14} {'H1-ADX-trend':<14} {'ADX':<6} {'Result'}")
print("  " + "-" * 75)

for i, (ts, entry, result, magic) in enumerate(trades, 1):
    t = pd.Timestamp(ts)
    # find closest H1 bar at or before open_time
    mask = df.index <= t
    if mask.any():
        bar = df[mask].iloc[-1]
        ema_trend = bar["trend_ema"]
        adx_trend = bar["trend_adx"]
        adx_val   = bar["adx"]
        mismatch = "!MISMATCH" if (ema_trend == "BEAR" or adx_trend == "BEAR") else ""
    else:
        ema_trend = adx_trend = "N/A"; adx_val = 0; mismatch = ""
    bot = "adx20" if magic == 555053 else "adx18"
    print(f"  {i:<3} {ts:<18} {entry:<8.2f} {ema_trend:<14} {adx_trend:<14} {adx_val:<6.1f} {result} {mismatch}")

print("\n INTERPRETATION:")
print("  MISMATCH = bot entered BUY but H1 trend already BEAR at that bar")
print("  This explains losses: bot signal (H1 pullback) fired into a downtrend")
print("=" * 80)
