#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gold_regime_filter_real_engine.py -- Re-test the "gold regime filter" idea
(ADX>22, ADX rising, |EMA50-EMA200|>1.2xATR) on the REAL live-bot engine
(FastHybridTrendPullback M15 entry + BacktestEngine), not the H1 proxy script
(gold_filter_backtest.py) that produced the earlier PF=1.54 / n=67 result.

Why this exists: gold_filter_backtest.py's BASE case gets 712 H1 trades and
PF=0.97, while the real live engine's BASE (adx18tp7, M15) gets 6,329 trades
and PF=1.23 -- a different underlying strategy entirely (H1 entries vs M15
pullback-to-EMA20). Any regime-filter conclusion from the H1 proxy does not
automatically transfer. This script applies the SAME frozen regime thresholds
to the actual FastHybridTrendPullback H1-trend-array construction so the
M15 entry/SL/TP/cost model exactly match what adx18tp7/adx20tp7 run live.

Gates applied (same discipline as BTC-HF validation this session):
  - minimum 200 trades after filtering, else FAIL outright (no partial credit)
  - WF-A yearly: train picks nothing (thresholds are frozen, not fit) --
    report PF/trades per year, how many years PF>1
  - Half/half split for a train/test consistency sanity check too

STOP discipline: report honestly. Do not loosen the gate to force a pass.
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forex_config import ForexConfig
from backtest_forex import (DataLoader, prepare_data, BacktestEngine,
                             FastHybridTrendPullback, compute_metrics)

GOLD_CSV = "download/xauusd-m15-bid-2013-01-01-2026-06-10.csv"
START = 10_000.0
RISK_PCT = 0.30
SPREAD, COMM = 0.10, 3.50

# frozen thresholds -- identical to gold_filter_backtest.py / portfolio_regime_filter.py,
# no re-tuning here
REGIME_ADX_MIN = 22
REGIME_GAP_MULT = 1.2
MIN_TRADES_GATE = 200


class RegimeFilteredHybrid(FastHybridTrendPullback):
    """Same M15 pullback entry as the live bot. Only the H1-trend array
    construction is extended with two extra frozen conditions:
      ADX(14, H1) rising  AND  |EMA50-EMA200|(H1) > 1.2 x ATR(14, H1)
    Everything else (M15 EMA20 pullback entry, SL/TP ATR multiples, cost
    model, BacktestEngine) is byte-identical to the live bot's class.
    """

    def _build_h1_trend_array(self, d: dict) -> np.ndarray:
        n = len(d["c"])
        n_h1 = n // self.H1_BARS
        out = np.zeros(n, dtype=np.int8)
        if n_h1 < self.EMA_H1_SLOW + 5:
            return out

        idx = np.arange(n_h1) * self.H1_BARS
        h1_c = np.array([d["c"][j + self.H1_BARS - 1] for j in idx])
        h1_h = np.array([d["h"][j:j + self.H1_BARS].max() for j in idx])
        h1_l = np.array([d["l"][j:j + self.H1_BARS].min() for j in idx])

        ema_f = self._ema(h1_c, self.EMA_H1_FAST)
        ema_s = self._ema(h1_c, self.EMA_H1_SLOW)
        adx_a = self._adx_array(h1_h, h1_l, h1_c, self.ADX_PERIOD)

        # H1 ATR(14), Wilder -- same TR formula as _adx_array uses internally,
        # recomputed here since _adx_array doesn't expose it
        prev_c = np.empty(n_h1); prev_c[0] = h1_c[0]; prev_c[1:] = h1_c[:-1]
        tr = np.maximum(h1_h - h1_l, np.maximum(np.abs(h1_h - prev_c), np.abs(h1_l - prev_c)))
        atr_h1 = self._wilder_smooth(tr, self.ADX_PERIOD)

        adx_rising = adx_a > np.roll(adx_a, 1)
        adx_rising[0] = False
        gap_ok = np.abs(ema_f - ema_s) > (REGIME_GAP_MULT * atr_h1)

        h1_trend = np.zeros(n_h1, dtype=np.int8)
        for k in range(n_h1):
            ef, es, adx = ema_f[k], ema_s[k], adx_a[k]
            if math.isnan(ef) or math.isnan(es) or math.isnan(adx):
                continue
            if adx < REGIME_ADX_MIN:
                continue
            if not adx_rising[k]:
                continue
            if not gap_ok[k]:
                continue
            c = h1_c[k]
            if c > ef > es:
                h1_trend[k] = 1
            elif c < ef < es:
                h1_trend[k] = -1

        for i in range(n):
            k = i // self.H1_BARS
            if k < n_h1:
                out[i] = h1_trend[k]
        return out


def gold_cfg():
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = RISK_PCT
    c.partial_tp_atr = 999.0
    c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False
    return c


