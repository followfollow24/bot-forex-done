#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test of the user's written spec (2026-08-05), implemented faithfully:

  1. Daily trade filter : at most ONE ENTRY per calendar day (broker/server day).
  2. H1 trend           : price > EMA50 AND EMA50 > EMA200  -> longs only
                          price < EMA50 AND EMA50 < EMA200  -> shorts only
                          (NO ADX condition -- the spec does not mention one)
  3. M15 pullback entry : long  = price pulls back to touch M15 EMA50
                                  OR RSI(14) < 30
                          short = price rallies to touch M15 EMA50
                                  OR RSI(14) > 70
  4. Risk               : 2% per trade, SL beyond the recent swing low/high,
                          TP at 1.5R / 2R (both tested) and a no-TP variant
                          because the user closes by hand.

Instrument: XAUUSD (as specified), M15 entry bars, H1 trend. Real costs.

CAUSALITY: the H1 trend array is built by the project's own
_build_h1_trend_array (timestamp-anchored bucketing, post-2026-07-30 fix), so
a bar only ever sees H1 buckets that have fully CLOSED. The swing low/high and
RSI/EMA are all computed on closes up to and including the signal bar only;
entry fills at the NEXT bar's open via the real BacktestEngine.

The 1-trade-per-day cap counts ACTUAL ENTRIES (not emitted signals) by
reusing the engine's existing day_blocked flag, which resets on day rollover.
"""
from __future__ import annotations
import os, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import (DataLoader, prepare_data, BacktestEngine,
                            FastHybridTrendPullback, compute_metrics)
from forex_indicators import Signal
from _all_paths import to_monthly, perf, START


def _rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(closes)
    out = np.full(n, np.nan)
    if n < period + 1:
        return out
    delta = np.diff(closes)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    ag = np.zeros(n); al = np.zeros(n)
    ag[period] = gain[:period].mean(); al[period] = loss[:period].mean()
    for i in range(period + 1, n):
        ag[i] = (ag[i - 1] * (period - 1) + gain[i - 1]) / period
        al[i] = (al[i - 1] * (period - 1) + loss[i - 1]) / period
    rs = np.divide(ag, al, out=np.full(n, np.inf), where=al > 0)
    out[period:] = 100 - 100 / (1 + rs[period:])
    out[al == 0] = 100.0
    out[:period] = np.nan
    return out


class SpecDailyTrendPullback(FastHybridTrendPullback):
    """The spec above. Inherits the project's causal H1 trend machinery."""

    name = "Spec Daily Trend-Pullback (1/day)"
    short_name = "SpecDaily"

    # step 2 -- spec has no ADX gate; 0 makes the inherited gate a no-op
    ADX_MIN = 0.0
    EMA_H1_FAST = 50
    EMA_H1_SLOW = 200

    # step 3
    EMA_M15_PULLBACK = 50
    RSI_PERIOD = 14
    RSI_BUY = 30.0
    RSI_SELL = 70.0
    TOUCH_TOL = 0.0015          # "touches" the EMA within 0.15%

    # step 4
    SWING_LOOKBACK = 10         # bars used for the swing low/high
    SWING_BUFFER_ATR = 0.10     # small cushion beyond the swing point
    RR = 1.5                    # TP = RR x risk (999 -> effectively no TP)

    sl_atr = 2.0
    tp_atr = 999.0
    trail_atr_mult = 999.0
    trail_activation_atr = 999.0
    max_spread_atr_ratio = 1.0
    MIN_BARS = 900              # needs 200 H1 bars = 800 M15 bars of warmup

    _spec_len = None

    def precompute(self, d):
        super().precompute(d)
        self._build_spec(d)

    def _build_spec(self, d):
        c = d["c"]
        self._ema_pb = self._ema(c, self.EMA_M15_PULLBACK)
        self._rsi_arr = _rsi(c, self.RSI_PERIOD)
        self._spec_len = len(c)

    def _ensure_spec(self, d):
        # live path never calls precompute(); rebuild when missing or stale
        if self._spec_len != len(d["c"]):
            self._build_spec(d)

    def signal(self, d: dict, i: int) -> Signal:
        if i < self.MIN_BARS:
            return Signal()

        # ---- step 2: H1 trend (causal, inherited) ----
        if self._h1_trend_arr is not None and len(self._h1_trend_arr) == len(d["c"]):
            trend = int(self._h1_trend_arr[i])
        else:
            trend = self._h1_trend(d, i)
        if trend == 0:
            return Signal()

        self._ensure_spec(d)
        atr = d["atr"][i]
        if np.isnan(atr) or atr <= 0:
            return Signal()
        ema_pb = self._ema_pb[i]
        rsi = self._rsi_arr[i]
        if np.isnan(ema_pb) or np.isnan(rsi):
            return Signal()

        c = d["c"][i]
        lo = d["l"][i]
        hi = d["h"][i]
        j0 = max(0, i - self.SWING_LOOKBACK + 1)

        # ---- step 3 + 4 ----
        if trend == 1:
            touched = lo <= ema_pb * (1 + self.TOUCH_TOL)
            oversold = rsi < self.RSI_BUY
            if touched or oversold:
                swing_low = float(np.min(d["l"][j0:i + 1]))
                sl_price = swing_low - self.SWING_BUFFER_ATR * atr
                risk = c - sl_price
                if risk > 0:
                    self.sl_atr = risk / atr
                    self.tp_atr = 999.0 if self.RR >= 900 else risk * self.RR / atr
                    return Signal("BUY", f"spec trend=up rsi={rsi:.0f}")

        elif trend == -1:
            touched = hi >= ema_pb * (1 - self.TOUCH_TOL)
            overbought = rsi > self.RSI_SELL
            if touched or overbought:
                swing_high = float(np.max(d["h"][j0:i + 1]))
                sl_price = swing_high + self.SWING_BUFFER_ATR * atr
                risk = sl_price - c
                if risk > 0:
                    self.sl_atr = risk / atr
                    self.tp_atr = 999.0 if self.RR >= 900 else risk * self.RR / atr
                    return Signal("SELL", f"spec trend=down rsi={rsi:.0f}")

        return Signal()


