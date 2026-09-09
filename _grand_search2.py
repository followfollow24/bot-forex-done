#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_grand_search2.py -- the same exhaustive hunt, with the three faults of
the first version fixed.

The first pass reported that 61% of candidates passing the first half also
passed the second against a 24% base rate, z = +34. A z of thirty-four does
not happen in price data, and three things were producing it.

  OVERLAP. Entries fired on every qualifying bar, so a single move was
  counted dozens of times with almost identical forward returns. The
  sample looked enormous and was not. Entries are now pinned to a grid --
  only bars where index % hold == 0 -- which guarantees no two trades of a
  candidate overlap, at the cost of dividing the sample by the hold.

  DRIFT. Gold went from 1675 to 4400 across this data. "Follow" takes more
  longs than shorts in a rising market, so it profits in BOTH halves from
  the drift alone, and that shows up as selection working. Each candidate's
  return is now measured as an EXCESS over the unconditional forward move
  for the same net exposure, which removes the drift exactly.

  A NULL THAT ASSUMED INDEPENDENCE. Ten thousand candidates carved from one
  price series are not ten thousand experiments, so a binomial z over them
  means nothing. The whole search is therefore re-run on a SURROGATE built
  by shuffling gold's own days -- identical drift, identical volatility
  clustering, identical intraday shape, no persistence across days. If the
  surrogate shows the same lift, the lift is the method, not the market.

The verdict is the comparison between the two, and nothing else.

Usage:  python _grand_search2.py [spread_pts]
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import DataLoader
from _idea_search import resample

CSV = "download/xauusd-m15-bid-2013-01-01-2026-06-10.csv"
SPREAD = float(sys.argv[1]) if len(sys.argv) > 1 else 0.24
TFS = ["15min", "1h", "4h"]
LOOKS = [2, 4, 8, 16, 32]
THRESH = [0.5, 1.0, 1.5, 2.0, 3.0]
HOLDS = [1, 2, 4, 8, 16, 32]
DIRS = [("follow", +1), ("fade", -1)]
VOLS = ["any", "calm", "wild"]
SESS = ["any", "asia", "london", "ny"]
MIN_TRADES = 30
RNG = np.random.default_rng(20260909)


def atr_arr(h, l, c, n=14):
    pc = np.concatenate(([c[0]], c[:-1]))
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    out = np.full(len(tr), np.nan)
    if len(tr) > n:
        out[n - 1:] = np.convolve(tr, np.ones(n) / n, mode="valid")
    return out


def session_of(hours):
    s = np.full(len(hours), "ny", dtype=object)
    s[(hours >= 0) & (hours < 7)] = "asia"
    s[(hours >= 7) & (hours < 13)] = "london"
    return s


def shuffle_days(df):
    """A surrogate with gold's drift, its volatility clustering and its
    intraday shape, but no memory from one day to the next."""
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
        g[tf] = (c, atr_arr(d["high"].to_numpy(float),
                            d["low"].to_numpy(float), c),
                 session_of(d["timestamp"].dt.hour.to_numpy()))
    return g


def sweep(g, cost):
    rows = []
    for tf in TFS:
        c, atr, sess = g[tf]
        n = len(c)
        half = n // 2
        med = np.nanmedian(atr)
        for K in LOOKS:
            mv = c[K:] - c[:-K]
            a, sv = atr[K:], sess[K:]
            base = np.arange(K, n)
            with np.errstate(invalid="ignore", divide="ignore"):
                norm = mv / a
            for H in HOLDS:
                m = len(base) - H
                if m < 400:
                    continue
                bi = base[:m]
                fwd = c[bi + H] - c[bi]
                # DRIFT: what a coin-flip position of the same net exposure
                # would have earned over the same horizon, removed below.
                mu = float(np.nanmean(fwd))
                nm, av, svv = norm[:m], a[:m], sv[:m]
                # OVERLAP: entries only on a grid H apart, so no two trades
                # of one candidate share a bar.
                grid = (bi % H == 0)
                ok = np.isfinite(nm) & grid
                for X in THRESH:
                    big = ok & (np.abs(nm) >= X)
                    if big.sum() < MIN_TRADES * 2:
                        continue
                    for dname, dmul in DIRS:
                        side = np.sign(nm) * dmul
                        pnl = fwd * side - mu * side - cost
                        for v in VOLS:
                            vm = (big if v == "any" else
                                  big & (av <= med) if v == "calm" else
                                  big & (av > med))
                            for s in SESS:
                                sm = vm if s == "any" else vm & (svv == s)
                                e = sm & (bi < half)
                                lt = sm & (bi >= half)
                                if e.sum() < MIN_TRADES or lt.sum() < MIN_TRADES:
                                    continue
                                rows.append((tf, K, X, H, dname, v, s,
                                             float(pnl[e].mean()), int(e.sum()),
                                             float(pnl[lt].mean()), int(lt.sum())))
    return rows


