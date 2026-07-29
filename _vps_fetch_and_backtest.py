#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Run on the VPS: fetch fresh XAUUSDc M15 bars via MT5 (2013-01-01 -> now,
using the historical CSV for the old part + live MT5 for the recent gap),
then backtest OLD vs NEW TP settings for adx18tp7/adx20tp7/regime22 across
several recent windows, closing the gap that the local CSV (which stops at
2026-06-10) could not cover -- specifically the live trading period itself
(2026-07-04 onward).
"""
from __future__ import annotations
import os, sys
from datetime import datetime
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

from forex_config import ForexConfig
from backtest_forex import (DataLoader, prepare_data, BacktestEngine,
                             FastHybridTrendPullback, compute_metrics)
from gold_regime_filter_real_engine import RegimeFilteredHybrid

OLD_CSV = "download/xauusd-m15-bid-2013-01-01-2026-06-10.csv"
START = 10_000.0
RISK_PCT = 0.30
SPREAD, COMM = 0.10, 3.50


def fetch_recent_df():
    if mt5 is None:
        print("[WARN] MetaTrader5 package not available -- cannot fetch recent bars")
        return None
    if not mt5.initialize():
        print("[WARN] mt5.initialize() failed -- cannot fetch recent bars")
        return None
    rates = mt5.copy_rates_range("XAUUSDc", mt5.TIMEFRAME_M15, datetime(2026, 6, 1), datetime.now())
    mt5.shutdown()
    if rates is None or len(rates) == 0:
        print("[WARN] no rates returned")
        return None
    rows = []
    for r in rates:
        rows.append({
            "timestamp": pd.Timestamp(int(r['time']), unit='s'),
            "open": float(r['open']), "high": float(r['high']),
            "low": float(r['low']), "close": float(r['close']),
        })
    df = pd.DataFrame(rows)
    print(f"[fetch] {len(df):,} recent bars {df['timestamp'].iloc[0]} -> {df['timestamp'].iloc[-1]}")
    return df


def gold_cfg():
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = RISK_PCT
    c.partial_tp_atr = 999.0
    c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False
    return c


def run(strat_cls, d, adx_min, sl, tp):
    strat = strat_cls()
    strat.ADX_MIN = adx_min
    strat.sl_atr, strat.tp_atr = sl, tp
    strat.trail_atr_mult, strat.trail_activation_atr = 999.0, 999.0
    strat.precompute(d)
    eng = BacktestEngine(d, gold_cfg(), strat, spread_price=SPREAD,
                          commission_per_lot=COMM, symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START)


def fmt(m, label):
    if m is None or m.get("trades", 0) == 0:
        return f"  {label:<22} NO TRADES"
    return (f"  {label:<22} trades={m['trades']:>5}  win%={m['win_rate']*100:>5.1f}  "
            f"PF={m['profit_factor']:>5.2f}  Sharpe={m['sharpe']:>5.2f}  "
            f"MaxDD%={m['max_dd_pct']:>5.1f}  TotRet%={m['total_return_pct']:>+8.1f}  "
            f"MaxLoseStreak={m['max_consec_losses']:>3}")


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    cfg0 = ForexConfig(); cfg0.total_capital_usd = START
    df_old, _ = loader.load("XAUUSD", 99.0, cfg0, csv_path=OLD_CSV, allow_synthetic=True)
    print(f"[old csv] {len(df_old):,} bars up to {df_old['timestamp'].iloc[-1]}")

    df_recent = fetch_recent_df()
    if df_recent is not None:
        cutoff = df_old["timestamp"].iloc[-1]
        df_recent = df_recent[df_recent["timestamp"] > cutoff]
        df_full = pd.concat([df_old, df_recent], ignore_index=True)
        print(f"[merged] {len(df_full):,} bars, now covers up to {df_full['timestamp'].iloc[-1]}\n")
    else:
        df_full = df_old
        print("[merged] using OLD csv only -- recent fetch failed\n")

    windows = [
        ("Last 6mo (incl. live period)", "2025-12-01", None),
        ("Live period only (2026-07-04 ->)", "2026-07-04", None),
    ]

    configs = [
        ("adx18tp7", FastHybridTrendPullback, 18, 3.0, 7.0, 1.0),
        ("adx20tp7", FastHybridTrendPullback, 20, 3.0, 7.0, 4.0),
        ("regime22", RegimeFilteredHybrid,    22, 3.0, 7.0, 3.0),
    ]

    for name, cls, adx_min, sl, tp_old, tp_new in configs:
        print("=" * 100)
        print(f" {name}  (ADX_MIN={adx_min}, SL={sl}xATR)")
        print("=" * 100)
        for wname, dfrom, dto in windows:
            df_w = df_full[df_full["timestamp"] >= pd.Timestamp(dfrom)].reset_index(drop=True)
            if len(df_w) < 500:
                print(f"  [{wname}] insufficient data ({len(df_w)} bars)")
                continue
            dw = prepare_data(df_w)
            m_old = run(cls, dw, adx_min, sl, tp_old)
            m_new = run(cls, dw, adx_min, sl, tp_new)
            print(f" [{wname}]  ({len(df_w):,} bars)")
            print(fmt(m_old, f"  OLD TP={tp_old}"))
            print(fmt(m_new, f"  NEW TP={tp_new}"))
        print()


if __name__ == "__main__":
    main()
