#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_squeeze_break.py -- the pattern the operator circled: price coils into a
tight range, then breaks out, then coils again.

Genuinely new here. Every earlier sweep keyed on price having ALREADY
MOVED -- so many ATR over so many bars. The circles are the opposite
condition: bars where price went nowhere, unusually quiet against its own
volatility, with the trade taken when it finally leaves that range. A
compression filter has never been in any grid in this project.

    squeeze   the high-low range over the last N bars, divided by ATR,
              below a threshold -- price covering less ground than its own
              volatility says it should
    trigger   the close leaving that range, up or down
    exit      a stop and a target in ATR, capped at 32 bars

Same protocol as everything else, and it now includes the check that
killed the last candidate: the search runs BOTH ways, first half into
second and second into first, against a day-shuffled surrogate. Gold's
second half holds the 1675->4400 run, so a one-way test rewards anything
that ends up long, and only a result that survives backwards means
anything.

Signals are also thinned greedily -- once one fires, the next 32 bars are
skipped -- because breakouts cluster, and a cluster counted as independent
trades is the same overlap fault by another name.

Usage:  python _squeeze_break.py [spread_pts]
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
NS = [8, 16, 32]                 # bars of coiling
RATIOS = [1.5, 2.5, 4.0, 6.0]    # range / ATR -- lower is tighter
SLS = [0.5, 1.0, 2.0]
TPS = [1.0, 2.0, 5.0, 10.0]
DIRS = [("break", +1), ("fade", -1)]
MAXHOLD = 32
MIN_TRADES = 25
RNG = np.random.default_rng(20260909)


def atr_arr(h, l, c, n=14):
    pc = np.concatenate(([c[0]], c[:-1]))
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    out = np.full(len(tr), np.nan)
    if len(tr) > n:
        out[n - 1:] = np.convolve(tr, np.ones(n) / n, mode="valid")
    return out


def roll_max(a, n):
    out = np.full(len(a), np.nan)
    if len(a) >= n:
        s = np.lib.stride_tricks.sliding_window_view(a, n)
        out[n - 1:] = s.max(axis=1)
    return out


def roll_min(a, n):
    out = np.full(len(a), np.nan)
    if len(a) >= n:
        s = np.lib.stride_tricks.sliding_window_view(a, n)
        out[n - 1:] = s.min(axis=1)
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
        g[tf] = dict(c=d["close"].to_numpy(float), h=d["high"].to_numpy(float),
                     l=d["low"].to_numpy(float))
        g[tf]["atr"] = atr_arr(g[tf]["h"], g[tf]["l"], g[tf]["c"])
    return g


def thin(idx, gap):
    """Keep signals at least `gap` bars apart, earliest first."""
    out, last = [], -10 ** 9
    for i in idx:
        if i - last >= gap:
            out.append(i); last = i
    return np.array(out, dtype=int)


def sweep(g, cost):
    rows = []
    for tf in TFS:
        c, h, l, atr = g[tf]["c"], g[tf]["h"], g[tf]["l"], g[tf]["atr"]
        n = len(c)
        half = n // 2
        for N in NS:
            # levels use bars strictly BEFORE the signal bar
            hi = np.concatenate(([np.nan], roll_max(h, N)[:-1]))
            lo = np.concatenate(([np.nan], roll_min(l, N)[:-1]))
            with np.errstate(invalid="ignore", divide="ignore"):
                tight = (hi - lo) / atr
            up = c > hi
            dn = c < lo
            for R in RATIOS:
                ok = np.isfinite(tight) & (tight <= R) & np.isfinite(atr) & (atr > 0)
                sig = np.flatnonzero(ok & (up | dn))
                sig = sig[(sig > N + 20) & (sig < n - MAXHOLD - 1)]
                if len(sig) < MIN_TRADES * 3:
                    continue
                sig = thin(sig, MAXHOLD)
                if len(sig) < MIN_TRADES * 2:
                    continue
                d0 = np.where(up[sig], 1.0, -1.0)
                p0, a0 = c[sig], atr[sig]
                steps = np.arange(1, MAXHOLD + 1)
                win_i = sig[:, None] + steps[None, :]
                rmax = np.maximum.accumulate(h[win_i], axis=1)
                rmin = np.minimum.accumulate(l[win_i], axis=1)
                endc = c[sig + MAXHOLD]
                early = sig < half
                for dname, dmul in DIRS:
                    side = d0 * dmul
                    for sl in SLS:
                        for tp in TPS:
                            tgt = np.where(side > 0, p0 + tp * a0, p0 - tp * a0)
                            stp = np.where(side > 0, p0 - sl * a0, p0 + sl * a0)
                            hit_t = (rmax >= tgt[:, None]) if True else None
                            t_t = np.where(side > 0,
                                           np.where((rmax >= tgt[:, None]).any(1),
                                                    (rmax >= tgt[:, None]).argmax(1),
                                                    MAXHOLD),
                                           np.where((rmin <= tgt[:, None]).any(1),
                                                    (rmin <= tgt[:, None]).argmax(1),
                                                    MAXHOLD))
                            t_s = np.where(side > 0,
                                           np.where((rmin <= stp[:, None]).any(1),
                                                    (rmin <= stp[:, None]).argmax(1),
                                                    MAXHOLD),
                                           np.where((rmax >= stp[:, None]).any(1),
                                                    (rmax >= stp[:, None]).argmax(1),
                                                    MAXHOLD))
                            won = t_t < t_s
                            lost = t_s < t_t
                            timed = (endc - p0) * side
                            pnl = np.where(won, tp * a0,
                                           np.where(lost, -sl * a0, timed)) - cost
                            e, l2 = pnl[early], pnl[~early]
                            if len(e) < MIN_TRADES or len(l2) < MIN_TRADES:
                                continue
                            rows.append((tf, N, R, dname, sl, tp,
                                         float(e.mean()), len(e),
                                         float(l2.mean()), len(l2)))
    return rows


