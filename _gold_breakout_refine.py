#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Refine the Gold Daily Donchian breakout candidate (from _gold_breakout_search.py
/ _gold_daily_breakout_validate.py: OOS PF 2.17, DD 4.5%, gains spread across
11/14 years -- unlike the trend-pullback dead end).

Search space (own precompute so DONCH_WIN is tunable, unlike the pipeline's
fixed BO_WIN=48):
  - DONCH_WIN: channel lookback (30/48/60/80 days)
  - BREAKOUT_MARGIN_ATR: require close beyond the band by this much (filters
    marginal/noise breakouts)
  - optional slow-EMA trend filter: only take breakouts in the direction of
    a longer-term trend (filters counter-trend whipsaws)

Strict discipline: every combo is scored on the FIRST HALF (train) only;
the single best-by-train-PF config is then run ONCE on the second half
(test/OOS) and reported honestly, win or lose.
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


class DonchianBreakoutV2:
    """Own rolling Donchian channel (configurable window, causal shift(1)),
    ATR stop, ATR trailing exit, optional EMA trend filter."""
    name = "Donchian Breakout v2"
    short_name = "DonchBOv2"

    sl_atr = 2.0
    tp_atr = 999.0
    trail_atr_mult = 3.0
    trail_activation_atr = 1.0
    max_spread_atr_ratio = 0.5

    DONCH_WIN = 48
    BREAKOUT_MARGIN_ATR = 0.0
    TREND_EMA = None          # None = no trend filter; else EMA period on close

    MIN_BARS = 60
    _donch_hi = None
    _donch_lo = None
    _trend_ema_arr = None

    def precompute(self, d):
        c = pd.Series(d["c"])
        self._donch_hi = c.rolling(self.DONCH_WIN).max().shift(1).to_numpy()
        self._donch_lo = c.rolling(self.DONCH_WIN).min().shift(1).to_numpy()
        # use high/low for the channel (more standard Donchian), not close
        h = pd.Series(d["h"]); l = pd.Series(d["l"])
        self._donch_hi = h.rolling(self.DONCH_WIN).max().shift(1).to_numpy()
        self._donch_lo = l.rolling(self.DONCH_WIN).min().shift(1).to_numpy()
        if self.TREND_EMA:
            alpha = 2.0 / (self.TREND_EMA + 1)
            ema = np.full(len(d["c"]), np.nan)
            ema[0] = d["c"][0]
            for j in range(1, len(d["c"])):
                ema[j] = d["c"][j] * alpha + ema[j-1] * (1 - alpha)
            self._trend_ema_arr = ema
        else:
            self._trend_ema_arr = None

    def signal(self, d, i):
        if i < max(self.MIN_BARS, self.DONCH_WIN + 1):
            return Signal()
        hi = self._donch_hi[i]
        lo = self._donch_lo[i]
        c = d["c"][i]
        atr = d["atr"][i]
        if np.isnan(hi) or np.isnan(lo) or np.isnan(atr) or atr <= 0:
            return Signal()
        margin = self.BREAKOUT_MARGIN_ATR * atr

        trend_ok_long = trend_ok_short = True
        if self._trend_ema_arr is not None:
            ema = self._trend_ema_arr[i]
            if np.isnan(ema):
                return Signal()
            trend_ok_long = c > ema
            trend_ok_short = c < ema

        if c > hi + margin and trend_ok_long:
            return Signal("BUY", f"breakout>donch_hi={hi:.2f}")
        if c < lo - margin and trend_ok_short:
            return Signal("SELL", f"breakout<donch_lo={lo:.2f}")
        return Signal()


def cfg(risk=0.50, hold=20):
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = risk
    c.partial_tp_atr = 999.0
    c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False
    c.max_hold_bars = hold
    return c


