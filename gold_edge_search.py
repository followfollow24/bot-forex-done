#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gold_edge_search.py -- does XAUUSD have ANY tradeable edge, given the live
trend-pullback strategy loses in every param config over 13.4 years?

Screens 7 STRUCTURALLY DIFFERENT signal families (not param tweaks of the same
idea) with a vectorized, no-lookahead position backtest + realistic cost, and
reports full-sample AND walk-forward (train 60% / test 40%) so we don't get
fooled by an in-sample fluke. This is a SCREEN for signal edge (does the signal
predict returns net of cost); SL/TP execution is a later refinement.

Cost: real XAUUSDc spread ~0.28 price units -> ~0.014%/side as a ratio, charged
on |change in position|. ASCII-only. Runs locally on the 13yr M15 CSV.
"""
import os
import numpy as np
import pandas as pd

DL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "download")
CSV = os.path.join(DL, "xauusd-m15-bid-2013-01-01-2026-06-10.csv")
SPREAD_PRICE = 0.28          # real live spread (price units)
BPY = 252                    # daily annualisation


def load_h1():
    df = pd.read_csv(CSV)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.set_index("timestamp")
    o = df["open"].resample("1h").first()
    h = df["high"].resample("1h").max()
    l = df["low"].resample("1h").min()
    c = df["close"].resample("1h").last()
    out = pd.DataFrame({"open": o, "high": h, "low": l, "close": c}).dropna()
    return out


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi(s, n):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100/(1+rs)).fillna(50)


def adx(df, n=14):
    up = df["high"].diff()
    dn = -df["low"].diff()
    plus = np.where((up > dn) & (up > 0), up, 0.0)
    minus = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([df["high"]-df["low"],
                    (df["high"]-df["close"].shift()).abs(),
                    (df["low"]-df["close"].shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/n, adjust=False).mean()
    pdi = 100*pd.Series(plus, index=df.index).ewm(alpha=1/n, adjust=False).mean()/atr
    mdi = 100*pd.Series(minus, index=df.index).ewm(alpha=1/n, adjust=False).mean()/atr
    dx = 100*(pdi-mdi).abs()/(pdi+mdi).replace(0, np.nan)
    return dx.ewm(alpha=1/n, adjust=False).mean().fillna(0)


# ---------- signal families (each returns position in [-1,1], pre-shift) ----------
def s_trend_lo(df):                       # long-only H1 EMA trend
    return (ema(df["close"], 50) > ema(df["close"], 200)).astype(float)

def s_trend_ls(df):                       # long/short EMA trend
    f, s = ema(df["close"], 50), ema(df["close"], 200)
    return np.sign(f - s)

def s_tsmom(df, n=24*5):                   # time-series momentum (5-day)
    return np.sign(df["close"] - df["close"].shift(n)).fillna(0)

def s_donchian(df, n=48):                  # breakout
    hh = df["high"].rolling(n).max().shift(1)
    ll = df["low"].rolling(n).min().shift(1)
    pos = pd.Series(np.nan, index=df.index)
    pos[df["close"] > hh] = 1
    pos[df["close"] < ll] = -1
    return pos.ffill().fillna(0)

def s_meanrev_rsi(df):                     # fade RSI2 extremes (mean reversion)
    r = rsi(df["close"], 2)
    pos = pd.Series(0.0, index=df.index)
    pos[r < 5] = 1
    pos[r > 95] = -1
    return pos.ffill(limit=8).fillna(0)    # hold up to 8h or until flip

def s_meanrev_z(df, n=48):                 # fade z-score vs rolling mean
    m = df["close"].rolling(n).mean()
    sd = df["close"].rolling(n).std()
    z = (df["close"]-m)/sd
    pos = pd.Series(0.0, index=df.index)
    pos[z < -2] = 1
    pos[z > 2] = -1
    return pos.ffill(limit=12).fillna(0)

def s_trend_session(df):                   # trend but ONLY London/NY overlap 12-16 UTC
    base = np.sign(ema(df["close"], 50) - ema(df["close"], 200))
    hr = df.index.hour
    inwin = (hr >= 12) & (hr < 16)
    return pd.Series(np.where(inwin, base, 0.0), index=df.index)


def backtest(df, pos):
    ret = df["close"].pct_change().fillna(0).values
    p = pd.Series(pos, index=df.index).shift(1).fillna(0).values
    px = df["close"].values
    cost_ratio = (SPREAD_PRICE / 2) / px          # half-spread as fraction, per side
    tc = cost_ratio * np.abs(np.diff(p, prepend=0.0))
    net = p * ret - tc
    return pd.Series(net, index=df.index)


def stats(net):
    daily = (1+net).groupby(net.index.normalize()).prod() - 1
    daily = daily[daily.index >= daily.index[0]]
    eq = (1+daily).cumprod()
    yrs = (daily.index[-1]-daily.index[0]).days/365.25
    cagr = eq.iloc[-1]**(1/yrs)-1 if eq.iloc[-1] > 0 else -1
    dd = -(eq/eq.cummax()-1).min()
    sh = daily.mean()/daily.std()*np.sqrt(BPY) if daily.std() > 0 else 0
    mar = cagr/dd if dd > 0 else float("nan")
    return cagr, dd, sh, mar


def s_buyhold(df):                         # benchmark: always long gold
    return pd.Series(1.0, index=df.index)

def s_trend_trail(df, atr_mult=6.0):       # trend-follow, ride winners w/ ATR trailing
    # long when EMA50>EMA200; exit only when close crosses a chandelier trail
    up = ema(df["close"], 50) > ema(df["close"], 200)
    tr = pd.concat([df["high"]-df["low"],
                    (df["high"]-df["close"].shift()).abs(),
                    (df["low"]-df["close"].shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/14, adjust=False).mean()
    pos = np.zeros(len(df)); trail = np.nan
    c = df["close"].values; a = atr.values; u = up.values
    for i in range(1, len(df)):
        if pos[i-1] == 0:
            if u[i]:
                pos[i] = 1; trail = c[i]-atr_mult*a[i]
            else:
                pos[i] = 0
        else:  # in a long
            trail = max(trail, c[i]-atr_mult*a[i])
            if c[i] < trail:
                pos[i] = 0
            else:
                pos[i] = 1
    return pd.Series(pos, index=df.index)


SIGS = {
    "Buy & Hold gold (benchmark)": s_buyhold,
    "Trend long-only (EMA50/200)": s_trend_lo,
    "Trend + ATR trail (ride)": s_trend_trail,
    "Trend long/short (EMA50/200)": s_trend_ls,
    "TSMOM 5-day": s_tsmom,
    "Donchian breakout 48h": s_donchian,
    "MeanRev RSI2 fade": s_meanrev_rsi,
    "MeanRev z-score fade": s_meanrev_z,
    "Trend session 12-16 UTC": s_trend_session,
}

df = load_h1()
n = len(df)
split = df.index[int(n*0.6)]
print("XAUUSD H1 bars: %d  %s -> %s   (train<%s<=test)"
      % (n, df.index[0].date(), df.index[-1].date(), split.date()))
print("cost: spread %.2f price (~%.3f%%/side)   holy grail = positive OOS Sharpe\n"
      % (SPREAD_PRICE, (SPREAD_PRICE/2/df['close'].mean())*100))

hdr = "%-30s %18s %18s %18s" % ("signal", "FULL (Sh/MAR)", "TRAIN (Sh/MAR)", "TEST/OOS (Sh/MAR)")
print(hdr); print("-"*len(hdr))
rows = []
for name, fn in SIGS.items():
    pos = fn(df)
    net = backtest(df, pos)
    fc, fd, fs, fm = stats(net)
    tr = net[net.index <= split]; te = net[net.index > split]
    _, _, trs, trm = stats(tr)
    toc, tod, tos, tom = stats(te)
    star = "  <== OOS EDGE" if tos > 0.3 and fs > 0 else ("  <- oos+" if tos > 0 else "")
    print("%-30s   %5.2f / %5.2f      %5.2f / %5.2f      %5.2f / %5.2f%s"
          % (name, fs, fm, trs, trm, tos, tom, star))
    rows.append((name, fs, tos))

print("\nread: TEST/OOS Sharpe > 0 = signal kept edge on unseen data. > 0.3 = worth")
print("developing into a live sleeve. all <= 0 => gold has no directional edge here.")
