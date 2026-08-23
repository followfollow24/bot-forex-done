#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fetch more crypto pairs (1h) from Binance via ccxt.

Rationale: the one clearly strong edge found in this whole search is the
pullback strategy on BTC intraday (M15 Sharpe 2.16, H1 1.65). The reason it
works there and died on gold is cost/ATR -- ~10% on BTC vs ~45% on gold. Crypto
intraday is therefore the only regime found where this signal family clears its
own costs comfortably, and we only have two instruments in it.

H1 rather than M15: it was validated too (Sharpe 1.65), needs 4x less data per
year of history, and the cost/ATR advantage still holds.

The obvious risk, which the test after this must measure rather than assume:
altcoins are highly correlated with BTC, so ten more crypto edges may be far
fewer than ten independent bets.
"""
from __future__ import annotations
import os, sys, time
import pandas as pd
import ccxt

OUT = "download"
TIMEFRAME = "1h"
SINCE = "2019-01-01T00:00:00Z"
SYMBOLS = ["SOL/USDT", "BNB/USDT", "XRP/USDT", "ADA/USDT", "DOGE/USDT",
           "AVAX/USDT", "LINK/USDT", "DOT/USDT", "LTC/USDT", "ATOM/USDT",
           "UNI/USDT", "AAVE/USDT"]


def fetch(ex, sym, since_ms, limit=1000):
    rows, cur = [], since_ms
    now = ex.milliseconds()
    while cur < now:
        try:
            batch = ex.fetch_ohlcv(sym, TIMEFRAME, since=cur, limit=limit)
        except Exception as e:
            print(f"    retry after error: {e}")
            time.sleep(3)
            continue
        if not batch:
            break
        rows += batch
        nxt = batch[-1][0] + 1
        if nxt <= cur:
            break
        cur = nxt
        time.sleep(ex.rateLimit / 1000)
        if len(batch) < limit:
            break
    return rows


def main():
    os.makedirs(OUT, exist_ok=True)
    ex = ccxt.binance({"enableRateLimit": True})
    since_ms = ex.parse8601(SINCE)
    ok, fail = [], []
    for sym in SYMBOLS:
        name = sym.split("/")[0].lower()
        path = os.path.join(OUT, f"{name}usdt-1h-binance.csv")
        if os.path.exists(path) and os.path.getsize(path) > 200_000:
            print(f"  {sym:<12} already present")
            ok.append(name); continue
        print(f"  {sym:<12} fetching...")
        try:
            rows = fetch(ex, sym, since_ms)
            if len(rows) < 5000:
                print(f"    too few bars ({len(rows)})"); fail.append(name); continue
            df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "vol"])
            df = df.drop_duplicates(subset="timestamp").sort_values("timestamp")
            df[["timestamp", "open", "high", "low", "close"]].to_csv(path, index=False)
            t0 = pd.to_datetime(df["timestamp"].iloc[0], unit="ms").date()
            t1 = pd.to_datetime(df["timestamp"].iloc[-1], unit="ms").date()
            print(f"    {len(df):>7} bars  {t0} -> {t1}")
            ok.append(name)
        except Exception as e:
            print(f"    FAILED: {e}")
            fail.append(name)
    print(f"\n  ok={len(ok)}  fail={len(fail)}")
    if fail:
        print(f"  failed: {', '.join(fail)}")


if __name__ == "__main__":
    main()
