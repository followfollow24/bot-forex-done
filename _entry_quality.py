#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Entry-signal quality for a MANUALLY-CLOSED bot.

If the bot only opens and the human closes, PF and Sharpe are the wrong
metrics -- they measure an exit rule that will not be used. What matters is:

  after this entry fires, how far does price actually travel in my favour
  before the stop would have been hit?

So this measures MFE (max favourable excursion) in ATR units, per signal, and
reports the distribution:

  P(reach +1 ATR), P(+2), P(+3), P(+5)  -- how often a profitable exit exists
  median / mean MFE                     -- the typical opportunity
  P(hit SL with MFE < 0.5)              -- entries that never gave a chance

A signal where 60% of entries reach +2 ATR is a good manual-trading signal even
if its automated PF is mediocre, because the human gets to choose the exit. The
opposite is also true: a signal that only pays via rare huge winners is a BAD
manual signal, because a human will take small profits and miss the tail.

Everything is measured net of the real entry cost.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import DataLoader, prepare_data, FastHybridTrendPullback
from gold_regime_filter_real_engine import RegimeFilteredHybrid
from _idea_search import DonchianBreakout, resample
from _crypto_multi import load_crypto_1h

GOLD_M15 = "download/xauusd-m15-bid-2013-01-01-2026-06-10.csv"
SL_ATR = 3.0
MAX_BARS = 200          # generous: a manual trader is not bound by a 16h timeout
LEVELS = [1.0, 2.0, 3.0, 5.0]


def collect_entries(strategy, d, cost_price, max_bars=MAX_BARS):
    """Walk the series, fire the signal, then measure MFE until SL or timeout."""
    n = len(d["c"])
    strategy.precompute(d)
    out = []
    i = getattr(strategy, "MIN_BARS", 300)
    while i < n - 2:
        sig = strategy.signal(d, i)
        if sig.action not in ("BUY", "SELL"):
            i += 1
            continue
        atr = float(d["atr"][i])
        if not np.isfinite(atr) or atr <= 0:
            i += 1
            continue
        long_ = sig.action == "BUY"
        entry = float(d["c"][i]) + (cost_price if long_ else -cost_price)
        sl = entry - SL_ATR * atr if long_ else entry + SL_ATR * atr
        mfe = 0.0
        exit_j = None
        for j in range(i + 1, min(i + max_bars, n)):
            hi, lo = float(d["h"][j]), float(d["l"][j])
            fav = (hi - entry) if long_ else (entry - lo)
            adv = (entry - lo) if long_ else (hi - entry)
            mfe = max(mfe, fav / atr)
            if adv >= SL_ATR * atr:
                exit_j = j
                break
        out.append(dict(mfe=mfe, stopped=exit_j is not None,
                        bars=(exit_j - i) if exit_j else max_bars))
        # don't re-enter while the same trade would still be open
        i = (exit_j if exit_j else i + max_bars // 4) + 1
    return pd.DataFrame(out)


def report(dfres, label):
    if dfres is None or len(dfres) < 40:
        print(f"  {label:<34} n={0 if dfres is None else len(dfres):>4}  (too few)")
        return None
    n = len(dfres)
    probs = {L: (dfres["mfe"] >= L).mean() for L in LEVELS}
    dead = (dfres["mfe"] < 0.5).mean()
    med = dfres["mfe"].median()
    # expected R if the human closes at a fixed level, else stopped at -3
    evs = {}
    for L in LEVELS:
        hit = dfres["mfe"] >= L
        evs[L] = (hit.mean() * L) + ((~hit).mean() * -SL_ATR)
    best_L = max(evs, key=evs.get)
    print(f"  {label:<34} n={n:>4}  medMFE={med:>5.2f}  "
          + "".join(f"P{int(L)}={probs[L]*100:>5.1f}% " for L in LEVELS)
          + f" dead={dead*100:>4.1f}%  bestExit=+{best_L:.0f}R (EV {evs[best_L]:+.2f}R)")
    return dict(n=n, probs=probs, med=med, dead=dead, evs=evs, best=best_L)


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = 10000

    print("MFE = how far price moved in your favour (in ATR units) before the 3xATR stop.")
    print("Pn = share of entries that reached +n ATR at some point -> your chance to close in profit.")
    print("bestExit = the fixed take-profit level that maximises expected R, given SL = -3R.\n")

    dfg, _ = loader.load("XAUUSD", 99.0, c0, csv_path=GOLD_M15, allow_synthetic=True)

    print("=" * 118)
    print(" GOLD -- entry quality by timeframe (cost $2.85)")
    print("=" * 118)
    for tf, dfx in [("M15", dfg), ("H1", resample(dfg, "1h")), ("H4", resample(dfg, "4h"))]:
        d = prepare_data(dfx)
        print(f"\n  --- {tf} ---")
        for lbl, cls, kw in [("pullback ADX18", FastHybridTrendPullback, dict(ADX_MIN=18)),
                             ("pullback ADX22", FastHybridTrendPullback, dict(ADX_MIN=22)),
                             ("pullback ADX26", FastHybridTrendPullback, dict(ADX_MIN=26)),
                             ("regime22", RegimeFilteredHybrid, dict(ADX_MIN=22)),
                             ("donchian 100", DonchianBreakout, dict(CHANNEL=100))]:
            s = cls()
            for k, v in kw.items():
                setattr(s, k, v)
            try:
                res = collect_entries(s, d, 2.85)
            except Exception as e:
                print(f"  {tf} {lbl}: error {e}"); continue
            report(res, f"{tf} {lbl}")

    print("\n" + "=" * 118)
    print(" GOLD 2024-2026 ONLY (current price level)")
    print("=" * 118)
    for tf, dfx in [("H1", resample(dfg, "1h")), ("H4", resample(dfg, "4h"))]:
        dfw = dfx[dfx["timestamp"] >= pd.Timestamp("2024-01-01")].reset_index(drop=True)
        d = prepare_data(dfw)
        print(f"\n  --- {tf} ---")
        for lbl, cls, kw in [("pullback ADX18", FastHybridTrendPullback, dict(ADX_MIN=18)),
                             ("pullback ADX22", FastHybridTrendPullback, dict(ADX_MIN=22)),
                             ("regime22", RegimeFilteredHybrid, dict(ADX_MIN=22))]:
            s = cls()
            for k, v in kw.items():
                setattr(s, k, v)
            try:
                res = collect_entries(s, d, 2.85)
            except Exception:
                continue
            report(res, f"{tf} {lbl}")

    print("\n" + "=" * 118)
    print(" CRYPTO H1 -- same measure, for comparison (cost 8bps)")
    print("=" * 118)
    data = load_crypto_1h()
    agg = []
    for nm in ["BTC", "ETH", "SOL", "BNB", "LINK"]:
        if nm not in data:
            continue
        df = data[nm]
        d = prepare_data(df)
        cost = float(df["close"].median()) * 8 / 1e4
        s = FastHybridTrendPullback(); s.ADX_MIN = 18
        try:
            res = collect_entries(s, d, cost)
        except Exception:
            continue
        r = report(res, f"{nm} H1 pullback18")
        if r:
            agg.append(res)
    if agg:
        report(pd.concat(agg), "ALL 5 COINS POOLED")


if __name__ == "__main__":
    main()
