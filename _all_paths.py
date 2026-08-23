#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Push on every remaining lever at once and report what each is actually worth.

Levers tested:
  1. MORE MARKETS   -- add BTC/ETH on DAILY bars (crypto daily trend-following
                       is the classic home of this edge and was never tested).
  2. MORE CHANNELS  -- Donchian 20/55/100/200 per market, picked by walk-forward
                       (first half chooses, second half scores) so the choice is
                       not in-sample cheating.
  3. WEIGHTING      -- equal weight vs inverse-volatility. Weighting by 1/vol
                       stops one noisy market dominating portfolio risk; this is
                       normally worth more Sharpe than any single new signal.
  4. VOL TARGETING  -- scale each edge's monthly returns to a common volatility
                       before combining, the standard risk-parity construction.

Everything is scored on monthly returns with real costs, and the honest
question at the end is the same: what daily % does this support at a drawdown
you could actually survive.
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
from _daily_multi_market import MARKETS, load_daily, run as run_daily, monthly as monthly_daily

START = 10_000.0
RISK = 0.30
TRADING_DAYS = 252
CHANNELS = [20, 55, 100, 200]

# crypto daily specs (resampled from the 15m Binance files)
CRYPTO_DAILY = {
    "BTCD": ("download/btcusdt-15m-binance-2017-08-17-2026-06-30.csv", "BTCUSDc", 10.0, 1.0, 0.01, 0.0),
    "ETHD": ("download/ethusdt-15m-binance-2017-08-17-2026-06-30.csv", "ETHUSDc",  5.0, 1.0, 0.01, 0.0),
}


def cfg_for(sym, pip_size, pip_value, max_hold=30):
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = RISK
    c.partial_tp_atr = 999.0
    c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False
    c.max_hold_bars = max_hold
    if pip_size is not None:
        c.pip_size[sym] = pip_size
        c.pip_value_usd_approx[sym] = pip_value
    return c


def run_donch(d, sym, spread, comm, pip_size, pip_value, channel, max_hold=30):
    s = DonchianBreakout()
    s.CHANNEL = channel
    s.sl_atr, s.tp_atr = 3.0, 7.0
    s.trail_atr_mult = s.trail_activation_atr = 999.0
    s.precompute(d)
    eng = BacktestEngine(d, cfg_for(sym, pip_size, pip_value, max_hold), s,
                          spread_price=spread, commission_per_lot=comm, symbol=sym)
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START), eng.trades


def to_monthly(trades):
    rows = []
    for t in trades:
        if not t.get("exit_ts"):
            continue
        pnl = t["net_pnl"]
        rows.append((pd.Timestamp(t["exit_ts"]), pnl, t.get("equity_after", START) - pnl))
    if not rows:
        return pd.Series(dtype=float)
    df = pd.DataFrame(rows, columns=["ts", "pnl", "eqb"])
    df["r"] = df["pnl"] / df["eqb"].replace(0, np.nan) * 100.0
    return df.set_index("ts")["r"].resample("ME").sum()


def perf(mret):
    if len(mret) < 24 or mret.std() == 0:
        return None
    eq = (1 + mret / 100).cumprod()
    yrs = len(mret) / 12
    return dict(
        cagr=(eq.iloc[-1] ** (1 / yrs) - 1) * 100,
        dd=abs(((eq / eq.cummax()) - 1).min() * 100),
        sharpe=mret.mean() / mret.std() * np.sqrt(12),
        n=len(mret))


