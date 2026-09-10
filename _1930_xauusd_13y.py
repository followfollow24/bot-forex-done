#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_1930_xauusd_13y.py -- the 19:30 rule on the instrument the operator
actually watches, over thirteen years instead of three months.

Every tick-level test of this rule in this project ran on XAUAUDm, because
that is what the bot was pointed at. The operator's charts are XAUUSDm.
The two are different instruments -- gold against the Australian dollar
versus gold against the US dollar -- and they differ where it matters
most: 1.124 points of spread against 0.260, about three times the cost per
trade in USD. Since the central conclusion was "the edge is real and
smaller than the spread", it was reached on the expensive one.

XAUUSDm tick history at this broker starts 2026-06-01, which is exactly
the window the 14-point gate was chosen on, so there is nothing clean to
test at tick level. This uses the local M15 archive instead: 13 years, the
right instrument, the re-confirmed 0.24 spread.

THE APPROXIMATION, stated plainly because it is the whole caveat. Ticks
give the instant the gate was crossed; M15 bars do not. So:
    reference   the open of the 12:30 UTC bar (= 19:30 Thai)
    trigger     that bar's high clearing open+gate, or its low clearing
                open-gate, within the 15 minutes the rule watches
    fill        at the gate level itself
    exit        the close two bars later (30-45 min after entry)
    stop        3 x ATR(H1), checked against the bars in between
When BOTH sides cleared inside the one bar, the order is unknowable. Those
days are counted separately and charged the LOSING side, which is the
pessimistic reading -- crediting them to the winner is how a backtest
flatters itself.

Nothing is chosen here. The gate came from other data on another
instrument, so all thirteen years are out-of-sample for it, and each fold
is simply reported.

Usage:  python _1930_xauusd_13y.py [folds] [gate_pts] [spread]
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
FOLDS = int(sys.argv[1]) if len(sys.argv) > 1 else 6
GATE = float(sys.argv[2]) if len(sys.argv) > 2 else 14.0
SPREAD = float(sys.argv[3]) if len(sys.argv) > 3 else 0.24
SL_ATR, HOLD_BARS = 3.0, 2
GATES = [5, 8, 11, 14, 17, 20, 25, 30]


def atr_h1_map(df):
    h1 = resample(df, "1h")
    c = h1["close"].to_numpy(float)
    hi = h1["high"].to_numpy(float)
    lo = h1["low"].to_numpy(float)
    pc = np.concatenate(([c[0]], c[:-1]))
    tr = np.maximum(hi - lo, np.maximum(np.abs(hi - pc), np.abs(lo - pc)))
    a = np.full(len(tr), np.nan)
    if len(tr) > 14:
        a[13:] = np.convolve(tr, np.ones(14) / 14, mode="valid")
    return dict(zip(h1["timestamp"].to_numpy(), a))


def build(df, gate):
    """One row per 19:30 session that cleared the gate."""
    ts = df["timestamp"]
    o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    is_bell = (ts.dt.hour == 12) & (ts.dt.minute == 30)
    idx = np.flatnonzero(is_bell.to_numpy())
    amap = atr_h1_map(df)
    hour_key = ts.dt.floor("h").to_numpy()
    rows = []
    for i in idx:
        if i + HOLD_BARS + 1 >= len(c):
            continue
        atr = amap.get(hour_key[i], np.nan)
        if not np.isfinite(atr) or atr <= 0:
            continue
        ref = o[i]
        up_hit = (h[i] - ref) >= gate
        dn_hit = (ref - l[i]) >= gate
        if not (up_hit or dn_hit):
            continue
        both = up_hit and dn_hit
        # both inside one bar -> order unknowable -> charge the losing side
        seg_h = h[i:i + HOLD_BARS + 1].max()
        seg_l = l[i:i + HOLD_BARS + 1].min()
        exit_px = c[i + HOLD_BARS]
        out = []
        for side in ((1, -1) if both else ((1,) if up_hit else (-1,))):
            entry = ref + side * gate
            sl = entry - side * SL_ATR * atr
            stopped = (seg_l <= sl) if side > 0 else (seg_h >= sl)
            px = sl if stopped else exit_px
            out.append((px - entry) * side - SPREAD)
        rows.append(dict(t=ts.iloc[i], both=both,
                         pts=min(out) if both else out[0]))
    return rows


