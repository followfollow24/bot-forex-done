#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_exit_geometry.py -- the one dimension every earlier search left out.

The 9,856-candidate sweep found nothing, but every candidate in it left
after a fixed number of bars: no stop, no target. That is the whole space
of TIMED exits, and it says nothing about geometry. The live H1 bot that
survives 6 of 6 windows uses a 2.5-ATR stop against a 15-ATR target -- it
loses 70% of its trades and still profits, which no timed exit can
reproduce. So the geometry is the untested part, and this tests it, over
the whole day with no clock anchor anywhere.

The protocol is the one that has survived: entries pinned to a grid so no
two trades of a candidate overlap, the search done on the FIRST half only,
confirmation on the SECOND, and the identical search re-run on a surrogate
built by shuffling gold's own days -- same drift, same volatility
clustering, same intraday shape, no memory across days. The verdict is
gold's lift over that surrogate and nothing else.

Drift is not subtracted here as it was for timed exits, because with a
stop and a target the holding time is not fixed and there is no clean
unconditional benchmark to subtract. The surrogate carries the same drift
instead, which handles it by comparison rather than by arithmetic.

When both barriers fall inside one bar the stop is assumed to hit first.
That is the pessimistic reading and it is the honest one: the alternative
quietly credits every ambiguous bar to the target.

Usage:  python _exit_geometry.py [spread_pts]
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
LOOKS = [4, 8, 16, 32]
THRESH = [0.0, 0.5, 1.0, 2.0, 3.0]     # 0.0 = no entry condition at all
SLS = [0.5, 1.0, 2.0, 3.0]
TPS = [1.0, 2.0, 5.0, 10.0, 15.0]
DIRS = [("follow", +1), ("fade", -1)]
VOLS = ["any", "calm", "wild"]
MAXHOLD = 32
MIN_TRADES = 30
RNG = np.random.default_rng(20260909)


def atr_arr(h, l, c, n=14):
    pc = np.concatenate(([c[0]], c[:-1]))
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    out = np.full(len(tr), np.nan)
    if len(tr) > n:
        out[n - 1:] = np.convolve(tr, np.ones(n) / n, mode="valid")
    return out


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


def build(df):
    g = {}
    for tf in TFS:
        d = resample(df, tf) if tf != "15min" else df
        c = d["close"].to_numpy(float)
        h = d["high"].to_numpy(float)
        l = d["low"].to_numpy(float)
        n = len(c)
        # entries on a grid MAXHOLD apart -> no candidate's trades overlap
        ent = np.arange(MAXHOLD * 2, n - MAXHOLD - 1, MAXHOLD)
        steps = np.arange(1, MAXHOLD + 1)
        idx = ent[:, None] + steps[None, :]
        g[tf] = dict(c=c, atr=atr_arr(h, l, c), ent=ent,
                     runmax=np.maximum.accumulate(h[idx], axis=1),
                     runmin=np.minimum.accumulate(l[idx], axis=1),
                     endc=c[ent + MAXHOLD])
    return g


def first_touch(run, level, greater):
    """Index of the first step touching `level`, or MAXHOLD if never."""
    hit = (run >= level[:, None]) if greater else (run <= level[:, None])
    any_hit = hit.any(axis=1)
    return np.where(any_hit, hit.argmax(axis=1), MAXHOLD), any_hit


def sweep(g, cost):
    rows = []
    for tf in TFS:
        G = g[tf]
        c, atr, ent = G["c"], G["atr"], G["ent"]
        p0, a0 = c[ent], atr[ent]
        med = np.nanmedian(atr)
        base_ok = np.isfinite(a0) & (a0 > 0)
        half = len(c) // 2
        early = ent < half
        for K in LOOKS:
            mv = c[ent] - c[ent - K]
            with np.errstate(invalid="ignore", divide="ignore"):
                nm = mv / a0
            for X in THRESH:
                cond = base_ok & (np.isfinite(nm)) & (
                    np.ones_like(nm, dtype=bool) if X == 0.0
                    else np.abs(nm) >= X)
                if cond.sum() < MIN_TRADES * 2:
                    continue
                for dname, dmul in DIRS:
                    side = (np.sign(nm) * dmul) if X > 0.0 else np.sign(nm) * dmul
                    live = cond & (side != 0)
                    if live.sum() < MIN_TRADES * 2:
                        continue
                    for sl in SLS:
                        for tp in TPS:
                            up = np.where(side > 0, p0 + tp * a0, p0 + sl * a0)
                            dn = np.where(side > 0, p0 - sl * a0, p0 - tp * a0)
                            t_up, h_up = first_touch(G["runmax"], up, True)
                            t_dn, h_dn = first_touch(G["runmin"], dn, False)
                            # ties inside one bar go to the stop
                            long_win = (t_up < t_dn) & h_up
                            short_win = (t_dn < t_up) & h_dn
                            win = np.where(side > 0, long_win, short_win)
                            lose = np.where(side > 0, h_dn & ~long_win,
                                            h_up & ~short_win)
                            timed = (G["endc"] - p0) * side
                            pnl = np.where(win, tp * a0,
                                           np.where(lose, -sl * a0, timed)) - cost
                            for v in VOLS:
                                m = (live if v == "any" else
                                     live & (a0 <= med) if v == "calm" else
                                     live & (a0 > med))
                                e, l2 = m & early, m & ~early
                                if e.sum() < MIN_TRADES or l2.sum() < MIN_TRADES:
                                    continue
                                rows.append((tf, K, X, dname, sl, tp, v,
                                             float(pnl[e].mean()), int(e.sum()),
                                             float(pnl[l2].mean()), int(l2.sum())))
    return rows


