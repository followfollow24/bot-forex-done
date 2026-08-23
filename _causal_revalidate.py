#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Re-validate BTC/ETH/Gold fresh-filter configs using the TRUE causal path
(what the live bot actually does), not the vectorized precompute() path (which
_build_h1_trend_array uses, and which can read up to 3 bars into the future
within an incomplete H1/H4 bucket -- see forex_hybrid_strategy.py's
FreshTrendFilterMixin docstring for how this was found).

This is slow (O(n) per bar instead of O(1)), so it runs on a capped recent
window rather than full history -- enough to get an honest read on whether
the edge survives, not a full walk-forward.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import DataLoader, prepare_data, BacktestEngine, compute_metrics
from forex_hybrid_strategy import HybridTrendPullback, FreshTrendFilterMixin
from gold_regime_live_strategy import RegimeFilteredHybridLive
from _idea_search import resample
from _all_paths import to_monthly, perf, START

GOLD_M15 = "download/xauusd-m15-bid-2013-01-01-2026-06-10.csv"
BTC_CSV  = "download/btcusdt-15m-binance-2017-08-17-2026-06-30.csv"
ETH_CSV  = "download/ethusdt-15m-binance-2017-08-17-2026-06-30.csv"


class CausalOnly:
    """Disables precompute() so every signal() call goes through the base
    class's causal _h1_trend(d, i) fallback -- exactly what live does.
    _trend_maturity() in the Fresh mixin already truncates d to i+1 itself,
    so it stays causal with or without this, but the BASE trend direction
    only becomes causal when _h1_trend_arr is never populated."""
    def precompute(self, d):
        pass  # deliberately does NOT call super().precompute(); _h1_trend_arr stays None


class CausalPullback(CausalOnly, FreshTrendFilterMixin, HybridTrendPullback):
    pass


class CausalRegime(CausalOnly, FreshTrendFilterMixin, RegimeFilteredHybridLive):
    pass


class CausalPullbackNoFilter(CausalOnly, HybridTrendPullback):
    pass


class CausalRegimeNoFilter(CausalOnly, RegimeFilteredHybridLive):
    pass


def cfg(sym, ps=None, pv=None, hold=64, risk=0.30):
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = risk
    c.partial_tp_atr = 999.0
    c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False
    c.max_hold_bars = hold
    if ps is not None:
        c.pip_size[sym] = ps
        c.pip_value_usd_approx[sym] = pv
    return c


def run(cls, d, sym, spread, adx, comm=3.5, ps=None, pv=None, maxmat=None, risk=0.30):
    s = cls()
    s.ADX_MIN = adx
    if maxmat is not None and hasattr(s, "MAX_MATURITY"):
        s.MAX_MATURITY = maxmat
    s.sl_atr, s.tp_atr = 3.0, 999.0
    s.trail_atr_mult = s.trail_activation_atr = 999.0
    s.precompute(d)   # no-op for CausalOnly, but harmless to call
    eng = BacktestEngine(d, cfg(sym, ps, pv, risk=risk), s, spread_price=spread,
                          commission_per_lot=comm, symbol=sym)
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START), eng.trades