def run(d, donch_win, margin, trend_ema, sl_atr=2.0, trail_mult=3.0, trail_act=1.0, risk=0.50, hold=20):
    s = DonchianBreakoutV2()
    s.DONCH_WIN = donch_win
    s.BREAKOUT_MARGIN_ATR = margin
    s.TREND_EMA = trend_ema
    s.sl_atr, s.tp_atr = sl_atr, 999.0
    s.trail_atr_mult = trail_mult
    s.trail_activation_atr = trail_act
    s.precompute(d)
    eng = BacktestEngine(d, cfg(risk, hold), s, spread_price=SPREAD,
                          commission_per_lot=COMM, symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START), eng.trades


def line(m, tr, label, yrs):
    if not m or m.get("trades", 0) < 15:
        n = m.get("trades", 0) if m else 0
        print(f"    {label:<42} n={n:>5}  too few")
        return None
    p = perf(to_monthly(tr))
    sh = p["sharpe"] if p else float("nan")
    tot = m["total_return_pct"]
    cg = -100.0 if tot <= -100 else ((1+tot/100)**(1/yrs)-1)*100
    print(f"    {label:<42} n={m['trades']:>5}  PF={m['profit_factor']:>5.2f}  "
          f"Sharpe={sh:>5.2f}  CAGR={cg:>+7.2f}%  DD={m['max_dd_pct']:>5.1f}%")
    return dict(pf=m["profit_factor"], sharpe=sh, cagr=cg, dd=m["max_dd_pct"], n=m["trades"])


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START
    dfg, _ = loader.load("XAUUSD", 99.0, c0, csv_path=GOLD_M15, allow_synthetic=True)
    df_d = resample(dfg, "1D")

    mid = df_d["timestamp"].iloc[len(df_d)//2]
    tr_df = df_d[df_d["timestamp"] <= mid].reset_index(drop=True)
    te_df = df_d[df_d["timestamp"] > mid].reset_index(drop=True)
    d_tr, d_te = prepare_data(tr_df), prepare_data(te_df)
    y_tr = (tr_df["timestamp"].iloc[-1] - tr_df["timestamp"].iloc[0]).days / 365.25
    y_te = (te_df["timestamp"].iloc[-1] - te_df["timestamp"].iloc[0]).days / 365.25
    print(f"train span={y_tr:.1f}y  test span={y_te:.1f}y\n")

    print("=" * 100)
    print(" TRAIN (1st half) -- parameter grid, high/low Donchian channel")
    print("=" * 100)

    grid = []
    for win in [30, 48, 60, 80]:
        for margin in [0.0, 0.25, 0.5]:
            grid.append((win, margin, None))
    for win in [48, 60]:
        for trend_ema in [100, 150]:
            grid.append((win, 0.0, trend_ema))

    results = {}
    for win, margin, trend_ema in grid:
        label = f"win{win} margin{margin} trend_ema={trend_ema}"
        m, tr = run(d_tr, win, margin, trend_ema, risk=0.50, hold=20)
        r = line(m, tr, label, y_tr)
        if r:
            results[(win, margin, trend_ema)] = r

    if not results:
        print("no config had enough trades"); return

    best_key = max(results, key=lambda k: results[k]["pf"] if results[k]["n"] >= 30 else -1)
    win, margin, trend_ema = best_key
    print(f"\n  -> best by train PF (n>=30): win={win} margin={margin} trend_ema={trend_ema} "
          f"(PF={results[best_key]['pf']:.2f})")

    print("\n" + "=" * 100)
    print(" TEST (2nd half, OOS) -- single run with the frozen best-train config")
    print("=" * 100)
    m, tr = run(d_te, win, margin, trend_ema, risk=0.50, hold=20)
    line(m, tr, f"TEST win{win} margin{margin} trend_ema={trend_ema}", y_te)

    # also show the previous baseline (win48 margin0.0 no-filter) on test for direct comparison
    print()
    m, tr = run(d_te, 48, 0.0, None, risk=0.50, hold=20)
    line(m, tr, "TEST win48 margin0.0 trend_ema=None (baseline, for comparison)", y_te)


if __name__ == "__main__":
    main()
