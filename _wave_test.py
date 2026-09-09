#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_wave_test.py -- the operator sees gold swinging in waves on M15 and wants
to know whether it can be traded.

The waves are real. They are also the first thing anyone sees in ANY price
series, including one generated from pure noise, because a random walk
drawn on a chart looks exactly like this -- runs up, runs down, swings
that seem to repeat. So "I can see waves" cannot by itself distinguish a
tradeable oscillation from a random walk, and the eye has no way to tell
them apart. A measurement does.

PART 1 asks the question directly, with nothing to choose. The variance
ratio compares how far price actually travels over q bars against how far
a random walk with the same bar-to-bar volatility would travel. VR below 1
means the moves partly cancel -- real oscillation, mean reversion. VR above
1 means they compound -- trending. VR at 1 is a random walk, waves and all.

Two nulls are reported rather than one. The Lo-MacKinlay z is the textbook
statistic; beside it sits an empirical band from 200 SHUFFLES of gold's own
returns, which keeps every real property of the distribution -- fat tails,
volatility clustering, the lot -- and destroys only the ordering. If the
real series sits inside that band, the waves carry no information the
shuffle does not also have.

PART 2 is the practical version: after price has moved X points in K bars,
does fading it pay? Every combination is printed for both halves of the
history, and what matters is not the best cell but whether a BLOCK agrees
in both halves and clears the spread. With eighty cells a few will agree by
chance, so the count is shown against what chance gives.

Usage:  python _wave_test.py [symbol_csv] [spread_pts]
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import DataLoader

CSV = sys.argv[1] if len(sys.argv) > 1 else \
    "download/xauusd-m15-bid-2013-01-01-2026-06-10.csv"
SPREAD = float(sys.argv[2]) if len(sys.argv) > 2 else 0.24
LAGS = [2, 4, 8, 16, 32, 64, 128]
SHUFFLES = 200
MOVES = [10, 20, 30, 50, 80]          # points travelled
LOOKS = [4, 8, 16, 32]                # bars it took (M15: 1h,2h,4h,8h)
HOLDS = [4, 8, 16, 32]                # bars held after
RNG = np.random.default_rng(20260909)


def variance_ratio(r, q):
    """VR(q) and the heteroskedasticity-robust z of Lo & MacKinlay."""
    n = len(r)
    mu = r.mean()
    dev = r - mu
    var1 = (dev ** 2).sum() / (n - 1)
    if var1 <= 0 or n <= q:
        return float("nan"), float("nan")
    csum = np.cumsum(np.insert(r, 0, 0.0))
    qsum = csum[q:] - csum[:-q]                   # overlapping q-bar returns
    m = q * (n - q + 1) * (1.0 - q / n)
    varq = ((qsum - q * mu) ** 2).sum() / m
    vr = varq / var1
    d2 = (dev ** 2)
    denom = d2.sum() ** 2
    theta = 0.0
    for j in range(1, q):
        num = (d2[j:] * d2[:-j]).sum()
        # Lo & MacKinlay's delta_j carries an n in its numerator AND the
        # statistic carries sqrt(n) in its own. Dropping either one gives a
        # z off by a factor of n or sqrt(n) -- once printing -0.00 for
        # everything, once printing -2000. Both read as answers.
        theta += (2.0 * (q - j) / q) ** 2 * (n * num / denom)
    z = np.sqrt(n) * (vr - 1.0) / np.sqrt(theta) if theta > 0 else float("nan")
    return vr, z


def part1(r, label):
    print(f"\n  {label}   {len(r):,} bars")
    print(f"    {'q bars':>8}{'VR':>8}{'z':>8}   {'shuffled VR band (200 draws)':>34}"
          f"   verdict")
    for q in LAGS:
        vr, z = variance_ratio(r, q)
        sims = np.empty(SHUFFLES)
        for i in range(SHUFFLES):
            sims[i] = variance_ratio(RNG.permutation(r), q)[0]
        lo, hi = np.percentile(sims, [2.5, 97.5])
        inside = lo <= vr <= hi
        verdict = ("indistinguishable from a shuffle" if inside else
                   "MEAN-REVERTING beyond chance" if vr < lo else
                   "TRENDING beyond chance")
        print(f"    {q:>8}{vr:>8.3f}{z:>8.2f}   [{lo:.3f}, {hi:.3f}]"
              f"{'':>14}   {verdict}")


