#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Both remaining levers at once:

  A) LONGER TIMEOUT. The trailing-stop test was confounded: max_hold_bars=30
     capped every trade at ~6 weeks, so "let winners run" never actually got
     to run. Re-test exits with 30 / 100 / 250 bar holds.

  B) MORE MARKETS. 23 new daily markets added -- bonds, ags, metals, energy,
     more indices and FX. Deliberately NON-crypto, because the previous result
     leaned on crypto (crypto Sharpe 1.53 vs non-crypto 0.63) and more crypto
     would deepen that concentration rather than fix it.

Channel per market is chosen on the FIRST HALF of that market's history and
scored on the whole series, so the choice is not made with hindsight.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import (DataLoader, prepare_data, BacktestEngine, compute_metrics)
from _idea_search import DonchianBreakout, resample
from _daily_multi_market import MARKETS as OLD_MARKETS, load_daily
from _all_paths import CRYPTO_DAILY, to_monthly, perf, START, RISK

CHANNELS = [20, 55, 100, 200]
TRADING_DAYS = 252

NEW = ["us10y", "us30y", "silver", "copper", "plat", "natgas", "brent",
       "corn", "wheat", "soybean", "sugar", "coffee", "cotton",
       "dax", "nikkei", "ftse", "russell", "hsi",
       "usdchf", "nzdusd", "eurjpy", "gbpjpy", "vix"]


def spec_for(name, df):
    """Realistic cost + pip scaling inferred from the instrument's price level."""
    px = float(df["close"].median())
    if px > 5000:      spread, ps, pv = px * 0.00015, 1.0, 1.0
    elif px > 500:     spread, ps, pv = px * 0.00020, 1.0, 1.0
    elif px > 50:      spread, ps, pv = px * 0.00030, 0.01, 10.0
    elif px > 5:       spread, ps, pv = px * 0.00040, 0.01, 10.0
    elif px > 0.5:     spread, ps, pv = px * 0.00015, 0.0001, 10.0
    else:              spread, ps, pv = px * 0.00050, 0.0001, 10.0
    return spread, ps, pv, 0.0


def cfg_for(sym, ps, pv, hold):
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = RISK
    c.partial_tp_atr = 999.0
    c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False
    c.max_hold_bars = hold
    c.pip_size[sym] = ps
    c.pip_value_usd_approx[sym] = pv
    return c


def run(d, sym, spread, comm, ps, pv, ch, tp, trail, act, hold):
    s = DonchianBreakout()
    s.CHANNEL = ch
    s.sl_atr, s.tp_atr = 3.0, tp
    s.trail_atr_mult, s.trail_activation_atr = trail, act
    s.precompute(d)
    eng = BacktestEngine(d, cfg_for(sym, ps, pv, hold), s,
                          spread_price=spread, commission_per_lot=comm, symbol=sym)
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START), eng.trades


def pick_channel(d, sym, spread, comm, ps, pv, hold):
    n = len(d["c"]); mid = n // 2
    best, bpf = None, -1
    for ch in CHANNELS:
        dd = {k: (v[:mid] if hasattr(v, "__len__") else v) for k, v in d.items()}
        try:
            m, _ = run(dd, sym, spread, comm, ps, pv, ch, 7.0, 999.0, 999.0, hold)
        except Exception:
            continue
        if m and m.get("trades", 0) >= 12 and m["profit_factor"] > bpf:
            bpf, best = m["profit_factor"], ch
    return best


def collect_all():
    """All markets -> (name, d, sym, spread, comm, ps, pv, channel)."""
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START
    out = []
    for mkt, (csv, spread, ps, pv, comm) in OLD_MARKETS.items():
        try:
            d = prepare_data(load_daily(csv))
        except Exception:
            continue
        ch = pick_channel(d, mkt, spread, comm, ps, pv, 30)
        if ch: out.append((mkt, d, mkt, spread, comm, ps, pv, ch, False))
    for nm in NEW:
        path = f"download/{nm}-daily-yahoo.csv"
        try:
            df = load_daily(path); d = prepare_data(df)
        except Exception:
            continue
        spread, ps, pv, comm = spec_for(nm, df)
        ch = pick_channel(d, nm.upper(), spread, comm, ps, pv, 30)
        if ch: out.append((nm.upper(), d, nm.upper(), spread, comm, ps, pv, ch, False))
    for name, (csv, sym, spread, ps, pv, comm) in CRYPTO_DAILY.items():
        try:
            df, _ = loader.load(sym, 99.0, c0, csv_path=csv, allow_synthetic=False)
            d = prepare_data(resample(df, "1D"))
        except Exception:
            continue
        ch = pick_channel(d, sym, spread, comm, ps, pv, 30)
        if ch: out.append((name, d, sym, spread, comm, ps, pv, ch, True))
    return out


