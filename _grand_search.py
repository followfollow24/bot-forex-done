#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_grand_search.py -- search the whole space, and be honest about having
searched it.

The operator asked for an exhaustive hunt for a tradeable angle. Searching
hard is not the problem; reporting the winner is. Test three thousand
things and roughly a hundred and fifty will clear p<0.05 with nothing
behind them, and the best of those will look magnificent. Every retracted
number in this project was produced exactly that way.

So the protocol is fixed here, before any result is seen, and the decisive
figure is not the best candidate.

  1. The grid is declared up front and swept completely -- timeframe,
     how far price moved (in ATR, so it means the same thing on M15 and
     H4), over how many bars, held how long, following or fading, and
     under volatility and session filters.
  2. Every candidate is scored on the FIRST half only. That is the search.
  3. Candidates that made money there are carried, untouched, to the
     SECOND half, which the search never saw.
  4. THE ANSWER IS A RATE, NOT A WINNER: does passing the first half
     raise the chance of passing the second? If P(pass 2nd | passed 1st)
     equals P(pass 2nd) across the whole grid, then selection learned
     nothing and every survivor is a coincidence -- however good it looks.
  5. A survivor is only interesting if its NEIGHBOURS survive too. One
     cell winning while the cells either side of it lose is noise wearing
     a result's clothes.

Costs are charged at the live spread and again at double it, because a
candidate that only works at zero slippage is not a candidate.

Usage:  python _grand_search.py [spread_pts]
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
TFS = ["15min", "1h", "4h"]
LOOKS = [2, 4, 8, 16, 32]           # bars the move happened over
THRESH = [0.5, 1.0, 1.5, 2.0, 3.0]  # size of that move, in ATR
HOLDS = [1, 2, 4, 8, 16, 32]        # bars held
DIRS = [("follow", +1), ("fade", -1)]
VOLS = ["any", "calm", "wild"]      # ATR below / above its median
SESS = ["any", "asia", "london", "ny"]
MIN_TRADES = 30


def atr_arr(h, l, c, n=14):
    pc = np.concatenate(([c[0]], c[:-1]))
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    out = np.full(len(tr), np.nan)
    if len(tr) > n:
        k = np.ones(n) / n
        out[n - 1:] = np.convolve(tr, k, mode="valid")
    return out


