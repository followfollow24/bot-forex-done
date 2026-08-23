#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test whether the U-shape seen in 183 live trades is real or noise.

Live finding (old M15 bots, trend TF = H1), bucketed by how many H1 bars the
EMA alignment had already held at entry:

    maturity 0-5   n=76  win 21.1%  avg PnL -31.79
    maturity 6-20  n=55  win 43.6%  avg PnL  +3.02   <- only profitable bucket
    maturity 21-50 n=50  win 16.0%  avg PnL -34.91

i.e. both too-early and too-late entries lost, only the middle paid. With
n=50-76 per bucket that could easily be noise, and acting on it directly would
repeat the TP mistake (fitting a rule to a small live sample).

So: re-run the same bucketing on the CURRENT config (H1 entry, trend TF = H4)
over the full backtest history, per market, and see whether the same shape
appears independently. A real effect should show up in thousands of trades
across three markets; noise will not.

Reports per-bucket win rate and expectancy in R (SL = 3xATR = -3R), plus a
train/test split so the shape has to survive out of sample too.
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

GOLD_M15 = "download/xauusd-m15-bid-2013-01-01-2026-06-10.csv"
BTC_CSV  = "download/btcusdt-15m-binance-2017-08-17-2026-06-30.csv"
ETH_CSV  = "download/ethusdt-15m-binance-2017-08-17-2026-06-30.csv"

SL_ATR = 3.0
MAX_BARS = 200          # generous horizon: manual close, not a 16h timeout
BUCKETS = [(0, 5, "0-5 fresh"), (6, 20, "6-20 mid"),
           (21, 50, "21-50 mature"), (51, 10**9, "51+ old")]


def collect(strategy, d, cost_price):
    """For each entry: trend maturity at entry, and whether it reached +1R/+2R
    before the 3xATR stop. Expectancy computed at a fixed +2R exit."""
    n = len(d["c"])
    strategy.precompute(d)
    trend = strategy._h1_trend_arr
    rows = []
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

        cur = trend[i] if trend is not None and i < len(trend) else 0
        maturity = 0
        if cur != 0 and trend is not None:
            k = i
            while k >= 0 and trend[k] == cur:
                maturity += 1
                k -= 1

        long_ = sig.action == "BUY"
        entry = float(d["c"][i]) + (cost_price if long_ else -cost_price)
        mfe = 0.0
        stopped_at = None
        for j in range(i + 1, min(i + MAX_BARS, n)):
            hi, lo = float(d["h"][j]), float(d["l"][j])
            fav = (hi - entry) if long_ else (entry - lo)
            adv = (entry - lo) if long_ else (hi - entry)
            mfe = max(mfe, fav / atr)
            if adv >= SL_ATR * atr:
                stopped_at = j
                break
        rows.append(dict(maturity=maturity, mfe=mfe, ts=i))
        i = (stopped_at if stopped_at else i + MAX_BARS // 4) + 1
    return pd.DataFrame(rows)


def bucket_report(df, label, exit_R=2.0):
    if df is None or len(df) < 100:
        print(f"  {label:<26} n={0 if df is None else len(df)} too few")
        return
    print(f"\n  {label}   (n={len(df)})")
    print(f"    {'bucket':<16}{'n':>6}{'hit+1R':>9}{'hit+2R':>9}{'EV@+2R':>10}")
    for lo, hi, name in BUCKETS:
        m = (df["maturity"] >= lo) & (df["maturity"] <= hi)
        if m.sum() < 20:
            continue
        sub = df[m]
        p1 = (sub["mfe"] >= 1.0).mean()
        p2 = (sub["mfe"] >= exit_R).mean()
        ev = p2 * exit_R + (1 - p2) * -SL_ATR
        print(f"    {name:<16}{m.sum():>6}{p1*100:>8.1f}%{p2*100:>8.1f}%{ev:>+10.2f}R")


def split_report(df, label, exit_R=2.0):
    """Same buckets, but first half vs second half of the sample."""
    if df is None or len(df) < 200:
        return
    mid = df["ts"].median()
    for part, sub in [("TRAIN(1st half)", df[df["ts"] <= mid]),
                      ("TEST (2nd half)", df[df["ts"] > mid])]:
        best_name, best_ev = None, -99
        line = []
        for lo, hi, name in BUCKETS:
            m = (sub["maturity"] >= lo) & (sub["maturity"] <= hi)
            if m.sum() < 15:
                continue
            p2 = (sub[m]["mfe"] >= exit_R).mean()
            ev = p2 * exit_R + (1 - p2) * -SL_ATR
            line.append(f"{name}={ev:+.2f}R(n={m.sum()})")
            if ev > best_ev:
                best_ev, best_name = ev, name
        print(f"    {part}: " + "  ".join(line))
        print(f"      -> best bucket: {best_name} ({best_ev:+.2f}R)")


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = 10000

    print("=" * 96)
    print(" Does the live U-shape (mid-maturity best) reproduce on the current H1 config?")
    print(" Buckets = H4 bars the EMA alignment already held at entry. SL=3xATR, exit at +2R.")
    print("=" * 96)

    results = {}

    dfg, _ = loader.load("XAUUSD", 99.0, c0, csv_path=GOLD_M15, allow_synthetic=True)
    dg = prepare_data(resample(dfg, "1h"))
    s = RegimeFilteredHybrid(); s.ADX_MIN = 22
    results["GOLD H1 regime22"] = collect(s, dg, 2.85)

    dfb, _ = loader.load("BTCUSDc", 99.0, c0, csv_path=BTC_CSV, allow_synthetic=False)
    db = prepare_data(resample(dfb, "1h"))
    s = FastHybridTrendPullback(); s.ADX_MIN = 18
    results["BTC H1 adx18"] = collect(s, db, 10.0)

    dfe, _ = loader.load("ETHUSDc", 99.0, c0, csv_path=ETH_CSV, allow_synthetic=False)
    de = prepare_data(resample(dfe, "1h"))
    s = FastHybridTrendPullback(); s.ADX_MIN = 18
    results["ETH H1 adx18"] = collect(s, de, 5.0)

    for k, v in results.items():
        bucket_report(v, k)

    print("\n" + "=" * 96)
    print(" POOLED across all three markets")
    print("=" * 96)
    pooled = pd.concat(results.values(), ignore_index=True)
    bucket_report(pooled, "ALL MARKETS POOLED")

    print("\n" + "=" * 96)
    print(" OUT-OF-SAMPLE CHECK: does the best bucket stay the best in the 2nd half?")
    print("=" * 96)
    for k, v in results.items():
        print(f"\n  {k}")
        split_report(v, k)

    print("\n" + "=" * 96)
    print(" VERDICT")
    print("=" * 96)
    print("""  A real effect: mid-maturity is best in MOST markets AND stays best in the
  2nd half. Noise: the best bucket moves around between markets and halves.""")


if __name__ == "__main__":
    main()
