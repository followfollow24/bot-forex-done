#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test the proven pullback strategy across 14 crypto pairs on H1.

Config is FROZEN from the BTC validation (ADX>=18, SL3/TP7) and applied
unchanged. No per-coin tuning -- if it only works on BTC, that shows up here,
and that is the answer we need.

Costs: modelled as a percentage of price rather than a fixed dollar amount,
because that is how crypto spreads actually behave and because a fixed $10
(correct for BTC at $60k) would be nonsense on DOGE at $0.15. 8bps per side is
a realistic retail crypto-CFD/spot spread; 20bps is stress.

The critical question is NOT whether each coin is profitable -- it is whether
they are independent. Twelve altcoins that all follow BTC are one bet, not
twelve, and the correlation matrix at the end decides how much this is worth.
"""
from __future__ import annotations
import os, sys, glob
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import (prepare_data, BacktestEngine,
                             FastHybridTrendPullback, compute_metrics)
from _idea_search import DonchianBreakout
from _all_paths import to_monthly, perf, START

RISK = 0.30
SPREAD_BPS = 8.0          # per side, realistic retail crypto
STRESS_BPS = 20.0
TRADING_DAYS = 252


def load_crypto_1h():
    out = {}
    for path in sorted(glob.glob("download/*usdt-1h-binance.csv")):
        name = os.path.basename(path).split("usdt")[0].upper()
        df = pd.read_csv(path)
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        out[name] = df[["timestamp", "open", "high", "low", "close"]].dropna()
    # BTC / ETH from the existing 15m files, resampled to 1h
    for nm, p in [("BTC", "download/btcusdt-15m-binance-2017-08-17-2026-06-30.csv"),
                  ("ETH", "download/ethusdt-15m-binance-2017-08-17-2026-06-30.csv")]:
        df = pd.read_csv(p)
        tcol = df.columns[0]
        ts = pd.to_datetime(df[tcol], unit="ms", errors="coerce")
        if ts.isna().all():
            ts = pd.to_datetime(df[tcol], errors="coerce")
        df["timestamp"] = ts
        s = df.dropna(subset=["timestamp"]).set_index("timestamp")
        agg = pd.DataFrame({
            "open": s["open"].resample("1h").first(),
            "high": s["high"].resample("1h").max(),
            "low":  s["low"].resample("1h").min(),
            "close": s["close"].resample("1h").last()}).dropna().reset_index()
        out[nm] = agg
    return out


def cfg(sym, ps, pv, hold=64):
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


def run(cls, d, sym, spread, ps, pv, **ov):
    s = cls()
    for k, v in ov.items():
        setattr(s, k, v)
    s.sl_atr, s.tp_atr = 3.0, 7.0
    s.trail_atr_mult = s.trail_activation_atr = 999.0
    s.precompute(d)
    eng = BacktestEngine(d, cfg(sym, ps, pv), s, spread_price=spread,
                          commission_per_lot=0.0, symbol=sym)
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START), eng.trades


def scale_for(px_med):
    """pip_size / pip_value that keep position sizing sane at any price level."""
    if px_med > 1000:  return 1.0, 0.01
    if px_med > 100:   return 0.1, 0.1
    if px_med > 1:     return 0.01, 1.0
    return 0.0001, 100.0


def main():
    data = load_crypto_1h()
    print(f"[data] {len(data)} crypto pairs on H1\n")

    print("=" * 108)
    print(f" pullback18 SL3/TP7 on H1, cost {SPREAD_BPS}bps/side  (config frozen from BTC)")
    print("=" * 108)
    print(f"  {'coin':<7}{'bars':>8}{'trades':>8}{'PF':>7}{'win%':>7}{'Sharpe':>9}"
          f"{'CAGR%':>9}{'DD%':>7}{'cost/ATR':>10}")
    series, ok = {}, []
    for nm, df in sorted(data.items()):
        d = prepare_data(df)
        px_med = float(df["close"].median())
        ps, pv = scale_for(px_med)
        spread = px_med * SPREAD_BPS / 1e4
        atr_med = float(np.nanmedian(d["atr"]))
        ratio = spread / atr_med * 100 if atr_med > 0 else np.nan
        try:
            m, tr = run(FastHybridTrendPullback, d, nm, spread, ps, pv, ADX_MIN=18)
        except Exception as e:
            print(f"  {nm:<7} ERROR {e}"); continue
        if not m or m.get("trades", 0) < 100:
            print(f"  {nm:<7}{len(df):>8}{m.get('trades',0):>8}  too few"); continue
        mr = to_monthly(tr); p = perf(mr)
        sh = p["sharpe"] if p else np.nan
        star = "  <==" if p and p["sharpe"] > 1.0 else ""
        print(f"  {nm:<7}{len(df):>8}{m['trades']:>8}{m['profit_factor']:>7.2f}"
              f"{m['win_rate']*100:>7.1f}{sh:>9.2f}{(p['cagr'] if p else 0):>9.2f}"
              f"{(p['dd'] if p else 0):>7.1f}{ratio:>9.1f}%{star}")
        if p and p["sharpe"] > 0.3 and m["profit_factor"] > 1.0:
            series[nm] = mr; ok.append(nm)

    print(f"\n  qualifying: {len(ok)}/{len(data)}  -> {', '.join(ok)}")
    if len(series) < 3:
        print("  not enough."); return

    allm = pd.concat(series, axis=1, sort=True)
    corr = allm.corr()
    iu = np.triu_indices_from(corr.values, k=1)
    v = corr.values[iu]; v = v[~np.isnan(v)]

    print("\n" + "=" * 108)
    print(" THE DECISIVE QUESTION: are these independent bets or one bet repeated?")
    print("=" * 108)
    print(f"  mean pairwise correlation = {v.mean():+.3f}   max = {v.max():+.2f}")
    n_eff = len(series) / (1 + (len(series) - 1) * max(v.mean(), 0))
    print(f"  nominal edges = {len(series)}   EFFECTIVE independent bets = {n_eff:.1f}")
    if v.mean() > 0.5:
        print("  -> highly correlated: this is close to ONE crypto bet, not many")
    elif v.mean() > 0.25:
        print("  -> moderately correlated: real but much less than the count suggests")
    else:
        print("  -> genuinely diversifying")

    print("\n" + "=" * 108)
    print(" PORTFOLIO")
    print("=" * 108)
    port = allm.mean(axis=1, skipna=True).dropna()
    p = perf(port, )
    if p:
        print(f"  equal weight: months={p['n']}  Sharpe={p['sharpe']:.2f}  "
              f"CAGR={p['cagr']:+.2f}%  DD={p['dd']:.2f}%")
        print(f"\n  {'risk/trade':<14}{'DD%':>10}{'CAGR%/yr':>12}{'%/day':>10}")
        for pt in [0.25, 0.50, 1.00, 1.50]:
            k = (pt / RISK) * len(series)
            p2 = perf(port * k)
            if p2:
                dpd = (1 + p2["cagr"] / 100) ** (1 / TRADING_DAYS) * 100 - 100
                print(f"  {pt:<14.2f}{p2['dd']:>9.1f}%{p2['cagr']:>12.2f}{dpd:>10.3f}")

    print("\n" + "=" * 108)
    print(f" COST STRESS at {STRESS_BPS}bps/side")
    print("=" * 108)
    s2 = {}
    for nm in ok:
        df = data[nm]; d = prepare_data(df)
        px_med = float(df["close"].median()); ps, pv = scale_for(px_med)
        m, tr = run(FastHybridTrendPullback, d, nm, px_med * STRESS_BPS / 1e4, ps, pv, ADX_MIN=18)
        if m and m.get("trades", 0) >= 50:
            mr = to_monthly(tr)
            if len(mr) >= 24: s2[nm] = mr
    if len(s2) >= 3:
        p3 = perf(pd.concat(s2, axis=1, sort=True).mean(axis=1, skipna=True).dropna())
        if p3:
            print(f"  portfolio at {STRESS_BPS}bps: Sharpe={p3['sharpe']:.2f}  "
                  f"CAGR={p3['cagr']:+.2f}%  DD={p3['dd']:.2f}%  ({len(s2)} coins survive)")


if __name__ == "__main__":
    main()