def session_of(hours):
    s = np.full(len(hours), "ny", dtype=object)
    s[(hours >= 0) & (hours < 7)] = "asia"
    s[(hours >= 7) & (hours < 13)] = "london"
    return s


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = 10_000.0
    print("loading 13 years of gold M15 ...", flush=True)
    df, _ = loader.load("XAUUSD", 99.0, c0, csv_path=CSV,
                        allow_synthetic=False)

    grid = {}
    for tf in TFS:
        d = resample(df, tf) if tf != "15min" else df
        c = d["close"].to_numpy(float)
        h = d["high"].to_numpy(float)
        l = d["low"].to_numpy(float)
        hours = d["timestamp"].dt.hour.to_numpy()
        grid[tf] = (c, atr_arr(h, l, c), session_of(hours))
        print(f"  {tf:>6}  {len(c):,} bars")

    n_cand = (len(TFS) * len(LOOKS) * len(THRESH) * len(HOLDS)
              * len(DIRS) * len(VOLS) * len(SESS))
    print("=" * 78)
    print(f" GRAND SEARCH -- {n_cand:,} candidates declared before looking")
    print(f" search on the FIRST half, confirm on the SECOND, at two costs")
    print("=" * 78)

    for cost in (SPREAD, SPREAD * 2):
        rows = []
        for tf in TFS:
            c, atr, sess = grid[tf]
            n = len(c)
            half = n // 2
            med = np.nanmedian(atr)
            for K in LOOKS:
                mv = c[K:] - c[:-K]
                a = atr[K:]
                sv = sess[K:]
                base = np.arange(K, n)
                with np.errstate(invalid="ignore", divide="ignore"):
                    norm = mv / a
                for H in HOLDS:
                    m = len(base) - H
                    if m < 400:
                        continue
                    fwd = c[base[:m] + H] - c[base[:m]]
                    nm, av, svv, bi = norm[:m], a[:m], sv[:m], base[:m]
                    ok = np.isfinite(nm)
                    for X in THRESH:
                        big = ok & (np.abs(nm) >= X)
                        if big.sum() < MIN_TRADES * 4:
                            continue
                        for dname, dmul in DIRS:
                            side = np.sign(nm) * dmul
                            pnl = fwd * side - cost
                            for v in VOLS:
                                vm = (big if v == "any" else
                                      big & (av <= med) if v == "calm" else
                                      big & (av > med))
                                for s in SESS:
                                    sm = vm if s == "any" else vm & (svv == s)
                                    if sm.sum() < MIN_TRADES * 2:
                                        continue
                                    e = sm & (bi < half)
                                    lt = sm & (bi >= half)
                                    if e.sum() < MIN_TRADES or lt.sum() < MIN_TRADES:
                                        continue
                                    rows.append((tf, K, X, H, dname, v, s,
                                                 float(pnl[e].mean()),
                                                 int(e.sum()),
                                                 float(pnl[lt].mean()),
                                                 int(lt.sum())))
        if not rows:
            print(f"\n COST {cost:.2f}: nothing traded enough to score")
            continue

        tested = len(rows)
        passed = [r for r in rows if r[7] > 0]
        late_all = sum(1 for r in rows if r[9] > 0) / tested
        survived = [r for r in passed if r[9] > 0]
        rate = len(survived) / len(passed) if passed else 0.0
        exp = late_all * len(passed)
        se = np.sqrt(len(passed) * late_all * (1 - late_all)) if passed else 0
        z = (len(survived) - exp) / se if se > 0 else float("nan")

        print(f"\n{'='*78}")
        print(f" COST {cost:.2f} pts per trade")
        print(f"{'='*78}")
        print(f"  scored             {tested:>6} candidates traded enough")
        print(f"  passed 1st half    {len(passed):>6}")
        print(f"  of those, 2nd half {len(survived):>6}"
              f"   ({rate*100:.0f}%)")
        print(f"  base rate for ANY  {late_all*100:>5.0f}%"
              f"   -- expected {exp:.0f} survivors by chance alone")
        print(f"  z of the excess    {z:>+6.2f}")
        if z < 2:
            print("\n  Passing the first half does NOT predict passing the")
            print("  second. Selection learned nothing, so every survivor")
            print("  below is a coincidence no matter how good it looks.")
        else:
            print("\n  Selection carries information. The survivors are worth")
            print("  reading -- but only where NEIGHBOURS survive too.")

        # neighbourhood coherence: how many survivors sit in a family whose
        # sibling holds also survived
        fam = defaultdict(list)
        for r in survived:
            fam[(r[0], r[1], r[2], r[4], r[5], r[6])].append(r)
        blocks = {k: v for k, v in fam.items() if len(v) >= 3}
        print(f"\n  survivors in a BLOCK (3+ hold lengths of the same setup):"
              f" {sum(len(v) for v in blocks.values())} in {len(blocks)} families")
        if blocks:
            print(f"\n    {'timeframe':>10}{'moved':>7}{'in':>4}{'dir':>8}"
                  f"{'vol':>6}{'session':>9}{'holds':>7}"
                  f"{'1st /trade':>12}{'2nd /trade':>12}")
            for k, v in sorted(blocks.items(),
                               key=lambda kv: -min(r[9] for r in kv[1]))[:12]:
                tf, K, X, dn, vv, ss = k
                hs = sorted(r[3] for r in v)
                e = np.mean([r[7] for r in v]); l2 = np.mean([r[9] for r in v])
                print(f"    {tf:>10}{X:>6.1f}A{K:>4}{dn:>8}{vv:>6}{ss:>9}"
                      f"{str(hs):>7}{e:>+12.2f}{l2:>+12.2f}")
    print("\n  Slippage beyond the doubled spread is not modelled, and every")
    print("  number above is gross of swap. Both push results down.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
