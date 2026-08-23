#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EDGE #3 candidate: SPX trend-following (new asset class). Test its standalone
edge + correlation to the existing gold-trend and BTC-combo, and what a 3-sleeve
portfolio delivers. Reuses the same EMA50/200 long-only trend logic that worked
on gold. Daily data. ASCII-only."""
import os
import numpy as np
import pandas as pd
from backtest_btc import s_trend_longflat, s_tsmom, s_donchian

DL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "download")
BPY = 365


def load_h1(path):
    df = pd.read_csv(path)
    ts = df["timestamp"]
    df["timestamp"] = pd.to_datetime(ts, unit="ms") if pd.api.types.is_numeric_dtype(ts) else pd.to_datetime(ts)
    df = df.set_index("timestamp")
    o = df["open"].resample("1h").first(); h = df["high"].resample("1h").max()
    l = df["low"].resample("1h").min();  c = df["close"].resample("1h").last()
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c}).dropna()


def load_daily(path):
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df.set_index("timestamp")[["open", "high", "low", "close"]].dropna()


def ema(s, n): return s.ewm(span=n, adjust=False).mean()


def daily_ret(df, pos, spread_side, swap_l=0.0, swap_s=0.0, intraday=True):
    ret = df["close"].pct_change().fillna(0).values
    p = pos.shift(1).fillna(0).values
    tc = spread_side * np.abs(np.diff(p, prepend=0.0))
    if intraday:
        day = df.index.normalize().values
        roll = (day != np.roll(day, 1)); roll[0] = False
    else:
        roll = np.ones(len(df), bool)
    swap = np.where(roll & (p > 0), swap_l/365, 0.0) + np.where(roll & (p < 0), swap_s/365, 0.0)
    n = pd.Series(p*ret - tc - swap, index=df.index)
    n = (1+n).groupby(n.index.normalize()).prod()-1     # -> index normalized to date for all assets
    return n


def trend_lo(df):
    return (ema(df["close"], 50) > ema(df["close"], 200)).astype(float)


def stats(d):
    d = d.dropna(); eq = (1+d).cumprod()
    yrs = (d.index[-1]-d.index[0]).days/365.25
    cagr = eq.iloc[-1]**(1/yrs)-1 if eq.iloc[-1] > 0 else -1
    dd = -(eq/eq.cummax()-1).min()
    sh = d.mean()/d.std()*np.sqrt(BPY) if d.std() > 0 else 0
    return cagr, dd, sh, (cagr/dd if dd > 0 else 0)


print("loading sleeves ...")
g = load_h1(os.path.join(DL, "xauusd-m15-bid-2013-01-01-2026-06-10.csv"))
b = load_h1(os.path.join(DL, "btcusdt-15m-binance-2017-08-17-2026-06-30.csv"))
spx = load_daily(os.path.join(DL, "spx-daily-yahoo.csv"))

gd = daily_ret(g, trend_lo(g), (0.28/2)/g["close"].mean(), 0.02, 0.02)
combo = ((s_trend_longflat(b, 25*24, 120*24) + s_tsmom(b, 40*24) + s_donchian(b, 20*24, 10*24))/3).clip(lower=0)
bd = daily_ret(b, combo, 0.00008, 0.069, 0.0)
sd = daily_ret(spx, trend_lo(spx), 0.0001, 0.02, 0.02, intraday=False)  # SPX index CFD ~0.01%/side

R = pd.DataFrame({"GOLD": gd, "BTC": bd, "SPX": sd}).loc["2017-08-17":]

print("\n%-8s %8s %7s %7s %6s" % ("sleeve", "CAGR", "MaxDD", "Sharpe", "MAR"))
print("-"*40)
for nm in ["GOLD", "BTC", "SPX"]:
    c, dd, sh, mar = stats(R[nm].dropna())
    print("%-8s %7.1f%% %6.0f%% %7.2f %6.2f" % (nm, c*100, dd*100, sh, mar))

print("\nCORRELATION matrix (daily, pairwise):")
print(R.corr().round(2).to_string())

# 3-sleeve inverse-vol
Rf = R.fillna(0.0); vol = R.std(); w = (1/vol)/(1/vol).sum()
port = (Rf*w).sum(axis=1)
c, dd, sh, mar = stats(port)
gp = 3*sh**2/8
print("\n3-SLEEVE inverse-vol portfolio (GOLD+BTC+SPX):")
print("  CAGR %+.1f%%  MaxDD %.0f%%  Sharpe %.2f  MAR %.2f" % (c*100, dd*100, sh, mar))
print("  half-Kelly ceiling ~%.3f%%/day (%.0f%%/yr)" % ((np.exp(gp/BPY)-1)*100, (np.exp(gp)-1)*100))

# vs 2-sleeve for comparison
R2 = R[["GOLD", "BTC"]].fillna(0.0); v2 = R2.std(); w2 = (1/v2)/(1/v2).sum()
_, _, sh2, _ = stats((R2*w2).sum(axis=1))
print("\n(for reference) 2-sleeve GOLD+BTC Sharpe %.2f -> adding SPX moves it to %.2f" % (sh2, sh))
print("keep SPX as edge #3 if it lifts portfolio Sharpe AND corr to both < ~0.4.")
print("CAVEAT: SPX/gold/BTC all 2016-26 = one big bull era; treat as provisional upper bound.")
