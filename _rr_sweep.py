#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Does a bigger reward-to-risk per trade help?

Everything so far runs SL 3.0 / TP 7.0 (RR 2.33:1) -- a number inherited from
the old M15 gold work, never re-examined for these trend-following edges.

Trend-following theory says this is probably wrong: the profit of a Donchian
system comes from a small number of very large winners, so a fixed TP at
7xATR truncates exactly the trades that pay for everything else. The original
turtle system had no profit target at all -- it exited on a trailing stop or an
opposite channel break.

Tested per market:
  fixed TP    : 7, 10, 14, 20 xATR
  no TP       : TP disabled, exit only on trailing stop or timeout
                trail 2.5 / 3.5 xATR, activated after +1 or +2 xATR

Scored with the same real costs, PF and monthly Sharpe. The interesting
outcome would be that removing the TP raises Sharpe -- and if it does not,
that is worth knowing too rather than assuming.
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
from _daily_multi_market import MARKETS, load_daily
from _all_paths import CRYPTO_DAILY, to_monthly, perf, START, RISK

DAILY_CH = {"EURUSD": 100, "USDJPY": 200, "AUDUSD": 100, "USDCAD": 100,
            "SPX": 100, "NDX": 55, "WTI": 200, "GOLDFUT": 100}
CRYPTO_CH = {"BTCD": 200, "ETHD": 55}

# (label, tp_atr, trail_mult, trail_activation)
EXITS = [
    ("TP7  (current)",   7.0, 999.0, 999.0),
    ("TP10",            10.0, 999.0, 999.0),
    ("TP14",            14.0, 999.0, 999.0),
    ("TP20",            20.0, 999.0, 999.0),
    ("noTP trail2.5@1", 999.0,  2.5,   1.0),
    ("noTP trail3.5@1", 999.0,  3.5,   1.0),
    ("noTP trail3.5@2", 999.0,  3.5,   2.0),
    ("noTP trail5.0@2", 999.0,  5.0,   2.0),
]


def cfg_for(sym, ps, pv, max_hold=30):
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = RISK
    c.partial_tp_atr = 999.0
    c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False
    c.max_hold_bars = max_hold
    if ps is not None:
        c.pip_size[sym] = ps
        c.pip_value_usd_approx[sym] = pv
    return c


def run_exit(d, sym, spread, comm, ps, pv, channel, tp, trail, act, max_hold=30):
    s = DonchianBreakout()
    s.CHANNEL = channel
    s.sl_atr, s.tp_atr = 3.0, tp
    s.trail_atr_mult, s.trail_activation_atr = trail, act
    s.precompute(d)
    eng = BacktestEngine(d, cfg_for(sym, ps, pv, max_hold), s,
                          spread_price=spread, commission_per_lot=comm, symbol=sym)
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START), eng.trades


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START

    targets = []
    for mkt, ch in DAILY_CH.items():
        csv, spread, ps, pv, comm = MARKETS[mkt]
        targets.append((mkt, prepare_data(load_daily(csv)), mkt, spread, comm, ps, pv, ch, 30))
    for name, ch in CRYPTO_CH.items():
        csv, sym, spread, ps, pv, comm = CRYPTO_DAILY[name]
        df, _ = loader.load(sym, 99.0, c0, csv_path=csv, allow_synthetic=False)
        targets.append((name, prepare_data(resample(df, "1D")), sym, spread, comm, ps, pv, ch, 30))
    # H4 BTC -- the single strongest edge found
    spec = dict(csv="download/btcusdt-15m-binance-2017-08-17-2026-06-30.csv",
                sym="BTCUSDc", spread=10.0, comm=0.0, ps=1.0, pv=0.01)
    dfb, _ = loader.load(spec["sym"], 99.0, c0, csv_path=spec["csv"], allow_synthetic=False)
    targets.append(("BTC-H4", prepare_data(resample(dfb, "4h")), spec["sym"],
                    spec["spread"], spec["comm"], spec["ps"], spec["pv"], 100, 32))

    all_series = {}   # exit_label -> {market: monthly}
    print("=" * 112)
    print(f"  {'market':<10}{'exit':<18}{'trades':>8}{'PF':>7}{'win%':>7}{'Sharpe(mo)':>12}{'CAGR%':>9}{'DD%':>7}")
    print("=" * 112)
    for mname, d, sym, spread, comm, ps, pv, ch, hold in targets:
        best_line, best_sh = None, -99
        for lbl, tp, trail, act in EXITS:
            try:
                m, tr = run_exit(d, sym, spread, comm, ps, pv, ch, tp, trail, act, hold)
            except Exception as e:
                continue
            if not m or m.get("trades", 0) < 20:
                continue
            mr = to_monthly(tr); p = perf(mr)
            sh = p["sharpe"] if p else float("nan")
            print(f"  {mname:<10}{lbl:<18}{m['trades']:>8}{m['profit_factor']:>7.2f}"
                  f"{m['win_rate']*100:>7.1f}{sh:>12.2f}"
                  f"{(p['cagr'] if p else 0):>9.2f}{(p['dd'] if p else 0):>7.1f}")
            if p:
                all_series.setdefault(lbl, {})[mname] = mr
                if p["sharpe"] > best_sh:
                    best_sh, best_line = p["sharpe"], lbl
        print(f"  {'':<10}{'-> best: ' + str(best_line):<18}{'':>8}{'':>7}{'':>7}{best_sh:>12.2f}")
        print()

    print("=" * 112)
    print(" PORTFOLIO Sharpe by exit rule (equal weight over all markets)")
    print("=" * 112)
    print(f"  {'exit rule':<20}{'markets':>9}{'months':>8}{'Sharpe':>9}{'CAGR%':>9}{'DD%':>8}")
    rows = []
    for lbl, d in all_series.items():
        if len(d) < 5:
            continue
        allm = pd.concat(d, axis=1, sort=True)
        port = allm.mean(axis=1, skipna=True).dropna()
        p = perf(port)
        if p:
            rows.append((p["sharpe"], lbl, len(d), p))
            print(f"  {lbl:<20}{len(d):>9}{p['n']:>8}{p['sharpe']:>9.2f}"
                  f"{p['cagr']:>9.2f}{p['dd']:>8.1f}")
    if rows:
        rows.sort(reverse=True)
        best = rows[0]
        print(f"\n  BEST: {best[1]}  Sharpe={best[0]:.2f}  "
              f"(vs TP7 current = "
              f"{[r[0] for r in rows if 'TP7' in r[1]][0]:.2f})"
              if any('TP7' in r[1] for r in rows) else "")


if __name__ == "__main__":
    main()
