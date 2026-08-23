#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hunt for more independent edges on DAILY bars across 9 untouched markets.

Why daily is the right place to look after everything above:
  the entire M15 failure was cost/ATR. On M15 gold, $2.85 of cost eats ~45% of
  a $6 ATR. On daily bars ATR is 10-40x larger, so the same dealing cost is
  ~1-3% of ATR. The cost problem essentially disappears, which means a weaker
  raw signal can still clear the bar.

Markets: EURUSD GBPUSD USDJPY AUDUSD USDCAD SPX NDX WTI GOLDFUT
  -- different drivers (rates, equity risk, energy, metals) so there is a real
  chance of low correlation, which is the thing that actually raises Sharpe.

Strategies: the two already validated on H4 gold, applied UNCHANGED. No
per-market tuning -- if a config only works where it was found, that shows up
here as a failure and that is the useful answer.

Costs: realistic retail spreads per instrument, in price units.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import (DataLoader, prepare_data, BacktestEngine,
                             FastHybridTrendPullback, compute_metrics)
from _idea_search import DonchianBreakout

START = 10_000.0
RISK = 0.30

# name: (csv, spread_price, pip_size, pip_value_usd_per_lot, commission)
MARKETS = {
    "EURUSD":  ("download/eurusd-daily-yahoo.csv",  0.00012, 0.0001, 10.0, 3.5),
    "GBPUSD":  ("download/gbpusd-daily-yahoo.csv",  0.00018, 0.0001, 10.0, 3.5),
    "USDJPY":  ("download/usdjpy-daily-yahoo.csv",  0.015,   0.01,    9.0, 3.5),
    "AUDUSD":  ("download/audusd-daily-yahoo.csv",  0.00020, 0.0001, 10.0, 3.5),
    "USDCAD":  ("download/usdcad-daily-yahoo.csv",  0.00022, 0.0001,  7.5, 3.5),
    "SPX":     ("download/spx-daily-yahoo.csv",     0.60,    1.0,     1.0, 0.0),
    "NDX":     ("download/ndx-daily-yahoo.csv",     2.00,    1.0,     1.0, 0.0),
    "WTI":     ("download/wti-daily-yahoo.csv",     0.035,   0.01,   10.0, 0.0),
    "GOLDFUT": ("download/goldfut-daily-yahoo.csv", 0.35,    0.01,    1.0, 0.0),
}

STRATS = [
    ("pullback18", FastHybridTrendPullback, dict(adx_min=18)),
    ("donch100",   DonchianBreakout,        dict(CHANNEL=100)),
    ("donch55",    DonchianBreakout,        dict(CHANNEL=55)),
]

MIN_TRADES = 40   # daily bars produce far fewer trades; gate scaled accordingly


def make_cfg(sym, pip_size, pip_value):
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = RISK
    c.partial_tp_atr = 999.0
    c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False
    c.max_hold_bars = 30           # 30 daily bars ~ 6 weeks
    c.pip_size[sym] = pip_size
    c.pip_value_usd_approx[sym] = pip_value
    return c


def run(cls, d, sym, spread, comm, pip_size, pip_value, adx_min=None, **ov):
    s = cls()
    if adx_min is not None and hasattr(s, "ADX_MIN"):
        s.ADX_MIN = adx_min
    for k, v in ov.items():
        setattr(s, k, v)
    s.sl_atr, s.tp_atr = 3.0, 7.0
    s.trail_atr_mult = s.trail_activation_atr = 999.0
    s.precompute(d)
    eng = BacktestEngine(d, make_cfg(sym, pip_size, pip_value), s,
                          spread_price=spread, commission_per_lot=comm, symbol=sym)
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START), eng.trades


