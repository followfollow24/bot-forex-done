#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reality-check: can the validated BTC combo deliver 0.5-1%/day? Uses our own
walk-forward-stable config on the OOS period + the leverage-to-ruin math."""
import numpy as np
import pandas as pd
from backtest_btc import load, s_trend_longflat, s_tsmom, s_donchian, run, BARS_PER_YEAR

D = 24
df = load()
idx = df.index

# walk-forward-stable best config (longbias): EMA25/120, TSMOM40d, Donchian20/10
t = s_trend_longflat(df, 25*D, 120*D)
m = s_tsmom(df, 40*D)
dc = s_donchian(df, 20*D, 10*D)
pos = ((t + m + dc)/3.0).clip(lower=0)     # longbias combo
net, _, _ = run(df, pos)
net = pd.Series(net, index=idx)

# OOS window only (2021-2026) for honesty
oos = net[idx.year >= 2021]
daily = (1 + oos).groupby(oos.index.normalize()).prod() - 1   # true daily compounded return

print("=" * 70)
print("WHAT 0.5-1%/day COMPOUNDS TO (why it is extreme):")
for d in [0.005, 0.01]:
    print(f"  {d*100:.1f}%/day  -> x{(1+d)**365:,.1f} per year  (+{((1+d)**365-1)*100:,.0f}%/yr)")
print("  (Renaissance Medallion, best fund ever, ~66%/yr ~= 0.20%/day gross)")

print("\n" + "=" * 70)
print("OUR VALIDATED COMBO -- actual daily stats (OOS 2021-2026):")
print(f"  mean daily return   : {daily.mean()*100:+.3f}%/day   (this is the real edge)")
print(f"  median daily        : {daily.median()*100:+.3f}%/day")
print(f"  std daily           : {daily.std()*100:.2f}%")
print(f"  % days >= +0.5%      : {(daily>=0.005).mean()*100:.0f}%")
print(f"  % days >= +1.0%      : {(daily>=0.01).mean()*100:.0f}%")
print(f"  best / worst day    : {daily.max()*100:+.1f}% / {daily.min()*100:+.1f}%")
print(f"  -> good DAYS of +0.5-1% happen, but the AVERAGE is ~{daily.mean()*100:.2f}%/day.")

print("\n" + "=" * 70)
print("LEVERAGE NEEDED TO FORCE 0.5%/day AVG -- and what it does (ruin check):")
oos_bar = oos.values
target = 0.005
lev_needed = target / daily.mean()
print(f"  leverage to reach ~0.5%/day avg: ~{lev_needed:.1f}x")
print(f"{'lev':>5}{'CAGR':>10}{'MaxDD':>9}{'ruined?':>9}")
for L in [1, 2, 3, 5, lev_needed, 10]:
    eq = np.cumprod(1 + L*oos_bar)
    ruined = (eq <= 0).any() or (1 + L*oos_bar).min() <= 0
    if ruined:
        # first bar where a factor goes <=0 = account wiped
        cagr, dd = float("nan"), float("inf")
        print(f"{L:>5.1f}{'  --':>10}{'  >100%':>9}{'  WIPED':>9}")
    else:
        yrs = len(eq)/BARS_PER_YEAR
        cagr = eq[-1]**(1/yrs)-1
        peak = np.maximum.accumulate(eq); dd = -(eq/peak-1).min()
        print(f"{L:>5.1f}{cagr*100:>9.0f}%{dd*100:>8.0f}%{'  ok':>9}")

print("\n  worst single 1H bar in OOS: "
      f"{oos_bar.min()*100:.1f}%  -> leverage {1/abs(oos_bar.min()):.1f}x liquidates in ONE bar")
print("=" * 70)
