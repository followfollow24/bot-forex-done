#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fetch more DAILY markets to widen the portfolio beyond the current 12.

Priority is deliberately NON-crypto. The modern-window portfolio scored
Sharpe 1.45 but crypto-only was 1.53 while non-crypto was only 0.63 -- the
result leans on crypto in a historically favourable era. Adding more crypto
pairs would make that concentration worse, not better. What the portfolio
actually needs is more uncorrelated non-crypto trend markets.

Chosen for genuinely different drivers: rates/bonds, ags, industrial and
precious metals, more equity indices and FX crosses.
"""
from __future__ import annotations
import os, sys, time
import pandas as pd
import yfinance as yf

OUT = "download"

TICKERS = {
    # bonds / rates -- different driver from everything we hold
    "US10Y":   "ZN=F",     # 10-year T-Note future
    "US30Y":   "ZB=F",     # 30-year T-Bond future
    # metals
    "SILVER":  "SI=F",
    "COPPER":  "HG=F",
    "PLAT":    "PL=F",
    # energy
    "NATGAS":  "NG=F",
    "BRENT":   "BZ=F",
    # ags -- classic trend-following markets, uncorrelated to financials
    "CORN":    "ZC=F",
    "WHEAT":   "ZW=F",
    "SOYBEAN": "ZS=F",
    "SUGAR":   "SB=F",
    "COFFEE":  "KC=F",
    "COTTON":  "CT=F",
    # more equity indices
    "DAX":     "^GDAXI",
    "NIKKEI":  "^N225",
    "FTSE":    "^FTSE",
    "RUSSELL": "^RUT",
    "HSI":     "^HSI",
    # more FX
    "USDCHF":  "CHF=X",
    "NZDUSD":  "NZDUSD=X",
    "EURJPY":  "EURJPY=X",
    "GBPJPY":  "GBPJPY=X",
    # vol
    "VIX":     "^VIX",
}


def main():
    os.makedirs(OUT, exist_ok=True)
    ok, fail = [], []
    for name, tk in TICKERS.items():
        path = os.path.join(OUT, f"{name.lower()}-daily-yahoo.csv")
        if os.path.exists(path) and os.path.getsize(path) > 50_000:
            print(f"  {name:<9} already present, skipping")
            ok.append(name)
            continue
        try:
            df = yf.download(tk, start="2005-01-01", progress=False, auto_adjust=False)
            if df is None or len(df) < 500:
                print(f"  {name:<9} too little data ({0 if df is None else len(df)})")
                fail.append(name); continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            out = pd.DataFrame({
                "timestamp": df.index,
                "open":  df["Open"].values,
                "high":  df["High"].values,
                "low":   df["Low"].values,
                "close": df["Close"].values,
            }).dropna()
            out = out[(out[["open", "high", "low", "close"]] > 0).all(axis=1)]
            out.to_csv(path, index=False)
            print(f"  {name:<9} {len(out):>6} bars  {out['timestamp'].iloc[0].date()} -> {out['timestamp'].iloc[-1].date()}")
            ok.append(name)
        except Exception as e:
            print(f"  {name:<9} FAILED: {e}")
            fail.append(name)
        time.sleep(0.4)
    print(f"\n  fetched {len(ok)}, failed {len(fail)}")
    if fail:
        print(f"  failed: {', '.join(fail)}")


if __name__ == "__main__":
    main()
