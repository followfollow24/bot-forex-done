#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Opening Range Breakout (ORB) -- classic day-trading strategy, not yet tried
this session. Each UTC calendar day, the first OR_HOURS of that day set a
high/low "opening range". Once that range is formed, a breakout of it
(by BREAKOUT_MARGIN_ATR) during the rest of the SAME day is a signal. One
signal opportunity per instrument per day, matching what the user asked for
("บอทเทรดแบบวันต่อวัน").

Uses the SAME calendar-bucket infrastructure (_epoch_seconds/_bucket_ids)
already validated this session for the H1/H4 trend bug fix, applied here to
UTC-day buckets instead -- avoids reintroducing a new bucket-anchoring bug.

Exit reuses the already-proven SL + ATR-trailing mechanics (not a true
end-of-day flat close -- BacktestEngine's Signal has no CLOSE action, only
BUY/SELL/HOLD, so EOD exit isn't wired here). This isolates the question
"does opening-range-breakout ENTRY timing have edge" on its own.
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


class OpeningRangeBreakout:
    name = "Opening Range Breakout"
    short_name = "ORB"

    OR_HOURS = 4              # opening range window length
    BREAKOUT_MARGIN_ATR = 0.25
    sl_atr = 2.0
    tp_atr = 999.0
    trail_atr_mult = 3.0
    trail_activation_atr = 1.0
    max_spread_atr_ratio = 0.5
    MIN_BARS = 100

    _day_id = None
    _or_hi = None
    _or_lo = None
    _in_or_window = None

    @staticmethod
    def _epoch_seconds(ts) -> np.ndarray:
        return pd.to_datetime(pd.Series(ts)).astype("datetime64[s]").astype("int64").to_numpy()

    def precompute(self, d: dict):
        epoch = self._epoch_seconds(d["ts"])
        day_id = epoch // 86400
        or_seconds = self.OR_HOURS * 3600
        in_or_window = (epoch % 86400) < or_seconds

        n = len(d["c"])
        h, l = d["h"], d["l"]
        tmp = pd.DataFrame({"day": day_id, "in_or": in_or_window, "h": h, "l": l})
        or_only = tmp[tmp["in_or"]]
        or_hi_by_day = or_only.groupby("day")["h"].max()
        or_lo_by_day = or_only.groupby("day")["l"].min()

        self._day_id = day_id
        self._or_hi = tmp["day"].map(or_hi_by_day).to_numpy()
        self._or_lo = tmp["day"].map(or_lo_by_day).to_numpy()
        self._in_or_window = in_or_window

    def signal(self, d: dict, i: int) -> Signal:
        if i < self.MIN_BARS or self._in_or_window[i]:
            return Signal()  # no entries while the opening range itself is still forming
        hi, lo = self._or_hi[i], self._or_lo[i]
        if np.isnan(hi) or np.isnan(lo):
            return Signal()
        c = d["c"][i]
        atr = d["atr"][i]
        if np.isnan(atr) or atr <= 0:
            return Signal()
        margin = self.BREAKOUT_MARGIN_ATR * atr
        if c > hi + margin:
            return Signal("BUY", f"ORB>hi={hi:.2f}")
        if c < lo - margin:
            return Signal("SELL", f"ORB<lo={lo:.2f}")
        return Signal()


def cfg(sym, ps, pv, risk=1.0, hold=64):
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = risk
    c.partial_tp_atr = 999.0
    c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False
    c.max_hold_bars = hold
    c.pip_size[sym] = ps
    c.pip_value_usd_approx[sym] = pv
    return c


def run(d, sym, spread, or_hours, margin, risk=1.0):
    s = OpeningRangeBreakout()
    s.OR_HOURS = or_hours
    s.BREAKOUT_MARGIN_ATR = margin
    s.precompute(d)
    eng = BacktestEngine(d, cfg(sym, 1.0, 0.01, risk=risk), s, spread_price=spread, commission_per_lot=0.0, symbol=sym)
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START), eng.trades


def line(m, tr, label, yrs):
    if not m or m.get("trades", 0) < 15:
        n = m.get("trades", 0) if m else 0
        print(f"    {label:<24} n={n:>5}  too few"); return
    p = perf(to_monthly(tr))
    sh = p["sharpe"] if p else float("nan")
    tot = m["total_return_pct"]
    cg = -100.0 if tot <= -100 else ((1+tot/100)**(1/yrs)-1)*100
    print(f"    {label:<24} n={m['trades']:>5} ({m['trades']/yrs:>4.0f}/yr {m['trades']/yrs/365:.2f}/day)  "
          f"win%={m['win_rate']*100:>5.1f}  PF={m['profit_factor']:>5.2f}  Sharpe={sh:>5.2f}  "
          f"CAGR={cg:>+7.2f}%  DD={m['max_dd_pct']:>5.1f}%")


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START
    BTC_CSV = "download/btcusdt-15m-binance-2017-08-17-2026-06-30.csv"
    ETH_CSV = "download/ethusdt-15m-binance-2017-08-17-2026-06-30.csv"

    dfb, _ = loader.load("BTCUSDc", 99.0, c0, csv_path=BTC_CSV, allow_synthetic=False)
    yb = (dfb["timestamp"].iloc[-1]-dfb["timestamp"].iloc[0]).days/365.25
    db = prepare_data(dfb)

    dfe, _ = loader.load("ETHUSDc", 99.0, c0, csv_path=ETH_CSV, allow_synthetic=False)
    ye = (dfe["timestamp"].iloc[-1]-dfe["timestamp"].iloc[0]).days/365.25
    de = prepare_data(dfe)

    print("=" * 100)
    print(" OPENING RANGE BREAKOUT (M15 bars, real costs) -- OR window sweep")
    print("=" * 100)
    print("\n  BTC")
    for or_h in [1, 2, 4, 6, 8]:
        m, tr = run(db, "BTCUSDc", 10.0, or_h, 0.25)
        line(m, tr, f"OR={or_h}h", yb)

    print("\n  ETH")
    for or_h in [1, 2, 4, 6, 8]:
        m, tr = run(de, "ETHUSDc", 1.0, or_h, 0.25)
        line(m, tr, f"OR={or_h}h", ye)


if __name__ == "__main__":
    main()
