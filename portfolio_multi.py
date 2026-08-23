#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Multi-sleeve portfolio: same validated trend-combo on BTC / ETH / Gold / S&P500.
Shows (b) which 'new assets' actually diversify (correlation matrix) and
(a) how allocation method (equal / inverse-vol / Sharpe-weighted) changes the
portfolio Sharpe. Foundation for scaling toward the 0.3%/day (Sharpe ~1.74) goal.
"""
import os
import numpy as np
import pandas as pd
from backtest_btc import s_trend_longflat, s_tsmom, s_donchian

DL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "download")
BPY = 365

# (path, bars_per_day, spread_rt, swap_long_yr, swap_short_yr)  -- costs approximate, flagged
ASSETS = {
    "BTC":  (f"{DL}/btcusdt-15m-binance-2017-08-17-2026-06-30.csv", 24, 0.00016, 0.069, 0.0),
    "ETH":  (f"{DL}/ethusdt-15m-binance-2017-08-17-2026-06-30.csv", 24, 0.00020, 0.070, 0.0),
    "GOLD": (f"{DL}/xauusd-m15-bid-2013-01-01-2026-06-10.csv",      24, 0.00024, 0.02, 0.02),
    "SPX":  (f"{DL}/spx-daily-yahoo.csv",                            1, 0.00010, 0.04, 0.04),
}


def load_csv(path, bpd):
    df = pd.read_csv(path)
    ts = df["timestamp"]
    if pd.api.types.is_numeric_dtype(ts):
        df["timestamp"] = pd.to_datetime(ts, unit="ms" if ts.iloc[0] > 1e12 else "s")
    else:
        df["timestamp"] = pd.to_datetime(ts)
    df = df.set_index("timestamp")
    if bpd == 1:                      # already daily
        return df[["open", "high", "low", "close"]].dropna()
    tf = "1h"
    o = df["open"].resample(tf).first(); h = df["high"].resample(tf).max()
    l = df["low"].resample(tf).min();  c = df["close"].resample(tf).last()
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c}).dropna()


def combo(df, bpd):
    t = s_trend_longflat(df, 25*bpd, 120*bpd)
    m = s_tsmom(df, 40*bpd)
    d = s_donchian(df, 20*bpd, 10*bpd)
    return ((t + m + d)/3.0).clip(lower=0)


def run_cost(df, pos, spread_rt, sl, ss):
    ret = df["close"].pct_change().fillna(0).values
    p = pos.shift(1).fillna(0).values
    tc = (spread_rt/2)*np.abs(np.diff(p, prepend=0.0))
    day = df.index.normalize()
    roll = (day != np.roll(day, 1)); roll[0] = False
    swap = np.where(roll & (p > 0), sl/365, 0.0) + np.where(roll & (p < 0), ss/365, 0.0)
    return pd.Series(p*ret - tc - swap, index=df.index)


def dstats(daily):
    daily = daily.dropna()
    eq = (1+daily).cumprod()
    yrs = (daily.index[-1]-daily.index[0]).days/365.25
    cagr = eq.iloc[-1]**(1/yrs)-1
    dd = -(eq/eq.cummax()-1).min()
    sh = daily.mean()/daily.std()*np.sqrt(BPY) if daily.std() > 0 else 0
    return cagr, dd, sh


# ---- build daily return stream per sleeve ----
daily = {}
for name, (path, bpd, sr, sl, ss) in ASSETS.items():
    df = load_csv(path, bpd)
    net = run_cost(df, combo(df, bpd), sr, sl, ss)
    daily[name] = (1+net).groupby(net.index.normalize()).prod()-1   # normalize -> align all to date

R = pd.DataFrame(daily)                       # daily returns, outer-joined
R = R.loc["2017-08-17":]                      # common era (crypto start)

print("="*60)
print(f"{'sleeve':<8}{'CAGR':>9}{'MaxDD':>9}{'Sharpe':>9}")
print("-"*60)
sh = {}
for name in ASSETS:
    c, d, s = dstats(R[name])
    sh[name] = s
    print(f"{name:<8}{c*100:>8.1f}%{d*100:>8.1f}%{s:>9.2f}")
print("="*60)

print("\nCORRELATION matrix (daily returns, pairwise):")
print(R.corr().round(2).to_string())

# ---- (a) allocation methods ----
Rf = R.fillna(0.0)                            # non-trading day = flat
vol = R.std()
methods = {
    "Equal (1/n)":      pd.Series(1.0, index=ASSETS.keys()),
    "Inverse-vol":      1.0/vol,
    "Sharpe-weighted":  pd.Series({k: max(v, 0) for k, v in sh.items()}),
}
print("\n" + "#"*60)
print("(a) PORTFOLIO by allocation method (weights full-sample = illustrative):")
print("#"*60)
print(f"{'method':<18}{'CAGR':>9}{'MaxDD':>9}{'Sharpe':>9}{'vs bestSingle':>14}")
print("-"*60)
best_single = max(sh.values())
best_port = (None, 0)
for mname, w in methods.items():
    w = w/w.sum()
    port = (Rf*w).sum(axis=1)
    c, d, s = dstats(port)
    print(f"{mname:<18}{c*100:>8.1f}%{d*100:>8.1f}%{s:>9.2f}{'x'+format(s/best_single,'.2f'):>14}")
    if s > best_port[1]:
        best_port = (mname, s)

# diversified-3 (drop ETH, redundant with BTC) with Sharpe-weight
w3 = pd.Series({k: max(sh[k], 0) for k in ["BTC", "GOLD", "SPX"]})
w3 = w3/w3.sum()
port3 = (Rf[["BTC", "GOLD", "SPX"]]*w3).sum(axis=1)
c, d, s3 = dstats(port3)
print(f"{'BTC+GOLD+SPX (SW)':<18}{c*100:>8.1f}%{d*100:>8.1f}%{s3:>9.2f}{'x'+format(s3/best_single,'.2f'):>14}")
print("#"*60)

gp = 3*best_port[1]**2/8
print(f"\nbest portfolio Sharpe {best_port[1]:.2f} ({best_port[0]}) -> half-Kelly ceiling "
      f"~{(np.exp(gp/365)-1)*100:.2f}%/day ({(np.exp(gp)-1)*100:.0f}%/yr)")
print("target 0.3%/day needs Sharpe ~1.74. NOTES: (1) weights are full-sample (real")
print("version re-estimates on rolling train). (2) costs approximate for non-BTC.")
print("(3) crypto-crypto (BTC-ETH) barely diversifies; cross-class (Gold/SPX) does.")
