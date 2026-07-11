#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
report_4bot_backtest.py -- Pull real backtest metrics for the 4 LIVE configs
(adx20tp7, adx18tp7, btc_cons, btc_aggr) directly from the same engine/config
used by oos_validation.py / walk_forward_candidates.py (gold) and
btc_walkforward.py (BTC). No numbers from memory -- every value here is
computed fresh from the trade list this run produces.

Gold configs match deploy.ps1 exactly:
  adx20tp7: --sl-atr 3.0 --tp-atr 7.0 --adx-min 20
  adx18tp7: --sl-atr 3.0 --tp-atr 7.0 --adx-min 18
BTC configs match deploy.ps1 exactly:
  btc_cons: --sl-atr 4.0 --tp-atr 12.0 --adx-min 15
  btc_aggr: --sl-atr 2.5 --tp-atr 7.5  --adx-min 12

Gold split: Train 2013-01-01..2020-01-01 / Test 2020-01-01..end (matches
oos_validation.py's IS/OOS boundary).
BTC split:  Train ..2022-01-01 / Test 2022-01-01..end (matches
btc_walkforward.py's WF-B boundary).

RISK NOTE: this script runs at RISK_PCT=0.30% (the same value baked into
oos_validation.py/btc_walkforward.py, used for cross-comparability). The live
BTC bots run at --risk 0.20% (see deploy.ps1) -- return%/MaxDD% here scale
roughly linearly with risk and will be ~0.67x smaller live; PF/Sharpe/win-rate/
trade-count/streak are risk-invariant (unaffected by position sizing).
"""
from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forex_config import ForexConfig
from backtest_forex import (DataLoader, prepare_data, BacktestEngine,
                             FastHybridTrendPullback, compute_metrics)
from btc_walkforward import BTCEngine, make_cfg as make_btc_cfg, SPREAD as BTC_SPREAD, COMM as BTC_COMM

GOLD_CSV = "download/xauusd-m15-bid-2013-01-01-2026-06-10.csv"
BTC_CSV  = "download/btcusdt-15m-binance-2017-08-17-2026-06-30.csv"

START    = 10_000.0
RISK_PCT = 0.30
GOLD_SPREAD, GOLD_COMM = 0.10, 3.50

GOLD_CONFIGS = [
    ("adx20tp7", 3.0, 7.0, 20),
    ("adx18tp7", 3.0, 7.0, 18),
]
BTC_CONFIGS = [
    ("btc_cons", 4.0, 12.0, 15),
    ("btc_aggr", 2.5, 7.5, 12),
]

GOLD_SPLIT = ("2013-01-01", "2020-01-01")   # train_end == test_start
BTC_SPLIT  = ("2022-01-01",)


def gold_cfg():
    c = ForexConfig()
    c.total_capital_usd    = START
    c.risk_per_trade_pct   = RISK_PCT
    c.partial_tp_atr       = 999.0
    c.partial_tp_frac      = 0.0
    c.move_sl_to_breakeven = False
    return c


def run_gold(df_full, sl, tp, adx, date_from=None, date_to=None):
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
    eng = BacktestEngine(d, gold_cfg(), strat, spread_price=GOLD_SPREAD,
                          commission_per_lot=GOLD_COMM, symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START)


def run_btc(df_full, sl, tp, adx, date_from=None, date_to=None):
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
    eng = BTCEngine(d, make_btc_cfg(), strat, spread_price=BTC_SPREAD,
                     commission_per_lot=BTC_COMM, symbol="BTCUSDc")
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START)


def fmt_row(label, period, m):
    if m is None or m.get("trades", 0) == 0:
        return f"  {label:<10} {period:<7} NO TRADES / insufficient data"
    return (f"  {label:<10} {period:<7} "
            f"trades={m['trades']:>5}  win%={m['win_rate']*100:>5.1f}  "
            f"PF={m['profit_factor']:>5.2f}  Sharpe={m['sharpe']:>5.2f}  "
            f"MaxDD%={m['max_dd_pct']:>5.1f}  TotRet%={m['total_return_pct']:>+7.1f}  "
            f"CAGR%={m['ann_return_pct']:>+6.1f}  "
            f"AvgWin$={m['avg_win_usd']:>7.2f}  AvgLoss$={m['avg_loss_usd']:>7.2f}  "
            f"MaxLoseStreak={m['max_consec_losses']:>3}")


def main():
    print("=" * 100)
    print(" REAL BACKTEST METRICS -- 4 LIVE CONFIGS (computed fresh this run, not from memory)")
    print(f" Source files: {GOLD_CSV}  |  {BTC_CSV}")
    print(f" Engine: backtest_forex.BacktestEngine (gold) / btc_walkforward.BTCEngine (BTC)")
    print(f" RISK_PCT={RISK_PCT}%  (live BTC bots run at 0.20% -- see risk note in file header)")
    print("=" * 100)

    print("\n[load] gold CSV ...", flush=True)
    loader = DataLoader(log_fn=lambda *a, **k: None)
    cfg0 = ForexConfig(); cfg0.total_capital_usd = START
    gold_df, _ = loader.load("XAUUSD", 99.0, cfg0, csv_path=GOLD_CSV, allow_synthetic=True)
    print(f"[load] gold: {len(gold_df):,} bars  {gold_df['timestamp'].iloc[0].date()} -> {gold_df['timestamp'].iloc[-1].date()}")

    print("[load] BTC CSV ...", flush=True)
    btc_df, _ = loader.load("BTCUSDc", 99.0, cfg0, csv_path=BTC_CSV, allow_synthetic=False)
    print(f"[load] BTC:  {len(btc_df):,} bars  {btc_df['timestamp'].iloc[0].date()} -> {btc_df['timestamp'].iloc[-1].date()}")

    results = {}

    print("\n" + "-" * 100)
    print(" GOLD (adx20tp7 / adx18tp7)  --  SL/TP fixed at 3.0/7.0xATR, ADX threshold varies")
    print("-" * 100)
    tr_from, tr_to = GOLD_SPLIT
    for label, sl, tp, adx in GOLD_CONFIGS:
        m_full  = run_gold(gold_df, sl, tp, adx)
        m_train = run_gold(gold_df, sl, tp, adx, date_to=tr_to)
        m_test  = run_gold(gold_df, sl, tp, adx, date_from=tr_to)
        results[label] = dict(full=m_full, train=m_train, test=m_test)
        print(fmt_row(label, "FULL", m_full))
        print(fmt_row(label, "TRAIN", m_train))
        print(fmt_row(label, "TEST", m_test))
        print()

    print("-" * 100)
    print(" BTC-HF (btc_cons / btc_aggr)")
    print("-" * 100)
    (split,) = BTC_SPLIT
    for label, sl, tp, adx in BTC_CONFIGS:
        m_full  = run_btc(btc_df, sl, tp, adx)
        m_train = run_btc(btc_df, sl, tp, adx, date_to=split)
        m_test  = run_btc(btc_df, sl, tp, adx, date_from=split)
        results[label] = dict(full=m_full, train=m_train, test=m_test)
        print(fmt_row(label, "FULL", m_full))
        print(fmt_row(label, "TRAIN", m_train))
        print(fmt_row(label, "TEST", m_test))
        print()

    print("=" * 100)
    print(" DONE -- table above is the source of truth for the next reply to the user.")
    print("=" * 100)


if __name__ == "__main__":
    main()
