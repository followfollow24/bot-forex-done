#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_1930_distance_filter.py -- "strong trend day" measured by DISTANCE, the
way the operator means it, not by an EMA slope.

Their correction: do not use the EMA20 line, use how far price has
travelled. Two readings, and both are tested here.

READING ONE, already answered: the gate IS the filter. A 14-point gate
only trades once price has actually covered 14 points, so a wider gate is
a stronger "the move is real" test. Over 13 years of XAUUSD every gate is
negative and the loss GROWS as the gate widens -- 11p -2.02, 14p -2.95,
17p -4.25, 20p -7.49 a trade. Filtering harder made it worse, so this
reading is closed.

READING TWO, new: use the distance covered BEFORE the bell as the
day-strength filter, then apply the gate. That is causal -- it only reads
bars that have already closed -- and it is a distance, not a line:

    pre-move   |price at the bell - price N hours earlier| / ATR(H1)
               for N of 1, 3 and 6 hours
    strength   that ratio at or above 0.5, 1.0, 1.5
    relation   with / against / any -- whether the 19:30 break agrees
               with the direction the day had already been running

A note that matters more than the result: the day CANNOT be selected by
how far price travels AFTER 19:30. At the bell that is unknowable, and
selecting on it is the look-ahead that produces any answer you ask for.
Everything here is measured before the bell.

Both search directions and the day-shuffled surrogate, because each alone
has already produced a false positive in this project.

Usage:  python _1930_distance_filter.py [spread]
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
GATES_ATR = [0.25, 0.5, 0.75, 1.0]
BACKS = [1, 3, 6]                 # hours before the bell
STRENGTH = [0.5, 1.0, 1.5]        # pre-move, in ATR
RELS = ["any", "with", "against"]
HOLDS = [2, 4, 8, 12]
SL_ATR = 3.0
MIN_TRADES = 25
RNG = np.random.default_rng(20260911)


def shuffle_days(df):
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


def shuffle_within_days(df):
    """The RIGHT null for this hypothesis.

    Day-shuffling keeps every day intact, so a claim of the form "the hour
    before the bell predicts the break at the bell" survives it untouched --
    both halves of the claim live inside the same day. Permuting the bars
    WITHIN each day destroys the intraday ordering, and therefore any power
    the pre-bell hour has to predict what follows, while keeping each day's
    own total move, volatility and fat tails.
    """
    out = df.copy()
    px = out["close"].to_numpy(float)
    # np.diff(log(px), prepend=0.0) makes the FIRST element log(px[0]) --
    # about 7.42 here -- which cumsum then exponentiates into a price
    # 1675x too high. Two runs of this control reported hundreds to
    # thousands of points per trade before that was the reason. Build the
    # return series explicitly so the first element is a real zero.
    lr = np.zeros_like(px)
    lr[1:] = np.diff(np.log(px))
    day = out["timestamp"].dt.floor("D").to_numpy()
    _, first = np.unique(day, return_index=True)
    bounds = list(np.sort(first)) + [len(lr)]
    r = lr.copy()
    for a, b in zip(bounds[:-1], bounds[1:]):
        # The first bar of a day carries the move ACROSS the break. Shuffled
        # into the middle of the day it becomes a synthetic gap that clears
        # any gate and pays enormously -- the first version of this control
        # reported +2,870 points a trade, which is how it was caught. It
        # stays where it is; only the intraday bars are permuted.
        if b - a < 3:
            continue
        seg = r[a + 1:b].copy()
        RNG.shuffle(seg)
        r[a + 1:b] = seg
    r[~np.isfinite(r)] = 0.0
    newpx = float(px[0]) * np.exp(np.cumsum(r))
    scale = newpx / px
    for col in ("open", "high", "low", "close"):
        out[col] = out[col].to_numpy(float) * scale
    return out


