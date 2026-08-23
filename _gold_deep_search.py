#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Is there ANY viable gold strategy? A focused search, since gold is what the
account actually trades.

The reason M15 gold failed is cost/ATR: $2.85 against a ~$6 M15 ATR is ~45% of
the move being paid away every trade. That ratio improves mechanically as the
bar gets bigger:

    M15   ATR ~ $6     -> ~45%
    H1    ATR ~ $12    -> ~23%
    H4    ATR ~ $25    -> ~11%
    D1    ATR ~ $55    -> ~5%

and it also improves over time, because gold went from ~$1,300 to ~$4,000, so
the same dollar cost is a far smaller share of a modern bar than a 2013 one.

So this tests every timeframe x strategy x era combination at the cost actually
paid, and reports the best gold configuration that exists -- or confirms there
isn't one.
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
from _idea_search import DonchianBreakout, resample
from _crypto_new_edges import ZScoreReversion
from _all_paths import to_monthly, perf, START

GOLD_M15 = "download/xauusd-m15-bid-2013-01-01-2026-06-10.csv"
GOLDFUT = "download/goldfut-daily-yahoo.csv"
RISK = 0.30
COST = 2.85          # measured from the live bots' own fills log
TRADING_DAYS = 252


def cfg(hold):
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = RISK
    c.partial_tp_atr = 999.0
    c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False
    c.max_hold_bars = hold
    return c


def run(cls, d, hold, spread=COST, tp=7.0, sl=3.0, **ov):
    s = cls()
    for k, v in ov.items():
        setattr(s, k, v)
    s.sl_atr, s.tp_atr = sl, tp
    s.trail_atr_mult = s.trail_activation_atr = 999.0
    s.precompute(d)
    eng = BacktestEngine(d, cfg(hold), s, spread_price=spread,
                          commission_per_lot=3.5, symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START), eng.trades


