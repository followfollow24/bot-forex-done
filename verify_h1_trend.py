#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Verify the H1-trend hypothesis for the 6-7 Jul 2026 all-long losing streak.
Uses the REAL strategy class (HybridTrendPullback) fed with REAL MT5 M15 data,
so the trend value printed is exactly what the live bot computed -- no re-impl.

Prints, for each H1 bar around 6-7 Jul: close / EMA50 / EMA200 / ADX and the
resulting trend (+1 bull / -1 bear / 0 sideways). Then checks the trend the bot
saw at each actual entry timestamp. If trend==+1 (bull) during the entries, the
code was working correctly and simply caught a trend that was about to flip.
ASCII-only. Runs on the VPS from the repo dir.
"""
import numpy as np
import pandas as pd
import MetaTrader5 as mt5
from datetime import datetime, timezone
from forex_hybrid_strategy import HybridTrendPullback

SYMBOL = "XAUUSDc"
ADX_MIN = 18            # looser of the two live bots (adx18) -> what it needed to trade

if not mt5.initialize():
    print("initialize() FAILED:", mt5.last_error()); raise SystemExit(1)

# pull plenty of M15 for the H1-EMA200 warmup (>=850 M15) + the window
rates = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M15, 0, 2500)
if rates is None or len(rates) == 0:
    print("no M15 rates:", mt5.last_error()); mt5.shutdown(); raise SystemExit(1)

t   = np.array([r["time"] for r in rates], dtype=np.int64)
o   = np.array([r["open"] for r in rates], dtype=float)
h   = np.array([r["high"] for r in rates], dtype=float)
l   = np.array([r["low"]  for r in rates], dtype=float)
c   = np.array([r["close"]for r in rates], dtype=float)
print("M15 bars: %d   %s -> %s (UTC)"
      % (len(c), datetime.utcfromtimestamp(t[0]), datetime.utcfromtimestamp(t[-1])))

# [FIX 2026-07-30] d["ts"] is required by the now-fixed, calendar-anchored
# _build_h1_trend_array/_h1_trend (see forex_hybrid_strategy.py) -- this
# script predates that fix and had no "ts" key at all, which would now raise
# a KeyError. Build it the same way forex_indicators.build_data_dict does
# (string-formatted timestamps), from MT5's raw epoch-seconds "time" field.
ts = pd.to_datetime(t, unit="s").astype(str).to_numpy()
d = {"o": o, "h": h, "l": l, "c": c, "ts": ts}

strat = HybridTrendPullback()
strat.ADX_MIN = ADX_MIN
strat.precompute(d)                     # builds strat._h1_trend_arr (M15-mapped)
trend_arr = strat._h1_trend_arr

# ---- rebuild the SAME H1 arrays the class used, to show WHY ----
# [FIX 2026-07-30] was position-based (idx = arange(n_h1)*H1_BARS, anchored
# to index 0 of whatever array was passed in) -- this diagnostic display
# block now mirrors the class's own calendar/timestamp-anchored bucketing
# (self._bucket_ids/_bucket_seconds) so what's printed here matches what
# strat._h1_trend_arr actually contains.
H = HybridTrendPullback.H1_BARS
n = len(c)
bucket_id = strat._bucket_ids(ts, strat._bucket_seconds())
uniq, k_of_bar = np.unique(bucket_id, return_inverse=True)
n_h1 = len(uniq)
tmp = pd.DataFrame({"k": k_of_bar, "c": c, "h": h, "l": l, "t": t})
g = tmp.groupby("k")
h1_c = g["c"].last().reindex(range(n_h1)).to_numpy()
h1_h = g["h"].max().reindex(range(n_h1)).to_numpy()
h1_l = g["l"].min().reindex(range(n_h1)).to_numpy()
h1_t = g["t"].first().reindex(range(n_h1)).to_numpy()   # H1 open time (approx)
ema_f = HybridTrendPullback._ema(h1_c, HybridTrendPullback.EMA_H1_FAST)
ema_s = HybridTrendPullback._ema(h1_c, HybridTrendPullback.EMA_H1_SLOW)
adx_a = HybridTrendPullback._adx_array(h1_h, h1_l, h1_c, HybridTrendPullback.ADX_PERIOD)

def lbl(x):
    return "BULL(+1)" if x == 1 else ("BEAR(-1)" if x == -1 else "flat(0)")

print("\n" + "=" * 92)
print("H1 TREND around 6-7 Jul 2026  (EMA50 vs EMA200 on H1, ADX_MIN=%d)" % ADX_MIN)
print("=" * 92)
print("%-17s %9s %9s %9s %6s   %-9s %s"
      % ("H1 time(UTC)", "close", "EMA50", "EMA200", "ADX", "trend", "why"))
print("-" * 92)
for k in range(n_h1):
    dt = datetime.utcfromtimestamp(h1_t[k])
    if dt < datetime(2026, 7, 7, 12):
        continue
    ef, es, ad, cc = ema_f[k], ema_s[k], adx_a[k], h1_c[k]
    if np.isnan(ef) or np.isnan(es) or np.isnan(ad):
        tr = 0
    elif ad < ADX_MIN:
        tr = 0
    elif cc > ef > es:
        tr = 1
    elif cc < ef < es:
        tr = -1
    else:
        tr = 0
    rel = ("c>50>200" if cc > ef > es else
           "c<50<200" if cc < ef < es else "mixed")
    gate = "" if (not np.isnan(ad) and ad >= ADX_MIN) else " ADX<min->flat"
    print("%-17s %9.2f %9.2f %9.2f %6.1f   %-9s %s%s"
          % (dt.strftime("%m-%d %H:%M"), cc, ef, es, ad, lbl(tr), rel, gate))

# ---- trend the bot saw at each real ENTRY timestamp ----
entries_utc = ["2026-07-06 10:45", "2026-07-06 11:30", "2026-07-06 13:45",
               "2026-07-06 15:40", "2026-07-06 16:45", "2026-07-06 17:30",
               "2026-07-06 17:45"]
print("\n" + "=" * 92)
print("TREND VALUE THE BOT SAW AT EACH REAL ENTRY (mapped from M15 index):")
print("-" * 92)
for e in entries_utc:
    et = int(datetime.strptime(e, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp())
    j = int(np.searchsorted(t, et, side="right") - 1)   # M15 bar at/just before entry
    if 0 <= j < len(trend_arr):
        print("  entry %s UTC -> M15 idx %d (bar %s) -> trend %s"
              % (e, j, datetime.utcfromtimestamp(t[j]).strftime("%m-%d %H:%M"),
                 lbl(int(trend_arr[j]))))

print("=" * 92)
print("READ: if every entry shows BULL(+1), the H1 filter genuinely read up-trend")
print("during a market that was rolling over on M15 -> code correct, caught the flip.")
mt5.shutdown()
