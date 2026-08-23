#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest_btc.py -- multi-strategy BTC backtest with realistic Exness BTCUSDc costs.

Runs on the Mac. Data = Binance 15m (shape only), resampled to 1H for swing/position
strategies. Costs modelled from the real BTCUSDc symbol_info snapshot (2026-07-07):
  * spread ~0.016% round-trip (price-independent %, applied on turnover)
  * swap: LONG -6.9%/yr, SHORT 0 (free) -- charged per daily rollover (24/7)
Strategies tested (unit exposure, position in {-1,0,+1}, no lookahead):
  0 Buy&Hold                (benchmark)
  1 MA-cross trend          (fast/slow EMA)  -- trend baseline proxy for C_wider
  2 Donchian breakout       (Turtle N/M)
  3 TSMOM                   (sign of L-lookback return)
  4 Vol-squeeze breakout    (Bollinger-in-Keltner release)
  5 Regime-filtered MR      (RSI2 fade, only when ADX says 'range')

Params are fixed textbook values (NOT optimised) -> the full-sample and per-year
numbers are honest (no in-sample fitting). Benchmark bar for BTC = beat Buy&Hold on
MAR (CAGR/MaxDD) and Sharpe, not just raw return.
"""
import os
import sys
import numpy as np
import pandas as pd

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "download", "btcusdt-15m-binance-2017-08-17-2026-06-30.csv")

# ---- cost model (from BTCUSDc symbol_info 2026-07-07) ----
SPREAD_RT = 0.00016          # round-trip spread as fraction of price (~$10 / $63k)
HALF_SPREAD = SPREAD_RT / 2  # cost per unit of turnover
SWAP_LONG_YR = 0.069         # long financing 6.9%/yr
SWAP_SHORT_YR = 0.0          # short is free
BARS_TF = "1h"
BARS_PER_YEAR = 24 * 365     # 1H bars


def load(tf=BARS_TF):
    df = pd.read_csv(DATA, parse_dates=["timestamp"]).set_index("timestamp")
    o = df["open"].resample(tf).first()
    h = df["high"].resample(tf).max()
    l = df["low"].resample(tf).min()
    c = df["close"].resample(tf).last()
    out = pd.DataFrame({"open": o, "high": h, "low": l, "close": c}).dropna()
    return out


# ---------------- indicators ----------------
def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()

def rsi(s, n):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100/(1+rs)).fillna(50)

def atr(df, n):
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift()
    tr = pd.concat([h-l, (h-pc).abs(), (l-pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()

def adx(df, n=14):
    h, l, c = df["high"], df["low"], df["close"]
    up = h.diff(); dn = -l.diff()
    plus = np.where((up > dn) & (up > 0), up, 0.0)
    minus = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    atr_ = tr.ewm(alpha=1/n, adjust=False).mean()
    pdi = 100 * pd.Series(plus, index=df.index).ewm(alpha=1/n, adjust=False).mean() / atr_
    mdi = 100 * pd.Series(minus, index=df.index).ewm(alpha=1/n, adjust=False).mean() / atr_
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1/n, adjust=False).mean().fillna(0)


# ---------------- strategies: return target position series in {-1,0,1} ----------------
def s_buyhold(df):
    return pd.Series(1.0, index=df.index)

def s_macross(df, fast=50*24, slow=200*24):   # 50d/200d EMA on 1H
    f, s = ema(df["close"], fast), ema(df["close"], slow)
    return np.sign(f - s).replace(0, np.nan).ffill().fillna(0)

def s_donchian(df, entry=20*24, exit=10*24):  # Turtle 20d entry / 10d exit
    c = df["close"].values
    hh = df["close"].rolling(entry).max().shift().values
    ll = df["close"].rolling(entry).min().shift().values
    xhh = df["close"].rolling(exit).max().shift().values
    xll = df["close"].rolling(exit).min().shift().values
    pos = np.zeros(len(c)); p = 0.0
    for i in range(len(c)):
        if np.isnan(hh[i]):
            pos[i] = 0; continue
        if p <= 0 and c[i] > hh[i]:
            p = 1.0
        elif p >= 0 and c[i] < ll[i]:
            p = -1.0
        elif p == 1.0 and c[i] < xll[i]:
            p = 0.0
        elif p == -1.0 and c[i] > xhh[i]:
            p = 0.0
        pos[i] = p
    return pd.Series(pos, index=df.index)

def s_tsmom(df, look=30*24):                  # 30d time-series momentum
    r = df["close"] / df["close"].shift(look) - 1
    return np.sign(r).fillna(0)

def s_trend_longflat(df, fast=20*24, slow=100*24):  # long in uptrend, CASH in downtrend
    f, s = ema(df["close"], fast), ema(df["close"], slow)
    return (f > s).astype(float)                 # 1 when fast>slow else 0 (never short)

def s_squeeze(df, n=20, bb=2.0, kc=1.5):      # TTM squeeze: 20-bar Bollinger-in-Keltner
    c = df["close"]
    ma = c.rolling(n).mean()
    sd = c.rolling(n).std()
    a = atr(df, n)
    bb_up, bb_dn = ma + bb*sd, ma - bb*sd
    kc_up, kc_dn = ma + kc*a, ma - kc*a
    squeeze_on = (bb_up < kc_up) & (bb_dn > kc_dn)   # BB inside KC = low vol
    released = squeeze_on.shift(1).fillna(False) & (~squeeze_on.fillna(False))
    direction = np.sign(c - ma)
    pos = np.zeros(len(c)); p = 0.0
    rel = released.values; dr = direction.values; cc = c.values; mm = ma.values
    for i in range(len(cc)):
        if rel[i]:
            p = dr[i]
        elif p == 1.0 and cc[i] < mm[i]:
            p = 0.0
        elif p == -1.0 and cc[i] > mm[i]:
            p = 0.0
        pos[i] = p if not np.isnan(mm[i]) else 0.0
    return pd.Series(pos, index=df.index)

def s_regime_mr(df, adx_n=14, adx_gate=25, rsi_n=2, lo=10, hi=90):
    a = adx(df, adx_n)
    r = rsi(df["close"], rsi_n)
    ranging = (a < adx_gate).values
    rv = r.values
    pos = np.zeros(len(rv)); p = 0.0
    for i in range(len(rv)):
        if not ranging[i]:
            p = 0.0                     # trend regime -> stand aside
        else:
            if p == 0.0:
                if rv[i] < lo: p = 1.0
                elif rv[i] > hi: p = -1.0
            elif p == 1.0 and rv[i] > 50: p = 0.0
            elif p == -1.0 and rv[i] < 50: p = 0.0
        pos[i] = p
    return pd.Series(pos, index=df.index)


# ---------------- backtest engine ----------------
def run(df, pos):
    ret = df["close"].pct_change().fillna(0).values
    p = pos.shift(1).fillna(0).values          # hold prior-bar signal -> no lookahead
    # transaction cost on turnover
    turn = np.abs(np.diff(p, prepend=0.0))
    tc = HALF_SPREAD * turn
    # swap: charge per daily rollover on the held position
    day = df.index.normalize()
    rollover = (day != np.roll(day, 1)); rollover[0] = False
    sw_long = SWAP_LONG_YR / 365.0
    swap = np.where(rollover & (p > 0), sw_long, 0.0) \
         + np.where(rollover & (p < 0), SWAP_SHORT_YR/365.0, 0.0)
    net = p * ret - tc - swap
    eq = (1 + net).cumprod()
    return net, eq, p


def metrics(net, eq, p, name):
    n = len(net)
    yrs = n / BARS_PER_YEAR
    cagr = eq[-1] ** (1/yrs) - 1
    peak = np.maximum.accumulate(eq)
    dd = (eq/peak - 1)
    maxdd = -dd.min()
    sharpe = net.mean()/net.std()*np.sqrt(BARS_PER_YEAR) if net.std() > 0 else 0
    mar = cagr/maxdd if maxdd > 0 else np.nan
    trades = int((np.abs(np.diff(p, prepend=0)) > 0).sum())
    expo = np.mean(np.abs(p) > 0)
    longpct = np.mean(p > 0); shortpct = np.mean(p < 0)
    return dict(name=name, cagr=cagr, maxdd=maxdd, mar=mar, sharpe=sharpe,
                total=eq[-1]-1, trades=trades, expo=expo, longp=longpct, shortp=shortpct)


STRATS = [
    ("Buy&Hold", s_buyhold),
    ("MA-cross 50/200d", s_macross),
    ("Trend long/flat", s_trend_longflat),
    ("Donchian 20/10d", s_donchian),
    ("TSMOM 30d", s_tsmom),
    ("VolSqueeze TTM", s_squeeze),
    ("Regime-MR (RSI2)", s_regime_mr),
]


def main():
    if not os.path.exists(DATA):
        sys.exit(f"data not found: {DATA}")
    df = load()
    print(f"loaded {len(df):,} {BARS_TF} bars  {df.index[0]} -> {df.index[-1]}  "
          f"(~{len(df)/BARS_PER_YEAR:.2f}y)\n")
    print(f"cost model: spread {SPREAD_RT*100:.3f}% RT, swap_long {SWAP_LONG_YR*100:.1f}%/yr, swap_short 0\n")

    results = {}
    rows = []
    for name, fn in STRATS:
        pos = fn(df)
        net, eq, p = run(df, pos)
        m = metrics(net, eq, p, name)
        results[name] = (net, eq, p, df)
        rows.append(m)

    # full-sample table
    print("=" * 100)
    print(f"{'strategy':<20}{'CAGR':>8}{'MaxDD':>8}{'MAR':>7}{'Sharpe':>8}"
          f"{'TotRet':>10}{'trades':>8}{'expo%':>7}{'long%':>7}{'short%':>7}")
    print("-" * 100)
    for m in rows:
        print(f"{m['name']:<20}{m['cagr']*100:>7.1f}%{m['maxdd']*100:>7.1f}%"
              f"{m['mar']:>7.2f}{m['sharpe']:>8.2f}{m['total']*100:>9.0f}%"
              f"{m['trades']:>8}{m['expo']*100:>6.0f}%{m['longp']*100:>6.0f}%{m['shortp']*100:>6.0f}%")
    print("=" * 100)

    # per-year CAGR (OOS-robustness view)
    print("\nPer-year return (%) -- consistency across regimes (honest OOS, params not fitted):")
    years = sorted(set(df.index.year))
    hdr = "year        " + "".join(f"{y:>8}" for y in years)
    print(hdr)
    for name, fn in STRATS:
        net, eq, p, _ = results[name]
        s = pd.Series(net, index=df.index)
        yr_ret = (1+s).groupby(df.index.year).prod() - 1
        line = f"{name:<12}" + "".join(f"{yr_ret.get(y, np.nan)*100:>7.0f}%" for y in years)
        print(line)

    # correlation of return streams (for later combination decisions)
    print("\nPairwise correlation of daily net returns (diversification check):")
    daily = {}
    for name, _ in STRATS:
        net = results[name][0]
        daily[name] = pd.Series(net, index=df.index).resample("1D").sum()
    corr = pd.DataFrame(daily).corr()
    print(corr.round(2).to_string())

    # ---------------- COMBINE best-3, cut self-conflicts ----------------
    # best 3 tradeable by MAR: Trend long/flat, TSMOM, Donchian
    t = s_trend_longflat(df)      # {0,1}  never short
    m = s_tsmom(df)               # {-1,0,1}
    d = s_donchian(df)            # {-1,0,1}
    avg = (t + m + d) / 3.0                      # equal-weight signal vote
    longbias = avg.clip(lower=0)                 # CUT conflicting net-short (short hurt on BTC)
    # consensus: trade only when >=2 of 3 agree on a direction, else flat
    votesum = t + m + d
    consensus = pd.Series(np.where(votesum >= 2, 1.0, np.where(votesum <= -2, -1.0, 0.0)),
                          index=df.index)

    combos = [
        ("Combo EW (3 avg)", avg),
        ("Combo LongBias (cut short)", longbias),
        ("Combo Consensus>=2", consensus),
        ("[ref] Trend long/flat", t),
        ("[ref] Buy&Hold", s_buyhold(df)),
    ]
    print("\n" + "#" * 100)
    print("COMBINED best-3 (Trend long/flat + TSMOM + Donchian), self-conflicts removed")
    print("#" * 100)
    print(f"{'strategy':<28}{'CAGR':>8}{'MaxDD':>8}{'MAR':>7}{'Sharpe':>8}"
          f"{'TotRet':>10}{'trades':>8}{'expo%':>7}{'short%':>7}")
    print("-" * 100)
    crows = []
    for name, pos in combos:
        net, eq, p = run(df, pos)
        mm = metrics(net, eq, p, name)
        crows.append((name, net))
        print(f"{name:<28}{mm['cagr']*100:>7.1f}%{mm['maxdd']*100:>7.1f}%"
              f"{mm['mar']:>7.2f}{mm['sharpe']:>8.2f}{mm['total']*100:>9.0f}%"
              f"{mm['trades']:>8}{mm['expo']*100:>6.0f}%{mm['shortp']*100:>6.0f}%")
    print("#" * 100)
    print("\nPer-year return (%) of combined variants:")
    print("year            " + "".join(f"{y:>8}" for y in years))
    for name, net in crows:
        s = pd.Series(net, index=df.index)
        yr = (1+s).groupby(df.index.year).prod() - 1
        print(f"{name:<16}" + "".join(f"{yr.get(y, np.nan)*100:>7.0f}%" for y in years))


if __name__ == "__main__":
    main()
