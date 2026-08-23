#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
What do the bots that are RUNNING RIGHT NOW actually return per year?

Measures the exact live configuration currently on the VPS, including the TP
values changed on 2026-07-28, at the cost the bots actually pay -- then
converts to CAGR so it is comparable to the H4 numbers.

Current live config (from the VPS process list):
  adx20tp7      M15  ADX>=20  SL3.0  TP4.0   risk 0.30%
  adx18tp7      M15  ADX>=18  SL3.0  TP1.0   risk 0.30%
  regime22      M15  ADX>=22  SL3.0  TP3.0   risk 0.30%  + regime filter
  adx20_manual  M15  ADX>=20  SL3.0  TP999   risk 0.30%  (manual exit)
  btc_cons/aggr M15  -- kill-switched, not opening new trades

Also shows the ORIGINAL config (TP7) for comparison, so the effect of the
2026-07-28 change is visible separately from the underlying strategy quality.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import (DataLoader, prepare_data, BacktestEngine,
                             FastHybridTrendPullback, compute_metrics)
from gold_regime_filter_real_engine import RegimeFilteredHybrid

GOLD_CSV = "download/xauusd-m15-bid-2013-01-01-2026-06-10.csv"
START = 10_000.0
RISK = 0.30
COMM = 3.50
LIVE_SPREAD = 2.85     # adx20tp7's own measured spread+slippage
PROP_SPREAD = 0.90     # price-proportional fair cost for full history


def cfg():
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = RISK
    c.partial_tp_atr = 999.0
    c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False
    c.max_hold_bars = 64
    return c


def run(cls, d, adx_min, sl, tp, spread):
    s = cls()
    s.ADX_MIN = adx_min
    s.sl_atr, s.tp_atr = sl, tp
    s.trail_atr_mult = s.trail_activation_atr = 999.0
    s.precompute(d)
    eng = BacktestEngine(d, cfg(), s, spread_price=spread,
                          commission_per_lot=COMM, symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START)


def report(m, label, years):
    if m is None or m.get("trades", 0) == 0:
        print(f"  {label:<34} NO TRADES"); return
    tot = m["total_return_pct"]
    if tot <= -100:
        cagr = -100.0
    else:
        cagr = ((1 + tot / 100.0) ** (1.0 / years) - 1) * 100
    print(f"  {label:<34} trades={m['trades']:>5}  PF={m['profit_factor']:>5.2f}  "
          f"TotRet={tot:>+8.1f}%  CAGR={cagr:>+7.2f}%/yr  MaxDD={m['max_dd_pct']:>5.1f}%")


# label, class, adx, sl, tp_now, tp_original
LIVE = [
    ("adx20tp7",     FastHybridTrendPullback, 20, 3.0, 4.0, 7.0),
    ("adx18tp7",     FastHybridTrendPullback, 18, 3.0, 1.0, 7.0),
    ("regime22",     RegimeFilteredHybrid,    22, 3.0, 3.0, 7.0),
    ("adx20_manual", FastHybridTrendPullback, 20, 3.0, 999.0, 999.0),
]


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START
    df, _ = loader.load("XAUUSD", 99.0, c0, csv_path=GOLD_CSV, allow_synthetic=True)

    d_full = prepare_data(df)
    yrs_full = (df["timestamp"].iloc[-1] - df["timestamp"].iloc[0]).days / 365.25

    df_rec = df[df["timestamp"] >= pd.Timestamp("2024-01-01")].reset_index(drop=True)
    d_rec = prepare_data(df_rec)
    yrs_rec = (df_rec["timestamp"].iloc[-1] - df_rec["timestamp"].iloc[0]).days / 365.25

    print(f"risk/trade = {RISK}%  (same as live)\n")

    print("=" * 106)
    print(f" A) CONFIG RUNNING NOW (TP changed 2026-07-28) -- recent era 2024-26, live cost ${LIVE_SPREAD}")
    print("=" * 106)
    for name, cls, adx, sl, tp_now, _ in LIVE:
        report(run(cls, d_rec, adx, sl, tp_now, LIVE_SPREAD), f"{name} (TP={tp_now})", yrs_rec)

    print("\n" + "=" * 106)
    print(f" B) ORIGINAL CONFIG (TP=7, before my change) -- recent era 2024-26, live cost ${LIVE_SPREAD}")
    print("=" * 106)
    for name, cls, adx, sl, _, tp_orig in LIVE:
        report(run(cls, d_rec, adx, sl, tp_orig, LIVE_SPREAD), f"{name} (TP={tp_orig})", yrs_rec)

    print("\n" + "=" * 106)
    print(f" C) ORIGINAL CONFIG over FULL 13.4y at fair proportional cost ${PROP_SPREAD}")
    print("=" * 106)
    for name, cls, adx, sl, _, tp_orig in LIVE:
        report(run(cls, d_full, adx, sl, tp_orig, PROP_SPREAD), f"{name} (TP={tp_orig})", yrs_full)

    print("\n" + "=" * 106)
    print(" D) WHAT ACTUALLY HAPPENED LIVE (real account, from MT5)")
    print("=" * 106)
    start_bal = 21826.0    # derived: current balance 17640.17 + realised loss 4185.83
    pnl = -4185.83
    days = 25              # 2026-07-04 -> 2026-07-29
    pct = pnl / start_bal * 100
    ann = ((1 + pct / 100.0) ** (365.0 / days) - 1) * 100
    print(f"  starting balance ~{start_bal:,.0f}   realised P&L {pnl:+,.2f}   over {days} days")
    print(f"  = {pct:+.1f}% in {days} days   (naive annualisation: {ann:+.1f}%/yr)")
    print(f"  NOTE: annualising 25 days is statistically meaningless -- shown only for scale.")


if __name__ == "__main__":
    main()