def report(rows, tag):
    if not rows:
        print(f"  {tag:>10}: nothing traded enough")
        return None
    tested = len(rows)
    passed = [r for r in rows if r[7] > 0]
    base = sum(1 for r in rows if r[9] > 0) / tested
    surv = [r for r in passed if r[9] > 0]
    lift = (len(surv) / len(passed)) if passed else 0.0
    print(f"  {tag:>10}{tested:>8}{len(passed):>9}{len(surv):>9}"
          f"{lift*100:>9.0f}%{base*100:>9.0f}%{(lift-base)*100:>+9.0f}")
    return dict(rows=rows, passed=passed, surv=surv, lift=lift, base=base)


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = 10_000.0
    print("loading 13 years of gold M15 ...", flush=True)
    df, _ = loader.load("XAUUSD", 99.0, c0, csv_path=CSV,
                        allow_synthetic=False)
    print("building the day-shuffled surrogate ...", flush=True)
    real, fake = build(df), build(shuffle_days(df))

    for cost in (SPREAD, SPREAD * 2):
        print("\n" + "=" * 78)
        print(f" COST {cost:.2f} pts   (overlap removed, drift removed)")
        print("=" * 78)
        print(f"  {'series':>10}{'scored':>8}{'pass 1st':>9}{'pass 2nd':>9}"
              f"{'rate':>10}{'base':>9}{'lift':>9}")
        R = report(sweep(real, cost), "GOLD")
        F = report(sweep(fake, cost), "shuffled")
        if not R or not F:
            continue
        gain = (R["lift"] - R["base"]) - (F["lift"] - F["base"])
        print(f"\n  gold's lift beyond the shuffle: {gain*100:+.0f} points")
        if gain <= 0.05:
            print("  The shuffle reproduces it. Selecting on the first half")
            print("  buys nothing the surrogate does not also give, so there")
            print("  is no persistent effect here to trade.")
        else:
            fam = defaultdict(list)
            for r in R["surv"]:
                fam[(r[0], r[1], r[2], r[4], r[5], r[6])].append(r)
            blocks = {k: v for k, v in fam.items() if len(v) >= 3}
            print(f"  Gold keeps a real excess. Survivors in a BLOCK of 3+"
                  f" hold lengths: {sum(len(v) for v in blocks.values())}"
                  f" in {len(blocks)} families")
            print(f"\n    {'tf':>7}{'moved':>7}{'in':>4}{'dir':>8}{'vol':>6}"
                  f"{'session':>9}{'holds':>16}{'1st':>9}{'2nd':>9}")
            for k, v in sorted(blocks.items(),
                               key=lambda kv: -min(r[9] for r in kv[1]))[:12]:
                tf, K, X, dn, vv, ss = k
                print(f"    {tf:>7}{X:>6.1f}A{K:>4}{dn:>8}{vv:>6}{ss:>9}"
                      f"{str(sorted(r[3] for r in v)):>16}"
                      f"{np.mean([r[7] for r in v]):>+9.2f}"
                      f"{np.mean([r[9] for r in v]):>+9.2f}")
    print("\n  Excess is over the unconditional move, so these are edges")
    print("  above buy-and-hold, not returns. Swap and slippage beyond the")
    print("  doubled spread are still unmodelled.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