def line(m, tr, label, yrs):
    if not m or m.get("trades", 0) < 15:
        print(f"    {label:<28} n={m.get('trades',0) if m else 0:>5}  too few")
        return
    p = perf(to_monthly(tr))
    sh = p["sharpe"] if p else float("nan")
    tot = m["total_return_pct"]
    cg = -100.0 if tot <= -100 else ((1+tot/100)**(1/yrs)-1)*100
    print(f"    {label:<28} n={m['trades']:>5}  PF={m['profit_factor']:>5.2f}  "
          f"Sharpe={sh:>5.2f}  CAGR={cg:>+7.2f}%  DD={m['max_dd_pct']:>5.1f}%")


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START

    # cap to a recent, manageable window since causal mode is O(n) per bar
    # (M15 crypto over even 6mo is ~17k bars -> O(n^2) blows up; 60 days keeps
    # it tractable while still being a real, recent, out-of-original-sample check)
    WINDOW_START = "2026-05-01"

    print("=" * 100)
    print(f" CAUSAL RE-VALIDATION (no look-ahead), window from {WINDOW_START}")
    print(" comparing: precomputed(reported earlier) vs TRUE causal, same window/config")
    print("=" * 100)

    dfg, _ = loader.load("XAUUSD", 99.0, c0, csv_path=GOLD_M15, allow_synthetic=True)
    dfg_h1 = resample(dfg, "1h")
    dfg_h1 = dfg_h1[dfg_h1["timestamp"] >= pd.Timestamp(WINDOW_START)].reset_index(drop=True)
    yg = (dfg_h1["timestamp"].iloc[-1] - dfg_h1["timestamp"].iloc[0]).days / 365.25
    dg = prepare_data(dfg_h1)

    dfb, _ = loader.load("BTCUSDc", 99.0, c0, csv_path=BTC_CSV, allow_synthetic=False)
    dfb = dfb[dfb["timestamp"] >= pd.Timestamp(WINDOW_START)].reset_index(drop=True)
    yb = (dfb["timestamp"].iloc[-1] - dfb["timestamp"].iloc[0]).days / 365.25
    db = prepare_data(dfb)

    dfe, _ = loader.load("ETHUSDc", 99.0, c0, csv_path=ETH_CSV, allow_synthetic=False)
    dfe = dfe[dfe["timestamp"] >= pd.Timestamp(WINDOW_START)].reset_index(drop=True)
    ye = (dfe["timestamp"].iloc[-1] - dfe["timestamp"].iloc[0]).days / 365.25
    de = prepare_data(dfe)

    print(f"\n  GOLD H1 regime22+fresh10, risk=0.30% ({yg:.1f}y)")
    from gold_regime_live_strategy import FreshRegimeFilteredHybridLive
    m, tr = run(FreshRegimeFilteredHybridLive, dg, "XAUUSD", 2.85, 22, maxmat=10, risk=0.30)
    line(m, tr, "PRECOMPUTED (fast, has lookahead)", yg)
    m, tr = run(CausalRegime, dg, "XAUUSD", 2.85, 22, maxmat=10, risk=0.30)
    line(m, tr, "CAUSAL (true, no lookahead)", yg)
    m, tr = run(CausalRegimeNoFilter, dg, "XAUUSD", 2.85, 22, risk=0.30)
    line(m, tr, "CAUSAL, no fresh-filter (baseline)", yg)

    print(f"\n  BTC M15 fresh5, risk=1.00% ({yb:.1f}y)")
    from forex_hybrid_strategy import FreshHybridTrendPullback
    m, tr = run(FreshHybridTrendPullback, db, "BTCUSDc", 10.0, 18, comm=0.0, ps=1.0, pv=0.01, maxmat=5, risk=1.00)
    line(m, tr, "PRECOMPUTED (fast, has lookahead)", yb)
    m, tr = run(CausalPullback, db, "BTCUSDc", 10.0, 18, comm=0.0, ps=1.0, pv=0.01, maxmat=5, risk=1.00)
    line(m, tr, "CAUSAL (true, no lookahead)", yb)
    m, tr = run(CausalPullbackNoFilter, db, "BTCUSDc", 10.0, 18, comm=0.0, ps=1.0, pv=0.01, risk=1.00)
    line(m, tr, "CAUSAL, no fresh-filter (baseline)", yb)

    print(f"\n  ETH M15 fresh3, risk=1.00% ({ye:.1f}y)")
    m, tr = run(FreshHybridTrendPullback, de, "ETHUSDc", 1.0, 18, comm=0.0, ps=1.0, pv=0.01, maxmat=3, risk=1.00)
    line(m, tr, "PRECOMPUTED (fast, has lookahead)", ye)
    m, tr = run(CausalPullback, de, "ETHUSDc", 1.0, 18, comm=0.0, ps=1.0, pv=0.01, maxmat=3, risk=1.00)
    line(m, tr, "CAUSAL (true, no lookahead)", ye)
    m, tr = run(CausalPullbackNoFilter, de, "ETHUSDc", 1.0, 18, comm=0.0, ps=1.0, pv=0.01, risk=1.00)
    line(m, tr, "CAUSAL, no fresh-filter (baseline)", ye)


if __name__ == "__main__":
    main()