def report(rows, tag, fwd=True):
    """fwd=True searches the first half and confirms on the second;
    fwd=False does it the other way round. Both must hold. Gold's second
    half contains the 1675->4400 run, so a one-way test rewards anything
    that is net long -- running it backwards is what separates persistence
    from a single favourable regime."""
    if not rows:
        print(f"  {tag:>10}  nothing traded enough")
        return None
    si, ci = (7, 9) if fwd else (9, 7)
    tested = len(rows)
    passed = [r for r in rows if r[si] > 0]
    base = sum(1 for r in rows if r[ci] > 0) / tested
    surv = [r for r in passed if r[ci] > 0]
    lift = len(surv) / len(passed) if passed else 0.0
    print(f"  {tag:>10}{tested:>8}{len(passed):>9}{len(surv):>9}"
          f"{lift*100:>8.0f}%{base*100:>8.0f}%{(lift-base)*100:>+8.0f}")
    return dict(passed=passed, surv=surv, lift=lift, base=base)


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = 10_000.0
    print("loading 13 years of gold M15 ...", flush=True)
    df, _ = loader.load("XAUUSD", 99.0, c0, csv_path=CSV,
                        allow_synthetic=False)
    print("building the day-shuffled surrogate ...", flush=True)
    real, fake = build(df), build(shuffle_days(df))
    n = (len(TFS) * len(LOOKS) * len(THRESH) * len(DIRS) * len(SLS)
         * len(TPS) * len(VOLS))
    print("=" * 76)
    print(f" EXIT GEOMETRY, WHOLE DAY, NO CLOCK -- {n:,} candidates declared")
    print(f" stop 0.5-3 ATR x target 1-15 ATR, {MAXHOLD}-bar cap, ties to the stop")
    print("=" * 76)

    for cost in (SPREAD, SPREAD * 2):
        rr, ff = sweep(real, cost), sweep(fake, cost)
        gains = {}
        for fwd, name in ((True, "1st -> 2nd"), (False, "2nd -> 1st")):
            print(f"\n COST {cost:.2f} pts   searching {name}")
            print(f"  {'series':>10}{'scored':>8}{'passed':>9}{'held':>9}"
                  f"{'rate':>8}{'base':>8}{'lift':>8}")
            R = report(rr, "GOLD", fwd)
            F = report(ff, "shuffled", fwd)
            if R and F:
                gains[name] = (R["lift"] - R["base"]) - (F["lift"] - F["base"])
                print(f"  gold beyond the shuffle: {gains[name]*100:+.0f}")
        if len(gains) < 2:
            continue
        print(f"\n  BOTH DIRECTIONS: "
              + "  ".join(f"{k} {v*100:+.0f}" for k, v in gains.items()))
        R = report(rr, "GOLD", True)
        gain = min(gains.values())
        if gain <= 0.05:
            print("\n  One direction only. Gold's second half holds the")
            print("  1675->4400 run, so searching into it rewards anything")
            print("  net long; the reverse test is the one that counts, and")
            print("  it does not hold. This is a regime, not an edge.")
            continue
        fam = defaultdict(list)
        for r in R["surv"]:
            fam[(r[0], r[1], r[2], r[3], r[6])].append(r)
        blocks = {k: v for k, v in fam.items() if len(v) >= 3}
        print(f"  Gold keeps an excess. Survivors in blocks of 3+ geometries:"
              f" {sum(len(v) for v in blocks.values())} in {len(blocks)} families")
        print(f"\n    {'tf':>7}{'in':>4}{'moved':>7}{'dir':>8}{'vol':>6}"
              f"{'SL/TP pairs':>28}{'1st':>8}{'2nd':>8}")
        for k, v in sorted(blocks.items(),
                           key=lambda kv: -min(r[9] for r in kv[1]))[:12]:
            tf, K, X, dn, vv = k
            pairs = sorted({(r[4], r[5]) for r in v})[:4]
            print(f"    {tf:>7}{K:>4}{X:>6.1f}A{dn:>8}{vv:>6}"
                  f"{str(pairs):>28}"
                  f"{np.mean([r[7] for r in v]):>+8.2f}"
                  f"{np.mean([r[9] for r in v]):>+8.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