def monthly(trades):
    rows = []
    for t in trades:
        if not t.get("exit_ts"):
            continue
        pnl = t["net_pnl"]
        eqb = t.get("equity_after", START) - pnl
        rows.append((pd.Timestamp(t["exit_ts"]), pnl, eqb))
    if not rows:
        return pd.Series(dtype=float)
    df = pd.DataFrame(rows, columns=["ts", "pnl", "eqb"])
    df["r"] = df["pnl"] / df["eqb"].replace(0, np.nan) * 100.0
    return df.set_index("ts")["r"].resample("ME").sum()


def load_daily(path):
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.dropna().reset_index(drop=True)
    return df


def main():
    print(f"DAILY bars, risk/trade={RISK}%, SL3/TP7, params frozen from H4 gold validation")
    print(f"gate: >= {MIN_TRADES} trades and PF > 1\n")

    series = {}
    winners = []
    print("=" * 104)
    print(f"  {'market-strategy':<26}{'trades':>8}{'PF':>7}{'win%':>7}{'Sharpe':>8}{'MaxDD%':>9}{'TotRet%':>10}")
    print("=" * 104)
    for mkt, (csv, spread, pip_size, pip_value, comm) in MARKETS.items():
        try:
            df = load_daily(csv)
        except Exception as e:
            print(f"  {mkt}: load failed {e}")
            continue
        d = prepare_data(df)
        for sname, cls, kw in STRATS:
            try:
                m, tr = run(cls, d, mkt, spread, comm, pip_size, pip_value, **kw)
            except Exception as e:
                print(f"  {mkt}-{sname:<14} ERROR {e}")
                continue
            key = f"{mkt}-{sname}"
            if not m or m.get("trades", 0) == 0:
                print(f"  {key:<26}{'--':>8}")
                continue
            ok = m["trades"] >= MIN_TRADES and m["profit_factor"] > 1.0
            mark = "  <==" if ok else ""
            print(f"  {key:<26}{m['trades']:>8}{m['profit_factor']:>7.2f}"
                  f"{m['win_rate']*100:>7.1f}{m['sharpe']:>8.2f}"
                  f"{m['max_dd_pct']:>9.1f}{m['total_return_pct']:>+10.1f}{mark}")
            if ok:
                mr = monthly(tr)
                if len(mr) >= 24:
                    series[key] = mr
                    winners.append(key)

    print(f"\n  passed gate: {len(winners)}  ->  {', '.join(winners) if winners else 'none'}")

    if len(series) < 2:
        print("\nNot enough qualifying edges for a portfolio.")
        return

    allm = pd.concat(series, axis=1, sort=True)
    print("\n" + "=" * 104)
    print(" CORRELATION (monthly returns, qualifying edges only)")
    print("=" * 104)
    print(allm.corr().round(2).to_string())

    print("\n" + "=" * 104)
    print(" PORTFOLIO of qualifying daily edges (equal weight)")
    print("=" * 104)
    port = allm.mean(axis=1, skipna=True).dropna()
    if len(port) >= 24:
        eq = (1 + port / 100).cumprod()
        yrs = len(port) / 12
        cagr = (eq.iloc[-1] ** (1 / yrs) - 1) * 100
        dd = abs(((eq / eq.cummax()) - 1).min() * 100)
        sharpe = port.mean() / port.std() * np.sqrt(12) if port.std() > 0 else 0
        print(f"  months={len(port)}  CAGR={cagr:+.2f}%/yr  MaxDD={dd:.1f}%  Sharpe={sharpe:.2f}")
        if dd > 0:
            for target in [10.0, 20.0]:
                k = target / dd
                sc = port * k
                eqs = (1 + sc / 100).cumprod()
                c2 = (eqs.iloc[-1] ** (1 / yrs) - 1) * 100
                d2 = abs(((eqs / eqs.cummax()) - 1).min() * 100)
                print(f"    scaled x{k:>5.1f} -> CAGR={c2:+7.2f}%/yr  MaxDD={d2:5.1f}%  "
                      f"(~{(1+c2/100)**(1/252)*100-100:.3f}%/day)")


if __name__ == "__main__":
    main()
