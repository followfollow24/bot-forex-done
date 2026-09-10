#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_1930_trend_days.py -- 19:30 on XAUUSD, but only on days that were already
trending hard when the bell rang.

Two fixes to the previous grid.

THE GATE IS NOW IN ATR, NOT POINTS. Gold ran 1675 -> 4400 across this
data, so a fixed 5-point gate is 0.30% early and 0.11% late -- the same
number describing two different conditions, which quietly confounds every
comparison across the 13 years.

AND THE TREND FILTER IS CAUSAL. "Strongly trending day" has to be decided
from bars BEFORE 19:30, never from how far price went afterwards. Selecting
days by the size of the move they went on to make is the oldest look-ahead
there is and it manufactures any result you like. So the filter is the H1
EMA20 slope over the six hours ENDING at the bell, measured in ATR:

    any        no filter
    trend      |slope| >= threshold, either way
    with       |slope| >= threshold AND the trade agrees with its sign
    against    |slope| >= threshold AND the trade opposes it

"with" is the H1 bot's idea -- trade only along the bigger trend -- applied
to this entry. "against" is included because leaving it out would make the
test unable to say the idea is backwards.

Protocol unchanged: search each half and score the other, BOTH ways, plus
the count of candidates keeping their sign against chance.

Usage:  python _1930_trend_days.py [spread]
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import DataLoader
from _idea_search import resample

RNG = np.random.default_rng(20260911)


def shuffle_days(df):
    """Gold's own drift, volatility clustering and intraday shape, with no
    memory from one day to the next. The lift statistic has been an
    artifact before -- in the 9,856-candidate sweep gold lifted +26 and
    this surrogate lifted +26 -- so it is necessary here."""
    out = df.copy()
    lr = np.diff(np.log(out["close"].to_numpy(float)), prepend=np.nan)
    day = out["timestamp"].dt.floor("D").to_numpy()
    _, first = np.unique(day, return_index=True)
    chunks = np.split(lr, np.sort(first)[1:])
    RNG.shuffle(chunks)
    r = np.concatenate(chunks)
    r[~np.isfinite(r)] = 0.0
    px = float(out["close"].iloc[0]) * np.exp(np.cumsum(r))
    scale = px / out["close"].to_numpy(float)
    for col in ("open", "high", "low", "close"):
        out[col] = out[col].to_numpy(float) * scale
    return out

CSV = "download/xauusd-m15-bid-2013-01-01-2026-06-10.csv"
SPREAD = float(sys.argv[1]) if len(sys.argv) > 1 else 0.24
GATES_ATR = [0.25, 0.5, 0.75, 1.0]     # of ATR(H1)
HOLDS = [2, 4, 8, 12, 24]              # M15 bars: 30 min .. 6 h
DIRS = [("follow", +1), ("fade", -1)]
SLOPES = [0.5, 1.0, 2.0]               # ATR of EMA20 drift over 6 h
MODES = ["any", "trend", "with", "against"]
SL_ATR = 3.0
MIN_TRADES = 25


