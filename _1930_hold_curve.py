#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_1930_hold_curve.py -- is there a best time to take profit, or does the
best time move?

The operator wants to hold until the best moment and take profit there.
Sweeping holds and reporting the winner cannot answer that, because the
winner exists by construction. The question is whether the SHAPE repeats:
if the return-versus-hold curve peaks in the same place in both halves of
the history, a best time exists and can be used. If the peak wanders, then
"the best time" was only ever the best time in hindsight, and picking it
is picking noise.

So the curve is drawn twice -- first half and second half, side by side,
every hold from 15 minutes to 8 hours -- on the pre-bell distance filter
that turned the family averages positive, and on the unfiltered window for
comparison.

Read the PEAK column. Agreement is the finding; a wandering peak is the
answer to the question as asked.

Usage:  python _1930_hold_curve.py [spread]
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
SPREAD = float(sys.argv[1]) if len(sys.argv) > 1 else 0.24
HOLDS = [1, 2, 3, 4, 6, 8, 12, 16, 20, 24, 32]
SETUPS = [
    ("no filter",          None, 0.0, 0.50),
    ("with 1h >=0.5 ATR",  1,    0.5, 0.50),
    ("with 1h >=0.5 ATR",  1,    0.5, 0.75),
    ("with 1h >=1.0 ATR",  1,    1.0, 0.50),
    ("with 1h >=1.0 ATR",  1,    1.0, 0.75),
    ("with 3h >=0.5 ATR",  3,    0.5, 0.75),
]
SL_ATR = 3.0


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = 10_000.0
    print("loading 13 years of XAUUSD M15 ...", flush=True)
    df, _ = loader.load("XAUUSD", 99.0, c0, csv_path=CSV, allow_synthetic=False)
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
    bells = bells[(bells > 20) & (bells < len(c) - max(HOLDS) - 3)]
    sess = []
    for i in bells:
        j = hidx.get(prev_h[i])
        if j is None or j < 20 or not np.isfinite(atr[j]) or atr[j] <= 0:
            continue
        sess.append((i, float(atr[j]), j))
    half = len(sess) // 2
    print(f"  {len(sess)} sessions, split at {ts.iloc[sess[half][0]]:%b %Y}")

    print("=" * 78)
    print(f" RETURN BY HOLD TIME, EACH HALF SEPARATELY   spread {SPREAD:.2f}")
    print(f" a best time exists only if the two peaks land in the same place")
    print("=" * 78)

    for label, back, st, ga in SETUPS:
        trig = []
        for n, (i, a, j) in enumerate(sess):
            if back is not None:
                pm = (hc[j] - hc[j - back]) / a
                if abs(pm) < st:
                    continue
                psign = 1 if pm > 0 else -1
            g = ga * a
            ref = o[i]
            up = (h[i] - ref) >= g
            dn = (ref - l[i]) >= g
            if not (up or dn):
                continue
            sides = (1, -1) if (up and dn) else ((1,) if up else (-1,))
            if back is not None:
                sides = tuple(s for s in sides if s == psign)
                if not sides:
                    continue
            trig.append((n, i, ref, a, g, sides, len(sides) > 1))
        if len(trig) < 60:
            continue
        ne = sum(1 for t in trig if t[0] < half)
        print(f"\n  {label}   gate {ga:g} ATR"
              f"   {len(trig)} trades  ({ne} early / {len(trig)-ne} late)")
        print(f"    {'hold':>7}{'minutes':>9}{'EARLY raw':>9}{'-drift':>9}"
              f"{'LATE raw':>9}{'-drift':>9}   sign (after drift)")
        curve = []
        for hold in HOLDS:
            # DRIFT. Gold ran 1675 -> 4400 over this data, and a "follow the
            # pre-bell direction" filter is net long in a rising market, so
            # a longer hold collects more of the trend whether or not the
            # setup means anything. The unconditional move over the SAME
            # horizon, times the net exposure, is what holding alone earns;
            # subtracting it leaves only what the setup added.
            drift_e, drift_l, side_e, side_l = [], [], [], []
            for n, (i, a, j) in enumerate(sess):
                if i + hold >= len(c):
                    continue
                (drift_e if n < half else drift_l).append(c[i + hold] - o[i])
            for n, i, ref, a, g, sides, amb in trig:
                s_net = float(np.mean(sides))
                (side_e if n < half else side_l).append(s_net)
            mu_e = float(np.mean(drift_e)) if drift_e else 0.0
            mu_l = float(np.mean(drift_l)) if drift_l else 0.0
            sd_e = float(np.mean(side_e)) if side_e else 0.0
            sd_l = float(np.mean(side_l)) if side_l else 0.0
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
            if len(e_p) < 25 or len(l_p) < 25:
                continue
            ea, la = float(np.mean(e_p)), float(np.mean(l_p))
            xe, xl = ea - mu_e * sd_e, la - mu_l * sd_l
            curve.append((hold, xe, xl))
            same = (xe > 0) == (xl > 0)
            print(f"    {hold:>6}b{hold*15:>9}{ea:>+9.2f}{xe:>+9.2f}"
                  f"{la:>+9.2f}{xl:>+9.2f}"
                  f"   {'both +' if same and xe > 0 else 'both -' if same else 'split'}")
        if curve:
            pe = max(curve, key=lambda x: x[1])
            pl = max(curve, key=lambda x: x[2])
            gap = abs(pe[0] - pl[0])
            print(f"    PEAK       early at {pe[0]}b ({pe[0]*15} min, "
                  f"{pe[1]:+.2f})   late at {pl[0]}b ({pl[0]*15} min, "
                  f"{pl[2]:+.2f})")
            print(f"    -> peaks {'AGREE' if gap <= 1 else f'differ by {gap} bars = {gap*15} min'}")
    print(f"\n  raw    = what the trade made")
    print(f"  -drift = minus what simply being in the market that long earned")
    print(f"           over the same horizon at the same net exposure")
    print(f"\n  A peak that moves between halves is not a best time, it is the")
    print(f"  best time IN HINDSIGHT. And a curve that only rises before the")
    print(f"  drift column is gold going up, not a setup working.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
