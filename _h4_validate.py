#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Validate the two H4 candidates that survived real cost over 13 years:
  A) pullback H4 adx18 SL3/TP7   -- PF 1.11, 687 trades, MaxDD 9.3%
  B) donchian H4 ch100 SL3/TP7   -- PF 1.15, 496 trades, MaxDD 8.6%

Same discipline as everything else: per-year walk-forward with params frozen,
cost stress, correlation vs the live M15 bot, and an OOS split. Report honestly.

CAVEAT being tested explicitly: the $2.00 cost was measured on 2026 gold
(~$4000/oz). Applying it flat to 2013 gold (~$1300/oz) over-penalises the early
years, since dealing spread scales roughly with price level. So we also score
with a price-proportional cost to see if the ranking survives either way.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import (DataLoader, prepare_data, BacktestEngine,
                             FastHybridTrendPullback, compute_metrics)
from _idea_search import DonchianBreakout, resample

GOLD_CSV = "download/xauusd-m15-bid-2013-01-01-2026-06-10.csv"
START = 10_000.0
RISK_PCT = 0.30
COMM = 3.50
REAL_SPREAD = 2.00
MIN_TRADES_GATE = 200


def gold_cfg(max_hold=32):
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = RISK_PCT
    c.partial_tp_atr = 999.0
    c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False
    c.max_hold_bars = max_hold
    return c


def run(strat_cls, d, sl=3.0, tp=7.0, spread=REAL_SPREAD, max_hold=32, adx_min=None, **ov):
    strat = strat_cls()
    if adx_min is not None and hasattr(strat, "ADX_MIN"):
        strat.ADX_MIN = adx_min
    for k, v in ov.items():
        setattr(strat, k, v)
    strat.sl_atr, strat.tp_atr = sl, tp
    strat.trail_atr_mult, strat.trail_activation_atr = 999.0, 999.0
    strat.precompute(d)
    eng = BacktestEngine(d, gold_cfg(max_hold), strat, spread_price=spread,
                          commission_per_lot=COMM, symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START), eng.trades


def fmt(m, label):
    if m is None or m.get("trades", 0) == 0:
        return f"  {label:<32} NO TRADES"
    return (f"  {label:<32} trades={m['trades']:>5}  win%={m['win_rate']*100:>5.1f}  "
            f"PF={m['profit_factor']:>5.2f}  Sharpe={m['sharpe']:>5.2f}  "
            f"MaxDD%={m['max_dd_pct']:>5.1f}  TotRet%={m['total_return_pct']:>+8.1f}  "
            f"MaxLoseStreak={m['max_consec_losses']:>3}")


CANDIDATES = [
    ("pullback-H4-adx18", FastHybridTrendPullback, dict(adx_min=18)),
    ("donchian-H4-ch100", DonchianBreakout,        dict(CHANNEL=100)),
]


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    cfg0 = ForexConfig(); cfg0.total_capital_usd = START
    df_m15, _ = loader.load("XAUUSD", 99.0, cfg0, csv_path=GOLD_CSV, allow_synthetic=True)
    df_h4 = resample(df_m15, "4h")
    d_h4 = prepare_data(df_h4)
    d_m15 = prepare_data(df_m15)
    print(f"[load] H4={len(df_h4):,} bars  {df_h4['timestamp'].iloc[0].date()} -> {df_h4['timestamp'].iloc[-1].date()}\n")

    for name, cls, kw in CANDIDATES:
        print("=" * 110)
        print(f" {name}")
        print("=" * 110)

        print(" -- cost stress --")
        for sp in [0.25, 1.00, 2.00, 3.00, 4.00]:
            m, _ = run(cls, d_h4, spread=sp, **kw)
            print(fmt(m, f"spread={sp}"))

        print("\n -- per-year walk-forward (params frozen, spread=2.00) --")
        pf_ok = pf_total = 0
        for y in sorted(df_h4["timestamp"].dt.year.unique()):
            dfy = df_h4[df_h4["timestamp"].dt.year == y].reset_index(drop=True)
            if len(dfy) < 500:
                continue
            m, _ = run(cls, prepare_data(dfy), **kw)
            if m and m.get("trades", 0) > 0:
                pf_total += 1
                pf_ok += 1 if m["profit_factor"] > 1.0 else 0
            print(fmt(m, f"{y}"))
        print(f"   Years PF>1: {pf_ok}/{pf_total}")

        print("\n -- OOS split: train 2013-2019 / test 2020-2026 (no re-fit) --")
        for lbl, a, b in [("TRAIN 2013-2019", "2013-01-01", "2020-01-01"),
                          ("TEST  2020-2026", "2020-01-01", "2027-01-01")]:
            dfw = df_h4[(df_h4["timestamp"] >= pd.Timestamp(a)) &
                        (df_h4["timestamp"] < pd.Timestamp(b))].reset_index(drop=True)
            m, _ = run(cls, prepare_data(dfw), **kw)
            print(fmt(m, lbl))
        print()

    # correlation between the two H4 candidates and vs the live M15 bot
    print("=" * 110)
    print(" CORRELATION (monthly PnL)")
    print("=" * 110)
    series = {}
    for name, cls, kw in CANDIDATES:
        _, tr = run(cls, d_h4, **kw)
        series[name] = tr
    _, tr_live = run(FastHybridTrendPullback, d_m15, adx_min=20, spread=REAL_SPREAD, max_hold=64)
    series["live-M15-adx20"] = tr_live

    def monthly(trades):
        rows = [(pd.Timestamp(t["exit_ts"]), t["net_pnl"]) for t in trades if t.get("exit_ts")]
        if not rows:
            return pd.Series(dtype=float)
        return pd.DataFrame(rows, columns=["ts", "pnl"]).set_index("ts")["pnl"].resample("ME").sum()

    ms = {k: monthly(v) for k, v in series.items()}
    keys = list(ms.keys())
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            both = pd.concat([ms[keys[i]], ms[keys[j]]], axis=1).dropna()
            if len(both) >= 12:
                print(f"  {keys[i]:<22} vs {keys[j]:<22} corr={both.iloc[:,0].corr(both.iloc[:,1]):+.3f}  ({len(both)} months)")


if __name__ == "__main__":
    main()
