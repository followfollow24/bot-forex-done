#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Apply the exact recipe that worked best (M15 entries + fresh-trend filter,
maturity<=5, ADX>=18, SL=3xATR, TP disabled) to every crypto pair already on
disk, unchanged. BTC scored Sharpe 2.38 / CAGR 16-28%/yr this way; the
question is whether that was a BTC-specific fluke or the recipe generalises.

Data: the 12 altcoins fetched earlier are 1H only (Binance via ccxt). Need M15
for this test -- fetch M15 for whichever don't already have it. BTC/ETH already
have M15 from the original 15m-binance files.

Costs: real spread inferred as bps-of-price (8bps baseline, matching the H1
crypto test earlier), since we don't have MT5-verified spreads for every
altcoin at every broker. Flagged explicitly -- BTC/ETH numbers ARE
broker-verified ($10 / $1), everything else here is an estimate.
"""
from __future__ import annotations
import os, sys, time
import numpy as np
import pandas as pd
import ccxt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import DataLoader, prepare_data, compute_metrics
from _all_paths import to_monthly, perf, START
from _fresh_filter_test import FreshPullback, cfg, run, line

OUT = "download"
SYMBOLS = ["SOL/USDT", "BNB/USDT", "XRP/USDT", "ADA/USDT", "DOGE/USDT",
           "AVAX/USDT", "LINK/USDT", "DOT/USDT", "LTC/USDT", "ATOM/USDT",
           "UNI/USDT", "AAVE/USDT"]
SPREAD_BPS = 8.0


def fetch_m15(ex, sym, since_ms, limit=1000):
    rows, cur = [], since_ms
    now = ex.milliseconds()
    while cur < now:
        try:
            batch = ex.fetch_ohlcv(sym, "15m", since=cur, limit=limit)
        except Exception as e:
            time.sleep(3); continue
        if not batch:
            break
        rows += batch
        nxt = batch[-1][0] + 1
        if nxt <= cur:
            break
        cur = nxt
        time.sleep(ex.rateLimit / 1000)
    return rows


def ensure_m15():
    ex = ccxt.binance({"enableRateLimit": True})
    since_ms = ex.parse8601("2021-01-01T00:00:00Z")   # 5.5y is plenty and much faster than full history
    for sym in SYMBOLS:
        name = sym.split("/")[0].lower()
        path = os.path.join(OUT, f"{name}usdt-15m-binance.csv")
        if os.path.exists(path) and os.path.getsize(path) > 500_000:
            continue
        print(f"  fetching M15 for {sym}...")
        rows = fetch_m15(ex, sym, since_ms)
        if len(rows) < 5000:
            print(f"    too few bars ({len(rows)}), skipping"); continue
        df = pd.DataFrame(rows, columns=["timestamp","open","high","low","close","vol"])
        df = df.drop_duplicates(subset="timestamp").sort_values("timestamp")
        df[["timestamp","open","high","low","close"]].to_csv(path, index=False)
        print(f"    saved {len(df):,} bars")


def load_m15(name):
    path = os.path.join(OUT, f"{name}usdt-15m-binance.csv")
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df


def scale_for(px_med):
    if px_med > 1000:  return 1.0, 0.01
    if px_med > 100:   return 0.1, 0.1
    if px_med > 1:     return 0.01, 1.0
    return 0.0001, 100.0


def main():
    print("ensuring M15 data for all altcoins (this may take a few minutes)...")
    ensure_m15()

    loader = DataLoader(log_fn=lambda *a, **k: None)

    print("\n" + "=" * 108)
    print(" M15 + fresh-trend filter (maturity<=5, ADX>=18) -- same recipe as BTC, unchanged")
    print(f" cost = {SPREAD_BPS}bps/side (ESTIMATE for altcoins -- BTC/ETH numbers elsewhere are MT5-verified)")
    print("=" * 108)
    print(f"  {'coin':<7}{'bars':>9}{'trades':>8}{'PF':>7}{'win%':>7}{'Sharpe(mo)':>12}"
          f"{'CAGR%/yr':>10}{'DD%':>7}{'trades/yr':>11}")

    series = {}
    for sym in SYMBOLS:
        name = sym.split("/")[0].lower()
        path = os.path.join(OUT, f"{name}usdt-15m-binance.csv")
        if not os.path.exists(path):
            print(f"  {name.upper():<7}  no data"); continue
        df = load_m15(name)
        if len(df) < 5000:
            print(f"  {name.upper():<7}  too few bars"); continue
        d = prepare_data(df)
        px_med = float(df["close"].median())
        ps, pv = scale_for(px_med)
        spread = px_med * SPREAD_BPS / 1e4
        yrs = (df["timestamp"].iloc[-1] - df["timestamp"].iloc[0]).days / 365.25

        m, tr = run(FreshPullback, d, name.upper(), spread, 18, comm=0.0, ps=ps, pv=pv, maxmat=5)
        if not m or m.get("trades", 0) < 50:
            print(f"  {name.upper():<7}{len(df):>9}{m.get('trades',0) if m else 0:>8}  too few trades")
            continue
        tot = m["total_return_pct"]
        cagr = -100.0 if tot <= -100 else ((1+tot/100)**(1/yrs)-1)*100
        mr = to_monthly(tr)
        p = perf(mr)
        sh = p["sharpe"] if p else float("nan")
        star = "  <== Sharpe>1.5" if p and p["sharpe"] > 1.5 else ""
        print(f"  {name.upper():<7}{len(df):>9}{m['trades']:>8}{m['profit_factor']:>7.2f}"
              f"{m['win_rate']*100:>7.1f}{sh:>12.2f}{cagr:>10.2f}{m['max_dd_pct']:>7.1f}"
              f"{m['trades']/yrs:>11.0f}{star}")
        if p and p["sharpe"] > 0.5 and m["profit_factor"] > 1.0 and len(mr) >= 24:
            series[name.upper()] = mr

    print(f"\n  qualifying (Sharpe>0.5, PF>1): {len(series)} -> {', '.join(series.keys())}")

    if len(series) >= 2:
        allm = pd.concat(series, axis=1, sort=True)
        corr = allm.corr()
        iu = np.triu_indices_from(corr.values, k=1)
        v = corr.values[iu]; v = v[~np.isnan(v)]
        print(f"\n  mean pairwise correlation (new altcoins only): {v.mean():+.3f}")

        print("\n" + "=" * 108)
        print(" ADD THE BEST NEW ALTCOINS TO THE EXISTING BTC+ETH+GOLD PORTFOLIO")
        print("=" * 108)
        # rebuild BTC/ETH/GOLD monthly series at their validated configs for comparison
        from _idea_search import resample
        from _fresh_filter_test import FreshRegime
        BTC_CSV = "download/btcusdt-15m-binance-2017-08-17-2026-06-30.csv"
        ETH_CSV = "download/ethusdt-15m-binance-2017-08-17-2026-06-30.csv"
        GOLD_M15 = "download/xauusd-m15-bid-2013-01-01-2026-06-10.csv"
        c0 = ForexConfig(); c0.total_capital_usd = START
        dfb, _ = loader.load("BTCUSDc", 99.0, c0, csv_path=BTC_CSV, allow_synthetic=False)
        dfe, _ = loader.load("ETHUSDc", 99.0, c0, csv_path=ETH_CSV, allow_synthetic=False)
        dfg, _ = loader.load("XAUUSD", 99.0, c0, csv_path=GOLD_M15, allow_synthetic=True)
        m, tr = run(FreshPullback, prepare_data(dfb), "BTCUSDc", 10.0, 18, comm=0.0, ps=1.0, pv=0.01, maxmat=5)
        series["BTC(verified)"] = to_monthly(tr)
        m, tr = run(FreshPullback, prepare_data(dfe), "ETHUSDc", 1.0, 18, comm=0.0, ps=1.0, pv=0.01, maxmat=3)
        series["ETH(verified)"] = to_monthly(tr)
        s = FreshRegime(); s.ADX_MIN = 22
        m, tr = run(FreshRegime, prepare_data(resample(dfg, "1h")), "XAUUSD", 2.85, 22, comm=3.5, maxmat=10)
        series["GOLD(verified)"] = to_monthly(tr)

        full = pd.concat(series, axis=1, sort=True).dropna()
        print(f"  overlapping months across ALL series: {len(full)}")
        port = full.mean(axis=1)
        p = perf(port)
        print(f"  ALL-COMBINED portfolio: n_edges={full.shape[1]}  Sharpe={p['sharpe']:.2f}  "
              f"CAGR={p['cagr']:+.2f}%  DD={p['dd']:.2f}%")


if __name__ == "__main__":
    main()