def sweep(df, quiet=False):
    h1 = resample(df, "1h")
    hc = h1["close"].to_numpy(float)
    hh, hl = h1["high"].to_numpy(float), h1["low"].to_numpy(float)
    pc = np.concatenate(([hc[0]], hc[:-1]))
    tr = np.maximum(hh - hl, np.maximum(np.abs(hh - pc), np.abs(hl - pc)))
    atr = np.full(len(tr), np.nan)
    atr[13:] = np.convolve(tr, np.ones(14) / 14, mode="valid")
    hidx = {t: i for i, t in enumerate(h1["timestamp"].to_numpy())}

    ts = df["timestamp"]
    o, h, l, c = (df[x].to_numpy(float) for x in ("open", "high", "low", "close"))
    # the H1 bar that CLOSED at the bell -> strictly pre-bell information
    prev_h = (ts.dt.floor("h") - np.timedelta64(1, "h")).to_numpy()
    bells = np.flatnonzero(((ts.dt.hour == 12) & (ts.dt.minute == 30)).to_numpy())
    bells = bells[(bells > 8) & (bells < len(c) - max(HOLDS) - 3)]

    sess = []
    for i in bells:
        j = hidx.get(prev_h[i])
        if j is None or j < max(BACKS) + 14 or not np.isfinite(atr[j]) or atr[j] <= 0:
            continue
        pre = {n: (hc[j] - hc[j - n]) / atr[j] for n in BACKS}
        sess.append((i, float(atr[j]), pre))
    half = len(sess) // 2
    if not quiet:
        print(f"  {len(sess)} sessions, split at {ts.iloc[sess[half][0]]:%b %Y}")

    rows = []
    for ga in GATES_ATR:
        for back in BACKS:
            for st in STRENGTH:
                for rel in RELS:
                    trig = []
                    for n, (i, a, pre) in enumerate(sess):
                        pm = pre[back]
                        if rel != "any" and abs(pm) < st:
                            continue
                        g = ga * a
                        ref = o[i]
                        up = (h[i] - ref) >= g
                        dn = (ref - l[i]) >= g
                        if not (up or dn):
                            continue
                        both = up and dn
                        sides = (1, -1) if both else ((1,) if up else (-1,))
                        if rel != "any":
                            psign = 1 if pm > 0 else -1
                            sides = tuple(s for s in sides
                                          if (s == psign) == (rel == "with"))
                            if not sides:
                                continue
                            both = len(sides) > 1
                        trig.append((n, i, ref, a, g, sides, both))
                    if len(trig) < MIN_TRADES * 2:
                        continue
                    for hold in HOLDS:
                        e_p, l_p = [], []
                        for n, i, ref, a, g, sides, amb in trig:
                            sh = h[i:i + hold + 1].max()
                            sl_ = l[i:i + hold + 1].min()
                            ex = c[i + hold]
                            got = []
                            for s0 in sides:
                                entry = ref + s0 * g
                                stp = entry - s0 * SL_ATR * a
                                hit = (sl_ <= stp) if s0 > 0 else (sh >= stp)
                                px = stp if hit else ex
                                got.append((px - entry) * s0 - SPREAD)
                            v = min(got) if amb else got[0]
                            (e_p if n < half else l_p).append(v)
                        if len(e_p) < MIN_TRADES or len(l_p) < MIN_TRADES:
                            continue
                        rows.append((ga, back, st, rel, hold,
                                     float(np.mean(e_p)), len(e_p),
                                     float(np.mean(l_p)), len(l_p)))
    return rows


