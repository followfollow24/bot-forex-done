#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# fetch_binance_history.py
# =============================================================================
# Download deep BTC (or any pair) OHLC history from Binance public data
# (data.binance.vision) and save it in the CSV format backtest_forex.py reads:
#     timestamp,open,high,low,close
#
# Runs ON THE MAC (needs internet + pandas). No MT5, no VPS, no RDP.
# Public data, no API key. Downloads monthly kline zips and concatenates them.
#
# =============================================================================
# *** PRICE SHAPE ONLY -- DO NOT USE FOR SPREAD / COST / PF ***
# =============================================================================
# Binance spot price is fine for testing STRATEGY LOGIC (trend, entry timing,
# SL/TP hit sequence) because for a liquid asset like BTC the Binance mid price
# tracks the Exness mid price closely. BUT the spread, commission and swap on
# Exness BTCUSDc are DIFFERENT and MUST be applied separately in the backtest
# cost model. Never derive PF / expectancy / net return from Binance-implied
# costs -- take the real BTCUSDc spread/tick value from MT5 symbol_info first
# (same lesson as the gold pip_value unit bug). The '-binance-' tag in the
# output filename is a deliberate reminder that this file is shape-only.
# =============================================================================
#
# Usage:
#     python3 fetch_binance_history.py                       # BTCUSDT 15m, 2017-08 -> now
#     python3 fetch_binance_history.py --symbol BTCUSDT --interval 15m --start 2017-08
#     python3 fetch_binance_history.py --symbol ETHUSDT --interval 5m  --start 2019-01
# =============================================================================
import argparse
import io
import os
import ssl
import sys
import zipfile
import urllib.request
import urllib.error
from datetime import datetime, date

try:
    import pandas as pd
except ImportError:
    sys.exit("[ERROR] pandas not found -- pip3 install pandas")

# macOS Python often ships without a usable CA bundle -> SSL verify fails.
# Prefer certifi's bundle; fall back to an unverified context (acceptable for
# fetching public read-only price data from data.binance.vision).
try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    _SSL_CTX = ssl._create_unverified_context()

BASE = "https://data.binance.vision/data/spot"
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(_BASE_DIR, "download")
CACHE_DIR = os.path.join(OUT_DIR, "_binance_cache")

# Binance kline CSV columns (no header row in the files)
KLINE_COLS = ["open_time", "open", "high", "low", "close", "volume",
              "close_time", "quote_volume", "trades",
              "taker_base", "taker_quote", "ignore"]


def month_iter(start_ym, end_ym):
    """Yield (year, month) from start_ym=(y,m) to end_ym=(y,m) inclusive."""
    y, m = start_ym
    ey, em = end_ym
    while (y, m) <= (ey, em):
        yield y, m
        m += 1
        if m > 12:
            m = 1
            y += 1


def download(url, cache_path):
    """Return raw bytes for url, using a local cache. None on 404/missing."""
    if os.path.exists(cache_path) and os.path.getsize(cache_path) > 0:
        with open(cache_path, "rb") as f:
            return f.read()
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (data-fetch)"})
    try:
        with urllib.request.urlopen(req, timeout=60, context=_SSL_CTX) as resp:
            data = resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "wb") as f:
        f.write(data)
    return data


def parse_zip(raw):
    """Extract the single CSV from a Binance kline zip -> normalized DataFrame."""
    zf = zipfile.ZipFile(io.BytesIO(raw))
    content = zf.read(zf.namelist()[0])
    # Some 2025+ files include a header row; older ones do not. Detect it.
    if content[:9].lower().startswith(b"open_time"):
        df = pd.read_csv(io.BytesIO(content))
        df = df.rename(columns={c: str(c).strip().lower() for c in df.columns})
    else:
        df = pd.read_csv(io.BytesIO(content), header=None, names=KLINE_COLS)
    # keep only what we need
    df = df[["open_time", "open", "high", "low", "close"]].copy()

    # open_time epoch unit detection (Binance switched ms -> us for newer data):
    # seconds ~1e9 (10 digits), ms ~1e12 (13), us ~1e15 (16)
    sample = float(df["open_time"].iloc[0])
    if sample > 1e15:
        unit = "us"
    elif sample > 1e12:
        unit = "ms"
    else:
        unit = "s"
    ts = pd.to_datetime(df["open_time"], unit=unit, utc=True).dt.tz_localize(None)
    # Force a single resolution (ns). Different months can arrive as ms vs us
    # epoch; mixing datetime64 resolutions makes concat fall back to object
    # dtype, which then serialises timestamps inconsistently (some with .000).
    df["timestamp"] = ts.to_numpy().astype("datetime64[ns]")
    return df[["timestamp", "open", "high", "low", "close"]]