def report(rows, tag, fwd=True):
    if not rows:
        print(f"  {tag:>10}  nothing traded enough")
        return None
    si, ci = (6, 8) if fwd else (8, 6)
    passed = [r for r in rows if r[si] > 0]
    base = sum(1 for r in rows if r[ci] > 0) / len(rows)
    surv = [r for r in passed if r[ci] > 0]
    lift = len(surv) / len(passed) if passed else 0.0
    print(f"  {tag:>10}{len(rows):>8}{len(passed):>9}{len(surv):>9}"
          f"{lift*100:>8.0f}%{base*100:>8.0f}%{(lift-base)*100:>+8.0f}")
    return dict(passed=passed, surv=surv, lift=lift, base=base)


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = 10_000.0
    print("loading 13 years of gold M15 ...", flush=True)
    df, _ = loader.load("XAUUSD", 99.0, c0, csv_path=CSV, allow_synthetic=False)
    print("building the day-shuffled surrogate ...", flush=True)
    real, fake = build(df), build(shuffle_days(df))
    ncand = len(TFS)*len(NS)*len(RATIOS)*len(DIRS)*len(SLS)*len(TPS)
    print("=" * 74)
    print(f" COILED RANGE, THEN BREAKOUT -- {ncand:,} candidates, whole day")
    print(f" range/ATR <= 1.5-6 over 8-32 bars, then the close leaves it")
    print("=" * 74)
    for cost in (SPREAD, SPREAD * 2):
        rr, ff = sweep(real, cost), sweep(fake, cost)
        gains = {}
        for fwd, name in ((True, "1st->2nd"), (False, "2nd->1st")):
            print(f"\n COST {cost:.2f}   searching {name}")
            print(f"  {'series':>10}{'scored':>8}{'passed':>9}{'held':>9}"
                  f"{'rate':>8}{'base':>8}{'lift':>8}")
            R = report(rr, "GOLD", fwd)
            F = report(ff, "shuffled", fwd)
            if R and F:
                gains[name] = (R["lift"] - R["base"]) - (F["lift"] - F["base"])
                print(f"  gold beyond the shuffle: {gains[name]*100:+.0f}")
        if len(gains) == 2:
            print(f"\n  BOTH DIRECTIONS: "
                  + "   ".join(f"{k} {v*100:+.0f}" for k, v in gains.items()))
            if min(gains.values()) <= 0.05:
                print("  Not both ways -- the coil-and-break pattern does not")
                print("  survive the test that killed the last candidate.")
            else:
                print("  SURVIVES BOTH DIRECTIONS. First result in this")
                print("  project to do so; worth a hard second look.")
                R = report(rr, "GOLD", True)
                fam = defaultdict(list)
                for r in R["surv"]:
                    fam[(r[0], r[1], r[2], r[3])].append(r)
                for k, v in sorted(fam.items(),
                                   key=lambda kv: -min(r[8] for r in kv[1]))[:10]:
                    if len(v) < 3:
                        continue
                    print(f"    {k[0]:>7} coil {k[1]:>3} bars  range/ATR<={k[2]}"
                          f"  {k[3]:>6}  {len(v)} geometries"
                          f"  1st {np.mean([r[6] for r in v]):+.2f}"
                          f"  2nd {np.mean([r[8] for r in v]):+.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