def run(strat_cls, df_full, sl, tp, date_from=None, date_to=None):
    df = df_full
    if date_from:
        df = df[df["timestamp"] >= pd.Timestamp(date_from)]
    if date_to:
        df = df[df["timestamp"] < pd.Timestamp(date_to)]
    df = df.reset_index(drop=True)
    if len(df) < 1000:
        return None
    d = prepare_data(df)
    strat = strat_cls()
    strat.sl_atr, strat.tp_atr = sl, tp
    strat.trail_atr_mult, strat.trail_activation_atr = 999.0, 999.0
    strat.precompute(d)
    eng = BacktestEngine(d, gold_cfg(), strat, spread_price=SPREAD,
                          commission_per_lot=COMM, symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START)


def fmt(m, label):
    if m is None or m.get("trades", 0) == 0:
        return f"  {label:<10} NO TRADES"
    return (f"  {label:<10} trades={m['trades']:>5}  win%={m['win_rate']*100:>5.1f}  "
            f"PF={m['profit_factor']:>5.2f}  Sharpe={m['sharpe']:>5.2f}  "
            f"MaxDD%={m['max_dd_pct']:>5.1f}  TotRet%={m['total_return_pct']:>+7.1f}  "
            f"MaxLoseStreak={m['max_consec_losses']:>3}")


def main():
    print("=" * 100)
    print(" GOLD REGIME FILTER -- REAL LIVE ENGINE (FastHybridTrendPullback M15, not H1 proxy)")
    print(f" Frozen thresholds: ADX>{REGIME_ADX_MIN}, ADX rising, |EMA50-EMA200|>{REGIME_GAP_MULT}xATR(H1)")
    print(f" Gate: minimum {MIN_TRADES_GATE} trades required, else FAIL outright")
    print("=" * 100)

    loader = DataLoader(log_fn=lambda *a, **k: None)
    cfg0 = ForexConfig(); cfg0.total_capital_usd = START
    df_full, _ = loader.load("XAUUSD", 99.0, cfg0, csv_path=GOLD_CSV, allow_synthetic=True)
    print(f"[load] {len(df_full):,} bars  {df_full['timestamp'].iloc[0].date()} -> {df_full['timestamp'].iloc[-1].date()}\n")

    for adx_min_label, sl, tp in [("adx18tp7 config (SL3/TP7)", 3.0, 7.0)]:
        print(f"--- BASE (no regime filter, real M15 engine, adx18tp7 config) ---")
        m_base = run(FastHybridTrendPullback, df_full, sl, tp)
        print(fmt(m_base, "BASE"))

        print(f"\n--- REGIME FILTER applied on top of the SAME M15 engine ---")
        m_regime = run(RegimeFilteredHybrid, df_full, sl, tp)
        print(fmt(m_regime, "REGIME"))

        n = m_regime.get("trades", 0) if m_regime else 0
        gate_pass = n >= MIN_TRADES_GATE
        print(f"\n  Trade-count gate (>={MIN_TRADES_GATE}): {'PASS' if gate_pass else 'FAIL'} (n={n})")

        if not gate_pass:
            print(f"\n  STOPPING per discipline -- regime filter fails the minimum trade-count gate")
            print(f"  on the real M15 engine too. Not proceeding to WF-A yearly for this config.")
            print("=" * 100)
            return

        # WF-A yearly (thresholds frozen -- no per-year re-fit, just report per-year metrics)
        print(f"\n--- WF-A YEARLY (frozen thresholds, no re-fit) ---")
        years = range(df_full["timestamp"].min().year, df_full["timestamp"].max().year + 1)
        pf_gt1 = 0; n_years = 0
        for y in years:
            m_y = run(RegimeFilteredHybrid, df_full, sl, tp,
                      date_from=f"{y}-01-01", date_to=f"{y+1}-01-01")
            if m_y is None or m_y.get("trades", 0) == 0:
                print(f"  {y}: no trades")
                continue
            n_years += 1
            verdict = "PF>1" if m_y["profit_factor"] > 1.0 else "PF<=1"
            if m_y["profit_factor"] > 1.0:
                pf_gt1 += 1
            print(f"  {y}: trades={m_y['trades']:>3}  PF={m_y['profit_factor']:.2f}  "
                  f"win%={m_y['win_rate']*100:.1f}  {verdict}")
        print(f"\n  WF-A summary: PF>1 in {pf_gt1}/{n_years} years with trades")

        # half-split sanity check
        half_date = df_full["timestamp"].iloc[len(df_full) // 2]
        print(f"\n--- HALF-SPLIT (H1={df_full['timestamp'].iloc[0].date()}..{half_date.date()}, "
              f"H2={half_date.date()}..{df_full['timestamp'].iloc[-1].date()}) ---")
        m_h1 = run(RegimeFilteredHybrid, df_full, sl, tp, date_to=half_date)
        m_h2 = run(RegimeFilteredHybrid, df_full, sl, tp, date_from=half_date)
        print(fmt(m_h1, "H1"))
        print(fmt(m_h2, "H2"))

    print("=" * 100)


if __name__ == "__main__":
    main()
