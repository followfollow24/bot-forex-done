#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Combine everything that passed: H4 edges (gold/BTC/ETH) + daily edges across
9 markets, into one portfolio.

Two problems from the daily run are fixed here:

  1. Redundancy. donch55 and donch100 on the same market correlate 0.74-0.93 --
     they are the same edge twice. Counting both inflates the apparent number
     of independent bets and does nothing for Sharpe. One config per market.

  2. Low capital utilisation. The daily edges fire only 3-6 times a year, so at
     0.3% risk most months are flat and monthly Sharpe collapses even though
     per-trade edge is strong (PF 1.3-2.5). Diversifying ACROSS markets is what
     fills the calendar -- 12 markets x 5 trades/yr is a trade every few days.

Reports the honest combined Sharpe and what daily return it supports at a
drawdown you could actually survive.
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
from _multi_portfolio import SYMBOLS as H4_SYMBOLS, run as run_h4, monthly as monthly_h4
from _daily_multi_market import (MARKETS, load_daily, run as run_daily,
                                  monthly as monthly_daily)

START = 10_000.0
RISK = 0.30
TRADING_DAYS = 252

# one config per market -- the better of donch55/donch100 from the daily run,
# chosen on PF, not cherry-picked afterwards from the portfolio result
DAILY_PICK = {
    "EURUSD":  ("donch55",  dict(CHANNEL=55)),
    "USDJPY":  ("donch100", dict(CHANNEL=100)),
    "AUDUSD":  ("donch100", dict(CHANNEL=100)),
    "USDCAD":  ("donch100", dict(CHANNEL=100)),
    "SPX":     ("donch100", dict(CHANNEL=100)),
    "NDX":     ("donch100", dict(CHANNEL=100)),
    "WTI":     ("donch55",  dict(CHANNEL=55)),
    "GOLDFUT": ("donch100", dict(CHANNEL=100)),
}
H4_PICK = [("donch100", DonchianBreakout, dict(CHANNEL=100)),
           ("pullback18", FastHybridTrendPullback, dict(adx_min=18))]


def summarize(mret, label):
    if len(mret) < 24:
        print(f"  {label:<34} too short ({len(mret)} months)")
        return None
    eq = (1 + mret / 100).cumprod()
    yrs = len(mret) / 12
    cagr = (eq.iloc[-1] ** (1 / yrs) - 1) * 100
    dd = abs(((eq / eq.cummax()) - 1).min() * 100)
    sharpe = mret.mean() / mret.std() * np.sqrt(12) if mret.std() > 0 else 0
    print(f"  {label:<34} months={len(mret):>4}  CAGR={cagr:>+7.2f}%/yr  "
          f"MaxDD={dd:>5.1f}%  Sharpe={sharpe:>5.2f}")
    return dict(cagr=cagr, dd=dd, sharpe=sharpe, mret=mret)


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START
    series = {}

    print("collecting H4 edges (gold/BTC/ETH)...")
    for symname, spec in H4_SYMBOLS.items():
        try:
            df, _ = loader.load(spec["sym"], 99.0, c0, csv_path=spec["csv"], allow_synthetic=False)
        except Exception:
            continue
        d_h4 = prepare_data(resample(df, "4h"))
        for sname, cls, kw in H4_PICK:
            m, tr = run_h4(cls, d_h4, spec, RISK, **kw)
            if m and m.get("trades", 0) >= 100 and m["profit_factor"] > 1.0:
                mr = monthly_h4(tr)
                if len(mr) >= 24:
                    series[f"H4:{symname}-{sname}"] = mr
                    print(f"   + H4:{symname}-{sname:<12} PF={m['profit_factor']:.2f} n={m['trades']}")

    print("\ncollecting daily edges (9 markets, one config each)...")
    for mkt, (sname, kw) in DAILY_PICK.items():
        csv, spread, pip_size, pip_value, comm = MARKETS[mkt]
        try:
            d = prepare_data(load_daily(csv))
        except Exception:
            continue
        m, tr = run_daily(DonchianBreakout, d, mkt, spread, comm, pip_size, pip_value, **kw)
        if m and m.get("trades", 0) >= 40 and m["profit_factor"] > 1.0:
            mr = monthly_daily(tr)
            if len(mr) >= 24:
                series[f"D:{mkt}-{sname}"] = mr
                print(f"   + D:{mkt}-{sname:<14} PF={m['profit_factor']:.2f} n={m['trades']}")

    print(f"\ntotal edges collected: {len(series)}")
    if len(series) < 3:
        print("not enough edges."); return

    allm = pd.concat(series, axis=1, sort=True)

    print("\n" + "=" * 100)
    print(" AVERAGE PAIRWISE CORRELATION (how independent the bets really are)")
    print("=" * 100)
    corr = allm.corr()
    iu = np.triu_indices_from(corr.values, k=1)
    vals = corr.values[iu]
    vals = vals[~np.isnan(vals)]
    print(f"  mean |corr| = {np.abs(vals).mean():.3f}   mean corr = {vals.mean():+.3f}   "
          f"max = {vals.max():+.2f}")
    n_eff = len(series) / (1 + (len(series) - 1) * max(vals.mean(), 0))
    print(f"  effective independent bets ~ {n_eff:.1f} (nominal {len(series)})")

    print("\n" + "=" * 100)
    print(" COMBINED PORTFOLIO (equal weight)")
    print("=" * 100)
    port = allm.mean(axis=1, skipna=True).dropna()
    base = summarize(port, "combined, risk 0.30%/trade")

    if base and base["dd"] > 0:
        print("\n  scaled to survivable drawdowns:")
        for target in [10.0, 15.0, 20.0, 25.0]:
            k = target / base["dd"]
            s = summarize(port * k, f"  DD~{target:.0f}%  (risk x{k:.0f})")
            if s:
                dpd = (1 + s["cagr"] / 100) ** (1 / TRADING_DAYS) * 100 - 100
                print(f"       -> {dpd:.3f}%/day")

    print("\n" + "=" * 100)
    print(" COMPARISON")
    print("=" * 100)
    print(f"  current live M15 bots               ~ -37%/yr")
    print(f"  H4 gold only                        ~ +5 to +6.5%/yr")
    if base:
        k20 = 20.0 / base["dd"]
        s20 = (1 + summarize.__wrapped__ if False else None)
    # recompute cleanly for the summary line
    if base and base["dd"] > 0:
        k = 20.0 / base["dd"]
        sc = port * k
        eq = (1 + sc / 100).cumprod(); yrs = len(sc) / 12
        c20 = (eq.iloc[-1] ** (1 / yrs) - 1) * 100
        print(f"  this combined portfolio @ DD 20%    ~ {c20:+.1f}%/yr "
              f"({(1+c20/100)**(1/TRADING_DAYS)*100-100:.3f}%/day)")


if __name__ == "__main__":
    main()
