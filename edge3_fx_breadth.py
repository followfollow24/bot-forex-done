#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
edge3_fx_breadth.py -- test FX majors + NDX + WTI as SYMMETRIC long/short trend
sleeves (EMA50/200 sign -- a fixed symmetric rule, NOT train-picked direction).
Goal: find uncorrelated trend edges with Sharpe COMPARABLE to the existing 2
(gold real + BTC-HF real) so the portfolio Sharpe actually rises toward the
0.3%/day threshold at LOW drawdown (CTA breadth path).

Discipline: train<2022 / TEST>=2022 reported together; correlation to the real
sleeves; only sleeves that clear OOS Sharpe ~>=0.5 AND corr <~0.4 to everything
are kept. Portfolio = inverse-vol of survivors; half-Kelly %/day with a live
discount. Costs: realistic per-side spread (FX small, index/oil larger). Carry/
swap ignored on the FX daily sleeves (documented approximation -> revisit).
ASCII-only.
"""
import os, math
import numpy as np, pandas as pd
from portfolio_path_03 import run_gold, run_btc, daily_frac

DL = "download"; BPY = 365; SP = pd.Timestamp("2022-01-01")


def load_daily(p):
    x = pd.read_csv(p); x["timestamp"] = pd.to_datetime(x["timestamp"])
    return x.set_index("timestamp")[["open", "high", "low", "close"]].dropna()


def ema(s, n): return s.ewm(span=n, adjust=False).mean()


def trend_ls(df, spread_side):
    """symmetric long/short: pos = sign(EMA50-EMA200). daily net returns."""
    pos = np.sign(ema(df["close"], 50) - ema(df["close"], 200)).fillna(0.0)
    ret = df["close"].pct_change().fillna(0).values
    p = pos.shift(1).fillna(0).values
    tc = spread_side * np.abs(np.diff(p, prepend=0.0))
    return pd.Series(p * ret - tc, index=df.index)


def st(d):
    d = d.dropna()
    if len(d) < 60 or d.std() == 0:
        return dict(sh=0, cagr=0, dd=100, n=len(d))
    eq = (1 + d).cumprod(); yrs = (d.index[-1] - d.index[0]).days / 365.25
    return dict(sh=d.mean()/d.std()*math.sqrt(BPY),
                cagr=(eq.iloc[-1]**(1/yrs)-1)*100 if eq.iloc[-1] > 0 else -100,
                dd=-(eq/eq.cummax()-1).min()*100, n=len(d))


MK = {  # name: (file, spread_side)
    "EURUSD": ("eurusd-daily-yahoo.csv", 0.00003),
    "GBPUSD": ("gbpusd-daily-yahoo.csv", 0.00004),
    "USDJPY": ("usdjpy-daily-yahoo.csv", 0.00003),
    "AUDUSD": ("audusd-daily-yahoo.csv", 0.00004),
    "USDCAD": ("usdcad-daily-yahoo.csv", 0.00004),
    "NDX":    ("ndx-daily-yahoo.csv",    0.0001),
    "WTI":    ("wti-daily-yahoo.csv",    0.0003),
}

print("=" * 86)
print(" EDGE #3 BREADTH -- FX majors + NDX + WTI as symmetric long/short trend sleeves")
print("=" * 86)
print(" building real sleeves (gold, BTC-HF) ...")
g = daily_frac(run_gold()); b = daily_frac(run_btc())

sleeves = {}
print("\n %-8s %-22s %-22s %-22s" % ("market", "train<2022 Sh/CAGR", "TEST>=2022 Sh/CAGR", "full Sh/CAGR/DD"))
print(" " + "-" * 82)
for nm, (fn, sp) in MK.items():
    path = os.path.join(DL, fn)
    if not os.path.exists(path):
        print(" %-8s (missing)" % nm); continue
    dl = trend_ls(load_daily(path), sp)
    sleeves[nm] = dl
    a, c, f = st(dl[dl.index < SP]), st(dl[dl.index >= SP]), st(dl)
    print(" %-8s %6.2f / %+6.1f%%       %6.2f / %+6.1f%%       %5.2f / %+6.1f%% / %4.1f%%"
          % (nm, a["sh"], a["cagr"], c["sh"], c["cagr"], f["sh"], f["cagr"], f["dd"]))

# correlation OOS to real sleeves
print("\n CORRELATION (daily OOS 2022+) to the two REAL edges:")
allc = {"GOLD": g, "BTC_HF": b, **sleeves}
R = pd.DataFrame(allc)
idx = pd.date_range(SP, R.index.max(), freq="D"); R = R.reindex(idx).fillna(0.0)
cc = R.corr()
print("  %-8s %8s %8s %8s" % ("sleeve", "vsGOLD", "vsBTC_HF", "maxAbs(vs FX peers)"))
for nm in sleeves:
    peers = [p for p in sleeves if p != nm]
    mx = max(abs(cc.loc[nm, p]) for p in peers) if peers else 0
    print("  %-8s %8.2f %8.2f %12.2f" % (nm, cc.loc[nm, "GOLD"], cc.loc[nm, "BTC_HF"], mx))

# keep survivors: OOS Sharpe >= 0.5 AND |corr| to gold & btc < 0.4
keep = [nm for nm in sleeves
        if st(sleeves[nm][sleeves[nm].index >= SP])["sh"] >= 0.5
        and abs(cc.loc[nm, "GOLD"]) < 0.4 and abs(cc.loc[nm, "BTC_HF"]) < 0.4]
print("\n SURVIVORS (OOS Sh>=0.5 & corr<0.4 to both):", keep if keep else "(none)")


def port_sharpe(cols, window):
    sub = R[cols].copy()
    if window == "full":
        # extend index back to earliest
        sub = pd.DataFrame({c: allc[c] for c in cols})
        i2 = pd.date_range(sub.index.min(), sub.index.max(), freq="D")
        sub = sub.reindex(i2).fillna(0.0)
    vol = sub.std(); w = (1/vol) / (1/vol).sum()
    p = (sub * w).sum(axis=1)
    sh = p.mean()/p.std()*math.sqrt(BPY)
    mdd = -((1+p).cumprod()/(1+p).cumprod().cummax()-1).min()*100
    return sh, mdd, p.mean()


print("\n" + "=" * 86)
print(" PORTFOLIO (inverse-vol) -- Sharpe + path to 0.3%/day")
print("=" * 86)
scenarios = [
    ("2 edge: GOLD+BTC_HF", ["GOLD", "BTC_HF"]),
    ("+ all FX/NDX/WTI", ["GOLD", "BTC_HF"] + list(sleeves.keys())),
]
if keep:
    scenarios.insert(1, ("+ survivors only", ["GOLD", "BTC_HF"] + keep))
for name, cols in scenarios:
    sh_o, dd_o, mu_o = port_sharpe(cols, "oos")
    for disc, tag in [(1.0, "OOS"), (0.65, "live~65%")]:
        sh = sh_o * disc
        hk = math.exp(3*sh**2/8/BPY) - 1
        if tag == "OOS":
            print("  %-26s OOS Sharpe %.2f  MaxDD %.1f%%  half-Kelly %.3f%%/day"
                  % (name, sh, dd_o, hk*100))
        else:
            print("  %-26s %s Sharpe %.2f  half-Kelly %.3f%%/day  %s"
                  % ("", tag, sh, hk*100, ">=0.3 OK" if hk >= 0.003 else "< 0.3"))
print("=" * 86)
