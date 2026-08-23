#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
One thing looks wrong in the 15-edge result and it is worth chasing:

  portfolio Sharpe = 1.00
  but the best single edges are HIGHER  (H4:BTC-donch 1.18, BTCD 1.01)

A portfolio should not score below its own components unless something is
diluting it. The cause is the window: the series spans 247 months back to
2005, but crypto only exists from 2017. For 2005-2017 the portfolio is just
the weak FX/index daily edges averaged together, and that long weak stretch
drags the whole number down.

That is a measurement artefact, not a property of what you would actually
trade tomorrow. What matters for a decision made today is: how does the
portfolio behave over the window where the edges you would actually run are
all available?

So this re-scores on the common modern window. To keep it honest, this is NOT
cherry-picking winners -- every edge that qualified is kept, only the DATE
RANGE is restricted to where they coexist, and the pre-crypto period is
reported separately so nothing is hidden.
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
from _daily_multi_market import MARKETS, load_daily
from _all_paths import (CRYPTO_DAILY, run_donch, to_monthly, perf,
                        wf_pick_channel, START, RISK, TRADING_DAYS)

# channels as selected by the walk-forward pass in _all_paths
DAILY_CH = {"EURUSD": 100, "USDJPY": 200, "AUDUSD": 100, "USDCAD": 100,
            "SPX": 100, "NDX": 55, "WTI": 200, "GOLDFUT": 100}
CRYPTO_CH = {"BTCD": 200, "ETHD": 55}


def build():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START
    edges = {}
    for mkt, ch in DAILY_CH.items():
        csv, spread, ps, pv, comm = MARKETS[mkt]
        d = prepare_data(load_daily(csv))
        m, tr = run_donch(d, mkt, spread, comm, ps, pv, ch)
        if m and m.get("trades", 0) > 0:
            edges[f"D:{mkt}"] = to_monthly(tr)
    for name, ch in CRYPTO_CH.items():
        csv, sym, spread, ps, pv, comm = CRYPTO_DAILY[name]
        df, _ = loader.load(sym, 99.0, c0, csv_path=csv, allow_synthetic=False)
        d = prepare_data(resample(df, "1D"))
        m, tr = run_donch(d, sym, spread, comm, ps, pv, ch)
        if m and m.get("trades", 0) > 0:
            edges[f"D:{name}"] = to_monthly(tr)
    for symname, spec in H4_SYMBOLS.items():
        df, _ = loader.load(spec["sym"], 99.0, c0, csv_path=spec["csv"], allow_synthetic=False)
        d_h4 = prepare_data(resample(df, "4h"))
        for sname, cls, kw in [("donch100", DonchianBreakout, dict(CHANNEL=100)),
                               ("pullback18", FastHybridTrendPullback, dict(adx_min=18))]:
            m, tr = run_h4(cls, d_h4, spec, RISK, **kw)
            if m and m.get("trades", 0) >= 100 and m["profit_factor"] > 1.0:
                edges[f"H4:{symname}-{sname}"] = monthly_h4(tr)
    return pd.concat(edges, axis=1, sort=True)


def show(mret, label):
    p = perf(mret)
    if not p:
        print(f"  {label:<38} too short"); return None
    print(f"  {label:<38} months={p['n']:>4}  Sharpe={p['sharpe']:>5.2f}  "
          f"CAGR={p['cagr']:>+7.2f}%  DD={p['dd']:>5.1f}%")
    return p


def main():
    allm = build()
    allm.index = pd.to_datetime(allm.index)

    print("=" * 100)
    print(" EDGE AVAILABILITY BY ERA")
    print("=" * 100)
    for era_label, a, b in [("2005-2016 (pre-crypto)", "2005-01-01", "2017-01-01"),
                            ("2017-2026 (modern)",     "2017-01-01", "2027-01-01")]:
        w = allm.loc[(allm.index >= a) & (allm.index < b)]
        live = w.notna().any()
        print(f"  {era_label:<26} edges available: {int(live.sum()):>2} / {allm.shape[1]}")

    print("\n" + "=" * 100)
    print(" PORTFOLIO BY ERA (equal weight, all qualifying edges)")
    print("=" * 100)
    full = allm.mean(axis=1, skipna=True).dropna()
    show(full, "FULL 2005-2026 (as reported before)")

    pre = allm.loc[allm.index < "2017-01-01"].mean(axis=1, skipna=True).dropna()
    show(pre, "  pre-crypto 2005-2016 only")

    mod = allm.loc[allm.index >= "2017-01-01"]
    mod = mod.dropna(axis=1, how="all")
    mod_p = mod.mean(axis=1, skipna=True).dropna()
    show(mod_p, "  MODERN 2017-2026 (what you'd run)")

    corr = mod.corr()
    iu = np.triu_indices_from(corr.values, k=1)
    v = corr.values[iu]; v = v[~np.isnan(v)]
    print(f"\n  modern-window mean corr = {v.mean():+.3f} over {mod.shape[1]} edges")

    mp = perf(mod_p)
    if mp:
        print("\n" + "=" * 100)
        print(" WHAT THE MODERN PORTFOLIO SUPPORTS")
        print("=" * 100)
        print(f"  {'target DD':<12}{'risk scale':>12}{'CAGR%/yr':>12}{'%/day':>10}")
        for target in [10.0, 15.0, 20.0, 25.0, 30.0]:
            k = target / mp["dd"]
            p2 = perf(mod_p * k)
            if p2:
                dpd = (1 + p2["cagr"] / 100) ** (1 / TRADING_DAYS) * 100 - 100
                print(f"  {target:<12.0f}{k:>11.0f}x{p2['cagr']:>12.2f}{dpd:>10.3f}")

        print("\n" + "=" * 100)
        print(" HONEST CHECK: is the modern number just a crypto bull market?")
        print("=" * 100)
        cry = [c for c in mod.columns if any(t in c for t in ("BTC", "ETH"))]
        non = [c for c in mod.columns if c not in cry]
        show(mod[cry].mean(axis=1, skipna=True).dropna(), f"  crypto-only ({len(cry)} edges)")
        show(mod[non].mean(axis=1, skipna=True).dropna(), f"  non-crypto ({len(non)} edges)")


if __name__ == "__main__":
    main()