EXITS = [("TP7", 7.0, 999.0, 999.0),
         ("TP20", 20.0, 999.0, 999.0),
         ("noTP trail3.5@2", 999.0, 3.5, 2.0),
         ("noTP trail5.0@2", 999.0, 5.0, 2.0)]
HOLDS = [30, 100, 250]


def portfolio(series):
    allm = pd.concat(series, axis=1, sort=True)
    return allm, allm.mean(axis=1, skipna=True).dropna()


def main():
    print("collecting markets and picking channels (walk-forward, first half)...")
    targets = collect_all()
    print(f"  markets ready: {len(targets)}\n")

    print("=" * 100)
    print(" A) EXIT RULE x HOLDING PERIOD  (portfolio Sharpe, all markets equal weight)")
    print("=" * 100)
    print(f"  {'exit':<18}{'hold=30':>12}{'hold=100':>12}{'hold=250':>12}")
    grid = {}
    for lbl, tp, tr_m, act in EXITS:
        row = []
        for hold in HOLDS:
            series = {}
            for nm, d, sym, sp, cm, ps, pv, ch, _ in targets:
                try:
                    m, trd = run(d, sym, sp, cm, ps, pv, ch, tp, tr_m, act, hold)
                except Exception:
                    continue
                if m and m.get("trades", 0) >= 20:
                    mr = to_monthly(trd)
                    if len(mr) >= 24:
                        series[nm] = mr
            if len(series) < 8:
                row.append(float("nan")); continue
            _, port = portfolio(series)
            p = perf(port)
            row.append(p["sharpe"] if p else float("nan"))
            grid[(lbl, hold)] = (p, series)
        print(f"  {lbl:<18}" + "".join(f"{v:>12.2f}" for v in row))

    best_key = max((k for k in grid if grid[k][0]), key=lambda k: grid[k][0]["sharpe"])
    bp, bseries = grid[best_key]
    print(f"\n  BEST: exit={best_key[0]}  hold={best_key[1]}  Sharpe={bp['sharpe']:.2f}  "
          f"({len(bseries)} markets)")

    print("\n" + "=" * 100)
    print(" B) THE WIDE PORTFOLIO AT THE BEST SETTING")
    print("=" * 100)
    allm, port = portfolio(bseries)
    corr = allm.corr(); iu = np.triu_indices_from(corr.values, k=1)
    v = corr.values[iu]; v = v[~np.isnan(v)]
    print(f"  markets={len(bseries)}  months={len(port)}  mean corr={v.mean():+.3f}  "
          f"Sharpe={bp['sharpe']:.2f}  DD={bp['dd']:.2f}%")

    cry = [c for c in allm.columns if c in ("BTCD", "ETHD")]
    non = [c for c in allm.columns if c not in cry]
    for nm, cols in [("crypto only", cry), ("NON-crypto only", non)]:
        if len(cols) >= 2:
            p = perf(allm[cols].mean(axis=1, skipna=True).dropna())
            if p:
                print(f"    {nm:<18} edges={len(cols):>3}  Sharpe={p['sharpe']:>5.2f}")

    mod = allm.loc[pd.to_datetime(allm.index) >= "2017-01-01"]
    pm = perf(mod.mean(axis=1, skipna=True).dropna())
    if pm:
        print(f"    {'modern 2017-26':<18} Sharpe={pm['sharpe']:>5.2f}  DD={pm['dd']:.2f}%")

    print("\n" + "=" * 100)
    print(" C) WHAT IT SUPPORTS (full-history portfolio, implementable framing)")
    print("=" * 100)
    n_mkt = len(bseries)
    print(f"  running all {n_mkt} markets in parallel at a given per-trade risk:")
    print(f"  {'risk/trade':<14}{'portfolio DD':>14}{'CAGR%/yr':>12}{'%/day':>10}")
    for per_trade in [0.25, 0.50, 1.00, 1.50, 2.00]:
        k = (per_trade / RISK) * n_mkt      # each edge at per_trade, all live together
        p2 = perf(port * k)
        if p2:
            dpd = (1 + p2["cagr"] / 100) ** (1 / TRADING_DAYS) * 100 - 100
            print(f"  {per_trade:<14.2f}{p2['dd']:>13.1f}%{p2['cagr']:>12.2f}{dpd:>10.3f}")


if __name__ == "__main__":
    main()
