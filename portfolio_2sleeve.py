#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
First sleeve of the multi-asset portfolio: run the SAME validated trend-combo on
GOLD (13y local data) and on BTC, then measure their correlation and the Sharpe
lift of a 2-sleeve portfolio. Low correlation => diversification is real =>
foundation for scaling toward the 0.3%/day (portfolio-Sharpe ~1.74) target.
"""
import os
import numpy as np
import pandas as pd
from backtest_btc import s_trend_longflat, s_tsmom, s_donchian

DL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "download")
BTC = os.path.join(DL, "btcusdt-15m-binance-2017-08-17-2026-06-30.csv")
GOLD = os.path.join(DL, "xauusd-m15-bid-2013-01-01-2026-06-10.csv")
BPY = 365  # daily calendar-day annualisation (consistent across both)


def load_csv(path, tf="1h"):
    df = pd.read_csv(path)
    ts = df["timestamp"]
    if pd.api.types.is_numeric_dtype(ts):         # epoch (gold = ms)
        unit = "ms" if ts.iloc[0] > 1e12 else "s"
        df["timestamp"] = pd.to_datetime(ts, unit=unit)
    else:
        df["timestamp"] = pd.to_datetime(ts)
    df = df.set_index("timestamp")
    o = df["open"].resample(tf).first(); h = df["high"].resample(tf).max()
    l = df["low"].resample(tf).min();  c = df["close"].resample(tf).last()
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c}).dropna()


def run_cost(df, pos, spread_rt, swap_long_yr, swap_short_yr):
    ret = df["close"].pct_change().fillna(0).values
    p = pos.shift(1).fillna(0).values
    tc = (spread_rt/2) * np.abs(np.diff(p, prepend=0.0))
    day = df.index.normalize()
    roll = (day != np.roll(day, 1)); roll[0] = False
    swap = np.where(roll & (p > 0), swap_long_yr/365, 0.0) \
         + np.where(roll & (p < 0), swap_short_yr/365, 0.0)
    return pd.Series(p*ret - tc - swap, index=df.index)


def combo(df):     # the walk-forward-stable long-bias trend combo
    t = s_trend_longflat(df, 25*24, 120*24)
    m = s_tsmom(df, 40*24)
    d = s_donchian(df, 20*24, 10*24)
    return ((t + m + d)/3.0).clip(lower=0)


def stats(daily):
    eq = (1+daily).cumprod()
    yrs = (daily.index[-1]-daily.index[0]).days/365.25
    cagr = eq.iloc[-1]**(1/yrs)-1
    dd = -(eq/eq.cummax()-1).min()
    sh = daily.mean()/daily.std()*np.sqrt(BPY) if daily.std() > 0 else 0
    return cagr, dd, sh


print("running trend-combo on BTC and GOLD (1H) ...")
btc = load_csv(BTC); gold = load_csv(GOLD)
# costs: BTC from snapshot; GOLD approximate (spread ~0.024% RT, swap ~2%/yr each side) -- flag as approx
btc_net = run_cost(btc, combo(btc), 0.00016, 0.069, 0.0)
gold_net = run_cost(gold, combo(gold), 0.00024, 0.02, 0.02)

btc_d = (1+btc_net).groupby(btc_net.index.normalize()).prod()-1
gold_d = (1+gold_net).groupby(gold_net.index.normalize()).prod()-1

# align on overlap for portfolio + correlation
both = pd.DataFrame({"BTC": btc_d, "GOLD": gold_d}).loc["2017-08-17":]
both_common = both.dropna()                       # days both trade (weekdays)
corr = both_common["BTC"].corr(both_common["GOLD"])

# 50/50 equal-weight portfolio over union (gold weekend -> 0 return, flat)
port = (0.5*both["BTC"].fillna(0) + 0.5*both["GOLD"].fillna(0)).loc[both.index]

print("\n" + "="*64)
print(f"{'sleeve':<18}{'CAGR':>9}{'MaxDD':>9}{'Sharpe':>9}")
print("-"*64)
for nm, s in [("BTC combo", btc_d.loc['2017-08-17':]), ("GOLD combo (13y)", gold_d),
              ("BTC (overlap)", both['BTC'].dropna()), ("GOLD (overlap)", both['GOLD'].dropna()),
              ("PORTFOLIO 50/50", port)]:
    c, d, sh = stats(s.dropna())
    print(f"{nm:<18}{c*100:>8.1f}%{d*100:>8.1f}%{sh:>9.2f}")
print("="*64)
print(f"\nBTC<->GOLD daily-return correlation (overlap): {corr:+.2f}")

_, pdd, psh = stats(port.dropna())
_, _, bsh = stats(both['BTC'].dropna()); _, _, gsh = stats(both['GOLD'].dropna())
best_single = max(bsh, gsh)
print(f"portfolio Sharpe {psh:.2f} vs best single {best_single:.2f}  "
      f"-> lift x{psh/best_single:.2f}")

# implied achievable daily at half-Kelly for the portfolio Sharpe
g = 3*psh**2/8
print(f"\nAt portfolio Sharpe {psh:.2f}, half-Kelly ceiling ~{(np.exp(g/365)-1)*100:.2f}%/day "
      f"({(np.exp(g)-1)*100:.0f}%/yr). Need ~Sharpe 1.74 (~0.31%/day) -> add ~3 more uncorrelated sleeves.")
print("\nNOTE: gold costs are APPROXIMATE here (real XAUUSDc spread/swap should be")
print("plugged in like BTC). Correlation is the key takeaway and is cost-robust.")