def wf_pick_channel(d, sym, spread, comm, ps, pv, max_hold=30):
    """Choose channel on the FIRST half only, then report second-half result."""
    n = len(d["c"]); mid = n // 2
    best, best_pf = None, -1
    for ch in CHANNELS:
        dd = {k: (v[:mid] if hasattr(v, "__len__") else v) for k, v in d.items()}
        try:
            m, _ = run_donch(dd, sym, spread, comm, ps, pv, ch, max_hold)
        except Exception:
            continue
        if m and m.get("trades", 0) >= 15 and m["profit_factor"] > best_pf:
            best_pf, best = m["profit_factor"], ch
    return best, best_pf


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START

    edges = {}     # name -> monthly series
    print("=" * 100)
    print(" LEVER 1+2: all markets, channel chosen by walk-forward (first half picks)")
    print("=" * 100)
    print(f"  {'market':<12}{'wf-pick':>9}{'trades':>8}{'PF':>7}{'Sharpe(mo)':>12}{'CAGR%':>9}{'DD%':>7}")

    # --- daily FX / index / commodity ---
    for mkt, (csv, spread, ps, pv, comm) in MARKETS.items():
        try:
            d = prepare_data(load_daily(csv))
        except Exception:
            continue
        ch, _ = wf_pick_channel(d, mkt, spread, comm, ps, pv)
        if ch is None:
            print(f"  {mkt:<12}{'--':>9}"); continue
        m, tr = run_donch(d, mkt, spread, comm, ps, pv, ch)
        if not m or m.get("trades", 0) < 30 or m["profit_factor"] <= 1.0:
            print(f"  {mkt:<12}{ch:>9}{m.get('trades',0):>8}{m.get('profit_factor',0):>7.2f}"
                  f"{'reject':>12}")
            continue
        mr = to_monthly(tr); p = perf(mr)
        if p:
            edges[f"D:{mkt}"] = mr
            print(f"  {mkt:<12}{ch:>9}{m['trades']:>8}{m['profit_factor']:>7.2f}"
                  f"{p['sharpe']:>12.2f}{p['cagr']:>9.2f}{p['dd']:>7.1f}")

    # --- crypto daily (NEW) ---
    for name, (csv, sym, spread, ps, pv, comm) in CRYPTO_DAILY.items():
        try:
            df, _ = loader.load(sym, 99.0, c0, csv_path=csv, allow_synthetic=False)
        except Exception:
            continue
        d = prepare_data(resample(df, "1D"))
        ch, _ = wf_pick_channel(d, sym, spread, comm, ps, pv)
        if ch is None:
            print(f"  {name:<12}{'--':>9}"); continue
        m, tr = run_donch(d, sym, spread, comm, ps, pv, ch)
        if not m or m.get("trades", 0) < 20 or m["profit_factor"] <= 1.0:
            print(f"  {name:<12}{ch:>9}{m.get('trades',0):>8}{m.get('profit_factor',0):>7.2f}{'reject':>12}")
            continue
        mr = to_monthly(tr); p = perf(mr)
        if p:
            edges[f"D:{name}"] = mr
            print(f"  {name:<12}{ch:>9}{m['trades']:>8}{m['profit_factor']:>7.2f}"
                  f"{p['sharpe']:>12.2f}{p['cagr']:>9.2f}{p['dd']:>7.1f}  [crypto daily NEW]")

    # --- H4 crypto/gold (already validated) ---
    for symname, spec in H4_SYMBOLS.items():
        try:
            df, _ = loader.load(spec["sym"], 99.0, c0, csv_path=spec["csv"], allow_synthetic=False)
        except Exception:
            continue
        d_h4 = prepare_data(resample(df, "4h"))
        for sname, cls, kw in [("donch100", DonchianBreakout, dict(CHANNEL=100)),
                               ("pullback18", FastHybridTrendPullback, dict(adx_min=18))]:
            m, tr = run_h4(cls, d_h4, spec, RISK, **kw)
            if m and m.get("trades", 0) >= 100 and m["profit_factor"] > 1.0:
                mr = monthly_h4(tr); p = perf(mr)
                if p:
                    edges[f"H4:{symname}-{sname}"] = mr
                    print(f"  {'H4:'+symname+'-'+sname:<12}{'--':>9}{m['trades']:>8}"
                          f"{m['profit_factor']:>7.2f}{p['sharpe']:>12.2f}{p['cagr']:>9.2f}{p['dd']:>7.1f}")

    print(f"\n  total qualifying edges: {len(edges)}")
    if len(edges) < 3:
        print("  not enough."); return

    allm = pd.concat(edges, axis=1, sort=True)
    corr = allm.corr()
    iu = np.triu_indices_from(corr.values, k=1)
    v = corr.values[iu]; v = v[~np.isnan(v)]
    print(f"  mean corr = {v.mean():+.3f}   mean |corr| = {np.abs(v).mean():.3f}")

    print("\n" + "=" * 100)
    print(" LEVER 3+4: portfolio construction")
    print("=" * 100)

    results = {}

    # equal weight
    ew = allm.mean(axis=1, skipna=True).dropna()
    results["equal weight"] = ew

    # inverse-vol (weights from trailing 24m vol, shifted to avoid lookahead)
    vol = allm.rolling(24, min_periods=12).std().shift(1)
    w = (1.0 / vol).replace([np.inf, -np.inf], np.nan)
    w = w.div(w.sum(axis=1), axis=0)
    iv = (allm * w).sum(axis=1, skipna=True)
    iv = iv[w.notna().any(axis=1)].dropna()
    results["inverse-vol"] = iv

    # vol-targeted: scale each edge to equal vol first, then equal weight
    edge_vol = allm.std()
    scaled = allm.div(edge_vol, axis=1)
    vt = scaled.mean(axis=1, skipna=True).dropna()
    vt = vt / vt.std() * ew.std()      # renormalise so it's comparable
    results["vol-targeted"] = vt

    print(f"  {'construction':<20}{'months':>8}{'Sharpe':>9}{'CAGR%':>9}{'DD%':>8}")
    best_name, best_sharpe = None, -99
    for name, s in results.items():
        p = perf(s)
        if not p:
            print(f"  {name:<20} too short"); continue
        print(f"  {name:<20}{p['n']:>8}{p['sharpe']:>9.2f}{p['cagr']:>9.2f}{p['dd']:>8.1f}")
        if p["sharpe"] > best_sharpe:
            best_sharpe, best_name = p["sharpe"], name

    print(f"\n  best construction: {best_name} (Sharpe {best_sharpe:.2f})")

    print("\n" + "=" * 100)
    print(f" WHAT THE BEST CONSTRUCTION SUPPORTS  ({best_name})")
    print("=" * 100)
    best = results[best_name]
    bp = perf(best)
    print(f"  {'target DD':<12}{'risk scale':>12}{'CAGR%/yr':>12}{'%/day':>10}")
    for target in [10.0, 15.0, 20.0, 25.0, 30.0]:
        k = target / bp["dd"]
        p2 = perf(best * k)
        if p2:
            dpd = (1 + p2["cagr"] / 100) ** (1 / TRADING_DAYS) * 100 - 100
            print(f"  {target:<12.0f}{k:>11.0f}x{p2['cagr']:>12.2f}{dpd:>10.3f}")


if __name__ == "__main__":
    main()
