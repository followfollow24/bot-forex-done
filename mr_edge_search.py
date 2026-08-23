#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mr_edge_search.py -- hunt for EDGE #3 from a DIFFERENT signal family:
mean-reversion (RSI-2 extremes, ADX-gated to ranging regimes). This is the
structural complement to the trend-pullback sleeves: MR profits in chop/reversals
-- exactly where trend-following bleeds (e.g. the gold 16-loss cluster now).

Test on BTC / ETH / gold (1h). Vectorized (EXPLORATORY -- not the real SL/TP
engine yet; real-engine validation required before any deploy). Walk-forward
train/test + full. Correlation measured against the REAL-engine gold + BTC-HF
daily sleeves (from portfolio_path_03). Costs: real spread/side + asym swap.

The bar we must clear: OOS edge (Sharpe > ~0.4) AND low corr (<~0.4) to BOTH
trend sleeves AND it must make money (or at least not bleed) during the trend
sleeves' worst reversal windows.
ASCII-only.
"""
import os, math
import numpy as np, pandas as pd

from portfolio_path_03 import run_gold, run_btc, daily_frac

DL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "download")
BPY = 365
SPLIT = pd.Timestamp("2022-01-01")


def load_h1(path):
    x = pd.read_csv(path); ts = x["timestamp"]
    x["timestamp"] = pd.to_datetime(ts) if not pd.api.types.is_numeric_dtype(ts) else pd.to_datetime(ts, unit="ms")
    x = x.set_index("timestamp")
    o = x["open"].resample("1h").first(); h = x["high"].resample("1h").max()
    l = x["low"].resample("1h").min();  c = x["close"].resample("1h").last()
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c}).dropna()


def rsi(s, n):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100/(1 + up/dn.replace(0, np.nan))


def adx(df, n=14):
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([h-l, (h-pc).abs(), (l-pc).abs()], axis=1).max(axis=1)
    up = h.diff(); dn = -l.diff()
    pdm = ((up > dn) & (up > 0)) * up
    mdm = ((dn > up) & (dn > 0)) * dn
    atr = tr.ewm(alpha=1/n, adjust=False).mean()
    pdi = 100*pdm.ewm(alpha=1/n, adjust=False).mean()/atr
    mdi = 100*mdm.ewm(alpha=1/n, adjust=False).mean()/atr
    dx = 100*(pdi-mdi).abs()/(pdi+mdi).replace(0, np.nan)
    return dx.ewm(alpha=1/n, adjust=False).mean()


def mr_pos(df, rsi_n=2, lo=5, hi=95, adx_gate=25, exitlvl=50):
    """RSI-2 mean-reversion, only when ADX < gate (ranging). State machine."""
    r = rsi(df["close"], rsi_n).values
    a = adx(df, 14).values
    pos = np.zeros(len(df))
    cur = 0.0
    for i in range(len(df)):
        if np.isnan(r[i]) or np.isnan(a[i]):
            pos[i] = cur; continue
        if cur == 0.0:
            if a[i] < adx_gate and r[i] < lo:   cur = 1.0
            elif a[i] < adx_gate and r[i] > hi: cur = -1.0
        elif cur > 0 and r[i] >= exitlvl:       cur = 0.0
        elif cur < 0 and r[i] <= exitlvl:        cur = 0.0
        pos[i] = cur
    return pd.Series(pos, index=df.index)


def net_daily(df, pos, spread_side, swap_l=0.0, swap_s=0.0):
    ret = df["close"].pct_change().fillna(0).values
    p = pos.shift(1).fillna(0).values
    tc = spread_side*np.abs(np.diff(p, prepend=0.0))
    day = df.index.normalize().values
    roll = (day != np.roll(day, 1)); roll[0] = False
    swap = np.where(roll & (p > 0), swap_l/365, 0.0) + np.where(roll & (p < 0), swap_s/365, 0.0)
    n = pd.Series(p*ret - tc - swap, index=df.index)
    return (1+n).groupby(n.index.normalize()).prod()-1


def stats(d, lbl):
    d = d.dropna()
    if len(d) < 30 or d.std() == 0:
        return dict(lbl=lbl, sh=0, ret=0, dd=1, n=len(d))
    eq = (1+d).cumprod(); yrs = (d.index[-1]-d.index[0]).days/365.25
    return dict(lbl=lbl, sh=d.mean()/d.std()*math.sqrt(BPY),
                ret=(eq.iloc[-1]-1)*100, cagr=(eq.iloc[-1]**(1/yrs)-1)*100 if eq.iloc[-1]>0 else -100,
                dd=-(eq/eq.cummax()-1).min()*100, n=len(d))


def show(m):
    print("    %-16s Sharpe %5.2f  CAGR %+6.1f%%  MaxDD %5.1f%%  (days %d)"
          % (m["lbl"], m["sh"], m.get("cagr", 0), m["dd"], m["n"]))


print("=" * 82)
print(" EDGE #3 SEARCH -- MEAN-REVERSION (RSI-2 + ADX gate), a DIFFERENT signal family")
print("=" * 82)

# real trend sleeves for correlation
print(" building real-engine trend sleeves (gold, BTC-HF) for correlation ...")
g_real = daily_frac(run_gold())
b_real = daily_frac(run_btc())

markets = {
    "MR_BTC": (os.path.join(DL, "btcusdt-15m-binance-2017-08-17-2026-06-30.csv"), 0.00008, 0.069, 0.0),
    "MR_ETH": (os.path.join(DL, "ethusdt-15m-binance-2017-08-17-2026-06-30.csv"), 0.0001, 0.10, 0.0),
    "MR_GOLD": (os.path.join(DL, "xauusd-m15-bid-2013-01-01-2026-06-10.csv"), 0.000035, 0.02, 0.02),
}

mr_daily = {}
print("\n MEAN-REVERSION sleeves (vectorized, real costs) -- TRAIN<2022 / TEST>=2022 / FULL:")
for name, (path, sp, swl, sws) in markets.items():
    df = load_h1(path)
    pos = mr_pos(df)
    dl = net_daily(df, pos, sp, swl, sws)
    mr_daily[name] = dl
    print(f"\n  {name}:")
    show(stats(dl[dl.index < SPLIT], "  train <2022"))
    show(stats(dl[dl.index >= SPLIT], "  TEST >=2022"))
    show(stats(dl, "  full"))

# correlation matrix (OOS 2022+)
print("\n" + "=" * 82)
print(" CORRELATION (daily, OOS 2022+) -- MR sleeves vs the two REAL trend sleeves")
print("=" * 82)
cols = {"GOLD_trend": g_real, "BTC_HF": b_real, **mr_daily}
R = pd.DataFrame(cols)
idx = pd.date_range(SPLIT, R.index.max(), freq="D")
R = R.reindex(idx).fillna(0.0)
print(R.corr().round(2).to_string().replace("\n", "\n "))

print("\n VERDICT: an MR sleeve qualifies as edge #3 if TEST Sharpe > ~0.4 AND corr to")
print(" BOTH GOLD_trend and BTC_HF < ~0.4 (ideally <=0). Negative corr = it actively")
print(" cushions the trend sleeves' reversal losses = the ideal complement.")
print("=" * 82)
