#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Full report on the ORIGINAL configuration -- SL 3.0 / TP 7.0 -- i.e. what the
bots would look like if the 2026-07-28 TP change were reverted.

Same structure as the report on the current config so the two are directly
comparable: all three cost assumptions, full history, recent era, and a
per-year walk-through at the cost actually paid.
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

GOLD_CSV = "download/xauusd-m15-bid-2013-01-01-2026-06-10.csv"
START = 10_000.0

BOTS = [
    ("adx20tp7",     FastHybridTrendPullback, 20),
    ("adx18tp7",     FastHybridTrendPullback, 18),
    ("regime22",     RegimeFilteredHybrid,    22),
]


def cfg(risk=0.30):
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = risk
    c.partial_tp_atr = 999.0
    c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False
    c.max_hold_bars = 64
    return c


def run(cls, d, adx, tp, spread, sl=3.0):
    s = cls()
    s.ADX_MIN = adx
    s.sl_atr, s.tp_atr = sl, tp
    s.trail_atr_mult = s.trail_activation_atr = 999.0
    s.precompute(d)
    eng = BacktestEngine(d, cfg(), s, spread_price=spread,
                          commission_per_lot=3.5, symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START)


def cagr(t, y):
    return -100.0 if t <= -100 else ((1 + t / 100) ** (1 / y) - 1) * 100


def line(m, label, yrs):
    if not m or m.get("trades", 0) == 0:
        print(f"    {label:<26} NO TRADES"); return None
    t = m["total_return_pct"]
    c = cagr(t, yrs)
    print(f"    {label:<26} n={m['trades']:>5}  PF={m['profit_factor']:>5.2f}  "
          f"win={m['win_rate']*100:>5.1f}%  CAGR={c:>+8.2f}%/yr  "
          f"DD={m['max_dd_pct']:>5.1f}%  streak={m['max_consec_losses']:>3}")
    return c


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START
    df, _ = loader.load("XAUUSD", 99.0, c0, csv_path=GOLD_CSV, allow_synthetic=True)
    d_full = prepare_data(df)
    yrs = (df["timestamp"].iloc[-1] - df["timestamp"].iloc[0]).days / 365.25

    df_rec = df[df["timestamp"] >= pd.Timestamp("2024-01-01")].reset_index(drop=True)
    d_rec = prepare_data(df_rec)
    yrs_rec = (df_rec["timestamp"].iloc[-1] - df_rec["timestamp"].iloc[0]).days / 365.25

    df_25 = df[df["timestamp"] >= pd.Timestamp("2025-01-01")].reset_index(drop=True)
    d_25 = prepare_data(df_25)
    yrs_25 = (df_25["timestamp"].iloc[-1] - df_25["timestamp"].iloc[0]).days / 365.25

    print("#" * 92)
    print(f" SL 3.0 / TP 7.0  --  FULL HISTORY {yrs:.1f} YEARS")
    print("#" * 92)
    for cl, sp in [("$0.10 repo assumption", 0.10),
                   ("$0.90 fair proportional", 0.90),
                   ("$2.85 real measured", 2.85)]:
        print(f"\n  cost = {cl}")
        for name, cls, adx in BOTS:
            line(run(cls, d_full, adx, 7.0, sp), name, yrs)
        line(run(FastHybridTrendPullback, d_full, 20, 999.0, sp), "adx20_manual (no TP)", yrs)

    print("\n" + "#" * 92)
    print(f" RECENT ERA 2024-2026 ({yrs_rec:.1f}y) -- gold near today's price")
    print("#" * 92)
    for cl, sp in [("$2.00", 2.00), ("$2.85 real measured", 2.85), ("$3.50 pessimistic", 3.50)]:
        print(f"\n  cost = {cl}")
        for name, cls, adx in BOTS:
            line(run(cls, d_rec, adx, 7.0, sp), name, yrs_rec)
        line(run(FastHybridTrendPullback, d_rec, 20, 999.0, sp), "adx20_manual (no TP)", yrs_rec)

    print("\n" + "#" * 92)
    print(f" 2025-2026 ONLY ({yrs_25:.1f}y) -- the regime the account is actually trading in")
    print("#" * 92)
    for cl, sp in [("$2.85 real measured", 2.85)]:
        print(f"\n  cost = {cl}")
        for name, cls, adx in BOTS:
            line(run(cls, d_25, adx, 7.0, sp), name, yrs_25)
        line(run(FastHybridTrendPullback, d_25, 20, 999.0, sp), "adx20_manual (no TP)", yrs_25)

    print("\n" + "#" * 92)
    print(" PER-YEAR, TP=7.0, cost $2.85")
    print("#" * 92)
    for name, cls, adx in BOTS:
        print(f"\n  {name}:")
        ok = tot = 0
        for y in sorted(df["timestamp"].dt.year.unique()):
            dfy = df[df["timestamp"].dt.year == y].reset_index(drop=True)
            if len(dfy) < 2000:
                continue
            m = run(cls, prepare_data(dfy), adx, 7.0, 2.85)
            if m and m.get("trades", 0):
                tot += 1; ok += 1 if m["profit_factor"] > 1 else 0
                line(m, str(y), 1.0)
        print(f"      years PF>1: {ok}/{tot}")

    print("\n" + "#" * 92)
    print(" TP COMPARISON at the cost actually paid ($2.85), recent era")
    print("#" * 92)
    print(f"    {'bot':<14}" + "".join(f"{f'TP{t}':>10}" for t in [1, 3, 4, 7, 10, 999]))
    for name, cls, adx in BOTS + [("adx20_manual", FastHybridTrendPullback, 20)]:
        row = []
        for tp in [1.0, 3.0, 4.0, 7.0, 10.0, 999.0]:
            m = run(cls, d_rec, adx, tp, 2.85)
            row.append(cagr(m["total_return_pct"], yrs_rec) if m and m.get("trades") else float("nan"))
        print(f"    {name:<14}" + "".join(f"{v:>+10.2f}" for v in row))


if __name__ == "__main__":
    main()