def sweep(df, quiet=False):
    h1 = resample(df, "1h")
    hc = h1["close"].to_numpy(float)
    hh, hl = h1["high"].to_numpy(float), h1["low"].to_numpy(float)
    pc = np.concatenate(([hc[0]], hc[:-1]))
    tr = np.maximum(hh - hl, np.maximum(np.abs(hh - pc), np.abs(hl - pc)))
    atr = np.full(len(tr), np.nan)
    atr[13:] = np.convolve(tr, np.ones(14) / 14, mode="valid")
    k = 2.0 / 21.0
    ema = np.full(len(hc), np.nan)
    ema[0] = hc[0]
    for i in range(1, len(hc)):
        ema[i] = hc[i] * k + ema[i - 1] * (1 - k)
    slope = np.full(len(hc), np.nan)
    slope[6:] = (ema[6:] - ema[:-6])
    with np.errstate(invalid="ignore", divide="ignore"):
        slope_atr = slope / atr           # drift over 6h, in ATR
    # the H1 bar that CLOSES at the bell -- so everything is pre-bell
    hts = h1["timestamp"].to_numpy()
    prev = {t: i for i, t in enumerate(hts)}

    ts = df["timestamp"]
    o, h, l, c = (df[x].to_numpy(float) for x in ("open", "high", "low", "close"))
    hkey = (ts.dt.floor("h") - np.timedelta64(1, "h")).to_numpy()
    bells = np.flatnonzero(((ts.dt.hour == 12) & (ts.dt.minute == 30)).to_numpy())
    bells = bells[(bells > 4) & (bells < len(c) - max(HOLDS) - 3)]

    sess = []
    for i in bells:
        j = prev.get(hkey[i])
        if j is None or not np.isfinite(atr[j]) or atr[j] <= 0 \
           or not np.isfinite(slope_atr[j]):
            continue
        sess.append((i, float(atr[j]), float(slope_atr[j])))
    half = len(sess) // 2
    if not quiet:
        print(f"  {len(sess)} sessions with a pre-bell trend reading,"
              f" split at {ts.iloc[sess[half][0]]:%b %Y}")

    rows = []
    for ga in GATES_ATR:
        for mode in MODES:
            for sth in (SLOPES if mode != "any" else [0.0]):
                trig = []
                for n, (i, a, sl_atr_val) in enumerate(sess):
                    g = ga * a
                    ref = o[i]
                    up = (h[i] - ref) >= g
                    dn = (ref - l[i]) >= g
                    if not (up or dn):
                        continue
                    both = up and dn
                    sides = (1, -1) if both else ((1,) if up else (-1,))
                    if mode != "any":
                        if abs(sl_atr_val) < sth:
                            continue
                        tsign = 1 if sl_atr_val > 0 else -1
                        if mode == "with":
                            sides = tuple(s for s in sides if s == tsign)
                        elif mode == "against":
                            sides = tuple(s for s in sides if s != tsign)
                        if not sides:
                            continue
                        both = len(sides) > 1
                    trig.append((n, i, ref, a, g, sides, both))
                if len(trig) < MIN_TRADES * 2:
                    continue
                for hold in HOLDS:
                    for dname, dmul in DIRS:
                        e_p, l_p = [], []
                        for n, i, ref, a, g, sides, amb in trig:
                            seg_h = h[i:i + hold + 1].max()
                            seg_l = l[i:i + hold + 1].min()
                            ex = c[i + hold]
                            got = []
                            for s0 in sides:
                                side = s0 * dmul
                                entry = ref + s0 * g
                                stp = entry - side * SL_ATR * a
                                hit = ((seg_l <= stp) if side > 0
                                       else (seg_h >= stp))
                                px = stp if hit else ex
                                got.append((px - entry) * side - SPREAD)
                            v = min(got) if amb else got[0]
                            (e_p if n < half else l_p).append(v)
                        if len(e_p) < MIN_TRADES or len(l_p) < MIN_TRADES:
                            continue
                        rows.append((ga, mode, sth, hold, dname,
                                     float(np.mean(e_p)), len(e_p),
                                     float(np.mean(l_p)), len(l_p)))
    return rows


