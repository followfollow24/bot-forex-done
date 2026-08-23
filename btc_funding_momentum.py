#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
btc_funding_momentum.py -- candidate EDGE #3 for the portfolio.

Hypothesis: BTCUSDc has an ASYMMETRIC carry (swap_long = -6.9%/yr, swap_short = 0).
A naive symmetric momentum ignores this. A funding-AWARE version demands stronger
momentum to justify a (carry-paying) long, but shorts freely (carry-free) -> it
should hold shorts in bear markets when the existing long-bias combo sits flat,
giving LOW correlation to combo = a genuine third edge.

Tests: symmetric vs funding-aware thresholds, honest expanding walk-forward
(train picks lookback + thresholds), OOS metrics, and correlation to the combo.
Real costs: spread 0.00008/side, swap_long 6.9%/yr on longs, 0 on shorts.
ASCII-only. Runs locally on the Binance-shape BTC CSV (SHAPE ONLY -> costs from
the real Exness snapshot, never from Binance).
"""
import os
import numpy as np
import pandas as pd
from backtest_btc import s_trend_longflat, s_tsmom, s_donchian

DL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "download")
BTC = os.path.join(DL, "btcusdt-15m-binance-2017-08-17-2026-06-30.csv")
SPREAD_SIDE = 0.00008      # 0.016% round-trip / 2
SWAP_LONG = 0.069          # 6.9%/yr paid on longs
SWAP_SHORT = 0.0           # free to hold short
BPY = 365
D = 24                     # 1h bars per day


def load_h1():
    df = pd.read_csv(BTC)
    ts = df["timestamp"]
    df["timestamp"] = pd.to_datetime(ts) if not pd.api.types.is_numeric_dtype(ts) \
        else pd.to_datetime(ts, unit="ms")
    df = df.set_index("timestamp")
    o = df["open"].resample("1h").first(); h = df["high"].resample("1h").max()
    l = df["low"].resample("1h").min();  c = df["close"].resample("1h").last()
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c}).dropna()


def fund_mom_pos(df, lb_days, a_long, a_short):
    """funding-aware TSMOM: signal = move / trailing vol (a z-score of momentum).
    long if z > a_long (must beat carry), short if z < -a_short (free)."""
    L = lb_days * D
    ret_L = df["close"] / df["close"].shift(L) - 1
    vol = df["close"].pct_change().rolling(L).std() * np.sqrt(L)
    z = (ret_L / vol).replace([np.inf, -np.inf], np.nan).fillna(0)
    pos = pd.Series(0.0, index=df.index)
    pos[z > a_long] = 1.0
    pos[z < -a_short] = -1.0
    return pos


def net(df, pos):
    ret = df["close"].pct_change().fillna(0).values
    p = pos.shift(1).fillna(0).values
    tc = SPREAD_SIDE * np.abs(np.diff(p, prepend=0.0))
    day = df.index.normalize().values
    roll = (day != np.roll(day, 1)); roll[0] = False
    swap = np.where(roll & (p > 0), SWAP_LONG/365, 0.0) + np.where(roll & (p < 0), SWAP_SHORT/365, 0.0)
    return pd.Series(p*ret - tc - swap, index=df.index)


def daily(n): return (1+n).groupby(n.index.normalize()).prod()-1


def stats(n):
    d = daily(n).dropna()
    if len(d) < 30 or d.std() == 0:
        return dict(cagr=0, dd=1, sh=0, mar=0)
    eq = (1+d).cumprod()
    yrs = (d.index[-1]-d.index[0]).days/365.25
    cagr = eq.iloc[-1]**(1/yrs)-1 if eq.iloc[-1] > 0 else -1
    dd = -(eq/eq.cummax()-1).min()
    sh = d.mean()/d.std()*np.sqrt(BPY)
    return dict(cagr=cagr, dd=dd, sh=sh, mar=(cagr/dd if dd > 0 else 0))


df = load_h1()
# existing long-bias combo (edge #2) for correlation
combo = ((s_trend_longflat(df, 25*D, 120*D) + s_tsmom(df, 40*D) + s_donchian(df, 20*D, 10*D))/3).clip(lower=0)
combo_net = net(df, combo)
split = df.index[int(len(df)*0.55)]
print("BTC H1 %d bars %s..%s  split=%s\n" % (len(df), df.index[0].date(), df.index[-1].date(), split.date()))

# ---- symmetric vs funding-aware, full + OOS + corr to combo ----
LBS = [15, 30, 60]
THR = {"symmetric a=0.5": (0.5, 0.5), "fund-aware 1.0/0.3": (1.0, 0.3),
       "fund-aware 0.7/0.3": (0.7, 0.3), "fund-aware 1.2/0.5": (1.2, 0.5)}
print("%-22s %-6s %13s %13s %8s" % ("thresholds", "lb", "FULL Sh/MAR", "OOS Sh/MAR", "corr>combo"))
print("-"*70)
cache = {}
for tname, (aL, aS) in THR.items():
    for lb in LBS:
        n = net(df, fund_mom_pos(df, lb, aL, aS))
        cache[(tname, lb)] = n
        f, o = stats(n), stats(n[n.index > split])
        j = daily(n).dropna().index.intersection(daily(combo_net).dropna().index)
        cr = daily(n).reindex(j).corr(daily(combo_net).reindex(j))
        flag = "  <== edge+lowcorr" if (o['sh'] > 0.4 and abs(cr) < 0.3) else ""
        print("%-22s %-6d  %5.2f / %5.2f   %5.2f / %5.2f   %+.2f%s"
              % (tname, lb, f['sh'], f['mar'], o['sh'], o['mar'], cr, flag))
    print()

# ---- honest walk-forward: train picks (threshold,lb) each year ----
print("="*70)
print("HONEST WALK-FORWARD (train picks funding thresholds + lookback / yr)")
print("="*70)
configs = list(cache.keys())
years = list(range(2020, 2027))
oos, picks = [], []
for ty in years:
    tr = df.index.year < ty; te = df.index.year == ty
    if tr.sum() < D*300 or te.sum() < D*30:
        continue
    best, bv = None, -9
    for c in configs:
        m = stats(cache[c][tr])
        if m['sh'] > bv:
            bv, best = m['sh'], c
    picks.append((ty, best, bv, stats(cache[best][te])))
    oos.append(cache[best][te])
print("%-6s %-26s %8s %8s" % ("yr", "train-picked", "trainSh", "testSh"))
print("-"*54)
for ty, c, bv, tm in picks:
    print("%-6d %-26s %8.2f %8.2f" % (ty, f"{c[0]} lb{c[1]}", bv, tm['sh']))
oo = stats(pd.concat(oos))
# correlation of the WF sleeve to combo
wf = pd.concat(oos)
j = daily(wf).dropna().index.intersection(daily(combo_net).dropna().index)
cr = daily(wf).reindex(j).corr(daily(combo_net).reindex(j))
print("-"*54)
print("WF funding-momentum OOS: CAGR %+.1f%%  DD %.0f%%  Sharpe %.2f  MAR %.2f"
      % (oo['cagr']*100, oo['dd']*100, oo['sh'], oo['mar']))
print("correlation to existing BTC combo: %+.2f  (%s)"
      % (cr, "diversifies" if abs(cr) < 0.4 else "too redundant"))
print("\nVERDICT: keep as edge #3 only if OOS Sharpe > ~0.4 AND corr to combo < ~0.4.")
print("CAVEAT: BTC price = Binance SHAPE only; ~2-3 bears in sample -> provisional.")
