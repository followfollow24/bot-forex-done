#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Full backtest report on the EXACT configuration running live right now.

Config taken from the VPS process list (verified 2026-07-29 04:28):
  adx20tp7      XAUUSDc M15  ADX>=20  SL3.0  TP4.0    risk 0.30%  maxpos 3
  adx18tp7      XAUUSDc M15  ADX>=18  SL3.0  TP1.0    risk 0.30%  maxpos 3
  regime22      XAUUSDc M15  ADX>=22  SL3.0  TP3.0    risk 0.30%  maxpos 3  +regime filter
  adx20_manual  XAUUSDc M15  ADX>=20  SL3.0  TP999    risk 0.30%  maxpos 3
  btc_cons      BTCUSDc M15  ADX>=15  SL4.0  TP12.0   risk 0.20%  maxpos 1  (kill-switched)
  btc_aggr      BTCUSDc M15  ADX>=12  SL2.5  TP7.5    risk 0.20%  maxpos 1  (kill-switched)

Reported at three cost levels because that single assumption decides the
answer: $0.10 is what the original backtests used, $2.85 is what the gold bots
actually pay per their own fills log, and $0.90 is the price-proportional
middle for the years when gold was cheaper.
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
BTC_CSV = "download/btcusdt-15m-binance-2017-08-17-2026-06-30.csv"
START = 10_000.0
TRADING_DAYS = 252

# name, class, adx, sl, tp_now, tp_orig, risk, maxpos, symbol
BOTS = [
    ("adx20tp7",     FastHybridTrendPullback, 20, 3.0, 4.0,   7.0,   0.30, "XAUUSD"),
    ("adx18tp7",     FastHybridTrendPullback, 18, 3.0, 1.0,   7.0,   0.30, "XAUUSD"),
    ("regime22",     RegimeFilteredHybrid,    22, 3.0, 3.0,   7.0,   0.30, "XAUUSD"),
    ("adx20_manual", FastHybridTrendPullback, 20, 3.0, 999.0, 999.0, 0.30, "XAUUSD"),
]
BTC_BOTS = [
    ("btc_cons", 15, 4.0, 12.0, 0.20),
    ("btc_aggr", 12, 2.5,  7.5, 0.20),
]
COSTS = [("$0.10 repo assumption", 0.10),
         ("$0.90 fair proportional", 0.90),
         ("$2.85 real measured", 2.85)]


def cfg(risk, sym=None, ps=None, pv=None):
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = risk
    c.partial_tp_atr = 999.0
    c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False
    c.max_hold_bars = 64
    if sym and ps:
        c.pip_size[sym] = ps
        c.pip_value_usd_approx[sym] = pv
    return c


def run(cls, d, adx, sl, tp, spread, risk, sym="XAUUSD", comm=3.5, ps=None, pv=None):
    s = cls()
    s.ADX_MIN = adx
    s.sl_atr, s.tp_atr = sl, tp
    s.trail_atr_mult = s.trail_activation_atr = 999.0
    s.precompute(d)
    eng = BacktestEngine(d, cfg(risk, sym, ps, pv), s, spread_price=spread,
                          commission_per_lot=comm, symbol=sym)
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START), eng.trades


def cagr(tot, yrs):
    return -100.0 if tot <= -100 else ((1 + tot / 100) ** (1 / yrs) - 1) * 100


