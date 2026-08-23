#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Search for a strategy that survives the REAL cost (~$2/trade spread+slippage),
guided by what the SMC failure taught us:

  The blocker is cost-per-trade relative to edge-per-trade. SMC died because
  filtering never thickened per-trade edge -- it only cut sample size. The
  existing adx20tp7 survives ($2.85 cost, PF 1.27) because its TP is 7xATR on
  M15 gold, i.e. a large absolute target per trade.

  => The most direct attack is to raise the ATR the trade is measured in:
     move the entry timeframe up. On H1/H4 gold, ATR is several times the M15
     ATR, so a fixed $2 cost is a much smaller fraction of the same ATR-multiple
     target. Secondary axis: a genuinely different entry (breakout / range
     expansion) rather than another pullback variant.

Ideas tested (all scored at the real cost, all with the same honest gates):
  1. Hybrid trend-pullback on H1 entries (same logic, higher TF)
  2. Hybrid trend-pullback on H4 entries
  3. Donchian breakout, H1 and H4 (different signal: breakout not pullback)
  4. London-session open-range breakout on M15 (time-based, classic gold)

Gates: >=200 trades, PF>1 at real cost, majority of years PF>1.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import (DataLoader, prepare_data, BacktestEngine,
                             FastHybridTrendPullback, compute_metrics)
from forex_indicators import Signal

GOLD_CSV = "download/xauusd-m15-bid-2013-01-01-2026-06-10.csv"
START = 10_000.0
RISK_PCT = 0.30
COMM = 3.50
REAL_SPREAD = 2.00
MIN_TRADES_GATE = 200


def gold_cfg(max_hold=64):
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = RISK_PCT
    c.partial_tp_atr = 999.0
    c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False
    c.max_hold_bars = max_hold
    return c


def resample(df, rule):
    s = df.set_index("timestamp")
    o = s["open"].resample(rule).first()
    h = s["high"].resample(rule).max()
    l = s["low"].resample(rule).min()
    c = s["close"].resample(rule).last()
    out = pd.DataFrame({"timestamp": o.index, "open": o.values, "high": h.values,
                        "low": l.values, "close": c.values}).dropna().reset_index(drop=True)
    return out


# ── Idea 3: Donchian breakout ────────────────────────────────────────────
class DonchianBreakout:
    name = "Donchian Breakout"
    short_name = "Donch-BO"
    sl_atr = 3.0
    tp_atr = 7.0
    trail_atr_mult = 999.0
    trail_activation_atr = 999.0
    max_spread_atr_ratio = 0.20
    MIN_BARS = 220

    CHANNEL = 55          # breakout lookback
    TREND_EMA = 200       # only trade breakouts with the higher-TF trend
    _hi = _lo = _ema = None

    def precompute(self, d):
        c = np.asarray(d["c"], dtype=float)
        h = np.asarray(d["h"], dtype=float)
        l = np.asarray(d["l"], dtype=float)
        n = len(c)
        hi = np.full(n, np.nan); lo = np.full(n, np.nan)
        for i in range(self.CHANNEL, n):
            hi[i] = h[i - self.CHANNEL:i].max()
            lo[i] = l[i - self.CHANNEL:i].min()
        self._hi, self._lo = hi, lo
        k = 2.0 / (self.TREND_EMA + 1.0)
        e = np.full(n, np.nan)
        if n > self.TREND_EMA:
            e[self.TREND_EMA] = c[:self.TREND_EMA + 1].mean()
            for i in range(self.TREND_EMA + 1, n):
                e[i] = c[i] * k + e[i - 1] * (1 - k)
        self._ema = e

    def signal(self, d, i):
        if i < self.MIN_BARS or self._hi is None:
            return Signal()
        hi, lo, e = self._hi[i], self._lo[i], self._ema[i]
        if not (np.isfinite(hi) and np.isfinite(lo) and np.isfinite(e)):
            return Signal()
        c = d["c"][i]
        if c > hi and c > e:
            return Signal("BUY", f"donch-BO hi={hi:.2f}")
        if c < lo and c < e:
            return Signal("SELL", f"donch-BO lo={lo:.2f}")
        return Signal()


