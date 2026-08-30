#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LINE B re-run: the gold strategies rejected at SPREAD=2.85 / 2.00, re-scored at
the real 0.24 round-trip spread, on the post-2026-07-30 fixed engine.

COST NOTE (measured, backtest_forex.py:636 and :728-736):
  the engine charges spread_price/2 ONCE, at entry, in the fill price, and
  commission_per_lot on BOTH sides. So spread_price=X charges an effective
  round-trip spread of X/2 price units.
  -> real 0.24 round-trip  => spread_price = 0.48
  -> the old 2.85          => 1.425 round-trip = 5.94x the real cost
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
from _idea_search import resample, LondonORB
from _gold_breakout_search import DonchianBreakout

GOLD_M15 = "download/xauusd-m15-bid-2013-01-01-2026-06-10.csv"
EQUITY = 14_900.0          # real cent-account equity
RISK   = 0.30              # %/trade, real setting
COMM   = 3.50              # $/lot/side -- unchanged from the rejecting scripts

SPREADS = {                # engine spread_price -> effective round-trip
    "OLD 2.85 (=1.425 rt)": 2.85,
    "REAL 0.24 rt":         0.48,
    "2x pessimistic 0.48rt":0.96,
}


def cfg(hold=40, risk=RISK):
    c = ForexConfig()
    c.total_capital_usd = EQUITY
    c.risk_per_trade_pct = risk
    c.partial_tp_atr = 999.0
    c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False
    c.max_hold_bars = hold
    c.min_lot = 0.01
    return c


class RandomEntry:
    """Control: fires at random bars, same exit machinery, long-share matched."""
    name = "random"; short_name = "RND"
    sl_atr = 2.0; tp_atr = 999.0
    trail_atr_mult = 3.0; trail_activation_atr = 1.0
    MIN_BARS = 60
    p = 0.01; long_share = 0.5; seed = 0

    def precompute(self, d):
        rng = np.random.default_rng(self.seed)
        n = len(d["c"])
        self._fire = rng.random(n) < self.p
        self._long = rng.random(n) < self.long_share

    def signal(self, d, i):
        if i < self.MIN_BARS or not self._fire[i]:
            return Signal()
        if np.isnan(d["atr"][i]) or d["atr"][i] <= 0:
            return Signal()
        return Signal("BUY", "rnd") if self._long[i] else Signal("SELL", "rnd")


def run(d, strat, spread, hold=40, risk=RISK):
    strat.precompute(d)
    eng = BacktestEngine(d, cfg(hold, risk), strat, spread_price=spread,
                         commission_per_lot=COMM, symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, EQUITY), eng.trades


def donch(sl=2.0, tm=3.0, ta=1.0, margin=0.0, win=None):
    s = DonchianBreakout()
    s.sl_atr, s.tp_atr = sl, 999.0
    s.trail_atr_mult, s.trail_activation_atr = tm, ta
    s.BREAKOUT_MARGIN_ATR = margin
    return s


def concentration(tr):
    if not tr:
        return None
    p = np.array([t["net_pnl"] for t in tr])
    tot = p.sum()
    k = max(1, int(round(len(p) * 0.10)))
    top = np.sort(p)[-k:]
    best = p.max()
    return dict(total=tot, best=best,
                best_share=(best / tot * 100) if tot > 0 else float("nan"),
                topdec=top.sum(),
                topdec_share=(top.sum() / tot * 100) if tot > 0 else float("nan"),
                ex_topdec=tot - top.sum(), k=k)


def show(m, tr, label, years):
    if not m or m.get("trades", 0) == 0:
        print(f"    {label:<44} NO TRADES"); return None
    c = concentration(tr)
    tot = m["total_return_pct"]
    cg = -100.0 if tot <= -100 else ((1 + tot / 100) ** (1 / years) - 1) * 100
    print(f"    {label:<44} n={m['trades']:>4}  PF={m['profit_factor']:>5.2f}  "
          f"win%={m['win_rate']*100:>4.1f}  CAGR={cg:>+6.2f}%  DD={m['max_dd_pct']:>5.1f}%  "
          f"net=${c['total']:>+9.1f}  ex-top10%=${c['ex_topdec']:>+9.1f}")
    return c