def show(m, tr, label, ratio=None):
    if not m or m.get("trades", 0) < 40:
        print(f"  {label:<40} n={m.get('trades',0) if m else 0:>5}  (too few)")
        return None
    p = perf(to_monthly(tr))
    sh = p["sharpe"] if p else float("nan")
    star = "  <==" if (p and p["sharpe"] > 0.5 and m["profit_factor"] > 1.05) else ""
    r = f"{ratio:>6.1f}%" if ratio is not None else "      "
    print(f"  {label:<40} n={m['trades']:>5}  PF={m['profit_factor']:>5.2f}  "
          f"Sharpe={sh:>5.2f}  CAGR={(p['cagr'] if p else 0):>+7.2f}%  "
          f"DD={(p['dd'] if p else 0):>5.1f}%{r}{star}")
    return p


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START
    df, _ = loader.load("XAUUSD", 99.0, c0, csv_path=GOLD_M15, allow_synthetic=True)

    frames = {
        "M15": (df, 64),
        "H1":  (resample(df, "1h"), 64),
        "H4":  (resample(df, "4h"), 32),
        "D1":  (resample(df, "1D"), 30),
    }

    print("=" * 112)
    print(f" GOLD (XAUUSD), all timeframes x strategies, cost ${COST} -- FULL 13.4 YEARS")
    print("=" * 112)
    best = []
    for tf, (dfx, hold) in frames.items():
        d = prepare_data(dfx)
        atr = float(np.nanmedian(d["atr"]))
        ratio = COST / atr * 100 if atr > 0 else np.nan
        print(f"\n  --- {tf}  (median ATR ${atr:.2f}, cost/ATR {ratio:.1f}%) ---")
        cands = [
            ("pullback ADX18",  FastHybridTrendPullback, dict(ADX_MIN=18)),
            ("pullback ADX22",  FastHybridTrendPullback, dict(ADX_MIN=22)),
            ("regime22",        RegimeFilteredHybrid,    dict(ADX_MIN=22)),
            ("donchian 55",     DonchianBreakout,        dict(CHANNEL=55)),
            ("donchian 100",    DonchianBreakout,        dict(CHANNEL=100)),
            ("donchian 200",    DonchianBreakout,        dict(CHANNEL=200)),
            ("zrev z2 w24",     ZScoreReversion,         dict(ENTRY_Z=2.0, WIN=24)),
        ]
        for lbl, cls, kw in cands:
            try:
                m, tr = run(cls, d, hold, **kw)
            except Exception:
                continue
            p = show(m, tr, f"{tf} {lbl}", ratio)
            if p and m and m["profit_factor"] > 1.05 and p["sharpe"] > 0.4:
                best.append((p["sharpe"], tf, lbl, cls, kw, hold, m, p))

    print("\n" + "=" * 112)
    print(" SAME, but 2024-2026 ONLY (gold near today's price -> cost/ATR much better)")
    print("=" * 112)
    for tf, (dfx, hold) in frames.items():
        dfw = dfx[dfx["timestamp"] >= pd.Timestamp("2024-01-01")].reset_index(drop=True)
        if len(dfw) < 400:
            continue
        d = prepare_data(dfw)
        atr = float(np.nanmedian(d["atr"]))
        ratio = COST / atr * 100 if atr > 0 else np.nan
        print(f"\n  --- {tf}  (median ATR ${atr:.2f}, cost/ATR {ratio:.1f}%) ---")
        for lbl, cls, kw in [("pullback ADX18", FastHybridTrendPullback, dict(ADX_MIN=18)),
                             ("pullback ADX22", FastHybridTrendPullback, dict(ADX_MIN=22)),
                             ("regime22", RegimeFilteredHybrid, dict(ADX_MIN=22)),
                             ("donchian 100", DonchianBreakout, dict(CHANNEL=100)),
                             ("zrev z2 w24", ZScoreReversion, dict(ENTRY_Z=2.0, WIN=24))]:
            try:
                m, tr = run(cls, d, hold, **kw)
            except Exception:
                continue
            show(m, tr, f"{tf} {lbl}", ratio)

    print("\n" + "=" * 112)
    print(" GOLD FUTURES DAILY (separate instrument, much lower relative cost)")
    print("=" * 112)
    gf = pd.read_csv(GOLDFUT)
    gf["timestamp"] = pd.to_datetime(gf["timestamp"], utc=True).dt.tz_localize(None)
    dgf = prepare_data(gf[["timestamp", "open", "high", "low", "close"]].dropna())
    atr = float(np.nanmedian(dgf["atr"]))
    for spread_lbl, sp in [("$0.35 (futures-like)", 0.35), ("$2.85 (same as CFD)", 2.85)]:
        print(f"\n  --- cost {spread_lbl}, cost/ATR {sp/atr*100:.1f}% ---")
        for lbl, cls, kw in [("donchian 55", DonchianBreakout, dict(CHANNEL=55)),
                             ("donchian 100", DonchianBreakout, dict(CHANNEL=100)),
                             ("donchian 200", DonchianBreakout, dict(CHANNEL=200)),
                             ("pullback ADX18", FastHybridTrendPullback, dict(ADX_MIN=18))]:
            try:
                m, tr = run(cls, dgf, 30, spread=sp, **kw)
            except Exception:
                continue
            show(m, tr, f"GOLDFUT-D1 {lbl}")

    if best:
        print("\n" + "=" * 112)
        print(" BEST GOLD CONFIGURATIONS (full history, cost $2.85)")
        print("=" * 112)
        best.sort(reverse=True, key=lambda x: x[0])
        for sh, tf, lbl, _, _, _, m, p in best[:8]:
            print(f"  {tf:<5}{lbl:<20} Sharpe={sh:>5.2f}  PF={m['profit_factor']:>5.2f}  "
                  f"CAGR={p['cagr']:>+7.2f}%  DD={p['dd']:>5.1f}%  n={m['trades']}")
    else:
        print("\n  NO gold configuration cleared PF>1.05 and Sharpe>0.4 on full history.")


if __name__ == "__main__":
    main()