def main():
    ap = argparse.ArgumentParser(description="Download Binance OHLC -> backtest CSV (shape only)")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--interval", default="15m",
                    help="Binance interval: 1m,5m,15m,30m,1h,4h,1d ...")
    ap.add_argument("--start", default="2017-08", help="YYYY-MM (BTCUSDT 15m starts 2017-08)")
    ap.add_argument("--end", default=None, help="YYYY-MM (default: current month)")
    args = ap.parse_args()

    sy, sm = map(int, args.start.split("-"))
    if args.end:
        ey, em = map(int, args.end.split("-"))
    else:
        today = date.today()
        ey, em = today.year, today.month

    print("=" * 64)
    print("  *** PRICE SHAPE ONLY -- spread/cost/PF must come from Exness ***")
    print("=" * 64)
    print(f"  symbol={args.symbol}  interval={args.interval}  "
          f"range={sy:04d}-{sm:02d} -> {ey:04d}-{em:02d}")
    print(f"  source: {BASE}/monthly/klines/{args.symbol}/{args.interval}/")
    print()

    frames = []
    missing = []
    for (y, m) in month_iter((sy, sm), (ey, em)):
        fname = f"{args.symbol}-{args.interval}-{y:04d}-{m:02d}.zip"
        url = f"{BASE}/monthly/klines/{args.symbol}/{args.interval}/{fname}"
        cache = os.path.join(CACHE_DIR, fname)
        try:
            raw = download(url, cache)
        except Exception as exc:
            print(f"  {y:04d}-{m:02d}  ERROR {exc}")
            missing.append((y, m))
            continue
        if raw is None:
            # monthly not published yet (usually the current month) -> note & skip
            print(f"  {y:04d}-{m:02d}  (no monthly file -- likely current month, skipping)")
            missing.append((y, m))
            continue
        try:
            df = parse_zip(raw)
            frames.append(df)
            print(f"  {y:04d}-{m:02d}  {len(df):>6,} bars")
        except Exception as exc:
            print(f"  {y:04d}-{m:02d}  PARSE ERROR {exc}")
            missing.append((y, m))

    if not frames:
        sys.exit("[ERROR] downloaded nothing -- check symbol/interval/start")

    full = pd.concat(frames, ignore_index=True)
    full = full.drop_duplicates(subset="timestamp").sort_values("timestamp")
    for c in ["open", "high", "low", "close"]:
        full[c] = pd.to_numeric(full[c], errors="coerce")
    full = full.dropna()

    first, last = full["timestamp"].iloc[0], full["timestamp"].iloc[-1]
    span_days = (last - first).days
    years = span_days / 365.25
    minutes = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}
    per_day_full = (24 * 60) / minutes.get(args.interval, 15)
    coverage = (len(full) / max(span_days, 1)) / per_day_full * 100

    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(
        OUT_DIR,
        f"{args.symbol.lower()}-{args.interval}-binance-"
        f"{first.strftime('%Y-%m-%d')}-{last.strftime('%Y-%m-%d')}.csv")
    save_df = full[["timestamp", "open", "high", "low", "close"]].copy()
    # Serialise timestamps in one uniform format (15m bars are always on whole
    # minutes, so no sub-second info is lost).
    save_df["timestamp"] = pd.to_datetime(save_df["timestamp"]).dt.strftime("%Y-%m-%d %H:%M:%S")
    save_df.to_csv(out, index=False)

    print("\n" + "=" * 64)
    print(f"  bars       : {len(full):,}")
    print(f"  first      : {first}")
    print(f"  last       : {last}")
    print(f"  span       : {span_days:,} days  (~{years:.2f} years)")
    print(f"  coverage   : ~{coverage:.0f}% of full 24/7 bars")
    print(f"  timezone   : UTC (Binance). Live bot uses broker SERVER time")
    print(f"               (~UTC+2/3) -- minor intraday-session offset; fine for")
    print(f"               24/7 trend logic, note it if you add session filters.")
    if missing:
        print(f"  missing    : {len(missing)} month(s) skipped (e.g. current month)")
    print(f"  saved -> {out}")
    print("=" * 64)
    print("  REMINDER: this is SHAPE ONLY. Apply real BTCUSDc spread/commission")
    print("  from MT5 symbol_info before trusting any PF / expectancy number.")
    print("=" * 64)


if __name__ == "__main__":
    main()
