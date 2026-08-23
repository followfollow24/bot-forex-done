#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test a "fresh-trend only" entry filter: skip the signal unless the H4 EMA
alignment has held for at most N bars at the moment of entry.

Motivation (from _maturity_ushape.py, 2,934 backtest entries across 3 markets):
    bucket 0-5 fresh   EV -0.10R   <- best in every market, and best in the
    bucket 6-20 mid    EV -0.91R      2nd half of all three, so unlike the
    bucket 21-50       EV -1.28R      live-183-trade U-shape it survives OOS
    bucket 51+ old     EV -0.67R

That was measured on raw signal geometry (MFE vs a 3xATR stop). This runs it
through the ACTUAL engine with real costs and the live TP=999 manual-exit
config, so the number is comparable to everything else deployed, and sweeps the
threshold rather than assuming 5 is special.

Includes a train/test split, because a threshold picked on the whole history is
exactly the kind of thing that looks great and then fails live.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import (DataLoader, prepare_data, BacktestEngine,
                             FastHybridTrendPullback, compute_metrics)
from gold_regime_filter_real_engine import RegimeFilteredHybrid
from forex_indicators import Signal
from _idea_search import resample
from _all_paths import to_monthly, perf, START

GOLD_M15 = "download/xauusd-m15-bid-2013-01-01-2026-06-10.csv"
BTC_CSV  = "download/btcusdt-15m-binance-2017-08-17-2026-06-30.csv"
ETH_CSV  = "download/ethusdt-15m-binance-2017-08-17-2026-06-30.csv"
THRESHOLDS = [3, 5, 10, 20, None]     # None = no filter (baseline)


class FreshTrendMixin:
    """Only take the entry if the trend alignment is at most MAX_MATURITY bars old."""
    MAX_MATURITY = 5
    _maturity_arr = None

    def precompute(self, d):
        super().precompute(d)
        t = self._h1_trend_arr
        if t is None:
            self._maturity_arr = None
            return
        mat = np.zeros(len(t), dtype=np.int32)
        run = 0
        prev = 0
        for i in range(len(t)):
            if t[i] != 0 and t[i] == prev:
                run += 1
            elif t[i] != 0:
                run = 1
            else:
                run = 0
            mat[i] = run
            prev = t[i]
        self._maturity_arr = mat

    def signal(self, d, i):
        sig = super().signal(d, i)
        if sig.action in ("BUY", "SELL") and self._maturity_arr is not None:
            if self._maturity_arr[i] > self.MAX_MATURITY:
                return Signal()
        return sig


class FreshPullback(FreshTrendMixin, FastHybridTrendPullback):
    pass


class FreshRegime(FreshTrendMixin, RegimeFilteredHybrid):
    pass


def cfg(sym, ps=None, pv=None, hold=64):
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = 0.30
    c.partial_tp_atr = 999.0
    c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False
    c.max_hold_bars = hold
    if ps is not None:
        c.pip_size[sym] = ps
        c.pip_value_usd_approx[sym] = pv
    return c


def run(cls, d, sym, spread, adx, comm=3.5, ps=None, pv=None, maxmat=None):
    s = cls()
    s.ADX_MIN = adx
    if maxmat is not None:
        s.MAX_MATURITY = maxmat
    s.sl_atr, s.tp_atr = 3.0, 999.0
    s.trail_atr_mult = s.trail_activation_atr = 999.0
    s.precompute(d)
    eng = BacktestEngine(d, cfg(sym, ps, pv), s, spread_price=spread,
                          commission_per_lot=comm, symbol=sym)
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START), eng.trades


def line(m, tr, label, years):
    if not m or m.get("trades", 0) < 15:
        print(f"    {label:<22} n={m.get('trades',0) if m else 0:>5}  too few")
        return None
    p = perf(to_monthly(tr))
    sh = p["sharpe"] if p else float("nan")
    tot = m["total_return_pct"]
    cg = -100.0 if tot <= -100 else ((1+tot/100)**(1/years)-1)*100
    print(f"    {label:<22} n={m['trades']:>5}  PF={m['profit_factor']:>5.2f}  "
          f"Sharpe={sh:>5.2f}  CAGR={cg:>+7.2f}%  DD={m['max_dd_pct']:>5.1f}%  "
          f"({m['trades']/years:>5.0f} trades/yr)")
    return dict(pf=m["profit_factor"], sharpe=sh, cagr=cg, n=m["trades"])


