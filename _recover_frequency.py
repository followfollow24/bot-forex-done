#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Keep the fresh-trend filter's quality gain, recover the lost trade frequency.

The filter works (OOS-confirmed on all 3 markets) but cuts ~70% of entries:
288 -> 91 trades/yr across the three bots, i.e. 0.25/day against a target of
>=1/day. Two ways to put trades back without simply removing the filter:

  A) LOWER THE ADX GATE
     The filter and ADX both cut the same population. If the filter is doing
     the real work, a looser ADX may add trades back without giving the
     quality up -- worth testing rather than assuming ADX 18/22 is optimal.

  B) DROP TO M15 ENTRIES ON CRYPTO ONLY
     M15 produces ~4x the signals of H1. It was fatal on gold (cost/ATR ~45%)
     but BTC M15 measured 9.8% and scored the best Sharpe of anything tested
     this session (2.16). Gold stays on H1 -- its M15 cost/ATR is hopeless and
     nothing here changes that.

Both are scored with real costs and the live TP=999 manual-exit config, and
the winner has to keep a train/test split honest, not just look good overall.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import (DataLoader, prepare_data, BacktestEngine,
                             FastHybridTrendPullback, compute_metrics)
from gold_regime_filter_real_engine import RegimeFilteredHybrid
from _idea_search import resample
from _all_paths import to_monthly, perf, START
from _fresh_filter_test import FreshPullback, FreshRegime, cfg, run, line

GOLD_M15 = "download/xauusd-m15-bid-2013-01-01-2026-06-10.csv"
BTC_CSV  = "download/btcusdt-15m-binance-2017-08-17-2026-06-30.csv"
ETH_CSV  = "download/ethusdt-15m-binance-2017-08-17-2026-06-30.csv"


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START

    dfg, _ = loader.load("XAUUSD", 99.0, c0, csv_path=GOLD_M15, allow_synthetic=True)
    dfb, _ = loader.load("BTCUSDc", 99.0, c0, csv_path=BTC_CSV, allow_synthetic=False)
    dfe, _ = loader.load("ETHUSDc", 99.0, c0, csv_path=ETH_CSV, allow_synthetic=False)

    dfg_h1 = resample(dfg, "1h"); yg = (dfg_h1["timestamp"].iloc[-1]-dfg_h1["timestamp"].iloc[0]).days/365.25
    dfb_h1 = resample(dfb, "1h"); yb = (dfb_h1["timestamp"].iloc[-1]-dfb_h1["timestamp"].iloc[0]).days/365.25
    dfe_h1 = resample(dfe, "1h"); ye = (dfe_h1["timestamp"].iloc[-1]-dfe_h1["timestamp"].iloc[0]).days/365.25
    dg, db, de = prepare_data(dfg_h1), prepare_data(dfb_h1), prepare_data(dfe_h1)

    print("=" * 106)
    print(" OPTION A — keep H1 + fresh filter, LOWER the ADX gate to add trades back")
    print("=" * 106)
    for label, d, cls, sym, sp, comm, ps, pv, yrs, adx_list, base_adx in [
        ("GOLD H1 regime", dg, FreshRegime,  "XAUUSD",  2.85, 3.5, None, None, yg, [22,18,14], 22),
        ("BTC  H1",        db, FreshPullback,"BTCUSDc", 10.0, 0.0, 1.0, 0.01, yb, [18,14,10], 18),
        ("ETH  H1",        de, FreshPullback,"ETHUSDc",  5.0, 0.0, 1.0, 0.01, ye, [18,14,10], 18),
    ]:
        print(f"\n  {label}   (fresh filter maturity<=10)")
        for adx in adx_list:
            m, tr = run(cls, d, sym, sp, adx, comm, ps, pv, maxmat=10)
            line(m, tr, f"ADX>={adx} + fresh<=10", yrs)

    print("\n" + "=" * 106)
    print(" OPTION B — M15 entries on CRYPTO only, with the fresh filter")
    print("   (gold stays H1: its M15 cost/ATR is ~45% and no filter fixes that)")
    print("=" * 106)
    yb15 = (dfb["timestamp"].iloc[-1]-dfb["timestamp"].iloc[0]).days/365.25
    ye15 = (dfe["timestamp"].iloc[-1]-dfe["timestamp"].iloc[0]).days/365.25
    db15, de15 = prepare_data(dfb), prepare_data(dfe)
    for label, d, sym, sp, yrs in [("BTC M15", db15, "BTCUSDc", 10.0, yb15),
                                    ("ETH M15", de15, "ETHUSDc", 5.0, ye15)]:
        print(f"\n  {label}")
        m, tr = run(FastHybridTrendPullback, d, sym, sp, 18, 0.0, 1.0, 0.01)
        line(m, tr, "no filter", yrs)
        for th in [3, 5, 10, 20]:
            m, tr = run(FreshPullback, d, sym, sp, 18, 0.0, 1.0, 0.01, maxmat=th)
            line(m, tr, f"fresh <= {th}", yrs)

    print("\n" + "=" * 106)
    print(" OPTION B — OOS check on the M15 crypto variant (threshold from 1st half)")
    print("=" * 106)
    for label, dfx, sym, sp in [("BTC M15", dfb, "BTCUSDc", 10.0), ("ETH M15", dfe, "ETHUSDc", 5.0)]:
        mid = dfx["timestamp"].iloc[len(dfx)//2]
        tr_df = dfx[dfx["timestamp"] <= mid].reset_index(drop=True)
        te_df = dfx[dfx["timestamp"] >  mid].reset_index(drop=True)
        d_tr, d_te = prepare_data(tr_df), prepare_data(te_df)
        y_te = (te_df["timestamp"].iloc[-1]-te_df["timestamp"].iloc[0]).days/365.25
        best_th, best_pf = None, -1
        for th in [3, 5, 10, 20]:
            m, _ = run(FreshPullback, d_tr, sym, sp, 18, 0.0, 1.0, 0.01, maxmat=th)
            if m and m.get("trades",0) >= 50 and m["profit_factor"] > best_pf:
                best_pf, best_th = m["profit_factor"], th
        print(f"\n  {label}: train picked fresh<={best_th} (train PF={best_pf:.2f})")
        m, tr = run(FastHybridTrendPullback, d_te, sym, sp, 18, 0.0, 1.0, 0.01)
        line(m, tr, "TEST no filter", y_te)
        if best_th:
            m, tr = run(FreshPullback, d_te, sym, sp, 18, 0.0, 1.0, 0.01, maxmat=best_th)
            line(m, tr, f"TEST fresh<={best_th}", y_te)


if __name__ == "__main__":
    main()
