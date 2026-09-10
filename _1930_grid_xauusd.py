#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_1930_grid_xauusd.py -- sweep entry and exit timing at 19:30 on XAUUSD,
13 years, with the protocol that has survived everything else here.

The operator wants the best entry and exit found. The honest way to look
for it is to be able to come back and say nothing was found, so:

  * search on the FIRST half only, score on the SECOND, and then the same
    thing BACKWARDS. One direction alone is what the exit-geometry sweep
    produced before gold's own trend was subtracted from it.
  * count how many candidates keep their SIGN across both halves against
    what chance gives, since 384 candidates produce agreement on their own.
  * report FADE beside FOLLOW. Following loses at every gate over these 13
    years and loses more as the gate widens, which makes fading a real
    hypothesis rather than a courtesy -- and a sweep that only looks in the
    direction already believed cannot say the belief was wrong.

    gate     how far price must travel from the 19:30 open
    watch    1 or 2 M15 bars in which that may happen
    hold     1 to 24 bars afterwards -- 15 minutes out to six hours,
             because on 2026-09-10 price was still falling after the
             30-minute exit and that is worth testing rather than assuming
    stop     1.5 or 3.0 x ATR(H1)

M15 approximation as before: the fill is the gate level, and a session
where BOTH sides cleared inside one bar is charged the losing side.

Usage:  python _1930_grid_xauusd.py [spread]
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

CSV = "download/xauusd-m15-bid-2013-01-01-2026-06-10.csv"
SPREAD = float(sys.argv[1]) if len(sys.argv) > 1 else 0.24
GATES = [5, 8, 11, 14, 17, 20, 25, 30]
WATCH = [1, 2]
HOLDS = [1, 2, 4, 8, 12, 24]
DIRS = [("follow", +1), ("fade", -1)]
STOPS = [1.5, 3.0]
MIN_TRADES = 25


def atr_h1_map(df):
    h1 = resample(df, "1h")
    c = h1["close"].to_numpy(float)
    hi, lo = h1["high"].to_numpy(float), h1["low"].to_numpy(float)
    pc = np.concatenate(([c[0]], c[:-1]))
    tr = np.maximum(hi - lo, np.maximum(np.abs(hi - pc), np.abs(lo - pc)))
    a = np.full(len(tr), np.nan)
    if len(tr) > 14:
        a[13:] = np.convolve(tr, np.ones(14) / 14, mode="valid")
    return dict(zip(h1["timestamp"].to_numpy(), a))


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = 10_000.0
    print("loading 13 years of XAUUSD M15 ...", flush=True)
    df, _ = loader.load("XAUUSD", 99.0, c0, csv_path=CSV,
                        allow_synthetic=False)
    ts = df["timestamp"]
    o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    amap = atr_h1_map(df)
    hkey = ts.dt.floor("h").to_numpy()
    bells = np.flatnonzero(((ts.dt.hour == 12) & (ts.dt.minute == 30)).to_numpy())
    bells = bells[(bells > 4) & (bells < len(c) - max(HOLDS) - 3)]
    half = len(bells) // 2
    print(f"  {len(bells)} sessions, split at {ts.iloc[bells[half]]:%b %Y}")

    rows = []
    for g in GATES:
        for wb in WATCH:
            # find the first bar in the watch window that clears the gate
            trig = []
            for k, i in enumerate(bells):
                atr = amap.get(hkey[i], np.nan)
                if not np.isfinite(atr) or atr <= 0:
                    continue
                ref = o[i]
                j = None
                for b in range(wb):
                    up = (h[i + b] - ref) >= g
                    dn = (ref - l[i + b]) >= g
                    if up or dn:
                        j = i + b
                        both = up and dn
                        sides = (1, -1) if both else ((1,) if up else (-1,))
                        break
                if j is None:
                    continue
                trig.append((k, j, ref, atr, sides, len(sides) > 1))
            if len(trig) < MIN_TRADES * 2:
                continue
            for hold in HOLDS:
                for dname, dmul in DIRS:
                    for sa in STOPS:
                        e_p, l_p = [], []
                        for k, j, ref, atr, sides, amb in trig:
                            seg_h = h[j:j + hold + 1].max()
                            seg_l = l[j:j + hold + 1].min()
                            ex = c[j + hold]
                            got = []
                            for s0 in sides:
                                side = s0 * dmul
                                entry = ref + s0 * g
                                sl = entry - side * sa * atr
                                stopped = ((seg_l <= sl) if side > 0
                                           else (seg_h >= sl))
                                px = sl if stopped else ex
                                got.append((px - entry) * side - SPREAD)
                            v = min(got) if amb else got[0]
                            (e_p if bells[k] < bells[half] else l_p).append(v)
                        if len(e_p) < MIN_TRADES or len(l_p) < MIN_TRADES:
                            continue
                        rows.append((g, wb, hold, dname, sa,
                                     float(np.mean(e_p)), len(e_p),
                                     float(np.mean(l_p)), len(l_p)))

    if not rows:
        print("[ERROR] nothing traded enough"); return 2
    print("=" * 76)
    print(f" 19:30 XAUUSD, 13 YEARS -- {len(rows)} candidates"
          f"   spread {SPREAD:.2f}")
    print("=" * 76)

    for fwd, name in ((True, "1st -> 2nd"), (False, "2nd -> 1st")):
        si, ci = (5, 7) if fwd else (7, 5)
        passed = [r for r in rows if r[si] > 0]
        base = sum(1 for r in rows if r[ci] > 0) / len(rows)
        surv = [r for r in passed if r[ci] > 0]
        lift = len(surv) / len(passed) if passed else 0.0
        print(f"\n  searching {name}:  {len(passed)} passed,"
              f" {len(surv)} held  ({lift*100:.0f}% vs base {base*100:.0f}%"
              f"  lift {(lift-base)*100:+.0f})")

    agree = sum(1 for r in rows if (r[5] > 0) == (r[7] > 0))
    both = [r for r in rows if r[5] > 0 and r[7] > 0]
    print(f"\n  {agree} of {len(rows)} keep their sign in both halves"
          f"   (chance gives {len(rows)//2})")
    print(f"  positive in BOTH halves: {len(both)}")

    if both:
        print(f"\n  {'gate':>6}{'watch':>7}{'hold':>6}{'dir':>8}{'stop':>6}"
              f"{'1st /trade':>12}{'n':>5}{'2nd /trade':>12}{'n':>5}")
        fam = defaultdict(list)
        for r in both:
            fam[(r[0], r[3])].append(r)
        for r in sorted(both, key=lambda r: -min(r[5], r[7]))[:15]:
            print(f"  {r[0]:>5}p{r[1]:>7}{r[2]:>6}{r[3]:>8}{r[4]:>6.1f}"
                  f"{r[5]:>+12.2f}{r[6]:>5}{r[7]:>+12.2f}{r[8]:>5}")
        print(f"\n  families (gate + direction) with 3+ survivors:")
        for k, v in sorted(fam.items(), key=lambda kv: -len(kv[1])):
            if len(v) >= 3:
                print(f"    gate {k[0]}p {k[1]}: {len(v)} survivors,"
                      f" 1st {np.mean([r[5] for r in v]):+.2f}"
                      f"  2nd {np.mean([r[7] for r in v]):+.2f}")
    else:
        print("\n  NONE is positive in both halves.")
    print(f"\n  A survivor needs BOTH search directions to lift, its family to")
    print(f"  hold together, and the agreement count to beat chance. Anything")
    print(f"  less is one of {len(rows)} candidates doing what {len(rows)} do.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