def sweep(name, base_cls, fresh_cls, d, sym, spread, adx, years, comm=3.5, ps=None, pv=None):
    print(f"\n  {name}")
    out = {}
    m, tr = run(base_cls, d, sym, spread, adx, comm, ps, pv)
    out["baseline"] = line(m, tr, "no filter (baseline)", years)
    for th in THRESHOLDS:
        if th is None:
            continue
        m, tr = run(fresh_cls, d, sym, spread, adx, comm, ps, pv, maxmat=th)
        out[th] = line(m, tr, f"maturity <= {th}", years)
    return out


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START

    print("=" * 104)
    print(" FRESH-TREND FILTER — full history, real costs, TP disabled (live manual-exit config)")
    print("=" * 104)

    dfg, _ = loader.load("XAUUSD", 99.0, c0, csv_path=GOLD_M15, allow_synthetic=True)
    dfg_h1 = resample(dfg, "1h")
    yg = (dfg_h1["timestamp"].iloc[-1] - dfg_h1["timestamp"].iloc[0]).days / 365.25
    dg = prepare_data(dfg_h1)
    sweep("GOLD H1 regime22 (cost $2.85)", RegimeFilteredHybrid, FreshRegime,
          dg, "XAUUSD", 2.85, 22, yg)

    dfb, _ = loader.load("BTCUSDc", 99.0, c0, csv_path=BTC_CSV, allow_synthetic=False)
    dfb_h1 = resample(dfb, "1h")
    yb = (dfb_h1["timestamp"].iloc[-1] - dfb_h1["timestamp"].iloc[0]).days / 365.25
    db = prepare_data(dfb_h1)
    sweep("BTC H1 adx18 (cost $10)", FastHybridTrendPullback, FreshPullback,
          db, "BTCUSDc", 10.0, 18, yb, comm=0.0, ps=1.0, pv=0.01)

    dfe, _ = loader.load("ETHUSDc", 99.0, c0, csv_path=ETH_CSV, allow_synthetic=False)
    dfe_h1 = resample(dfe, "1h")
    ye = (dfe_h1["timestamp"].iloc[-1] - dfe_h1["timestamp"].iloc[0]).days / 365.25
    de = prepare_data(dfe_h1)
    sweep("ETH H1 adx18 (cost $5)", FastHybridTrendPullback, FreshPullback,
          de, "ETHUSDc", 5.0, 18, ye, comm=0.0, ps=1.0, pv=0.01)

    # ── out-of-sample: pick threshold on 1st half, score on 2nd half ──────────
    print("\n" + "=" * 104)
    print(" OUT-OF-SAMPLE: threshold chosen on 1st half only, scored on 2nd half")
    print("=" * 104)
    for label, dfx, cls_b, cls_f, sym, sp, adx, comm, ps, pv in [
        ("GOLD", dfg_h1, RegimeFilteredHybrid, FreshRegime, "XAUUSD", 2.85, 22, 3.5, None, None),
        ("BTC",  dfb_h1, FastHybridTrendPullback, FreshPullback, "BTCUSDc", 10.0, 18, 0.0, 1.0, 0.01),
        ("ETH",  dfe_h1, FastHybridTrendPullback, FreshPullback, "ETHUSDc", 5.0, 18, 0.0, 1.0, 0.01),
    ]:
        mid = dfx["timestamp"].iloc[len(dfx)//2]
        tr_df = dfx[dfx["timestamp"] <= mid].reset_index(drop=True)
        te_df = dfx[dfx["timestamp"] > mid].reset_index(drop=True)
        if len(tr_df) < 2000 or len(te_df) < 2000:
            continue
        d_tr, d_te = prepare_data(tr_df), prepare_data(te_df)
        y_te = (te_df["timestamp"].iloc[-1] - te_df["timestamp"].iloc[0]).days / 365.25

        best_th, best_pf = None, -1
        for th in [3, 5, 10, 20]:
            m, _ = run(cls_f, d_tr, sym, sp, adx, comm, ps, pv, maxmat=th)
            if m and m.get("trades", 0) >= 30 and m["profit_factor"] > best_pf:
                best_pf, best_th = m["profit_factor"], th
        mb, trb = run(cls_b, d_te, sym, sp, adx, comm, ps, pv)
        print(f"\n  {label}: train picked maturity<={best_th} (train PF={best_pf:.2f})")
        line(mb, trb, "TEST baseline", y_te)
        if best_th:
            mf, trf = run(cls_f, d_te, sym, sp, adx, comm, ps, pv, maxmat=best_th)
            line(mf, trf, f"TEST maturity<={best_th}", y_te)


if __name__ == "__main__":
    main()
