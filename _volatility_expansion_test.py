#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Volatility Expansion Breakout -- reacts directly to "the market is moving
right now" instead of waiting for a pullback/session/sweep setup.

Rule: current bar's TRUE RANGE (not ATR, the raw range) exceeds
EXPANSION_MULT x the recent average ATR, AND the bar closes strongly in
one direction (close near the high for BUY, near the low for SELL). That
is a volatility-regime-shift signal -- fires the moment an outsized bar
appears, no waiting for confirmation candles or specific hours.

Tested on BTC/ETH/Gold H1, real costs, same causal discipline as everything
else this session (EXPANSION uses the PRIOR bar's ATR, never the current
bar's own range in the denominator, so there's no look-ahead).
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import DataLoader, prepare_data, BacktestEngine, compute_metrics
from forex_indicators import Signal
from _idea_search import resample
from _all_paths import to_monthly, perf, START


class VolExpansion:
    name = "Volatility Expansion Breakout"
    short_name = "VolExp"

    EXPANSION_MULT = 1.8       # current true range >= this x prior ATR
    CLOSE_POS_MIN = 0.65       # close must be in the outer 35% of the bar's range
    sl_atr = 1.5
    tp_atr = 999.0
    trail_atr_mult = 2.5
    trail_activation_atr = 0.8
    max_spread_atr_ratio = 0.5
    MIN_BARS = 60

    def precompute(self, d):
        pass

    def signal(self, d, i):
        if i < self.MIN_BARS:
            return Signal()
        atr_prev = d["atr"][i - 1]
        if np.isnan(atr_prev) or atr_prev <= 0:
            return Signal()
        o, h, l, c = d["o"][i], d["h"][i], d["l"][i], d["c"][i]
        c_prev = d["c"][i - 1]
        tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
        rng = h - l
        if rng <= 0:
            return Signal()
        close_pos = (c - l) / rng

        if tr >= self.EXPANSION_MULT * atr_prev:
            if close_pos >= self.CLOSE_POS_MIN and c > o:
                return Signal("BUY", f"volexp tr={tr:.2f} atr_prev={atr_prev:.2f}")
            if close_pos <= 1 - self.CLOSE_POS_MIN and c < o:
                return Signal("SELL", f"volexp tr={tr:.2f} atr_prev={atr_prev:.2f}")
        return Signal()


def cfg(risk=1.0, hold=32):
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = risk
    c.partial_tp_atr = 999.0; c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False; c.max_hold_bars = hold
    return c


def run(d, sym, spread, ps=None, pv=None, comm=0.0, risk=1.0, **kw):
    s = VolExpansion()
    for k, v in kw.items(): setattr(s, k, v)
    s.precompute(d)
    c = cfg(risk)
    if ps is not None:
        c.pip_size[sym] = ps; c.pip_value_usd_approx[sym] = pv
    eng = BacktestEngine(d, c, s, spread_price=spread, commission_per_lot=comm, symbol=sym)
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START), eng.trades


def line(m, tr, label, yrs):
    if not m or m.get("trades", 0) < 20:
        print(f"    {label:<28} n={m.get('trades',0) if m else 0:>5}  too few"); return
    p = perf(to_monthly(tr)); sh = p["sharpe"] if p else float("nan")
    tot = m["total_return_pct"]
    cg = -100.0 if tot <= -100 else ((1+tot/100)**(1/yrs)-1)*100
    print(f"    {label:<28} n={m['trades']:>5} ({m['trades']/yrs:>4.0f}/yr {m['trades']/yrs/365:.2f}/day)  "
          f"win%={m['win_rate']*100:>5.1f}  PF={m['profit_factor']:>5.2f}  Sharpe={sh:>5.2f}  "
          f"CAGR={cg:>+7.2f}%  DD={m['max_dd_pct']:>5.1f}%")


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START
    dfg, _ = loader.load("XAUUSD", 99.0, c0, csv_path="download/xauusd-m15-bid-2013-01-01-2026-06-10.csv", allow_synthetic=True)
    dfb, _ = loader.load("BTCUSDc", 99.0, c0, csv_path="download/btcusdt-15m-binance-2017-08-17-2026-06-30.csv", allow_synthetic=False)
    dfe, _ = loader.load("ETHUSDc", 99.0, c0, csv_path="download/ethusdt-15m-binance-2017-08-17-2026-06-30.csv", allow_synthetic=False)

    markets = [
        ("GOLD H1", resample(dfg, "1h"), "XAUUSD", 0.24, None, None, 3.5, 0.30),
        ("BTC  H1", resample(dfb, "1h"), "BTCUSDc", 10.0, 1.0, 0.01, 0.0, 1.00),
        ("ETH  H1", resample(dfe, "1h"), "ETHUSDc", 1.0, 1.0, 0.01, 0.0, 1.00),
    ]

    print("=" * 100)
    print(" VOLATILITY EXPANSION BREAKOUT -- H1, real costs, EXPANSION_MULT sweep")
    print("=" * 100)
    for label, df, sym, sp, ps, pv, comm, risk in markets:
        yrs = (df["timestamp"].iloc[-1]-df["timestamp"].iloc[0]).days/365.25
        d = prepare_data(df)
        print(f"\n  {label} ({yrs:.1f}y, risk={risk}%)")
        for mult in [1.4, 1.6, 1.8, 2.0, 2.5]:
            m, tr = run(d, sym, sp, ps, pv, comm, risk, EXPANSION_MULT=mult)
            line(m, tr, f"mult={mult}", yrs)

    # OOS + yearly for the best-looking BTC config once identified above
    print("\n" + "=" * 100)
    print(" OOS + yearly WF (BTC H1, mult=1.8 as a representative check)")
    print("=" * 100)
    dfb_h1 = resample(dfb, "1h")
    mid = dfb_h1["timestamp"].iloc[len(dfb_h1)//2]
    tr_df = dfb_h1[dfb_h1["timestamp"]<=mid].reset_index(drop=True)
    te_df = dfb_h1[dfb_h1["timestamp"]> mid].reset_index(drop=True)
    y_tr=(tr_df["timestamp"].iloc[-1]-tr_df["timestamp"].iloc[0]).days/365.25
    y_te=(te_df["timestamp"].iloc[-1]-te_df["timestamp"].iloc[0]).days/365.25
    m,tr = run(prepare_data(tr_df), "BTCUSDc", 10.0, 1.0, 0.01, risk=1.0, EXPANSION_MULT=1.8)
    line(m,tr,"1st half (train)", y_tr)
    m,tr = run(prepare_data(te_df), "BTCUSDc", 10.0, 1.0, 0.01, risk=1.0, EXPANSION_MULT=1.8)
    line(m,tr,"2nd half (OOS)", y_te)


if __name__ == "__main__":
    main()
