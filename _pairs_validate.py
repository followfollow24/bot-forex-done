#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Validate the pairs stat-arb result (Sharpe 1.29) before believing it.

It is the best number found in this whole search, which is exactly why it
deserves the harshest checks. Specifically:

  1. per-pair breakdown  -- is it broad, or one lucky pair carrying everything?
  2. walk-forward        -- first half vs second half, parameters frozen
  3. cost stress         -- 5bps was assumed; retail CFD spreads are wider.
                            At what cost does the edge die?
  4. parameter sensitivity -- if it only works at one exact z-threshold it is
                            a fitting artefact, not an edge
  5. correlation vs trend -- the real prize: if this is uncorrelated with the
                            Donchian book, the two together beat either alone
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _new_signal_classes import (load_all_daily, build_panel, PAIRS,
                                  TRADING_DAYS, VOL_LOOKBACK)


def perf(r, label=None):
    r = r.dropna()
    if len(r) < 400 or r.std() == 0:
        if label: print(f"  {label:<30} insufficient")
        return None
    eq = (1 + r).cumprod()
    yrs = len(r) / TRADING_DAYS
    d = dict(sharpe=r.mean() / r.std() * np.sqrt(TRADING_DAYS),
             cagr=(eq.iloc[-1] ** (1 / yrs) - 1) * 100,
             dd=abs(((eq / eq.cummax()) - 1).min() * 100),
             n=len(r))
    if label:
        print(f"  {label:<30} days={d['n']:>5}  Sharpe={d['sharpe']:>6.2f}  "
              f"CAGR={d['cagr']:>+7.2f}%  DD={d['dd']:>5.1f}%")
    return d


def pair_returns(px, a, b, entry_z=2.0, exit_z=0.5, win=60, cost_bps=5.0):
    if a not in px.columns or b not in px.columns:
        return None
    sub = px[[a, b]].dropna()
    if len(sub) < 800:
        return None
    rets = sub.pct_change()
    spread = np.log(sub[a]) - np.log(sub[b])
    z = (spread - spread.rolling(win).mean()) / spread.rolling(win).std()
    pos = pd.Series(np.nan, index=sub.index)
    pos[z > entry_z] = -1.0
    pos[z < -entry_z] = 1.0
    pos[z.abs() < exit_z] = 0.0
    pos = pos.ffill().fillna(0.0).shift(1)
    pr = pos * (rets[a] - rets[b])
    turn = pos.diff().abs().fillna(0.0)
    return (pr - turn * 2 * cost_bps / 1e4).dropna()


def book(px, entry_z=2.0, exit_z=0.5, win=60, cost_bps=5.0, pairs=None):
    pairs = pairs or PAIRS
    cols = {}
    for a, b in pairs:
        r = pair_returns(px, a, b, entry_z, exit_z, win, cost_bps)
        if r is not None and len(r) > 400:
            cols[f"{a}/{b}"] = r
    if not cols:
        return pd.Series(dtype=float), {}
    df = pd.concat(cols, axis=1)
    return df.mean(axis=1), cols


def main():
    px = build_panel(load_all_daily())
    print(f"[panel] {px.shape[1]} markets  {px.index[0].date()} -> {px.index[-1].date()}\n")

    print("=" * 92)
    print(" 1) PER-PAIR BREAKDOWN (is it broad or one lucky pair?)")
    print("=" * 92)
    _, cols = book(px)
    rows = []
    for k, r in cols.items():
        p = perf(r)
        if p:
            rows.append((p["sharpe"], k, p))
    rows.sort(reverse=True)
    for sh, k, p in rows:
        flag = "  <== works" if sh > 0.3 else ("  (dead)" if sh < 0 else "")
        print(f"  {k:<22} Sharpe={sh:>6.2f}  CAGR={p['cagr']:>+7.2f}%  DD={p['dd']:>5.1f}%{flag}")
    pos_n = sum(1 for sh, _, _ in rows if sh > 0.3)
    print(f"\n  pairs with Sharpe>0.3: {pos_n}/{len(rows)}")

    print("\n" + "=" * 92)
    print(" 2) WALK-FORWARD (parameters frozen, split in half)")
    print("=" * 92)
    full, _ = book(px)
    mid = full.index[len(full) // 2]
    perf(full.loc[:mid], f"TRAIN  ..{mid.date()}")
    perf(full.loc[mid:], f"TEST   {mid.date()}..")

    print("\n" + "=" * 92)
    print(" 3) COST STRESS (5bps was assumed -- retail CFD is worse)")
    print("=" * 92)
    for c in [0, 5, 10, 20, 40]:
        r, _ = book(px, cost_bps=c)
        perf(r, f"cost {c}bps/side")

    print("\n" + "=" * 92)
    print(" 4) PARAMETER SENSITIVITY (a real edge should not need one exact setting)")
    print("=" * 92)
    print(f"  {'entry_z':<10}{'exit_z':<10}{'window':<10}{'Sharpe':>9}{'CAGR%':>9}{'DD%':>8}")
    for ez in [1.5, 2.0, 2.5]:
        for xz in [0.0, 0.5]:
            for w in [40, 60, 90]:
                r, _ = book(px, entry_z=ez, exit_z=xz, win=w)
                p = perf(r)
                if p:
                    print(f"  {ez:<10.1f}{xz:<10.1f}{w:<10}{p['sharpe']:>9.2f}"
                          f"{p['cagr']:>9.2f}{p['dd']:>8.1f}")

    print("\n" + "=" * 92)
    print(" 5) CORRELATION vs the Donchian trend book")
    print("=" * 92)
    try:
        from _final_test import collect_all, run
        from _all_paths import to_monthly
        targets = collect_all()
        series = {}
        for nm, d, sym, sp, cm, ps, pv, ch, _ in targets:
            try:
                m, trd = run(d, sym, sp, cm, ps, pv, ch, 7.0, 999.0, 999.0, 100)
            except Exception:
                continue
            if m and m.get("trades", 0) >= 20:
                mr = to_monthly(trd)
                if len(mr) >= 24:
                    series[nm] = mr
        trend_m = pd.concat(series, axis=1, sort=True).mean(axis=1, skipna=True).dropna()
        pairs_m = (1 + full).resample("ME").prod() - 1
        pairs_m = pairs_m * 100
        both = pd.concat([pairs_m.rename("pairs"), trend_m.rename("trend")], axis=1).dropna()
        if len(both) >= 24:
            c = both["pairs"].corr(both["trend"])
            print(f"  monthly correlation over {len(both)} months: {c:+.3f}")
            comb = both.mean(axis=1)
            eq = (1 + comb / 100).cumprod(); yrs = len(comb) / 12
            print(f"  pairs alone : Sharpe {both['pairs'].mean()/both['pairs'].std()*np.sqrt(12):.2f}")
            print(f"  trend alone : Sharpe {both['trend'].mean()/both['trend'].std()*np.sqrt(12):.2f}")
            print(f"  50/50 blend : Sharpe {comb.mean()/comb.std()*np.sqrt(12):.2f}"
                  f"   CAGR {(eq.iloc[-1]**(1/yrs)-1)*100:+.2f}%"
                  f"   DD {abs(((eq/eq.cummax())-1).min()*100):.1f}%")
    except Exception as e:
        print(f"  (trend comparison unavailable: {e})")


if __name__ == "__main__":
    main()
