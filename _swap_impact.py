#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Close the biggest untested gap before deploying: SWAP.

The backtest engine does not model swap at all. reference_btcusdc_specs.md
records Exness BTCUSDc as swap_long -6.9%/yr, swap_short 0 -- i.e. an
asymmetric overnight financing cost that only hits long positions. The proposed
config trades M15 with 156-425 entries/yr and no take-profit, so positions are
held for a while and many cross midnight. If the average hold is long enough,
that -6.9%/yr can eat a meaningful share of a +16%/yr edge.

This measures, from the actual backtest trades:
  1. average and median hold time, and what share of trades are long
  2. total nights held per year
  3. the resulting swap drag in %/yr
  4. CAGR before vs after that drag

If the drag is small the plan survives; if it is large the M15 crypto variant
needs rethinking (e.g. short-biased, or a hold-time cap).
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import (DataLoader, prepare_data, BacktestEngine,
                             FastHybridTrendPullback, compute_metrics)
from _fresh_filter_test import FreshPullback, cfg

BTC_CSV = "download/btcusdt-15m-binance-2017-08-17-2026-06-30.csv"
ETH_CSV = "download/ethusdt-15m-binance-2017-08-17-2026-06-30.csv"
START = 10_000.0

# reference_btcusdc_specs.md: swap_long -6.9%/yr, swap_short 0 (asymmetric)
SWAP_LONG_PCT_PER_YEAR = -6.9
SWAP_SHORT_PCT_PER_YEAR = 0.0


def run(cls, d, sym, spread, adx, maxmat=None, ps=1.0, pv=0.01):
    s = cls()
    s.ADX_MIN = adx
    if maxmat is not None:
        s.MAX_MATURITY = maxmat
    s.sl_atr, s.tp_atr = 3.0, 999.0
    s.trail_atr_mult = s.trail_activation_atr = 999.0
    s.precompute(d)
    eng = BacktestEngine(d, cfg(sym, ps, pv), s, spread_price=spread,
                          commission_per_lot=0.0, symbol=sym)
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START), eng.trades


def analyse(trades, years, label, cagr_before):
    if not trades:
        print(f"  {label}: no trades"); return
    holds, longs, nights = [], 0, 0
    for t in trades:
        try:
            a = pd.Timestamp(t["entry_ts"]); b = pd.Timestamp(t["exit_ts"])
        except Exception:
            continue
        hrs = (b - a).total_seconds() / 3600.0
        if hrs < 0:
            continue
        holds.append(hrs)
        is_long = t.get("side") == "long"
        if is_long:
            longs += 1
            # count midnights crossed
            nights += max(0, (b.normalize() - a.normalize()).days)
    if not holds:
        print(f"  {label}: no usable timestamps"); return
    holds = np.array(holds)
    n = len(holds)
    long_pct = longs / n * 100

    # swap drag: a position open for H hours pays SWAP_PCT * (H/24/365) of its
    # notional. Risk-based sizing means notional is not constant, so express the
    # drag as a fraction of the year the book spends holding longs.
    long_hours = sum(h for h, t in zip(holds, trades) if t.get("side") == "long")
    years_long_exposure = long_hours / 24.0 / 365.0
    drag_pct_total = abs(SWAP_LONG_PCT_PER_YEAR) * years_long_exposure
    drag_pct_per_year = drag_pct_total / years

    print(f"\n  {label}")
    print(f"    trades={n}  long={long_pct:.0f}%  short={100-long_pct:.0f}%")
    print(f"    hold time: median={np.median(holds):.1f}h  mean={holds.mean():.1f}h  "
          f"max={holds.max():.0f}h")
    print(f"    midnights crossed by longs: {nights}  ({nights/years:.0f}/yr)")
    print(f"    long exposure = {years_long_exposure:.2f} position-years over {years:.1f}y")
    print(f"    swap drag  = {drag_pct_per_year:+.2f}%/yr   (at {SWAP_LONG_PCT_PER_YEAR}%/yr on longs)")
    print(f"    CAGR before swap = {cagr_before:+.2f}%/yr")
    print(f"    CAGR AFTER swap  = {cagr_before - drag_pct_per_year:+.2f}%/yr"
          f"   {'<-- still positive' if cagr_before - drag_pct_per_year > 0 else '<-- TURNS NEGATIVE'}")


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START

    print("=" * 96)
    print(" SWAP IMPACT on the proposed M15 crypto config")
    print(f" (swap_long {SWAP_LONG_PCT_PER_YEAR}%/yr, swap_short {SWAP_SHORT_PCT_PER_YEAR}%/yr — asymmetric)")
    print("=" * 96)

    dfb, _ = loader.load("BTCUSDc", 99.0, c0, csv_path=BTC_CSV, allow_synthetic=False)
    yb = (dfb["timestamp"].iloc[-1] - dfb["timestamp"].iloc[0]).days / 365.25
    db = prepare_data(dfb)
    m, tr = run(FreshPullback, db, "BTCUSDc", 10.0, 18, maxmat=5)
    cg = ((1 + m["total_return_pct"]/100) ** (1/yb) - 1) * 100
    analyse(tr, yb, "BTC M15 + fresh<=5", cg)

    dfe, _ = loader.load("ETHUSDc", 99.0, c0, csv_path=ETH_CSV, allow_synthetic=False)
    ye = (dfe["timestamp"].iloc[-1] - dfe["timestamp"].iloc[0]).days / 365.25
    de = prepare_data(dfe)
    m, tr = run(FreshPullback, de, "ETHUSDc", 5.0, 18, maxmat=3)
    cg = ((1 + m["total_return_pct"]/100) ** (1/ye) - 1) * 100
    analyse(tr, ye, "ETH M15 + fresh<=3", cg)

    print("\n" + "=" * 96)
    print(" SENSITIVITY — what if the real spread is worse than assumed?")
    print("=" * 96)
    print(f"  {'config':<26}{'spread':>9}{'PF':>7}{'CAGR%':>9}{'DD%':>8}")
    for lbl, d, sym, base_sp, mm, yrs in [
        ("BTC M15 fresh<=5", db, "BTCUSDc", 10.0, 5, yb),
        ("ETH M15 fresh<=3", de, "ETHUSDc",  5.0, 3, ye),
    ]:
        for mult in [1.0, 1.5, 2.0, 3.0]:
            sp = base_sp * mult
            m, _ = run(FreshPullback, d, sym, sp, 18, maxmat=mm)
            if m and m.get("trades", 0) > 20:
                cg = ((1 + m["total_return_pct"]/100) ** (1/yrs) - 1) * 100
                print(f"  {lbl:<26}{sp:>8.0f}{m['profit_factor']:>7.2f}"
                      f"{cg:>9.2f}{m['max_dd_pct']:>8.1f}")


if __name__ == "__main__":
    main()