def table(rows, folds, label):
    if not rows:
        print(f"  {label}: no sessions cleared the gate")
        return
    n = len(rows)
    edges = [round(k * n / folds) for k in range(folds + 1)]
    print(f"\n  {label}   {n} trades, "
          f"{sum(1 for r in rows if r['both'])} ambiguous "
          f"(charged the losing side)")
    print(f"    {'window':>20}{'trades':>8}{'wins':>6}{'win %':>7}"
          f"{'total pts':>11}{'per trade':>11}")
    won = 0
    for k in range(folds):
        part = rows[edges[k]:edges[k + 1]]
        if not part:
            continue
        p = np.array([r["pts"] for r in part])
        w = int((p > 0).sum())
        won += p.sum() > 0
        span = (f"{part[0]['t']:%b %y}..{part[-1]['t']:%b %y}")
        print(f"    {span:>20}{len(p):>8}{w:>6}{100*w/len(p):>6.0f}%"
              f"{p.sum():>+11.1f}{p.mean():>+11.2f}")
    allp = np.array([r["pts"] for r in rows])
    sd = allp.std(ddof=1)
    t = allp.mean() / (sd / np.sqrt(len(allp))) if sd > 0 else 0.0
    print(f"    {'ALL':>20}{len(allp):>8}{int((allp>0).sum()):>6}"
          f"{100*(allp>0).mean():>6.0f}%{allp.sum():>+11.1f}"
          f"{allp.mean():>+11.2f}   t = {t:+.2f}")
    print(f"    profitable windows: {won} of {folds}")


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = 10_000.0
    print("loading 13 years of XAUUSD M15 ...", flush=True)
    df, _ = loader.load("XAUUSD", 99.0, c0, csv_path=CSV,
                        allow_synthetic=False)
    print("=" * 76)
    print(f" 19:30 THAI ON XAUUSD -- 13 YEARS   gate {GATE:g} pts"
          f"   spread {SPREAD:.2f}   stop {SL_ATR}xATR   hold ~30-45 min")
    print(f" M15 approximation; ambiguous bars charged the losing side")
    print("=" * 76)
    table(build(df, GATE), FOLDS, f"GATE {GATE:g} PTS")

    print(f"\n{'='*76}")
    print(f" AND EVERY OTHER GATE, for shape -- not a choice, just reported")
    print(f"{'='*76}")
    print(f"\n    {'gate':>6}{'trades':>8}{'win %':>8}{'total pts':>12}"
          f"{'per trade':>11}{'t':>8}   windows won")
    for g in GATES:
        rows = build(df, g)
        if len(rows) < 30:
            continue
        p = np.array([r["pts"] for r in rows])
        sd = p.std(ddof=1)
        t = p.mean() / (sd / np.sqrt(len(p))) if sd > 0 else 0.0
        edges = [round(k * len(p) / FOLDS) for k in range(FOLDS + 1)]
        won = sum(1 for k in range(FOLDS)
                  if len(p[edges[k]:edges[k+1]]) and p[edges[k]:edges[k+1]].sum() > 0)
        print(f"    {g:>5}p{len(p):>8}{100*(p>0).mean():>7.0f}%{p.sum():>+12.1f}"
              f"{p.mean():>+11.2f}{t:>+8.2f}   {won} of {FOLDS}")
    print(f"\n  A gate whose per-trade figure is positive, whose t clears 2,")
    print(f"  and which wins most windows is worth something. One that only")
    print(f"  looks good in total is a few big days.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
