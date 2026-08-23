#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gold_breakeven_test.py -- Test move-to-breakeven on the real gold live-bot
engine (adx20tp7, adx18tp7 configs). Independent of the existing
cfg.move_sl_to_breakeven (which only fires on partial-TP hits, and partial-TP
is OFF for these live bots -- so that flag is currently dead code for them).

Rule tested: once unrealized price move reaches BE_TRIGGER_FRAC (0.5) of the
TP distance (i.e. 0.5 x TP_ATR x entry_atr), move SL to breakeven (entry
price). Triggers once per trade, independent of partial-TP.

Reports WITH vs WITHOUT breakeven, full history + train/test + WF-A yearly,
for both adx20tp7 and adx18tp7 -- same discipline as every other backtest
this session. Do not touch the live bot files until this is reviewed.
"""
from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forex_config import ForexConfig
from backtest_forex import (DataLoader, prepare_data, BacktestEngine,
                             FastHybridTrendPullback, compute_metrics)

GOLD_CSV = "download/xauusd-m15-bid-2013-01-01-2026-06-10.csv"
START = 10_000.0
RISK_PCT = 0.30
SPREAD, COMM = 0.10, 3.50
BE_TRIGGER_FRAC = 0.5   # move to breakeven at 50% of TP distance

CONFIGS = [
    ("adx20tp7", 3.0, 7.0, 20),
    ("adx18tp7", 3.0, 7.0, 18),
]
SPLIT = ("2013-01-01", "2020-01-01")


class BreakevenEngine(BacktestEngine):
    """Adds an independent move-to-breakeven trigger at BE_TRIGGER_FRAC of
    the TP distance, unrelated to partial-TP (which stays off)."""

    def _check_exit(self, h, l, c):
        pos = self.position
        if pos is not None:
            tp_dist = abs(pos.tp - pos.entry)
            trigger = BE_TRIGGER_FRAC * tp_dist
            if pos.side == "long":
                already_be = pos.sl >= pos.entry
                if not already_be and h >= pos.entry + trigger:
                    pos.sl = pos.entry
            else:
                already_be = pos.sl <= pos.entry
                if not already_be and l <= pos.entry - trigger:
                    pos.sl = pos.entry
        return super()._check_exit(h, l, c)


def gold_cfg():
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = RISK_PCT
    c.partial_tp_atr = 999.0
    c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False   # the OLD partial-tp-linked flag stays off
    return c


def run(engine_cls, df_full, sl, tp, adx, date_from=None, date_to=None):
    df = df_full
    if date_from:
        df = df[df["timestamp"] >= pd.Timestamp(date_from)]
    if date_to:
        df = df[df["timestamp"] < pd.Timestamp(date_to)]
    df = df.reset_index(drop=True)
    if len(df) < 1000:
        return None
    d = prepare_data(df)
    strat = FastHybridTrendPullback()
    strat.ADX_MIN = adx
    strat.precompute(d)
    strat.sl_atr, strat.tp_atr = sl, tp
    strat.trail_atr_mult, strat.trail_activation_atr = 999.0, 999.0
    eng = engine_cls(d, gold_cfg(), strat, spread_price=SPREAD,
                      commission_per_lot=COMM, symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START)


def fmt(m, label):
    if m is None or m.get("trades", 0) == 0:
        return f"  {label:<12} NO TRADES"
    return (f"  {label:<12} trades={m['trades']:>5}  win%={m['win_rate']*100:>5.1f}  "
            f"PF={m['profit_factor']:>5.2f}  Sharpe={m['sharpe']:>5.2f}  "
            f"MaxDD%={m['max_dd_pct']:>5.1f}  TotRet%={m['total_return_pct']:>+7.1f}  "
            f"AvgWin$={m['avg_win_usd']:>7.2f}  AvgLoss$={m['avg_loss_usd']:>7.2f}  "
            f"MaxLoseStreak={m['max_consec_losses']:>3}")


def main():
    print("=" * 100)
    print(f" MOVE-TO-BREAKEVEN TEST -- trigger at {BE_TRIGGER_FRAC*100:.0f}% of TP distance")
    print(" Real live-bot engine (FastHybridTrendPullback M15), WITH vs WITHOUT")
    print("=" * 100)

    loader = DataLoader(log_fn=lambda *a, **k: None)
    cfg0 = ForexConfig(); cfg0.total_capital_usd = START
    df_full, _ = loader.load("XAUUSD", 99.0, cfg0, csv_path=GOLD_CSV, allow_synthetic=True)
    print(f"[load] {len(df_full):,} bars\n")

    tr_from, tr_to = SPLIT
    for label, sl, tp, adx in CONFIGS:
        print(f"--- {label} ---")
        for tag, engine_cls in [("NO-BE (baseline)", BacktestEngine), ("WITH-BE", BreakevenEngine)]:
            m_full = run(engine_cls, df_full, sl, tp, adx)
            m_train = run(engine_cls, df_full, sl, tp, adx, date_to=tr_to)
            m_test = run(engine_cls, df_full, sl, tp, adx, date_from=tr_to)
            print(f" [{tag}]")
            print(fmt(m_full, "FULL"))
            print(fmt(m_train, "TRAIN"))
            print(fmt(m_test, "TEST"))
        print()

    print("=" * 100)
    print(" WF-A YEARLY -- adx20tp7 only, WITH-BE (frozen trigger, no re-fit)")
    print("=" * 100)
    label, sl, tp, adx = CONFIGS[0]
    years = range(df_full["timestamp"].min().year, df_full["timestamp"].max().year + 1)
    pf_gt1_nobe = pf_gt1_be = n_years = 0
    for y in years:
        m_nobe = run(BacktestEngine, df_full, sl, tp, adx, date_from=f"{y}-01-01", date_to=f"{y+1}-01-01")
        m_be = run(BreakevenEngine, df_full, sl, tp, adx, date_from=f"{y}-01-01", date_to=f"{y+1}-01-01")
        if m_nobe is None or m_nobe.get("trades", 0) == 0:
            continue
        n_years += 1
        if m_nobe["profit_factor"] > 1.0:
            pf_gt1_nobe += 1
        if m_be and m_be.get("profit_factor", 0) > 1.0:
            pf_gt1_be += 1
        print(f"  {y}: NO-BE PF={m_nobe['profit_factor']:.2f} DD={m_nobe['max_dd_pct']:.1f}%  |  "
              f"WITH-BE PF={m_be['profit_factor']:.2f} DD={m_be['max_dd_pct']:.1f}%" if m_be else f"  {y}: WITH-BE NO TRADES")
    print(f"\n  PF>1 years: NO-BE {pf_gt1_nobe}/{n_years}  |  WITH-BE {pf_gt1_be}/{n_years}")
    print("=" * 100)


if __name__ == "__main__":
    main()