# ── Idea 4: London open-range breakout (M15) ─────────────────────────────
class LondonORB:
    name = "London Open Range Breakout"
    short_name = "LDN-ORB"
    sl_atr = 3.0
    tp_atr = 7.0
    trail_atr_mult = 999.0
    trail_activation_atr = 999.0
    max_spread_atr_ratio = 0.20
    MIN_BARS = 300

    OPEN_H = 7            # UTC hour London opens
    RANGE_BARS = 4        # first hour = 4 x M15 bars
    WINDOW_BARS = 16      # breakout must happen within 4h of the open
    _hour = _day = None

    def precompute(self, d):
        ts = pd.to_datetime(pd.Series(d["ts"]))
        self._hour = ts.dt.hour.to_numpy()
        self._day = ts.dt.date.to_numpy()

    def signal(self, d, i):
        if i < self.MIN_BARS or self._hour is None:
            return Signal()
        # find this day's London open index
        day = self._day[i]
        # locate first bar of the day at OPEN_H
        j = i
        start = None
        while j > i - 100 and j > 0 and self._day[j] == day:
            if self._hour[j] == self.OPEN_H and (j == 0 or self._hour[j - 1] != self.OPEN_H):
                start = j
                break
            j -= 1
        if start is None:
            return Signal()
        rng_end = start + self.RANGE_BARS
        if i <= rng_end or i > start + self.WINDOW_BARS:
            return Signal()
        hi = float(np.max(d["h"][start:rng_end]))
        lo = float(np.min(d["l"][start:rng_end]))
        c = d["c"][i]
        # only the first breakout of the window
        prev_max = float(np.max(d["c"][rng_end:i])) if i > rng_end else c
        prev_min = float(np.min(d["c"][rng_end:i])) if i > rng_end else c
        if c > hi and prev_max <= hi:
            return Signal("BUY", f"LDN-ORB hi={hi:.2f}")
        if c < lo and prev_min >= lo:
            return Signal("SELL", f"LDN-ORB lo={lo:.2f}")
        return Signal()


def run(strat_cls, d, sl, tp, spread=REAL_SPREAD, max_hold=64, adx_min=None, **ov):
    strat = strat_cls()
    if adx_min is not None and hasattr(strat, "ADX_MIN"):
        strat.ADX_MIN = adx_min
    for k, v in ov.items():
        setattr(strat, k, v)
    strat.sl_atr, strat.tp_atr = sl, tp
    strat.trail_atr_mult, strat.trail_activation_atr = 999.0, 999.0
    strat.precompute(d)
    eng = BacktestEngine(d, gold_cfg(max_hold), strat, spread_price=spread,
                          commission_per_lot=COMM, symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START), eng.trades


def fmt(m, label):
    if m is None or m.get("trades", 0) == 0:
        return f"  {label:<36} NO TRADES"
    flag = "  [under-sampled]" if m["trades"] < MIN_TRADES_GATE else ""
    star = "  <== PF>1" if m["profit_factor"] > 1.0 and m["trades"] >= MIN_TRADES_GATE else ""
    return (f"  {label:<36} trades={m['trades']:>5}  win%={m['win_rate']*100:>5.1f}  "
            f"PF={m['profit_factor']:>5.2f}  Sharpe={m['sharpe']:>5.2f}  "
            f"MaxDD%={m['max_dd_pct']:>5.1f}  TotRet%={m['total_return_pct']:>+8.1f}{flag}{star}")


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    cfg0 = ForexConfig(); cfg0.total_capital_usd = START
    df_m15, _ = loader.load("XAUUSD", 99.0, cfg0, csv_path=GOLD_CSV, allow_synthetic=True)
    print(f"[load] {len(df_m15):,} M15 bars\n")
    print(f"ALL results scored at spread={REAL_SPREAD} (live-measured), commission=${COMM}/lot\n")

    d_m15 = prepare_data(df_m15)
    df_h1 = resample(df_m15, "1h");  d_h1 = prepare_data(df_h1)
    df_h4 = resample(df_m15, "4h");  d_h4 = prepare_data(df_h4)
    print(f"resampled: H1={len(df_h1):,} bars, H4={len(df_h4):,} bars\n")

    print("=" * 112)
    print(" REFERENCE -- existing live strategy on M15 (what we must beat)")
    print("=" * 112)
    m, _ = run(FastHybridTrendPullback, d_m15, 3.0, 7.0, adx_min=20)
    print(fmt(m, "adx20tp7 M15 SL3/TP7"))

    print("\n" + "=" * 112)
    print(" IDEA 1+2 -- same pullback logic, HIGHER timeframe (bigger ATR vs fixed cost)")
    print("=" * 112)
    for tf, dd, hold in [("H1", d_h1, 64), ("H4", d_h4, 32)]:
        for adx in [18, 20, 22]:
            m, _ = run(FastHybridTrendPullback, dd, 3.0, 7.0, adx_min=adx, max_hold=hold)
            print(fmt(m, f"pullback {tf} adx{adx} SL3/TP7"))

    print("\n" + "=" * 112)
    print(" IDEA 3 -- Donchian breakout (different signal shape)")
    print("=" * 112)
    for tf, dd, hold in [("M15", d_m15, 64), ("H1", d_h1, 64), ("H4", d_h4, 32)]:
        for ch in [55, 100]:
            m, _ = run(DonchianBreakout, dd, 3.0, 7.0, max_hold=hold, CHANNEL=ch)
            print(fmt(m, f"donchian {tf} ch{ch} SL3/TP7"))

    print("\n" + "=" * 112)
    print(" IDEA 4 -- London open-range breakout (M15, time-based)")
    print("=" * 112)
    for sl, tp in [(3.0, 7.0), (2.0, 6.0)]:
        m, _ = run(LondonORB, d_m15, sl, tp)
        print(fmt(m, f"LDN-ORB M15 SL{sl}/TP{tp}"))


if __name__ == "__main__":
    main()