def lift(rows, fwd):
    si, ci = (5, 7) if fwd else (7, 5)
    passed = [r for r in rows if r[si] > 0]
    base = sum(1 for r in rows if r[ci] > 0) / len(rows)
    surv = [r for r in passed if r[ci] > 0]
    return len(passed), len(surv), (len(surv)/len(passed) if passed else 0.0), base


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = 10_000.0
    print("loading 13 years of XAUUSD M15 ...", flush=True)
    df, _ = loader.load("XAUUSD", 99.0, c0, csv_path=CSV, allow_synthetic=False)
    rows = sweep(df)
    if not rows:
        print("[ERROR] nothing traded enough"); return 2
    print("building BOTH surrogates ...", flush=True)
    frows = sweep(shuffle_days(df), quiet=True)          # keeps intraday shape
    wrows = sweep(shuffle_within_days(df), quiet=True)   # destroys it

    for nm, rs in (("day-shuffled", frows), ("within-day", wrows)):
        if rs:
            a = np.mean([abs(r[5]) for r in rs])
            print(f"  sanity: {nm} surrogate averages {a:.2f} pts/trade"
                  + ("" if a < 50 else "   <-- BROKEN, do not interpret"))
    print("=" * 78)
    print(f" 19:30 XAUUSD -- PRE-BELL DISTANCE AS THE FILTER"
          f"   {len(rows)} candidates")
    print("=" * 78)
    print(f"\n  {'direction':>12}{'series':>11}{'passed':>8}{'held':>7}"
          f"{'rate':>8}{'base':>8}{'lift':>8}")
    ver = {}
    for fwd, nm in ((True, "1st->2nd"), (False, "2nd->1st")):
        g = lift(rows, fwd)
        f = lift(frows, fwd) if frows else (0, 0, 0.0, 0.0)
        for tag, v in (("GOLD", g), ("shuffled", f)):
            print(f"  {nm if tag=='GOLD' else '':>12}{tag:>11}{v[0]:>8}"
                  f"{v[1]:>7}{v[2]*100:>7.0f}%{v[3]*100:>7.0f}%"
                  f"{(v[2]-v[3])*100:>+8.0f}")
        ver[nm] = (g[2]-g[3]) - (f[2]-f[3])
    print(f"\n  gold beyond the surrogate: "
          + "   ".join(f"{k} {v*100:+.0f}" for k, v in ver.items()))
    ok = min(ver.values()) > 0.05

    # The lift statistic asks whether SEARCHING helps. If a whole family is
    # positive, searching inside it adds nothing and the lift goes to zero
    # even when the family is real -- so the family averages are compared
    # against the surrogates directly, which is the test that fits the
    # claim being made.
    def fam_avg(rs, rel, back, st):
        v = [r for r in rs if r[3] == rel and r[1] == back and r[2] == st]
        if not v:
            return None
        return (float(np.mean([r[5] for r in v])),
                float(np.mean([r[7] for r in v])), len(v))

    print(f"\n  THE FAMILY TEST -- gold vs both surrogates, per trade")
    print(f"  (within-day shuffle is the one that fits this claim)")
    print(f"\n    {'filter':>22}{'GOLD 1st':>10}{'GOLD 2nd':>10}"
          f"{'dayshuf':>10}{'withinday':>11}   verdict")
    for rel in ("with", "against"):
        for back in BACKS:
            for st in STRENGTH:
                g = fam_avg(rows, rel, back, st)
                f = fam_avg(frows, rel, back, st)
                w = fam_avg(wrows, rel, back, st)
                if not g or not w:
                    continue
                gm = min(g[0], g[1])
                wm = max(w[0], w[1]) if w else 0.0
                fm = max(f[0], f[1]) if f else 0.0
                good = gm > 0 and gm > wm and gm > fm
                print(f"    {f'{rel} {back}h >={st}':>22}{g[0]:>+10.2f}"
                      f"{g[1]:>+10.2f}{fm:>+10.2f}{wm:>+11.2f}"
                      f"   {'CLEARS BOTH' if good else 'no'}")

    print(f"\n  BY FILTER  (average per trade over all gates and holds)")
    print(f"    {'rel':>9}{'back':>6}{'str>=':>7}{'cands':>7}"
          f"{'1st avg':>10}{'2nd avg':>10}{'both +':>8}")
    fam = defaultdict(list)
    for r in rows:
        fam[(r[3], r[1], r[2])].append(r)
    for k in sorted(fam):
        v = fam[k]
        bp = sum(1 for r in v if r[5] > 0 and r[7] > 0)
        print(f"    {k[0]:>9}{k[1]:>6}{k[2]:>7.1f}{len(v):>7}"
              f"{np.mean([r[5] for r in v]):>+10.2f}"
              f"{np.mean([r[7] for r in v]):>+10.2f}{bp:>8}")
    both = [r for r in rows if r[5] > 0 and r[7] > 0]
    print(f"\n  positive in BOTH halves: {len(both)} of {len(rows)}")
    if both:
        print(f"\n    {'gate':>7}{'rel':>9}{'back':>6}{'str':>6}{'hold':>6}"
              f"{'1st':>9}{'n':>6}{'2nd':>9}{'n':>6}")
        for r in sorted(both, key=lambda r: -min(r[5], r[7]))[:12]:
            print(f"    {r[0]:>6.2f}A{r[3]:>9}{r[1]:>6}{r[2]:>6.1f}{r[4]:>6}"
                  f"{r[5]:>+9.2f}{r[6]:>6}{r[7]:>+9.2f}{r[8]:>6}")
    print()
    if ok:
        print("  SURVIVES both directions AND the surrogate.")
    else:
        print("  Does not clear the surrogate. Same as every other reading of")
        print("  this window -- the lift is the search, not the market.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
