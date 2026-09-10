#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_1930_final_test.py -- the closing test on the pre-bell distance filter.

Where it stands. Unfiltered, 19:30 on XAUUSD is negative at every hold in
both halves. Filter on "price already covered >=X ATR in the hour before
the bell, and the break at the bell agrees with it" and the family turns
positive at every hold in both halves, and removing gold's drift changes
almost nothing -- the filter takes longs and shorts about equally, so net
exposure is near zero and there is no trend to collect.

One thing is left. A single within-day shuffle reproduced much of it, and
this project has a written rule never to trust a single-draw control: one
draw once swung an edge column by 0.26R. So this runs MANY draws and gives
a band instead of a point.

The shuffle permutes the M15 bars inside each day, holding the first bar
of each day where it is because it carries the move across the break --
shuffled into mid-session it becomes a synthetic gap that clears any gate
and pays enormously, which is how two earlier versions of this control
came to report thousands of points a trade. It destroys exactly what the
claim needs: the power of the hour before the bell to say anything about
the break that follows. Everything else about each day survives.

If gold sits above the band, this is the first thing in the project to
clear every control. If it sits inside, the window is closed for good.

Usage:  python _1930_final_test.py [draws] [spread]
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import DataLoader
from _idea_search import resample

CSV = "download/xauusd-m15-bid-2013-01-01-2026-06-10.csv"
DRAWS = int(sys.argv[1]) if len(sys.argv) > 1 else 100
SPREAD = float(sys.argv[2]) if len(sys.argv) > 2 else 0.24
# fixed before looking: the three best-sampled setups from the hold curve
SETUPS = [(1, 0.5, 0.50, 12), (1, 0.5, 0.75, 12), (3, 0.5, 0.75, 24)]
SL_ATR = 3.0
RNG = np.random.default_rng(20260911)


def shuffle_within_days(df):
    out = df.copy()
    px = out["close"].to_numpy(float)
    lr = np.zeros_like(px)
    lr[1:] = np.diff(np.log(px))
    day = out["timestamp"].dt.floor("D").to_numpy()
    _, first = np.unique(day, return_index=True)
    b = list(np.sort(first)) + [len(lr)]
    r = lr.copy()
    for a, z in zip(b[:-1], b[1:]):
        if z - a < 3:
            continue
        seg = r[a + 1:z].copy()
        RNG.shuffle(seg)
        r[a + 1:z] = seg
    newpx = float(px[0]) * np.exp(np.cumsum(r))
    scale = newpx / px
    for col in ("open", "high", "low", "close"):
        out[col] = out[col].to_numpy(float) * scale
    return out


def run(df):
    """per-trade mean for each setup, whole sample."""
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
    prev_h = (ts.dt.floor("h") - np.timedelta64(1, "h")).to_numpy()
    bells = np.flatnonzero(((ts.dt.hour == 12) & (ts.dt.minute == 30)).to_numpy())
    bells = bells[(bells > 40) & (bells < len(c) - 40)]
    out = {}
    for back, st, ga, hold in SETUPS:
        pnl = []
        for i in bells:
            j = hidx.get(prev_h[i])
            if j is None or j < 20 or not np.isfinite(atr[j]) or atr[j] <= 0:
                continue
            a = float(atr[j])
            pm = (hc[j] - hc[j - back]) / a
            if abs(pm) < st:
                continue
            ps = 1 if pm > 0 else -1
            g = ga * a
            ref = o[i]
            up, dn = (h[i] - ref) >= g, (ref - l[i]) >= g
            if not (up or dn):
                continue
            sides = (1, -1) if (up and dn) else ((1,) if up else (-1,))
            sides = tuple(s for s in sides if s == ps)
            if not sides:
                continue
            sh = h[i:i + hold + 1].max()
            sl_ = l[i:i + hold + 1].min()
            ex = c[i + hold]
            s0 = sides[0]
            entry = ref + s0 * g
            stp = entry - s0 * SL_ATR * a
            hit = (sl_ <= stp) if s0 > 0 else (sh >= stp)
            px = stp if hit else ex
            pnl.append((px - entry) * s0 - SPREAD)
        out[(back, st, ga, hold)] = (float(np.mean(pnl)) if pnl else 0.0,
                                     len(pnl))
    return out


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = 10_000.0
    print("loading 13 years of XAUUSD M15 ...", flush=True)
    df, _ = loader.load("XAUUSD", 99.0, c0, csv_path=CSV, allow_synthetic=False)
    real = run(df)
    print(f"running {DRAWS} within-day shuffles ...", flush=True)
    sims = {k: [] for k in real}
    for d in range(DRAWS):
        r = run(shuffle_within_days(df))
        for k in real:
            sims[k].append(r[k][0])
        if (d + 1) % 20 == 0:
            print(f"  {d+1}/{DRAWS}", flush=True)

    print("=" * 78)
    print(f" THE CLOSING TEST -- gold against {DRAWS} within-day shuffles")
    print("=" * 78)
    print(f"\n  {'setup':>26}{'n':>7}{'GOLD':>9}{'shuffle mean':>14}"
          f"{'95th pct':>10}{'beat':>7}   verdict")
    any_clear = False
    for k, (g, n) in real.items():
        s = np.array(sims[k])
        p95 = float(np.percentile(s, 95))
        beat = int((s >= g).sum())
        clear = g > p95
        any_clear |= clear
        lbl = f"with {k[0]}h>={k[1]} gate {k[2]:g}A h{k[3]}"
        print(f"  {lbl:>26}{n:>7}{g:>+9.2f}{s.mean():>+14.2f}{p95:>+10.2f}"
              f"{beat:>5}/{DRAWS}   {'CLEARS' if clear else 'inside the band'}")
    print()
    if any_clear:
        print("  Gold sits ABOVE the shuffled band. This is the first result")
        print("  in the project to clear every control -- two-way split, day")
        print("  shuffle, drift removal, and now a within-day band.")
        print("  It is still a backtest. Forward days are the next evidence.")
    else:
        print("  Gold sits INSIDE the band. Selecting days by how far price")
        print("  already moved picks volatile days, and volatile days give")
        print("  bigger numbers in a shuffle too. The 19:30 window is closed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
