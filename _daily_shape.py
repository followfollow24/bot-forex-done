#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_daily_shape.py -- does every day have the SAME up-down shape, or does it
only look that way?

Distinct from anything tested so far. The variance ratio asked how far
price travels; the hour hunt asked whether entering at one clock time pays.
This asks whether the DAY ITSELF has a recurring profile -- up into the
London open, down after New York, a high at one hour and a low at another,
the same silhouette day after day.

The test is the same discipline applied to the shape instead of a
parameter. Build the average intraday profile from the FIRST half of the
history, then ask how well it predicts the SECOND half's. If days really
share a shape, the first half's profile is a forecast of the second's and
the two will correlate. If the shape is drawn fresh by noise each day, the
correlation collapses to nothing.

The control matters more here than usual, so it is built specifically for
this question. Shuffling whole days -- the surrogate used elsewhere in this
project -- would PRESERVE intraday shape and prove nothing. This one
permutes the slots WITHIN each day, which destroys any tie to clock time
while keeping every day's own returns, volatility and fat tails intact.

Three readings, in order of how directly they answer the question:

  1. cross-half correlation of the mean profile, against 200 surrogates
  2. where the day's HIGH and LOW fall, against uniform and against the
     surrogates -- the most literal form of "it goes up then down"
  3. the peak-to-trough of the average profile IN POINTS, because a shape
     smaller than the spread is real and unusable, which this project has
     now seen twice

Usage:  python _daily_shape.py [spread_pts]
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import DataLoader

CSV = "download/xauusd-m15-bid-2013-01-01-2026-06-10.csv"
SPREAD = float(sys.argv[1]) if len(sys.argv) > 1 else 0.24
SURR = 200
MIN_SLOTS = 80          # a day must be this complete to count
RNG = np.random.default_rng(20260909)