def part2(px, half_idx, pt):
    """After moving X points in K bars, fade it and hold H bars."""
    print(f"\n{'='*78}")
    print(f" PART 2 -- FADING THE SWING   (cost {pt:.2f} pts per trade)")
    print(f"{'='*78}")
    print(f"\n    {'moved':>7}{'in':>5}{'hold':>6}"
          f"{'EARLY /trade':>14}{'n':>7}{'LATE /trade':>13}{'n':>7}   sign")
    agree = tot = 0
    good = []
    for X in MOVES:
        for K in LOOKS:
            move = px[K:] - px[:-K]
            for H in HOLDS:
                nn = len(move) - H
                if nn < 200:
                    continue
                mv = move[:nn]
                fwd = px[K + H:K + H + nn] - px[K:K + nn]
                hit = np.abs(mv) >= X
                if hit.sum() < 40:
                    continue
                side = -np.sign(mv[hit])           # fade
                pnl = fwd[hit] * side - pt
                cut = half_idx - K
                early = pnl[:max(0, (np.where(hit)[0] < cut).sum())]
                late = pnl[len(early):]
                if len(early) < 20 or len(late) < 20:
                    continue
                tot += 1
                a, b = early.mean(), late.mean()
                same = (a > 0) == (b > 0)
                agree += same
                mark = ("BOTH +" if same and a > 0 else
                        "BOTH -" if same else "split")
                if same and a > 0:
                    good.append((X, K, H, a, b, len(early), len(late)))
                print(f"    {X:>6}p{K:>5}{H:>6}{a:>+14.2f}{len(early):>7}"
                      f"{b:>+13.2f}{len(late):>7}   {mark}")
    print(f"\n    {agree} of {tot} cells agree in sign  "
          f"(chance gives ~{tot/2:.0f})")
    if good:
        print(f"    {len(good)} are positive in BOTH halves after costs:")
        for X, K, H, a, b, na, nb in sorted(good, key=lambda g: -min(g[3], g[4])):
            print(f"      moved {X}p in {K} bars, held {H}: "
                  f"{a:+.2f} / {b:+.2f} pts per trade  (n {na}/{nb})")
    else:
        print("    NONE is positive in both halves after costs.")


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = 10_000.0
    print(f"loading {CSV} ...", flush=True)
    df, _ = loader.load("XAUUSD", 99.0, c0, csv_path=CSV,
                        allow_synthetic=False)
    px = df["close"].to_numpy(dtype=float)
    ts = df.index.to_numpy() if df.index.name else None
    # Weekend gaps make one enormous "return" every Sunday; those are not
    # part of the wave anyone is looking at, so drop the bar that spans them.
    lr = np.diff(np.log(px))
    keep = np.abs(lr) < np.nanpercentile(np.abs(lr), 99.9)
    r = lr[keep]
    half = len(r) // 2

    print("=" * 78)
    print(f" ARE THE WAVES REAL, OR WHAT NOISE LOOKS LIKE? -- {len(px):,} M15 bars")
    print(" VR < 1 = oscillates (mean-reverting) | VR = 1 = random walk |"
          " VR > 1 = trends")
    print("=" * 78)
    part1(r[:half], "FIRST HALF")
    part1(r[half:], "SECOND HALF")
    print("\n  A wave you can trade has to sit OUTSIDE the shuffled band, and")
    print("  in the same direction in both halves. Inside the band means the")
    print("  swing you see is the swing a coin-flip series also produces.")

    part2(px, len(px) // 2, SPREAD)
    print("\n  Slippage is not modelled and the spread is a constant; both")
    print("  push the real result below what is printed here.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
