#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests two proposals from the user (2026-08-02) against data:

 (1) PORTFOLIO EXPANSION -- run the same three tool logics (AMD / LQ-Sweep /
     TPO-Profile) on ETH and SOL, not just BTC. Hypothesis: higher-vol
     majors with similar algorithmic microstructure sweep deeper and
     distribute harder, so expectancy should improve.

 (2) CRYPTO KILLZONES -- replace the FX/gold London+NY killzones with the
     two windows where crypto liquidity actually concentrates (Thai time):
        06:30-07:30  daily-candle close/open sweep (crypto day rolls 07:00 TH)
        20:30-22:00  US equity open overlap / ETF flow
     Implemented on H1 bars as Thai hours {6,7} and {20,21}.

Both are applied as a wrapper so the underlying tool logic is untouched and
the comparison is like-for-like.

Costs: BTC $10 spread, ETH $1, SOL estimated at 0.05% of price (SOL is NOT
verified tradeable on the live Exness MT5 account -- see report).
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import DataLoader, prepare_data, BacktestEngine, compute_metrics
from forex_indicators import Signal
from ict_tools_strategies import ToolAMD, ToolLQSweep, ToolTPOProfile, _epoch_seconds
from _idea_search import resample
from _all_paths import to_monthly, perf, START

THAI = 7 * 3600
CRYPTO_KZ_HOURS = {6, 7, 20, 21}     # Thai hours


def make_kz(base_cls, hours=CRYPTO_KZ_HOURS):
    """Wrap a tool with a Thai-hour killzone filter, logic otherwise identical."""
    class _KZ(base_cls):
        name = base_cls.name + " +cryptoKZ"
        short_name = base_cls.short_name + "+KZ"
        _kz_len = None

        def _kz_build(self, d):
            ep = _epoch_seconds(d["ts"]) + THAI
            hr = (ep % 86400) // 3600
            self._kz_ok = np.isin(hr, list(hours))
            self._kz_len = len(d["c"])

        def signal(self, d, i):
            if self._kz_len != len(d["c"]):
                self._kz_build(d)
            if not self._kz_ok[i]:
                return Signal()
            return super().signal(d, i)
    _KZ.__name__ = base_cls.__name__ + "KZ"
    return _KZ


def cfg(sym, pv, risk=1.0, hold=48):
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = risk
    c.partial_tp_atr = 999.0; c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False; c.max_hold_bars = hold
    c.pip_size[sym] = 1.0; c.pip_value_usd_approx[sym] = pv
    c.max_lot = 1e9; c.max_risk_per_trade_pct = 100.0
    return c


def run(cls, d, sym, spread, pv, risk=1.0):
    s = cls(); s.precompute(d)
    eng = BacktestEngine(d, cfg(sym, pv, risk), s, spread_price=spread,
                         commission_per_lot=0.0, symbol=sym)
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START), eng.trades


def line(m, tr, label, yrs):
    if not m or m.get("trades", 0) < 20:
        print(f"      {label:<26} n={m.get('trades',0) if m else 0:>5}  too few"); return None
    p = perf(to_monthly(tr)); sh = p["sharpe"] if p else float("nan")
    tot = m["total_return_pct"]
    cg = -100.0 if tot <= -100 else ((1+tot/100)**(1/yrs)-1)*100
    print(f"      {label:<26} n={m['trades']:>5} ({m['trades']/yrs/365:.2f}/day)  "
          f"PF={m['profit_factor']:>5.2f}  Sharpe={sh:>5.2f}  CAGR={cg:>+7.2f}%  "
          f"DD={m['max_dd_pct']:>5.1f}%")
    return m["profit_factor"]


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START

    markets = []
    for tag, path, spread, pv in [
        ("BTC", "download/btcusdt-15m-binance-2017-08-17-2026-06-30.csv", 10.0, 0.01),
        ("ETH", "download/ethusdt-15m-binance-2017-08-17-2026-06-30.csv",  1.0, None),
        ("SOL", "download/solusdt-1h-binance.csv",                        None, None),
    ]:
        df, _ = loader.load(f"{tag}USDc", 99.0, c0, csv_path=path, allow_synthetic=False)
        dfh = df if tag == "SOL" else resample(df, "1h")
        price = float(dfh["close"].iloc[-1])
        if spread is None:
            spread = round(price * 0.0005, 6)
        if pv is None:
            pv = 0.01 * (65000.0 / price)
        markets.append((tag, dfh, f"{tag}USDc", spread, pv))

    print("=" * 100)
    print(" (1) PORTFOLIO EXPANSION + (2) CRYPTO KILLZONES -- H1, real/estimated costs")
    print("=" * 100)

    results = {}
    for tag, dfh, sym, spread, pv in markets:
        yrs = (dfh["timestamp"].iloc[-1] - dfh["timestamp"].iloc[0]).days / 365.25
        d = prepare_data(dfh)
        print(f"\n  {tag}  ({yrs:.1f}y, spread={spread})")
        for base in (ToolAMD, ToolLQSweep, ToolTPOProfile):
            print(f"    {base.short_name}")
            pf_plain = line(*run(base, d, sym, spread, pv), "baseline (no KZ)", yrs)
            pf_kz    = line(*run(make_kz(base), d, sym, spread, pv), "+ crypto killzone", yrs)
            results[(tag, base.short_name)] = (pf_plain, pf_kz)

    print("\n" + "=" * 100)
    print(" OOS (2nd half) for every market x tool, WITH crypto killzone")
    print("=" * 100)
    for tag, dfh, sym, spread, pv in markets:
        mid = dfh["timestamp"].iloc[len(dfh)//2]
        te = dfh[dfh["timestamp"] > mid].reset_index(drop=True)
        tr_ = dfh[dfh["timestamp"] <= mid].reset_index(drop=True)
        y_te = (te["timestamp"].iloc[-1]-te["timestamp"].iloc[0]).days/365.25
        y_tr = (tr_["timestamp"].iloc[-1]-tr_["timestamp"].iloc[0]).days/365.25
        d_te, d_tr = prepare_data(te), prepare_data(tr_)
        print(f"\n  {tag}")
        for base in (ToolAMD, ToolLQSweep, ToolTPOProfile):
            kz = make_kz(base)
            print(f"    {base.short_name}+KZ")
            line(*run(kz, d_tr, sym, spread, pv), "train", y_tr)
            line(*run(kz, d_te, sym, spread, pv), "OOS", y_te)

    print("\n" + "=" * 100)
    print(" SUMMARY: did the crypto killzone help? (full-history PF, no-KZ -> +KZ)")
    print("=" * 100)
    for k, (a, b) in results.items():
        if a is None or b is None: continue
        arrow = "BETTER" if b > a else ("worse" if b < a else "same")
        print(f"   {k[0]:<5} {k[1]:<14} {a:>5.2f} -> {b:>5.2f}   {arrow}")


if __name__ == "__main__":
    main()
