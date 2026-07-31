#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gold breakout/momentum search -- trend-pullback (M15/H1/H4/Daily) all failed
or turned out to be regime-luck (see _gold_new_edge_search.py /
_gold_daily_weekly_validate.py: the one good-looking config was just the
2024-2026 gold bull run). This tries a structurally different signal:
Donchian-channel breakout, using d["donch_hi"]/d["donch_lo"] which are
ALREADY correctly causal (rolling().max()/.min().shift(1) on whatever
timeframe the input df is at -- no bucket-resampling bug possible here,
since there's no cross-timeframe aggregation at all).

Tested at H4 and Daily (cost/ATR 53% and 18.5% respectively -- M15/H1 are
cost-prohibitive, established earlier this session).
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

GOLD_M15 = "download/xauusd-m15-bid-2013-01-01-2026-06-10.csv"
SPREAD, COMM = 2.85, 3.50


class DonchianBreakout:
    """Enter on a close beyond the N-bar Donchian channel (donch_hi/donch_lo,
    already shift(1)'d causal in the data pipeline). ATR stop, optional ATR
    trailing exit (no fixed TP -- momentum trades should be let to run)."""
    name = "Donchian Breakout"
    short_name = "DonchBO"

    sl_atr = 2.0
    tp_atr = 999.0
    trail_atr_mult = 3.0
    trail_activation_atr = 1.0
    max_spread_atr_ratio = 0.5

    MIN_BARS = 60
    BREAKOUT_MARGIN_ATR = 0.0   # require close beyond band by this x ATR (0 = touch is enough)

    def precompute(self, d):
        pass

    def signal(self, d, i):
        if i < self.MIN_BARS:
            return Signal()
        hi = d["donch_hi"][i]
        lo = d["donch_lo"][i]
        c = d["c"][i]
        atr = d["atr"][i]
        if np.isnan(hi) or np.isnan(lo) or np.isnan(atr) or atr <= 0:
            return Signal()
        margin = self.BREAKOUT_MARGIN_ATR * atr
        if c > hi + margin:
            return Signal("BUY", f"breakout>donch_hi={hi:.2f}")
        if c < lo - margin:
            return Signal("SELL", f"breakout<donch_lo={lo:.2f}")
        return Signal()


def cfg(risk=0.50, hold=40):
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = risk
    c.partial_tp_atr = 999.0
    c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False
    c.max_hold_bars = hold
    return c


def run(d, sl_atr, trail_mult, trail_act, risk=0.50, hold=40, margin=0.0):
    s = DonchianBreakout()
    s.sl_atr = sl_atr
    s.tp_atr = 999.0
    s.trail_atr_mult = trail_mult
    s.trail_activation_atr = trail_act
    s.BREAKOUT_MARGIN_ATR = margin
    eng = BacktestEngine(d, cfg(risk, hold), s, spread_price=SPREAD,
                          commission_per_lot=COMM, symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START), eng.trades


def line(m, tr, label, yrs):
    if not m or m.get("trades", 0) < 15:
        print(f"    {label:<40} n={m.get('trades',0) if m else 0:>5}  too few")
        return
    p = perf(to_monthly(tr))
    sh = p["sharpe"] if p else float("nan")
    tot = m["total_return_pct"]
    cg = -100.0 if tot <= -100 else ((1+tot/100)**(1/yrs)-1)*100
    print(f"    {label:<40} n={m['trades']:>5}  PF={m['profit_factor']:>5.2f}  "
          f"Sharpe={sh:>5.2f}  CAGR={cg:>+7.2f}%  DD={m['max_dd_pct']:>5.1f}%  "
          f"({m['trades']/yrs:>4.0f} trades/yr)")


def yearly_from_full_run(d_full, df_full, sl_atr, trail_mult, trail_act, risk, hold, margin, label):
    print(f"\n  {label} -- per-year breakdown (single full-history run, correct warmup)")
    m, tr = run(d_full, sl_atr, trail_mult, trail_act, risk, hold, margin)
    if not tr:
        print("    no trades"); return
    df_tr = pd.DataFrame(tr)
    df_tr["entry_ts"] = pd.to_datetime(df_tr["entry_ts"])
    df_tr["year"] = df_tr["entry_ts"].dt.year
    for y, grp in df_tr.groupby("year"):
        wins = grp[grp["net_pnl"] > 0]["net_pnl"].sum()
        losses = -grp[grp["net_pnl"] < 0]["net_pnl"].sum()
        pf = wins / losses if losses > 0 else float("inf")
        print(f"    {y}: n={len(grp):>3}  PF={pf:>5.2f}  net=${grp['net_pnl'].sum():>+8.1f}")
    years_pf_gt1 = sum(1 for y, grp in df_tr.groupby("year")
                       if (grp[grp['net_pnl']>0]['net_pnl'].sum() /
                           max(-grp[grp['net_pnl']<0]['net_pnl'].sum(), 1e-9)) > 1.0)
    n_years = df_tr["year"].nunique()
    print(f"    -> years PF>1: {years_pf_gt1}/{n_years}")


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START
    dfg, _ = loader.load("XAUUSD", 99.0, c0, csv_path=GOLD_M15, allow_synthetic=True)

    print("=" * 100)
    print(" GOLD H4 -- Donchian breakout (spread=$2.85, comm=$3.50/lot)")
    print("=" * 100)
    df_h4 = resample(dfg, "4h")
    years_h4 = (df_h4["timestamp"].iloc[-1] - df_h4["timestamp"].iloc[0]).days / 365.25
    d_h4 = prepare_data(df_h4)
    for sl, tmult, tact in [(2.0, 3.0, 1.0), (2.5, 3.0, 1.0), (2.0, 999.0, 999.0)]:
        m, tr = run(d_h4, sl, tmult, tact, risk=0.50, hold=40)
        label = f"sl{sl} trail{tmult}x@{tact}" if tmult < 900 else f"sl{sl} no-trail(manual)"
        line(m, tr, label, years_h4)

    print("\n" + "=" * 100)
    print(" GOLD Daily -- Donchian breakout (spread=$2.85, comm=$3.50/lot)")
    print("=" * 100)
    df_d = resample(dfg, "1D")
    years_d = (df_d["timestamp"].iloc[-1] - df_d["timestamp"].iloc[0]).days / 365.25
    d_d = prepare_data(df_d)
    for sl, tmult, tact in [(2.0, 3.0, 1.0), (2.5, 3.0, 1.0), (2.0, 999.0, 999.0), (2.5, 999.0, 999.0)]:
        m, tr = run(d_d, sl, tmult, tact, risk=0.50, hold=20)
        label = f"sl{sl} trail{tmult}x@{tact}" if tmult < 900 else f"sl{sl} no-trail(manual)"
        line(m, tr, label, years_d)

    print("\n" + "=" * 100)
    print(" BEST H4/Daily CANDIDATES -- per-year breakdown check (regime-luck sniff test)")
    print("=" * 100)
    yearly_from_full_run(d_h4, df_h4, 2.0, 3.0, 1.0, 0.50, 40, 0.0, "H4 sl2.0 trail3.0x@1.0")
    yearly_from_full_run(d_d, df_d, 2.0, 3.0, 1.0, 0.50, 20, 0.0, "Daily sl2.0 trail3.0x@1.0")


if __name__ == "__main__":
    main()
