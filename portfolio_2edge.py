#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
portfolio_2edge.py -- the TWO edges we can actually defend, combined honestly:
  GOLD  = EMA50/200 long-only trend-following (validated in gold_trend_sleeve.py)
  BTC   = walk-forward-validated Combo LongBias (backtest_btc / walkforward_btc)

Measures their correlation and what a 2-sleeve portfolio really delivers, then
resets the daily-% target to what these two edges can honestly support and shows
the gap to 0.3-0.5%/day (how many MORE uncorrelated edges are needed).

Calendar-daily returns, BPY=365 (weekend = flat for gold). Real costs.
ASCII-only. Runs locally.
"""
import os
import numpy as np
import pandas as pd
from backtest_btc import s_trend_longflat, s_tsmom, s_donchian

DL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "download")
BTC_CSV = os.path.join(DL, "btcusdt-15m-binance-2017-08-17-2026-06-30.csv")
GOLD_CSV = os.path.join(DL, "xauusd-m15-bid-2013-01-01-2026-06-10.csv")
BPY = 365


def load_h1(path, unit_ms=True):
    df = pd.read_csv(path)
    ts = df["timestamp"]
    if pd.api.types.is_numeric_dtype(ts):
        df["timestamp"] = pd.to_datetime(ts, unit="ms")
    else:
        df["timestamp"] = pd.to_datetime(ts)
    df = df.set_index("timestamp")
    o = df["open"].resample("1h").first(); h = df["high"].resample("1h").max()
    l = df["low"].resample("1h").min();  c = df["close"].resample("1h").last()
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c}).dropna()


def ema(s, n): return s.ewm(span=n, adjust=False).mean()


def gold_pos(df):                               # EMA50/200 long-only trend
    return (ema(df["close"], 50) > ema(df["close"], 200)).astype(float)


def btc_pos(df):                                # validated combo longbias
    t = s_trend_longflat(df, 25*24, 120*24)
    m = s_tsmom(df, 40*24)
    d = s_donchian(df, 20*24, 10*24)
    return ((t + m + d)/3.0).clip(lower=0)


def net_daily(df, pos, spread_ratio_side, swap_long=0.0, swap_short=0.0):
    ret = df["close"].pct_change().fillna(0).values
    p = pos.shift(1).fillna(0).values
    tc = spread_ratio_side * np.abs(np.diff(p, prepend=0.0))
    day = df.index.normalize()
    roll = (day.values != np.roll(day.values, 1)); roll[0] = False
    swap = np.where(roll & (p > 0), swap_long/365, 0.0) + np.where(roll & (p < 0), swap_short/365, 0.0)
    net = pd.Series(p*ret - tc - swap, index=df.index)
    return (1+net).groupby(net.index.normalize()).prod()-1


def stats(d):
    d = d.dropna()
    eq = (1+d).cumprod()
    yrs = (d.index[-1]-d.index[0]).days/365.25
    cagr = eq.iloc[-1]**(1/yrs)-1 if eq.iloc[-1] > 0 else -1
    dd = -(eq/eq.cummax()-1).min()
    sh = d.mean()/d.std()*np.sqrt(BPY) if d.std() > 0 else 0
    return cagr, dd, sh, (cagr/dd if dd > 0 else 0)


print("loading & running the two validated sleeves ...")
g = load_h1(GOLD_CSV); b = load_h1(BTC_CSV)
# gold spread 0.28 price -> half-spread ratio; gold swap ~ negligible here
gd = net_daily(g, gold_pos(g), (0.28/2)/g["close"].mean(), 0.02, 0.02)
# btc real costs: spread 0.016% RT -> 0.00008/side, swap_long 6.9%/yr, short 0
bd = net_daily(b, btc_pos(b), 0.00008, 0.069, 0.0)

R = pd.DataFrame({"GOLD": gd, "BTC": bd}).loc["2017-08-17":]
common = R.dropna()
corr = common["GOLD"].corr(common["BTC"])

print("\n" + "="*66)
print("%-22s %9s %8s %8s %7s" % ("sleeve (2017-26 overlap)", "CAGR", "MaxDD", "Sharpe", "MAR"))
print("-"*66)
for nm in ["GOLD", "BTC"]:
    c, dd, sh, mar = stats(R[nm].dropna())
    print("%-22s %8.1f%% %7.0f%% %8.2f %7.2f" % (nm, c*100, dd*100, sh, mar))
print("="*66)
print("GOLD<->BTC daily-return correlation: %+.2f  (%s)"
      % (corr, "great diversifier" if abs(corr) < 0.2 else "partly redundant"))

# ---- allocations ----
Rf = R.fillna(0.0)
vol = R.std()
allocs = {
    "50 / 50":        pd.Series({"GOLD": 0.5, "BTC": 0.5}),
    "inverse-vol":    (1/vol)/(1/vol).sum(),
    "risk-parity~":   (1/vol)/(1/vol).sum(),
}
print("\n%-16s %9s %8s %8s %7s %14s" % ("allocation", "CAGR", "MaxDD", "Sharpe", "MAR", "halfKelly/day"))
print("-"*66)
best = (None, 0)
for nm, w in allocs.items():
    port = (Rf*w).sum(axis=1)
    c, dd, sh, mar = stats(port)
    hk = np.exp(3*sh**2/8/BPY)-1
    print("%-16s %8.1f%% %7.0f%% %8.2f %7.2f %12.3f%%" % (nm, c*100, dd*100, sh, mar, hk*100))
    if sh > best[1]:
        best = (nm, sh)

# ---- target reset ----
print("\n" + "#"*66)
print("(3) HONEST TARGET RESET")
print("#"*66)
bs = best[1]
print("best 2-edge portfolio Sharpe = %.2f (%s)" % (bs, best[0]))
for tgt in [0.0008, 0.0010, 0.0030, 0.0050]:
    # daily% at half-Kelly = exp(3S^2/8/365)-1 ; invert for needed S
    # S_needed from: exp(3S^2/8/365)-1 = tgt
    Sneed = np.sqrt(np.log(1+tgt)*365*8/3)
    n_edges = (Sneed/0.6)**2   # assume each new edge ~Sharpe 0.6 (our sleeves' level)
    print("  %.2f%%/day  -> needs portfolio Sharpe %.2f  (~%.1f uncorrelated Sharpe-0.6 edges)"
          % (tgt*100, Sneed, n_edges))
print("-"*66)
hk_opt = np.exp(3*bs**2/8/BPY)-1
# realistic haircut: BTC walk-forward OOS Sharpe ~0.78, gold ex-beta ~0.50, corr 0.06
S_g, S_b = 0.50, 0.78
S_real = np.sqrt(S_g**2 + S_b**2 + 2*0.06*S_g*S_b)   # ~uncorrelated combine
hk_real = np.exp(3*S_real**2/8/BPY)-1
print("OPTIMISTIC (full-sample inputs): Sharpe %.2f -> ~%.3f%%/day (~%.0f%%/yr) half-Kelly"
      % (bs, hk_opt*100, (np.exp(3*bs**2/8)-1)*100))
print("REALISTIC (BTC WF-OOS 0.78 + gold ex-beta 0.50): Sharpe %.2f -> ~%.3f%%/day (~%.0f%%/yr)"
      % (S_real, hk_real*100, (np.exp(3*S_real**2/8)-1)*100))
print("-"*66)
print("=> honest target with 2 edges: ~0.08-0.10%%/day (realistic) up to ~0.2%%/day (if")
print("   the full-sample edges hold). 0.3-0.5%%/day still needs ~3-5 MORE such edges.")
print("\nCAVEATS: BTC combo Sharpe 1.25 here is FULL-SAMPLE (walk-forward OOS was")
print("weaker, MAR 0.59 ~ Sharpe 0.78); gold Sharpe is beta-heavy (2013-26 bull).")
print("Half-Kelly at Sharpe ~1 still implies ~30-50%% drawdowns -- size accordingly.")