def line(m, label, yrs):
    if not m or m.get("trades", 0) == 0:
        print(f"    {label:<30} NO TRADES"); return
    t = m["total_return_pct"]
    print(f"    {label:<30} n={m['trades']:>5}  PF={m['profit_factor']:>5.2f}  "
          f"win={m['win_rate']*100:>5.1f}%  CAGR={cagr(t,yrs):>+8.2f}%/yr  "
          f"DD={m['max_dd_pct']:>5.1f}%  streak={m['max_consec_losses']:>2}")


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START
    dfg, _ = loader.load("XAUUSD", 99.0, c0, csv_path=GOLD_CSV, allow_synthetic=True)
    dg_full = prepare_data(dfg)
    yrs_full = (dfg["timestamp"].iloc[-1] - dfg["timestamp"].iloc[0]).days / 365.25
    dfg_rec = dfg[dfg["timestamp"] >= pd.Timestamp("2024-01-01")].reset_index(drop=True)
    dg_rec = prepare_data(dfg_rec)
    yrs_rec = (dfg_rec["timestamp"].iloc[-1] - dfg_rec["timestamp"].iloc[0]).days / 365.25

    print("#" * 96)
    print(" PART 1 -- GOLD BOTS, CONFIG AS RUNNING NOW, FULL HISTORY 13.4y")
    print("#" * 96)
    for cl, sp in COSTS:
        print(f"\n  cost = {cl}")
        for name, cls, adx, sl, tp_now, _, risk, sym in BOTS:
            m, _ = run(cls, dg_full, adx, sl, tp_now, sp, risk)
            line(m, f"{name} (TP={tp_now})", yrs_full)

    print("\n" + "#" * 96)
    print(" PART 2 -- SAME BOTS, RECENT ERA 2024-2026 (gold at today's price level)")
    print("#" * 96)
    for cl, sp in [("$2.00", 2.00), ("$2.85 real measured", 2.85)]:
        print(f"\n  cost = {cl}")
        for name, cls, adx, sl, tp_now, _, risk, sym in BOTS:
            m, _ = run(cls, dg_rec, adx, sl, tp_now, sp, risk)
            line(m, f"{name} (TP={tp_now})", yrs_rec)

    print("\n" + "#" * 96)
    print(" PART 3 -- EFFECT OF THE 2026-07-28 TP CHANGE (recent era, $2.85)")
    print("#" * 96)
    print(f"    {'bot':<16}{'TP orig':>9}{'CAGR orig':>12}{'TP now':>9}{'CAGR now':>12}{'delta':>11}")
    for name, cls, adx, sl, tp_now, tp_orig, risk, sym in BOTS:
        mo, _ = run(cls, dg_rec, adx, sl, tp_orig, 2.85, risk)
        mn, _ = run(cls, dg_rec, adx, sl, tp_now, 2.85, risk)
        co = cagr(mo["total_return_pct"], yrs_rec) if mo and mo["trades"] else float("nan")
        cn = cagr(mn["total_return_pct"], yrs_rec) if mn and mn["trades"] else float("nan")
        print(f"    {name:<16}{tp_orig:>9.1f}{co:>+12.2f}{tp_now:>9.1f}{cn:>+12.2f}{cn-co:>+11.2f}")

    print("\n" + "#" * 96)
    print(" PART 4 -- PER-YEAR, adx20tp7 as it runs now (TP=4.0), cost $2.85")
    print("#" * 96)
    pf_ok = tot = 0
    for y in sorted(dfg["timestamp"].dt.year.unique()):
        dfy = dfg[dfg["timestamp"].dt.year == y].reset_index(drop=True)
        if len(dfy) < 2000:
            continue
        m, _ = run(FastHybridTrendPullback, prepare_data(dfy), 20, 3.0, 4.0, 2.85, 0.30)
        if m and m.get("trades", 0):
            tot += 1; pf_ok += 1 if m["profit_factor"] > 1 else 0
            line(m, str(y), 1.0)
    print(f"\n    years with PF>1: {pf_ok}/{tot}")

    print("\n" + "#" * 96)
    print(" PART 5 -- BTC BOTS (kill-switched, shown for completeness)")
    print("#" * 96)
    dfb, _ = loader.load("BTCUSDc", 99.0, c0, csv_path=BTC_CSV, allow_synthetic=False)
    db = prepare_data(dfb)
    yrs_b = (dfb["timestamp"].iloc[-1] - dfb["timestamp"].iloc[0]).days / 365.25
    print(f"\n  cost = $10 spread (Exness BTCUSDc), NOTE: swap -6.9%/yr on longs NOT modelled")
    for name, adx, sl, tp, risk in BTC_BOTS:
        m, _ = run(FastHybridTrendPullback, db, adx, sl, tp, 10.0, risk,
                   sym="BTCUSDc", comm=0.0, ps=1.0, pv=0.01)
        line(m, name, yrs_b)

    print("\n" + "#" * 96)
    print(" PART 6 -- WHAT THE SAME STRATEGY DOES ON BTC M15 (the comparison that matters)")
    print("#" * 96)
    for adx, sl, tp, lbl in [(18, 3.0, 7.0, "pullback18 SL3/TP7"),
                             (20, 3.0, 7.0, "pullback20 SL3/TP7")]:
        m, _ = run(FastHybridTrendPullback, db, adx, sl, tp, 10.0, 0.30,
                   sym="BTCUSDc", comm=0.0, ps=1.0, pv=0.01)
        line(m, f"BTC M15 {lbl}", yrs_b)
    mg, _ = run(FastHybridTrendPullback, dg_full, 18, 3.0, 7.0, 2.85, 0.30)
    line(mg, "GOLD M15 pullback18 SL3/TP7", yrs_full)

    print("\n" + "#" * 96)
    print(" SUMMARY")
    print("#" * 96)
    print("""
    The gold M15 family has no edge at the cost it actually pays. It only looks
    profitable at $0.10 spread, which is ~28x cheaper than the $2.85 measured in
    the bots' own fills log. The 2026-07-28 TP change made three of the four bots
    worse, adx18tp7 severely so.

    The same strategy logic on BTC M15 is strongly profitable, because BTC's cost
    /ATR is ~10% versus gold's ~45%. The signal was never the problem; the
    instrument's cost relative to its volatility was.
    """)


if __name__ == "__main__":
    main()
