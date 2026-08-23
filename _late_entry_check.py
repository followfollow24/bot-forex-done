#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test the hypothesis: "by the time we enter, the trend is already over."

Two separate measurements, both needed to actually answer this (not just one):

  1. TREND MATURITY AT ENTRY
     How many H4 bars has the EMA50>EMA200 (or <) alignment already been true,
     continuously, by the time this specific pullback entry fires? If entries
     cluster at HIGH maturity (trend has run a long time already), that
     supports "we're late." If they cluster at LOW maturity (trend just
     started), entries are actually early/mid-trend.

  2. IMMEDIATE REVERSAL AFTER ENTRY
     Of the first few bars after entry, how often does price move AGAINST us
     before ever moving in our favour? If a large share of trades show max
     adverse excursion (MAE) bigger than max favourable excursion (MFE) within
     the first 1-3 bars, that is direct evidence entries are mistimed -- the
     move we were chasing had already exhausted.

Both measured on the current live H1 config (gold/BTC/ETH, ADX filter,
SL=3xATR), so the numbers describe exactly what's actually running.
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
LOOKAHEAD_BARS = 20   # how far forward to measure MFE/MAE evolution


def entries_with_context(strategy, d, max_bars=200):
    """Walk the series; for each fired entry, record trend-maturity and the
    bar-by-bar favourable/adverse path for the next LOOKAHEAD_BARS bars."""
    n = len(d["c"])
    strategy.precompute(d)
    trend_arr = strategy._h1_trend_arr  # +1/-1/0 per bar, already computed by precompute
    out = []
    i = strategy.MIN_BARS
    while i < n - LOOKAHEAD_BARS - 2:
        sig = strategy.signal(d, i)
        if sig.action not in ("BUY", "SELL"):
            i += 1
            continue

        # trend maturity: consecutive bars (looking back from i) with the same
        # nonzero trend sign as at i
        cur_trend = trend_arr[i] if trend_arr is not None and i < len(trend_arr) else 0
        maturity = 0
        k = i
        while k >= 0 and trend_arr is not None and k < len(trend_arr) and trend_arr[k] == cur_trend and cur_trend != 0:
            maturity += 1
            k -= 1

        atr = float(d["atr"][i])
        if not np.isfinite(atr) or atr <= 0:
            i += 1
            continue
        long_ = sig.action == "BUY"
        entry = float(d["c"][i])

        # bar-by-bar path for the next LOOKAHEAD_BARS bars
        mfe_path = []
        mae_path = []
        best_fav = 0.0
        worst_adv = 0.0
        sl_hit_bar = None
        for step in range(1, LOOKAHEAD_BARS + 1):
            j = i + step
            hi, lo = float(d["h"][j]), float(d["l"][j])
            fav = (hi - entry) if long_ else (entry - lo)
            adv = (entry - lo) if long_ else (hi - entry)
            best_fav = max(best_fav, fav)
            worst_adv = max(worst_adv, adv)
            mfe_path.append(best_fav / atr)
            mae_path.append(worst_adv / atr)
            if worst_adv >= SL_ATR * atr and sl_hit_bar is None:
                sl_hit_bar = step

        out.append(dict(maturity=maturity, mfe_path=mfe_path, mae_path=mae_path,
                        sl_hit_bar=sl_hit_bar))
        i += 1  # allow overlapping-but-distinct entries (matches live behaviour: one open per symbol at a time is a position-mgmt constraint, not a signal constraint)
    return out


def report(entries, label):
    if len(entries) < 30:
        print(f"  {label}: n={len(entries)} too few"); return
    n = len(entries)
    mat = np.array([e["maturity"] for e in entries])

    print(f"\n=== {label}  (n={n}) ===")
    print(f"  Trend maturity at entry (H4 bars the trend already held, continuously):")
    print(f"    median={np.median(mat):.0f}  p25={np.percentile(mat,25):.0f}  "
          f"p75={np.percentile(mat,75):.0f}  max={mat.max():.0f}")
    print(f"    entries with maturity <=3 bars (trend JUST started)   : {(mat<=3).mean()*100:.1f}%")
    print(f"    entries with maturity >=20 bars (trend already mature): {(mat>=20).mean()*100:.1f}%")
    print(f"    entries with maturity >=50 bars (trend very old)      : {(mat>=50).mean()*100:.1f}%")

    # immediate reversal: within first 3 bars, is MAE > MFE (moved against us
    # more than in our favour before either resolves)?
    immediate_against = 0
    for e in entries:
        mfe3 = e["mfe_path"][2] if len(e["mfe_path"]) > 2 else e["mfe_path"][-1]
        mae3 = e["mae_path"][2] if len(e["mae_path"]) > 2 else e["mae_path"][-1]
        if mae3 > mfe3 and mae3 > 0.3:
            immediate_against += 1
    print(f"  Within first 3 bars, moved against us more than in our favour "
          f"(>0.3xATR adverse): {immediate_against/n*100:.1f}%")

    # how the average path evolves bar by bar (rising MFE = trend continuing;
    # flat/falling relative position = chasing an exhausted move)
    avg_mfe = np.mean([e["mfe_path"] for e in entries], axis=0)
    avg_mae = np.mean([e["mae_path"] for e in entries], axis=0)
    print(f"  Avg MFE by bars-after-entry: "
          + " ".join(f"b{k+1}={avg_mfe[k]:.2f}" for k in [0,2,4,9,19]))
    print(f"  Avg MAE by bars-after-entry: "
          + " ".join(f"b{k+1}={avg_mae[k]:.2f}" for k in [0,2,4,9,19]))

    # correlation: does higher trend-maturity at entry predict worse outcome?
    final_mfe = np.array([e["mfe_path"][-1] for e in entries])
    corr = np.corrcoef(mat, final_mfe)[0, 1]
    print(f"  Correlation(trend maturity at entry, eventual MFE): {corr:+.3f}"
          f"  {'(negative = later entries do WORSE, supports your hypothesis)' if corr < -0.1 else ''}"
          f"  {'(no meaningful relationship)' if -0.1 <= corr <= 0.1 else ''}")


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = 10000

    dfg, _ = loader.load("XAUUSD", 99.0, c0, csv_path=GOLD_M15, allow_synthetic=True)
    dfg_h1 = resample(dfg, "1h")
    dfg_h1 = dfg_h1[dfg_h1["timestamp"] >= pd.Timestamp("2023-01-01")].reset_index(drop=True)
    dg = prepare_data(dfg_h1)
    s = RegimeFilteredHybrid(); s.ADX_MIN = 22
    report(entries_with_context(s, dg), "GOLD H1 (regime22 config)")

    dfb, _ = loader.load("BTCUSDc", 99.0, c0, csv_path=BTC_CSV, allow_synthetic=False)
    db = prepare_data(resample(dfb, "1h"))
    s = FastHybridTrendPullback(); s.ADX_MIN = 18
    report(entries_with_context(s, db), "BTC H1")

    dfe, _ = loader.load("ETHUSDc", 99.0, c0, csv_path=ETH_CSV, allow_synthetic=False)
    de = prepare_data(resample(dfe, "1h"))
    s = FastHybridTrendPullback(); s.ADX_MIN = 18
    report(entries_with_context(s, de), "ETH H1")


if __name__ == "__main__":
    main()
