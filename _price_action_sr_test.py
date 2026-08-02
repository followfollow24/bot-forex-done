#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Support/Resistance + reversal-candle price-action strategy, the mechanical
proxy for "discretionary chart trading" the user asked to test.

Rules:
  1. Track recent swing highs/lows (confirmed SWING_LOOKBACK bars later, so
     no look-ahead -- same causal-confirmation pattern as
     smc_liquidity_strategy.py's H1 structure detection).
  2. A bar is a bullish reversal candle if its lower wick >= WICK_MULT x body
     and it closes in the upper HALF of its range (rejection of the low);
     mirror for bearish (upper wick, closes in lower half).
  3. Entry: price's low (for support) or high (for resistance) comes within
     TOUCH_TOL_ATR x ATR of a recent confirmed swing level, AND that same
     bar is the matching reversal candle -> fade the level (BUY at support,
     SELL at resistance).
  4. Exit: SL beyond the touched level (+ buffer), ATR trailing stop --
     same proven exit mechanics as everything else this session.
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


class SRReversalCandle:
    name = "Support/Resistance + Reversal Candle"
    short_name = "SR-RevCandle"

    SWING_LOOKBACK = 5
    TOUCH_TOL_ATR = 0.3
    WICK_MULT = 2.0
    SL_BUFFER_ATR = 0.5

    sl_atr = 2.0            # fallback if level-based SL is tighter than this
    tp_atr = 999.0
    trail_atr_mult = 3.0
    trail_activation_atr = 1.0
    max_spread_atr_ratio = 0.5
    MIN_BARS = 100

    _swing_hi_arr = None
    _swing_lo_arr = None

    def precompute(self, d: dict):
        n = len(d["c"])
        h, l = d["h"], d["l"]
        lb = self.SWING_LOOKBACK

        is_swing_hi = np.zeros(n, dtype=bool)
        is_swing_lo = np.zeros(n, dtype=bool)
        for k in range(lb, n - lb):
            window_h = h[k - lb:k + lb + 1]
            window_l = l[k - lb:k + lb + 1]
            if h[k] == window_h.max():
                is_swing_hi[k] = True
            if l[k] == window_l.min():
                is_swing_lo[k] = True

        # causal "most recent CONFIRMED swing" as of bar i (confirmed at k+lb)
        last_swing_hi = np.full(n, np.nan)
        last_swing_lo = np.full(n, np.nan)
        cur_hi, cur_lo = np.nan, np.nan
        for i in range(n):
            confirm_idx = i - lb
            if confirm_idx >= 0:
                if is_swing_hi[confirm_idx]:
                    cur_hi = h[confirm_idx]
                if is_swing_lo[confirm_idx]:
                    cur_lo = l[confirm_idx]
            last_swing_hi[i] = cur_hi
            last_swing_lo[i] = cur_lo

        self._swing_hi_arr = last_swing_hi
        self._swing_lo_arr = last_swing_lo

    def signal(self, d: dict, i: int) -> Signal:
        if i < self.MIN_BARS:
            return Signal()
        atr = d["atr"][i]
        if np.isnan(atr) or atr <= 0:
            return Signal()

        o, h, l, c = d["o"][i], d["h"][i], d["l"][i], d["c"][i]
        rng = h - l
        if rng <= 0:
            return Signal()
        body = abs(c - o)
        upper_wick = h - max(o, c)
        lower_wick = min(o, c) - l
        close_pos = (c - l) / rng   # 0 = closed at low, 1 = closed at high

        bullish_pin = (lower_wick >= self.WICK_MULT * max(body, 1e-9)) and close_pos > 0.5
        bearish_pin = (upper_wick >= self.WICK_MULT * max(body, 1e-9)) and close_pos < 0.5

        tol = self.TOUCH_TOL_ATR * atr
        sup = self._swing_lo_arr[i]
        res = self._swing_hi_arr[i]

        if bullish_pin and not np.isnan(sup) and l <= sup + tol:
            return Signal("BUY", f"support={sup:.2f} pin low={l:.2f}")
        if bearish_pin and not np.isnan(res) and h >= res - tol:
            return Signal("SELL", f"resistance={res:.2f} pin high={h:.2f}")
        return Signal()


def cfg(sym, risk=0.5, hold=64):
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = risk
    c.partial_tp_atr = 999.0
    c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False
    c.max_hold_bars = hold
    return c


def run(d, sym, spread, risk=0.5, hold=64, comm=3.5):
    s = SRReversalCandle()
    s.precompute(d)
    eng = BacktestEngine(d, cfg(sym, risk=risk, hold=hold), s, spread_price=spread, commission_per_lot=comm, symbol=sym)
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START), eng.trades


def line(m, tr, label, yrs):
    if not m or m.get("trades", 0) < 15:
        n = m.get("trades", 0) if m else 0
        print(f"    {label:<20} n={n:>5}  too few"); return
    p = perf(to_monthly(tr))
    sh = p["sharpe"] if p else float("nan")
    tot = m["total_return_pct"]
    cg = -100.0 if tot <= -100 else ((1+tot/100)**(1/yrs)-1)*100
    print(f"    {label:<20} n={m['trades']:>5} ({m['trades']/yrs:>4.0f}/yr {m['trades']/yrs/365:.2f}/day)  "
          f"win%={m['win_rate']*100:>5.1f}  PF={m['profit_factor']:>5.2f}  Sharpe={sh:>5.2f}  "
          f"CAGR={cg:>+7.2f}%  DD={m['max_dd_pct']:>5.1f}%")


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START
    GOLD_M15 = "download/xauusd-m15-bid-2013-01-01-2026-06-10.csv"
    dfg, _ = loader.load("XAUUSD", 99.0, c0, csv_path=GOLD_M15, allow_synthetic=True)

    print("=" * 100)
    print(" SUPPORT/RESISTANCE + REVERSAL CANDLE -- Gold, real costs, multiple timeframes")
    print("=" * 100)

    for tf_name, tf_str in [("M15", "15min"), ("H1", "1h"), ("H4", "4h"), ("Daily", "1D")]:
        dft = resample(dfg, tf_str) if tf_str != "15min" else dfg
        yrs = (dft["timestamp"].iloc[-1]-dft["timestamp"].iloc[0]).days/365.25
        d = prepare_data(dft)
        m, tr = run(d, "XAUUSD", 2.85, risk=0.5)
        line(m, tr, tf_name, yrs)


if __name__ == "__main__":
    main()