def lifts(rows):
    out = {}
    for fwd, name in ((True, "1st->2nd"), (False, "2nd->1st")):
        si, ci = (5, 7) if fwd else (7, 5)
        passed = [r for r in rows if r[si] > 0]
        base = sum(1 for r in rows if r[ci] > 0) / len(rows)
        surv = [r for r in passed if r[ci] > 0]
        lift = len(surv) / len(passed) if passed else 0.0
        out[name] = (len(passed), len(surv), lift, base)
    return out


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = 10_000.0
    print("loading 13 years of XAUUSD M15 ...", flush=True)
    df, _ = loader.load("XAUUSD", 99.0, c0, csv_path=CSV,
                        allow_synthetic=False)
    rows = sweep(df)
    if not rows:
        print("[ERROR] nothing traded enough"); return 2
    print("building the day-shuffled surrogate ...", flush=True)
    frows = sweep(shuffle_days(df), quiet=True)

    print("=" * 78)
    print(" IS THE LIFT REAL? -- gold against a day-shuffled surrogate")
    print("=" * 78)
    print(f"\n  {'direction':>12}{'series':>11}{'passed':>8}{'held':>7}"
          f"{'rate':>8}{'base':>8}{'lift':>8}")
    verdict = {}
    for name in ("1st->2nd", "2nd->1st"):
        g = lifts(rows)[name]
        f = lifts(frows)[name] if frows else (0, 0, 0.0, 0.0)
        for tag, v in (("GOLD", g), ("shuffled", f)):
            print(f"  {name if tag=='GOLD' else '':>12}{tag:>11}{v[0]:>8}"
                  f"{v[1]:>7}{v[2]*100:>7.0f}%{v[3]*100:>7.0f}%"
                  f"{(v[2]-v[3])*100:>+8.0f}")
        verdict[name] = (g[2] - g[3]) - (f[2] - f[3])
    print(f"\n  gold beyond the surrogate: "
          + "   ".join(f"{k} {v*100:+.0f}" for k, v in verdict.items()))
    if min(verdict.values()) <= 0.05:
        print("\n  The surrogate reproduces it. The lift is the search")
        print("  procedure, not the market -- same as the 9,856-candidate")
        print("  sweep. Nothing here.")
    else:
        print("\n  SURVIVES the surrogate in BOTH directions. First result")
        print("  in this project to do so. Read the survivors below, then")
        print("  test it forward on days nobody has looked at.")

    print("=" * 78)
    print(f" 19:30 XAUUSD, TREND-FILTERED -- {len(rows)} candidates"
          f"   gate in ATR   spread {SPREAD:.2f}")
    print("=" * 78)
    for fwd, name in ((True, "1st -> 2nd"), (False, "2nd -> 1st")):
        si, ci = (5, 7) if fwd else (7, 5)
        passed = [r for r in rows if r[si] > 0]
        base = sum(1 for r in rows if r[ci] > 0) / len(rows)
        surv = [r for r in passed if r[ci] > 0]
        lift = len(surv) / len(passed) if passed else 0.0
        print(f"\n  searching {name}: {len(passed)} passed, {len(surv)} held"
              f"  ({lift*100:.0f}% vs base {base*100:.0f}%,"
              f" lift {(lift-base)*100:+.0f})")
    agree = sum(1 for r in rows if (r[5] > 0) == (r[7] > 0))
    both = [r for r in rows if r[5] > 0 and r[7] > 0]
    print(f"\n  {agree} of {len(rows)} keep their sign both halves"
          f"  (chance {len(rows)//2})   positive in BOTH: {len(both)}")

    print(f"\n  BY TREND MODE  (average per trade, all gates and holds)")
    print(f"    {'mode':>9}{'slope>=':>9}{'cands':>7}{'1st avg':>10}"
          f"{'2nd avg':>10}{'both +':>8}")
    fam = defaultdict(list)
    for r in rows:
        fam[(r[1], r[2])].append(r)
    for key in sorted(fam, key=lambda k: (k[0], k[1])):
        v = fam[key]
        bp = sum(1 for r in v if r[5] > 0 and r[7] > 0)
        print(f"    {key[0]:>9}{key[1]:>9.1f}{len(v):>7}"
              f"{np.mean([r[5] for r in v]):>+10.2f}"
              f"{np.mean([r[7] for r in v]):>+10.2f}{bp:>8}")
    if both:
        print(f"\n  POSITIVE IN BOTH HALVES\n")
        print(f"    {'gate':>7}{'mode':>9}{'slope':>7}{'hold':>6}{'dir':>8}"
              f"{'1st':>9}{'n':>5}{'2nd':>9}{'n':>5}")
        for r in sorted(both, key=lambda r: -min(r[5], r[7]))[:15]:
            print(f"    {r[0]:>6.2f}A{r[1]:>9}{r[2]:>7.1f}{r[3]:>6}{r[4]:>8}"
                  f"{r[5]:>+9.2f}{r[6]:>5}{r[7]:>+9.2f}{r[8]:>5}")
    else:
        print("\n  NONE positive in both halves.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
