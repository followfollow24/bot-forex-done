#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scalp_recon.py -- Phase 0 reconnaissance for the 3-SMA scalping research task.
RUN ON THE VPS ONLY (needs the live MT5 terminal).

For each candidate low-spread pair, reports:
  1. Exact broker symbol name (Exness Cent suffix, e.g. "c")
  2. Live spread (points and price), tick value/size (real cost model inputs)
  3. Commission (if any)
  4. M1 and M5 history depth actually available in this terminal (bars, years,
     coverage%) -- this determines whether Phase 1 (M1/M5 scalping grid) is
     even feasible before any backtest code is written.

Does NOT place orders. Read-only (symbol_info + copy_rates_from_pos probe).
ASCII-only (ASCII output to survive the Windows console code page).
"""
import sys
import os

try:
    import MetaTrader5 as mt5
except ImportError:
    sys.exit("[ERROR] MetaTrader5 package not found -- run this on the VPS.")

import pandas as pd

CANDIDATES = ["EURUSD", "USDJPY", "GBPUSD", "AUDUSD", "USDCAD", "NZDUSD"]
TF_MAP = {"M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5}
PROBE_COUNT = 600_000  # generous upper bound; MT5 caps at whatever the terminal actually has


def resolve_symbol(base):
    """Find the exact broker symbol name for a base pair (handles c/m/. suffixes)."""
    all_syms = mt5.symbols_get()
    if not all_syms:
        return None
    names = [s.name for s in all_syms]
    if base in names:
        return base
    for n in names:
        if n.upper().startswith(base.upper()) and len(n) <= len(base) + 2:
            return n
    return None


def depth_probe(symbol, tf_key):
    tf = TF_MAP[tf_key]
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, PROBE_COUNT)
    if rates is None or len(rates) == 0:
        return None
    df = pd.DataFrame(rates)
    df["timestamp"] = pd.to_datetime(df["time"], unit="s")
    first, last = df["timestamp"].iloc[0], df["timestamp"].iloc[-1]
    span_days = (last - first).days
    years = span_days / 365.25
    return dict(bars=len(df), first=first, last=last, years=years)


def main():
    if not mt5.initialize():
        sys.exit(f"[ERROR] mt5.initialize() failed: {mt5.last_error()}")

    ai = mt5.account_info()
    print(f"[OK] connected: login={ai.login if ai else None}  "
          f"server={ai.server if ai else None}")
    print("=" * 100)
    print(" SCALP RECON -- symbol resolution, live cost model, M1/M5 depth")
    print("=" * 100)

    rows = []
    for base in CANDIDATES:
        sym = resolve_symbol(base)
        if sym is None:
            print(f"\n{base}: NOT FOUND on this account -- skipping")
            rows.append(dict(base=base, symbol=None))
            continue
        mt5.symbol_select(sym, True)
        si = mt5.symbol_info(sym)
        if si is None:
            print(f"\n{base} -> {sym}: symbol_info() returned None -- skipping")
            rows.append(dict(base=base, symbol=sym))
            continue

        point = si.point
        spread_price = si.spread * point
        print(f"\n{base} -> broker symbol: {sym}")
        print(f"  spread          : {si.spread} points = {spread_price:.5f} price  "
              f"(float={si.spread_float})")
        print(f"  bid/ask         : {si.bid} / {si.ask}")
        print(f"  digits/point    : {si.digits} / {point}")
        print(f"  tick_size/value : {si.trade_tick_size} / {si.trade_tick_value}")
        print(f"  volume min/step : {si.volume_min} / {si.volume_step}")
        # commission is not exposed on symbol_info directly on most builds;
        # flag for manual check via a real order fill if this matters.
        print(f"  trade_mode      : {si.trade_mode}  (4=FULL)")

        depth = {}
        for tf_key in ("M1", "M5"):
            d = depth_probe(sym, tf_key)
            depth[tf_key] = d
            if d is None:
                print(f"  {tf_key} depth      : NO DATA")
            else:
                print(f"  {tf_key} depth      : {d['bars']:,} bars  "
                      f"{d['first']} -> {d['last']}  (~{d['years']:.2f} yr)")

        rows.append(dict(base=base, symbol=sym, spread_points=si.spread,
                          spread_price=spread_price, tick_value=si.trade_tick_value,
                          tick_size=si.trade_tick_size, m1_years=depth["M1"]["years"] if depth["M1"] else 0,
                          m5_years=depth["M5"]["years"] if depth["M5"] else 0,
                          m1_bars=depth["M1"]["bars"] if depth["M1"] else 0,
                          m5_bars=depth["M5"]["bars"] if depth["M5"] else 0))

    print("\n" + "=" * 100)
    print(" SUMMARY")
    print("=" * 100)
    print(f"  {'pair':<8} {'symbol':<10} {'spread_pts':>10} {'spread_price':>13} "
          f"{'M1_years':>9} {'M1_bars':>10} {'M5_years':>9} {'M5_bars':>10}")
    for r in rows:
        if r.get("symbol") is None:
            print(f"  {r['base']:<8} NOT FOUND")
            continue
        print(f"  {r['base']:<8} {r['symbol']:<10} {r['spread_points']:>10} "
              f"{r['spread_price']:>13.5f} {r['m1_years']:>9.2f} {r['m1_bars']:>10,} "
              f"{r['m5_years']:>9.2f} {r['m5_bars']:>10,}")
    print("=" * 100)
    mt5.shutdown()


if __name__ == "__main__":
    main()