class OneTradePerDayEngine(BacktestEngine):
    """Step 1 of the spec: block further ENTRIES once one has happened today.

    Reuses the engine's own day_blocked flag (already checked before signal
    generation and already reset on calendar-day rollover), so this counts
    real entries rather than signals that may never have been acted on.
    """

    def _enter(self, i, bar_open, ts):
        before = self.position
        super()._enter(i, bar_open, ts)
        if self.position is not None and before is None:
            self.day_blocked = True


def cfg(risk=2.0, hold=192):
    c = ForexConfig()
    c.total_capital_usd = 1000.0      # the spec's $1,000 account
    c.risk_per_trade_pct = risk
    c.max_risk_per_trade_pct = max(c.max_risk_per_trade_pct, risk)
    c.partial_tp_atr = 999.0
    c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False
    c.max_hold_bars = hold
    return c


def run(d, rr, risk=2.0, spread=0.24, comm=3.5, one_per_day=True, sym="XAUUSD"):
    s = SpecDailyTrendPullback()
    s.RR = rr
    s.TIMEFRAME_SECONDS = 900          # M15 entry bars
    s.precompute(d)
    eng_cls = OneTradePerDayEngine if one_per_day else BacktestEngine
    eng = eng_cls(d, cfg(risk), s, spread_price=spread,
                  commission_per_lot=comm, symbol=sym)
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, 1000.0), eng.trades


def line(m, tr, label, yrs):
    if not m or m.get("trades", 0) < 20:
        print(f"    {label:<30} n={m.get('trades',0) if m else 0:>5}  too few")
        return
    p = perf(to_monthly(tr)); sh = p["sharpe"] if p else float("nan")
    tot = m["total_return_pct"]
    cg = -100.0 if tot <= -100 else ((1 + tot / 100) ** (1 / yrs) - 1) * 100
    print(f"    {label:<30} n={m['trades']:>5} ({m['trades']/yrs:>4.0f}/yr "
          f"{m['trades']/yrs/365:.2f}/day)  win%={m['win_rate']*100:>5.1f}  "
          f"PF={m['profit_factor']:>5.2f}  Sharpe={sh:>5.2f}  "
          f"CAGR={cg:>+7.2f}%  DD={m['max_dd_pct']:>5.1f}%")


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START
    dfg, _ = loader.load("XAUUSD", 99.0, c0,
                         csv_path="download/xauusd-m15-bid-2013-01-01-2026-06-10.csv",
                         allow_synthetic=True)
    yrs = (dfg["timestamp"].iloc[-1] - dfg["timestamp"].iloc[0]).days / 365.25
    d = prepare_data(dfg)

    print("=" * 104)
    print(" USER SPEC -- XAUUSD, M15 entry / H1 trend, 1 trade/day, risk 2%, $1,000 account, REAL COSTS")
    print("=" * 104)
    print(f"  data: {yrs:.1f} years of M15\n")

    for rr, tag in [(1.5, "TP 1:1.5"), (2.0, "TP 1:2"), (999, "no TP (close by hand)")]:
        m, tr = run(d, rr)
        line(m, tr, tag, yrs)

    print("\n  -- effect of the 1-trade/day cap (TP 1:1.5) --")
    m, tr = run(d, 1.5, one_per_day=False)
    line(m, tr, "cap OFF", yrs)
    m, tr = run(d, 1.5, one_per_day=True)
    line(m, tr, "cap ON", yrs)

    print("\n  -- is it costs, or is it the signal? (TP 1:1.5, cap ON) --")
    m, tr = run(d, 1.5, spread=0.24, comm=3.5)
    line(m, tr, "real cost", yrs)
    m, tr = run(d, 1.5, spread=0.0, comm=0.0)
    line(m, tr, "ZERO cost", yrs)


if __name__ == "__main__":
    main()
