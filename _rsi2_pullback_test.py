#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RSI(2) pullback-in-trend (Connors-style) -- genuinely different structure from
every RSI variant tried this session: enters the INSTANT RSI(2) hits an
extreme inside an established trend (EMA200 filter), exits fast (RSI back to
midline OR close above/below a short EMA), not a fixed R:R swing trade. Meant
to fire much more often than trend-pullback/breakout families.

Causal: trend filter uses EMA200 up to and including bar i (no look-ahead).
RSI(2) computed on closes up to bar i. Entry decided AFTER bar i closes,
executed at bar i+1 open via the real BacktestEngine (same as everything else
this session).
"""
from __future__ import annotations
import os, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import DataLoader, prepare_data, BacktestEngine, compute_metrics
from forex_indicators import Signal
from _idea_search import resample
from _all_paths import to_monthly, perf, START


def _ema(prices, span):
    out = np.full(len(prices), np.nan)
    if len(prices) < span:
        return out
    alpha = 2.0 / (span + 1)
    out[0] = prices[0]
    for j in range(1, len(prices)):
        out[j] = prices[j] * alpha + out[j - 1] * (1 - alpha)
    return out


def _rsi(closes, period=2):
    n = len(closes)
    out = np.full(n, np.nan)
    if n < period + 1:
        return out
    delta = np.diff(closes)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = np.zeros(n); avg_loss = np.zeros(n)
    avg_gain[period] = gain[:period].mean()
    avg_loss[period] = loss[:period].mean()
    for i in range(period + 1, n):
        avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gain[i - 1]) / period
        avg_loss[i] = (avg_loss[i - 1] * (period - 1) + loss[i - 1]) / period
    rs = np.divide(avg_gain, avg_loss, out=np.full(n, np.inf), where=avg_loss > 0)
    out[period:] = 100 - 100 / (1 + rs[period:])
    out[avg_loss == 0] = 100.0
    out[:period] = np.nan
    return out


class RSI2Pullback:
    name = "RSI(2) Pullback-in-Trend"
    short_name = "RSI2PB"

    EMA_TREND = 200
    EMA_FAST = 5
    RSI_PERIOD = 2
    RSI_BUY = 10.0
    RSI_SELL = 90.0
    sl_atr = 1.5
    tp_atr = 999.0
    trail_atr_mult = 999.0
    trail_activation_atr = 999.0
    max_hold_bars = 10       # fast exit by design; engine max_hold as backstop
    max_spread_atr_ratio = 1.0
    MIN_BARS = 210

    _built_len = None

    def precompute(self, d):
        c = d["c"]
        self._ema_trend = _ema(c, self.EMA_TREND)
        self._ema_fast = _ema(c, self.EMA_FAST)
        self._rsi = _rsi(c, self.RSI_PERIOD)
        self._built_len = len(c)

    def _ensure(self, d):
        if self._built_len != len(d["c"]):
            self.precompute(d)

    def signal(self, d, i):
        if i < self.MIN_BARS:
            return Signal()
        self._ensure(d)
        atr = d["atr"][i]
        if np.isnan(atr) or atr <= 0:
            return Signal()
        et, ef, rsi = self._ema_trend[i], self._ema_fast[i], self._rsi[i]
        if np.isnan(et) or np.isnan(ef) or np.isnan(rsi):
            return Signal()
        c = d["c"][i]
        l = d["l"][i]; h = d["h"][i]

        if c > et and rsi <= self.RSI_BUY:
            sl_price = l - 0.2 * atr
            risk = c - sl_price
            if risk > 0:
                self.sl_atr = risk / atr
                return Signal("BUY", f"rsi2={rsi:.0f} trend=up")

        if c < et and rsi >= self.RSI_SELL:
            sl_price = h + 0.2 * atr
            risk = sl_price - c
            if risk > 0:
                self.sl_atr = risk / atr
                return Signal("SELL", f"rsi2={rsi:.0f} trend=down")

        return Signal()


def cfg(risk=1.0, hold=10):
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = risk
    c.partial_tp_atr = 999.0; c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False; c.max_hold_bars = hold
    return c


def run(d, sym, spread, ps=None, pv=None, comm=0.0, risk=1.0, **kw):
    s = RSI2Pullback()
    for k, v in kw.items(): setattr(s, k, v)
    s.precompute(d)
    c = cfg(risk, s.max_hold_bars)
    if ps is not None:
        c.pip_size[sym] = ps; c.pip_value_usd_approx[sym] = pv
    eng = BacktestEngine(d, c, s, spread_price=spread, commission_per_lot=comm, symbol=sym)
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START), eng.trades


def line(m, tr, label, yrs):
    if not m or m.get("trades", 0) < 20:
        print(f"    {label:<28} n={m.get('trades',0) if m else 0:>5}  too few"); return
    p = perf(to_monthly(tr)); sh = p["sharpe"] if p else float("nan")
    tot = m["total_return_pct"]
    cg = -100.0 if tot <= -100 else ((1+tot/100)**(1/yrs)-1)*100
    print(f"    {label:<28} n={m['trades']:>5} ({m['trades']/yrs:>4.0f}/yr {m['trades']/yrs/365:.2f}/day)  "
          f"win%={m['win_rate']*100:>5.1f}  PF={m['profit_factor']:>5.2f}  Sharpe={sh:>5.2f}  "
          f"CAGR={cg:>+7.2f}%  DD={m['max_dd_pct']:>5.1f}%")


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START
    dfg, _ = loader.load("XAUUSD", 99.0, c0, csv_path="download/xauusd-m15-bid-2013-01-01-2026-06-10.csv", allow_synthetic=True)
    dfb, _ = loader.load("BTCUSDc", 99.0, c0, csv_path="download/btcusdt-15m-binance-2017-08-17-2026-06-30.csv", allow_synthetic=False)
    dfe, _ = loader.load("ETHUSDc", 99.0, c0, csv_path="download/ethusdt-15m-binance-2017-08-17-2026-06-30.csv", allow_synthetic=False)

    markets = [
        ("GOLD H1", resample(dfg, "1h"), "XAUUSD", 0.24, None, None, 3.5, 0.30),
        ("BTC  H1", resample(dfb, "1h"), "BTCUSDc", 10.0, 1.0, 0.01, 0.0, 1.00),
        ("ETH  H1", resample(dfe, "1h"), "ETHUSDc", 1.0, 1.0, 0.01, 0.0, 1.00),
    ]

    print("=" * 100)
    print(" RSI(2) PULLBACK-IN-TREND (Connors-style) -- H1, real costs, RSI extreme sweep")
    print("=" * 100)
    for label, df, sym, sp, ps, pv, comm, risk in markets:
        yrs = (df["timestamp"].iloc[-1]-df["timestamp"].iloc[0]).days/365.25
        d = prepare_data(df)
        print(f"\n  {label} ({yrs:.1f}y, risk={risk}%)")
        for rb in [5, 10, 15]:
            m, tr = run(d, sym, sp, ps, pv, comm, risk, RSI_BUY=float(rb), RSI_SELL=float(100-rb))
            line(m, tr, f"RSI<={rb}/>={100-rb}", yrs)


if __name__ == "__main__":
    main()
