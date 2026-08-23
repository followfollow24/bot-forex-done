#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Find a configuration that fires AT LEAST ~1 entry per day while keeping the
entry quality that makes manual closing work.

The tension: entry quality improves as the timeframe rises (gold M15 gives 21%
of entries a chance to close in profit, H1 gives 73%), but higher timeframes
fire less often. So the frequency has to come from running several instruments
in parallel rather than from dropping back down to M15.

Reports, per configuration:
  trades/year and trades/day     -- does it actually meet the 1/day requirement
  P(+1R), P(+2R), dead%          -- is it still worth manually managing
  EV at the best fixed exit      -- expected R per trade if closed at that level

Then combines instruments until the 1/day threshold is met, and shows what the
pooled entry quality looks like at that point.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import DataLoader, prepare_data, FastHybridTrendPullback
from gold_regime_filter_real_engine import RegimeFilteredHybrid
from _idea_search import resample
from _crypto_multi import load_crypto_1h
from _entry_quality import collect_entries, SL_ATR, LEVELS

GOLD_M15 = "download/xauusd-m15-bid-2013-01-01-2026-06-10.csv"


def stats(res, years):
    if res is None or len(res) < 30:
        return None
    n = len(res)
    probs = {L: (res["mfe"] >= L).mean() for L in LEVELS}
    evs = {L: ((res["mfe"] >= L).mean() * L) + ((res["mfe"] < L).mean() * -SL_ATR)
           for L in LEVELS}
    bestL = max(evs, key=evs.get)
    return dict(n=n, per_year=n / years, per_day=n / (years * 365),
                probs=probs, dead=(res["mfe"] < 0.5).mean(),
                med=res["mfe"].median(), bestL=bestL, ev=evs[bestL], raw=res)


def line(s, label):
    if not s:
        print(f"  {label:<30} (too few)"); return
    flag = "  ✓1/day" if s["per_day"] >= 1.0 else ""
    print(f"  {label:<30} n={s['n']:>5} {s['per_year']:>6.0f}/yr {s['per_day']:>5.2f}/day  "
          f"medMFE={s['med']:>5.2f}  P1={s['probs'][1.0]*100:>5.1f}%  "
          f"P2={s['probs'][2.0]*100:>5.1f}%  dead={s['dead']*100:>4.1f}%  "
          f"EV@+{s['bestL']:.0f}R={s['ev']:>+5.2f}R{flag}")


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = 10000
    dfg, _ = loader.load("XAUUSD", 99.0, c0, csv_path=GOLD_M15, allow_synthetic=True)

    print("Target: >= 1 entry/day, while keeping manual-close quality high.\n")

    # ---------- gold, recent era ----------
    print("=" * 118)
    print(" GOLD alone (2024-2026) -- can one instrument reach 1/day?")
    print("=" * 118)
    for tf, dfx in [("H1", resample(dfg, "1h")), ("H4", resample(dfg, "4h"))]:
        dfw = dfx[dfx["timestamp"] >= pd.Timestamp("2024-01-01")].reset_index(drop=True)
        yrs = (dfw["timestamp"].iloc[-1] - dfw["timestamp"].iloc[0]).days / 365.25
        d = prepare_data(dfw)
        for lbl, cls, kw in [("pullback ADX14", FastHybridTrendPullback, dict(ADX_MIN=14)),
                             ("pullback ADX18", FastHybridTrendPullback, dict(ADX_MIN=18)),
                             ("pullback ADX22", FastHybridTrendPullback, dict(ADX_MIN=22)),
                             ("regime22", RegimeFilteredHybrid, dict(ADX_MIN=22))]:
            s = cls()
            for k, v in kw.items():
                setattr(s, k, v)
            try:
                line(stats(collect_entries(s, d, 2.85), yrs), f"gold {tf} {lbl}")
            except Exception:
                pass

    # ---------- crypto per coin ----------
    print("\n" + "=" * 118)
    print(" CRYPTO H1 -- per coin (pullback ADX18, cost 8bps)")
    print("=" * 118)
    data = load_crypto_1h()
    per_coin = {}
    for nm, df in sorted(data.items()):
        d = prepare_data(df)
        yrs = (df["timestamp"].iloc[-1] - df["timestamp"].iloc[0]).days / 365.25
        cost = float(df["close"].median()) * 8 / 1e4
        s = FastHybridTrendPullback(); s.ADX_MIN = 18
        try:
            st = stats(collect_entries(s, d, cost), yrs)
        except Exception:
            continue
        if st:
            per_coin[nm] = st
            line(st, f"{nm} H1")

    # ---------- pooled combinations ----------
    print("\n" + "=" * 118)
    print(" POOLED -- adding coins until >= 1 entry/day")
    print("=" * 118)
    ranked = sorted(per_coin.items(), key=lambda kv: -kv[1]["ev"])
    chosen, pooled = [], []
    for nm, st in ranked:
        chosen.append(nm)
        pooled.append(st["raw"])
        tot_per_day = sum(per_coin[c]["per_day"] for c in chosen)
        allr = pd.concat(pooled)
        probs = {L: (allr["mfe"] >= L).mean() for L in LEVELS}
        evs = {L: ((allr["mfe"] >= L).mean() * L) + ((allr["mfe"] < L).mean() * -SL_ATR)
               for L in LEVELS}
        bL = max(evs, key=evs.get)
        mark = "  <== MEETS 1/day" if tot_per_day >= 1.0 else ""
        print(f"  {len(chosen):>2} coins ({','.join(chosen):<40}) "
              f"{tot_per_day:>5.2f}/day  P1={probs[1.0]*100:>5.1f}%  "
              f"P2={probs[2.0]*100:>5.1f}%  dead={(allr['mfe']<0.5).mean()*100:>4.1f}%  "
              f"EV@+{bL:.0f}R={evs[bL]:+.2f}R{mark}")
        if tot_per_day >= 1.6:
            break

    # ---------- recommended ----------
    print("\n" + "=" * 118)
    print(" RECOMMENDATION")
    print("=" * 118)
    need = []
    run_pd = 0.0
    for nm, st in ranked:
        need.append(nm); run_pd += st["per_day"]
        if run_pd >= 1.0:
            break
    allr = pd.concat([per_coin[c]["raw"] for c in need])
    probs = {L: (allr["mfe"] >= L).mean() for L in LEVELS}
    evs = {L: ((allr["mfe"] >= L).mean() * L) + ((allr["mfe"] < L).mean() * -SL_ATR)
           for L in LEVELS}
    bL = max(evs, key=evs.get)
    print(f"  {len(need)} coins on H1 pullback ADX18: {', '.join(need)}")
    print(f"  entries      : {run_pd:.2f}/day  (~{run_pd*365:.0f}/year)")
    print(f"  chance to close in profit : {probs[1.0]*100:.1f}% reach +1R, "
          f"{probs[2.0]*100:.1f}% reach +2R, {probs[3.0]*100:.1f}% reach +3R")
    print(f"  no-chance trades          : {(allr['mfe']<0.5).mean()*100:.1f}%")
    print(f"  best fixed exit           : +{bL:.0f}R  (EV {evs[bL]:+.2f}R per trade)")
    print(f"\n  Adding gold H1 regime22 on top adds ~0.14/day at similar quality")
    print(f"  (73% reach +1R) if you want gold represented as well.")


if __name__ == "__main__":
    main()