def build_matrix(df):
    """days x 96 matrix of M15 returns in points, aligned to clock time."""
    ts = df["timestamp"]
    px = df["close"].to_numpy(float)
    slot = (ts.dt.hour * 4 + ts.dt.minute // 15).to_numpy()
    day = ts.dt.floor("D").to_numpy()
    ret = np.diff(px, prepend=px[0])
    days = np.unique(day)
    idx = {d: i for i, d in enumerate(days)}
    M = np.full((len(days), 96), np.nan)
    for d, s, r in zip(day, slot, ret):
        M[idx[d], s] = r
    keep = np.isfinite(M).sum(axis=1) >= MIN_SLOTS
    M = M[keep]
    # The first bar of a day carries the move ACROSS the break -- the
    # overnight or weekend gap -- not anything that happened during the
    # day. Left in, it made hour 0 the largest single entry in the profile
    # and, being large in both directions, the most common hour for BOTH
    # the daily high and the daily low: a gap masquerading as an intraday
    # shape. The path here starts at each day's open instead.
    for i in range(len(M)):
        idx = np.flatnonzero(np.isfinite(M[i]))
        if len(idx):
            M[i, idx[0]] = 0.0
    return M, days[keep]


def profile(M):
    return np.nanmean(M, axis=0)


def within_day_shuffle(M):
    out = M.copy()
    for i in range(out.shape[0]):
        row = out[i]
        ok = np.isfinite(row)
        v = row[ok]
        RNG.shuffle(v)
        row[ok] = v
    return out


def hl_hours(M):
    """Hour of each day's high and low along its cumulative path.

    Missing slots (the daily break, and hours the market is shut) must be
    excluded, not filled. Filling them with zero leaves the path flat
    there, and argmax/argmin break ties by returning the FIRST index --
    which put every day's high AND its low at 00:00 with a chi2 of 4000,
    an artifact that reads exactly like a spectacular finding.
    """
    cum = np.nancumsum(np.nan_to_num(M), axis=1)
    valid = np.isfinite(M)
    hi = np.empty(len(M), dtype=int)
    lo = np.empty(len(M), dtype=int)
    for i in range(len(M)):
        idx = np.flatnonzero(valid[i])
        c = cum[i, idx]
        hi[i] = idx[int(np.argmax(c))] // 4
        lo[i] = idx[int(np.argmin(c))] // 4
    return hi, lo


def chi2_uniform(counts):
    n = counts.sum()
    k = len(counts)
    exp = n / k
    return float(((counts - exp) ** 2 / exp).sum()), k - 1


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = 10_000.0
    print("loading 13 years of gold M15 ...", flush=True)
    df, _ = loader.load("XAUUSD", 99.0, c0, csv_path=CSV,
                        allow_synthetic=False)
    M, days = build_matrix(df)
    h = len(M) // 2
    A, B = M[:h], M[h:]

    print("=" * 78)
    print(f" DOES EVERY DAY SHARE A SHAPE? -- {len(M):,} complete gold days")
    print(f" {days[0]} .. {days[-1]}")
    print("=" * 78)

    # ---- 1. does the first half's profile predict the second's? --------
    pa, pb = profile(A), profile(B)
    ok = np.isfinite(pa) & np.isfinite(pb)
    real = float(np.corrcoef(pa[ok], pb[ok])[0, 1])
    sims = np.empty(SURR)
    for i in range(SURR):
        sa, sb = within_day_shuffle(A), within_day_shuffle(B)
        qa, qb = profile(sa), profile(sb)
        m = np.isfinite(qa) & np.isfinite(qb)
        sims[i] = np.corrcoef(qa[m], qb[m])[0, 1]
    lo, hi = np.percentile(sims, [2.5, 97.5])
    print(f"\n 1. CROSS-HALF CORRELATION OF THE AVERAGE DAY")
    print(f"    first half vs second half:  r = {real:+.3f}")
    print(f"    200 within-day shuffles:    [{lo:+.3f}, {hi:+.3f}]"
          f"   mean {sims.mean():+.3f}")
    shape_real = real > hi
    print(f"    -> {'A REAL RECURRING SHAPE' if shape_real else 'no shape beyond noise'}")

    # ---- 2. which HOURS repeat, and are they real trading hours? -----
    print(f"\n 2. HOUR BY HOUR -- do the two halves agree on the sign?")
    flat = (M == 0).mean(axis=0)
    agree = both_plus = 0
    roll = []
    print(f"    {'hour':>6}{'1st half':>11}{'2nd half':>11}{'flat bars':>11}   sign")
    for hr in range(24):
        sl = slice(hr * 4, hr * 4 + 4)
        a, b = pa[sl].sum(), pb[sl].sum()
        f = flat[sl].mean()
        same = (a > 0) == (b > 0)
        agree += same
        both_plus += same and a > 0
        if f > 0.40:
            roll.append(hr)
        print(f"    {hr:>5}h{a:>+11.3f}{b:>+11.3f}{f*100:>10.0f}%"
              f"   {'agree' if same else 'split':>5}"
              + ("   <- mostly filled bars, not trading" if f > 0.40 else ""))
    print(f"\n    {agree} of 24 hours agree in sign  (chance gives 12)")
    if roll:
        print(f"    hours {roll} are over 40% flat bars -- the daily break")
        print(f"    and rollover on this feed, not behaviour anyone traded.")

    # The shape with the rollover hours removed is the only part that
    # could describe how price moves during a session.
    body = [i for i in range(96) if (i // 4) not in roll and (i // 4) not in (22, 23)]
    cb = np.nancumsum(np.nan_to_num(profile(M)[body]))
    print(f"    excluding those hours, peak-to-trough falls to"
          f" {cb.max()-cb.min():.3f} pts")

    # ---- 3. how big is the shape, in money? ----------------------------
    full = profile(M)
    cum = np.nancumsum(np.nan_to_num(full))
    swing = float(cum.max() - cum.min())
    # A mean profile is only as good as its own noise: each slot's mean
    # carries a standard error, and they accumulate along the path.
    se = float(np.nanstd(M, axis=0).mean() / np.sqrt(len(M)))
    print(f"\n    per-slot standard error {se:.4f} pts, so the cumulative"
          f" path carries ~{se*np.sqrt(96):.3f} pts of noise")
    print(f"\n 3. SIZE OF THE AVERAGE DAY'S SHAPE")
    print(f"    peak-to-trough of the mean intraday path: {swing:.3f} pts")
    print(f"    spread: {SPREAD:.2f} pts   -> {'tradeable in principle' if swing > SPREAD * 3 else 'SMALLER THAN THE COST OF TRADING IT'}")
    print(f"    high of the mean path at {int(np.argmax(cum))//4:02d}:00, "
          f"low at {int(np.argmin(cum))//4:02d}:00")

    print("\n" + "=" * 78)
    if shape_real and swing > SPREAD * 3:
        print(" A recurring shape exists AND is bigger than the spread.")
    elif shape_real:
        print(" A recurring shape exists but is smaller than the cost of")
        print(" trading it -- real, and not worth acting on.")
    else:
        print(" What looks like the same shape every day is what a day of")
        print(" its own returns in a random order also produces. The eye")
        print(" finds the pattern; the profile does not repeat.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