def split(df):
    mid = df["timestamp"].iloc[len(df) // 2]
    return df[df["timestamp"] < mid].reset_index(drop=True), \
           df[df["timestamp"] >= mid].reset_index(drop=True), mid


def yrs(df):
    return (df["timestamp"].iloc[-1] - df["timestamp"].iloc[0]).days / 365.25


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = EQUITY
    dfg, _ = loader.load("XAUUSD", 99.0, c0, csv_path=GOLD_M15, allow_synthetic=True)
    print(f"[load] {len(dfg):,} M15 bars  {dfg['timestamp'].iloc[0]} .. {dfg['timestamp'].iloc[-1]}")
    print(f"[cost] engine charges spread_price/2 at entry only + ${COMM}/lot/side both sides")
    print(f"[acct] equity=${EQUITY:,.0f}  risk={RISK}%/trade  min_lot=0.01\n")

    tfs = {}
    for name, rule, hold in [("M15", None, 64), ("H1", "1h", 48), ("H4", "4h", 40), ("D1", "1D", 20)]:
        d_df = dfg if rule is None else resample(dfg, rule)
        tfs[name] = (d_df, prepare_data(d_df), hold)
        a = np.asarray(tfs[name][1]["atr"], dtype=float)
        med = np.nanmedian(a)
        print(f"  {name}: {len(d_df):,} bars, median ATR ${med:.2f} -> "
              f"cost/ATR at 2.85={1.425/med*100:.0f}%  at real 0.24={0.24/med*100:.1f}%")

    # ---------------- 1. DONCHIAN BREAKOUT, FULL HISTORY, COST SWEEP ----------
    print("\n" + "=" * 108)
    print(" [1] GOLD DONCHIAN BREAKOUT (sl2.0 trail3.0x@1.0) -- full history, spread sweep")
    print("=" * 108)
    for tf in ("M15", "H1", "H4", "D1"):
        d_df, d, hold = tfs[tf]
        y = yrs(d_df)
        print(f"\n  -- {tf} ({y:.1f}y)")
        for lbl, sp in SPREADS.items():
            m, tr = run(d, donch(), sp, hold)
            show(m, tr, f"{tf}  spread={lbl}", y)

    # ---------------- 2. LONDON ORB M15, COST SWEEP --------------------------
    print("\n" + "=" * 108)
    print(" [2] LONDON OPEN-RANGE BREAKOUT, M15 (sl3 tp7) -- rejected at spread 2.00")
    print("=" * 108)
    d_df, d, hold = tfs["M15"]; y = yrs(d_df)
    for lbl, sp in list(SPREADS.items()) + [("_idea_search 2.00", 2.00)]:
        s = LondonORB(); s.sl_atr, s.tp_atr = 3.0, 7.0
        s.trail_atr_mult = s.trail_activation_atr = 999.0
        m, tr = run(d, s, sp, hold)
        show(m, tr, f"M15 LDN-ORB spread={lbl}", y)

    # ---------------- 3. TREND-PULLBACK, COST SWEEP --------------------------
    print("\n" + "=" * 108)
    print(" [3] HYBRID TREND-PULLBACK adx20 sl3/tp7 -- cost sweep by timeframe")
    print("=" * 108)
    for tf in ("M15", "H1", "H4"):
        d_df, d, hold = tfs[tf]; y = yrs(d_df)
        print(f"\n  -- {tf} ({y:.1f}y)")
        for lbl, sp in SPREADS.items():
            s = FastHybridTrendPullback(); s.ADX_MIN = 20
            s.sl_atr, s.tp_atr = 3.0, 7.0
            s.trail_atr_mult = s.trail_activation_atr = 999.0
            m, tr = run(d, s, sp, hold)
            show(m, tr, f"{tf}  spread={lbl}", y)


if __name__ == "__main__":
    main()
