#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Dynamic (structure-aware) TP vs a flat ATR-multiple TP.

CONSTRAINT that shapes every design here: the TP must be submitted to MT5 as
part of the ENTRY order (see _open_position() in forex_live_bot_gold_cwider.py),
because that is what makes it survive a bot hang -- the whole point of having
it. So the target must be computable ONCE, at entry, from bars up to and
including the signal bar. Anything that re-adjusts while the position is open
is a trailing stop, which cannot ship: modify_sl() in forex_executor.py has
zero callers anywhere in the repo (dead code on the live path).

Variants tested, all causal (only bars <= i):
  swing     : TP at the highest high / lowest low of the last N bars -- i.e.
              "take profit where the chart last turned around".
  donchian  : same idea but the channel extreme EXCLUDING the current bar,
              plus an optional push beyond it (breakout targets).
  volscale  : flat multiple scaled by the CURRENT volatility regime (rolling
              ATR percentile) -- wider target when the market is already
              moving fast, tighter when it is quiet.
  fixed     : control -- the flat multiples already measured.

All have a floor and a cap in ATR units, because a raw structural target can
land behind the entry (long breaking out above every recent high -> negative
distance) or absurdly far.
"""
from __future__ import annotations
import os, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import (DataLoader, prepare_data, BacktestEngine,
                            FastHybridTrendPullback, compute_metrics)
from _idea_search import resample
from _all_paths import to_monthly, perf, START


class DynamicTP(FastHybridTrendPullback):
    """Inherits the live entry logic untouched; only sets tp_atr per signal."""

    TP_MODE = "fixed"      # fixed | swing | donchian | volscale
    TP_FIXED = 15.0
    TP_LOOKBACK = 50
    TP_EXTEND = 0.0        # push the target this many ATR beyond the structure
    TP_MIN = 3.0           # floor, in ATR
    TP_MAX = 30.0          # cap, in ATR
    VOL_WIN = 500
    VOL_K = 1.0            # volscale sensitivity

    _vol_pct = None
    _vol_len = None

    def _ensure_vol(self, d):
        if self._vol_len == len(d["c"]):
            return
        a = d["atr"]; n = len(a)
        pr = np.full(n, np.nan)
        for i in range(self.VOL_WIN, n):
            w = a[i - self.VOL_WIN:i]          # PRIOR window only -> causal
            w = w[~np.isnan(w)]
            if len(w) > 50 and not np.isnan(a[i]):
                pr[i] = (w < a[i]).mean()
        self._vol_pct = pr
        self._vol_len = n

    def signal(self, d, i):
        s = super().signal(d, i)
        if s.action not in ("BUY", "SELL"):
            return s

        atr = d["atr"][i]
        if np.isnan(atr) or atr <= 0:
            return s
        c = d["c"][i]

        if self.TP_MODE == "fixed":
            dist = self.TP_FIXED

        elif self.TP_MODE == "swing":
            j0 = max(0, i - self.TP_LOOKBACK + 1)
            if s.action == "BUY":
                target = float(np.max(d["h"][j0:i + 1]))
                dist = (target - c) / atr + self.TP_EXTEND
            else:
                target = float(np.min(d["l"][j0:i + 1]))
                dist = (c - target) / atr + self.TP_EXTEND

        elif self.TP_MODE == "donchian":
            # channel of the PRIOR N bars, current bar excluded
            j0 = max(0, i - self.TP_LOOKBACK)
            if i == 0:
                return s
            if s.action == "BUY":
                target = float(np.max(d["h"][j0:i]))
                dist = (target - c) / atr + self.TP_EXTEND
            else:
                target = float(np.min(d["l"][j0:i]))
                dist = (c - target) / atr + self.TP_EXTEND

        elif self.TP_MODE == "volscale":
            self._ensure_vol(d)
            p = self._vol_pct[i]
            if np.isnan(p):
                dist = self.TP_FIXED
            else:
                # p=0.5 -> unchanged; p=1 -> +VOL_K*TP_FIXED; p=0 -> -VOL_K*TP_FIXED
                dist = self.TP_FIXED * (1.0 + self.VOL_K * (p - 0.5) * 2.0)
        else:
            dist = self.TP_FIXED

        self.tp_atr = float(max(self.TP_MIN, min(self.TP_MAX, dist)))
        return s


def cfg(risk, sym, ps=None, pv=None):
    c = ForexConfig(); c.total_capital_usd = START; c.risk_per_trade_pct = risk
    c.partial_tp_atr = 999.0; c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False; c.max_hold_bars = 64
    if ps is not None:
        c.pip_size[sym] = ps; c.pip_value_usd_approx[sym] = pv
    return c


def go(df, adx, touch, sym, spread, comm, risk, ps=None, pv=None, **kw):
    d = prepare_data(df)
    s = DynamicTP()
    s.ADX_MIN = adx; s.TIMEFRAME_SECONDS = 3600; s.TOUCH_TOLERANCE = touch
    s.sl_atr = 3.0; s.trail_atr_mult = 999.0; s.trail_activation_atr = 999.0
    for k, v in kw.items():
        setattr(s, k, v)
    s.precompute(d)
    eng = BacktestEngine(d, cfg(risk, sym, ps, pv), s,
                         spread_price=spread, commission_per_lot=comm, symbol=sym)
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START), eng.trades


def stat(m, tr, yrs):
    if not m or m.get("trades", 0) < 20:
        return None
    p = perf(to_monthly(tr)); sh = p["sharpe"] if p else float("nan")
    tot = m["total_return_pct"]
    cg = -100.0 if tot <= -100 else ((1 + tot / 100) ** (1 / yrs) - 1) * 100
    dd = m["max_dd_pct"]
    return sh, cg, dd, (cg / dd if dd > 0 else 0), m["trades"], m["win_rate"] * 100


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START
    dfg, _ = loader.load("XAUUSD", 99.0, c0, csv_path="download/xauusd-m15-bid-2013-01-01-2026-06-10.csv", allow_synthetic=True)
    dfb, _ = loader.load("BTCUSDc", 99.0, c0, csv_path="download/btcusdt-15m-binance-2017-08-17-2026-06-30.csv", allow_synthetic=False)
    dfe, _ = loader.load("ETHUSDc", 99.0, c0, csv_path="download/ethusdt-15m-binance-2017-08-17-2026-06-30.csv", allow_synthetic=False)

    MK = [("BTC ", dfb, 10, 0.012, "BTCUSDc", 10.0, 0.0, 1.00, 1.0, 0.01),
          ("GOLD", dfg, 10, 0.012, "XAUUSD", 0.24, 3.5, 0.30, None, None),
          ("ETH ", dfe, 18, 0.0015, "ETHUSDc", 1.0, 0.0, 1.90, 1.0, 0.01)]

    VARIANTS = [
        ("no TP (baseline)",        dict(TP_MODE="fixed", TP_FIXED=999.0, TP_MAX=999.0)),
        ("fixed 5xATR (live now)",  dict(TP_MODE="fixed", TP_FIXED=5.0)),
        ("fixed 15xATR",            dict(TP_MODE="fixed", TP_FIXED=15.0)),
        ("swing 20b",               dict(TP_MODE="swing", TP_LOOKBACK=20)),
        ("swing 50b",               dict(TP_MODE="swing", TP_LOOKBACK=50)),
        ("swing 100b",              dict(TP_MODE="swing", TP_LOOKBACK=100)),
        ("swing 50b +2ATR",         dict(TP_MODE="swing", TP_LOOKBACK=50, TP_EXTEND=2.0)),
        ("swing 100b +3ATR",        dict(TP_MODE="swing", TP_LOOKBACK=100, TP_EXTEND=3.0)),
        ("donch 50b",               dict(TP_MODE="donchian", TP_LOOKBACK=50)),
        ("donch 100b +2ATR",        dict(TP_MODE="donchian", TP_LOOKBACK=100, TP_EXTEND=2.0)),
        ("volscale 15 k=0.5",       dict(TP_MODE="volscale", TP_FIXED=15.0, VOL_K=0.5)),
        ("volscale 15 k=1.0",       dict(TP_MODE="volscale", TP_FIXED=15.0, VOL_K=1.0)),
    ]

    print("=" * 118)
    print(" DYNAMIC (structure-aware) TP vs FLAT TP -- all causal, TP fixed at entry (broker-side compatible)")
    print("=" * 118)
    hdr = f"{'variant':<24}"
    for nm, *_ in MK:
        hdr += f"{nm+' Sh':>9}{nm+' CAGR':>10}{nm+' DD':>9}{nm+' Cal':>8}"
    print(hdr)
    print("-" * 118)

    for label, kw in VARIANTS:
        row = f"{label:<24}"
        for nm, df, adx, touch, sym, sp, cm, rk, ps, pv in MK:
            dh1 = resample(df, "1h")
            yrs = (dh1["timestamp"].iloc[-1] - dh1["timestamp"].iloc[0]).days / 365.25
            m, tr = go(dh1, adx, touch, sym, sp, cm, rk, ps, pv, **kw)
            st = stat(m, tr, yrs)
            if st is None:
                row += f"{'too few':>36}"
            else:
                sh, cg, dd, cal, n, wr = st
                row += f"{sh:>9.2f}{cg:>9.2f}%{dd:>8.1f}%{cal:>8.2f}"
        print(row)


if __name__ == "__main__":
    main()
