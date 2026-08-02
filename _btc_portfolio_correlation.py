#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BTC portfolio correlation / concurrent-exposure audit.

The question is not just "are the two new sleeves correlated with each
other" -- there are already FIVE BTC bots live or queued, all trading the
same underlying asset:

    btc_h1_manual      HybridTrendPullback  (live, risk 1.90%)
    btc_h1_breakout    Donchian breakout    (live, risk 1.00%)
    btc_amd            ToolAMD              (live, risk 0.30%)
    btc_lqsweep        ToolLQSweep          (live, risk 0.50%)
    btc_tpo            ToolTPOProfile       (live, risk 0.50%)

Correlation of monthly RETURNS understates the real danger here, because
the actual risk is DIRECTIONAL CONCURRENCY: several bots holding the same
side of the same asset at the same moment turns "5 x small risk" into one
large undiversified bet. So this measures three separate things:

  1. monthly return correlation (the usual diversification number)
  2. % of bars with >1 sleeve in a position at once  (concurrency)
  3. of those, same-direction vs opposite-direction  (stacking vs hedging)
  4. worst-case simultaneous risk: max sleeves aligned on one side, and
     the total account risk that represents at the configured risk %

Run with and without --crypto-killzone equivalents to see whether the
time filter also reduces stacking.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import (DataLoader, prepare_data, BacktestEngine,
                            FastHybridTrendPullback, compute_metrics)
from _gold_breakout_refine import DonchianBreakoutV2
from ict_tools_strategies import ToolAMD, ToolLQSweep, ToolTPOProfile
from _idea_search import resample
from _all_paths import to_monthly, perf, START


def kz(base):
    return type(base.__name__ + "KZ", (base,), {"USE_CRYPTO_KZ": True})


def cfg(risk, hold=64):
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = risk
    c.partial_tp_atr = 999.0; c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False; c.max_hold_bars = hold
    c.pip_size["BTCUSDc"] = 1.0; c.pip_value_usd_approx["BTCUSDc"] = 0.01
    return c


def run(build, d, risk):
    s = build()
    s.precompute(d)
    eng = BacktestEngine(d, cfg(risk), s, spread_price=10.0,
                         commission_per_lot=0.0, symbol="BTCUSDc")
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START), eng.trades


def make_pullback():
    s = FastHybridTrendPullback(); s.ADX_MIN = 18
    s.sl_atr, s.tp_atr = 3.0, 999.0
    s.trail_atr_mult = s.trail_activation_atr = 999.0
    return s


def make_breakout():
    s = DonchianBreakoutV2(); s.DONCH_WIN = 80; s.BREAKOUT_MARGIN_ATR = 0.25
    s.sl_atr, s.tp_atr = 2.0, 999.0
    s.trail_atr_mult, s.trail_activation_atr = 3.0, 1.0
    return s


def position_series(trades, ts_index):
    """+1 while long, -1 while short, 0 flat -- aligned to the bar index."""
    pos = np.zeros(len(ts_index), dtype=np.int8)
    lut = {t: k for k, t in enumerate(ts_index)}
    for tr in trades:
        a = lut.get(str(tr["entry_ts"])); b = lut.get(str(tr["exit_ts"]))
        if a is None:
            continue
        if b is None or b < a:
            b = len(pos) - 1
        pos[a:b + 1] = 1 if tr["side"] == "long" else -1
    return pos


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START
    df, _ = loader.load("BTCUSDc", 99.0, c0,
                        csv_path="download/btcusdt-15m-binance-2017-08-17-2026-06-30.csv",
                        allow_synthetic=False)
    dfh = resample(df, "1h")
    d = prepare_data(dfh)
    ts_index = [str(x) for x in d["ts"]]
    yrs = (dfh["timestamp"].iloc[-1] - dfh["timestamp"].iloc[0]).days / 365.25

    sleeves = [
        ("h1_manual",  make_pullback,          1.90),
        ("h1_breakout", make_breakout,         1.00),
        ("amd+KZ",     lambda: kz(ToolAMD)(),  0.30),
        ("lqsweep+KZ", lambda: kz(ToolLQSweep)(), 0.50),
        ("tpo+KZ",     lambda: kz(ToolTPOProfile)(), 0.50),
    ]

    pos_map, mret_map, risks = {}, {}, {}
    print("=" * 92)
    print(" BTC SLEEVES -- individual (H1, real $10 spread, each at its LIVE risk %)")
    print("=" * 92)
    for name, build, risk in sleeves:
        m, tr = run(build, d, risk)
        pos_map[name] = position_series(tr, ts_index)
        mr = to_monthly(tr)
        if len(mr) >= 12:
            mret_map[name] = mr
        risks[name] = risk
        p = perf(mr) if len(mr) >= 12 else None
        print(f"  {name:<13} risk={risk:>4.2f}%  n={m['trades']:>4}  PF={m['profit_factor']:>5.2f}  "
              f"Sharpe={p['sharpe'] if p else float('nan'):>5.2f}  DD={m['max_dd_pct']:>5.1f}%")

    print("\n" + "=" * 92)
    print(" 1. MONTHLY RETURN CORRELATION")
    print("=" * 92)
    allm = pd.concat(mret_map, axis=1, sort=True).dropna()
    print(f"  overlapping months: {len(allm)}\n")
    print(allm.corr().round(2).to_string())

    print("\n" + "=" * 92)
    print(" 2-3. DIRECTIONAL CONCURRENCY (the risk correlation misses)")
    print("=" * 92)
    names = [n for n, _, _ in sleeves]
    P = np.vstack([pos_map[n] for n in names])
    active = (P != 0)
    n_active = active.sum(axis=0)
    in_mkt = n_active > 0
    print(f"  bars with >=1 sleeve in a position : {in_mkt.mean()*100:5.1f}%")
    for k in range(2, len(names) + 1):
        print(f"  bars with >={k} sleeves in a position: {(n_active >= k).mean()*100:5.1f}%")

    longs = (P == 1).sum(axis=0); shorts = (P == -1).sum(axis=0)
    both = (longs > 0) & (shorts > 0)
    stacked = (n_active >= 2) & ~both
    multi = n_active >= 2
    if multi.sum():
        print(f"\n  when >=2 sleeves are open ({multi.mean()*100:.1f}% of bars):")
        print(f"    SAME direction (risk stacks) : {stacked[multi].mean()*100:5.1f}%")
        print(f"    OPPOSITE (partially hedged)  : {both[multi].mean()*100:5.1f}%")

    aligned = np.maximum(longs, shorts)
    print(f"\n  max sleeves aligned on ONE side at once: {aligned.max()}")
    for k in range(2, int(aligned.max()) + 1):
        frac = (aligned >= k).mean() * 100
        # account risk if all k aligned sleeves hit SL together
        worst = sorted(risks.values(), reverse=True)[:k]
        print(f"    >={k} aligned: {frac:5.1f}% of bars   worst-case combined risk "
              f"if all stop out: {sum(worst):.2f}% of account")

    print("\n" + "=" * 92)
    print(" 4. COMBINED PORTFOLIO vs BEST SINGLE SLEEVE")
    print("=" * 92)
    port = allm.mean(axis=1)
    pp = perf(port)
    print(f"  equal-weight 5 sleeves : Sharpe={pp['sharpe']:5.2f}  CAGR={pp['cagr']:+7.2f}%  DD={pp['dd']:5.1f}%")
    for c in allm.columns:
        p = perf(allm[c])
        print(f"    {c:<13}        Sharpe={p['sharpe']:5.2f}  CAGR={p['cagr']:+7.2f}%  DD={p['dd']:5.1f}%")
    worst_m = allm.sum(axis=1).min(); worst_d = allm.sum(axis=1).idxmin()
    print(f"\n  worst month if ALL five hit at once: {worst_m:+.2f}% ({worst_d.strftime('%Y-%m')})")

    # the two new sleeves alone (the user's specific question)
    pair = allm[["lqsweep+KZ", "tpo+KZ"]] if set(["lqsweep+KZ","tpo+KZ"]).issubset(allm.columns) else None
    if pair is not None:
        pr = perf(pair.mean(axis=1))
        print(f"\n  the two NEW sleeves only (lqsweep+KZ & tpo+KZ):")
        print(f"    correlation           : {pair.corr().iloc[0,1]:.2f}")
        print(f"    combined              : Sharpe={pr['sharpe']:5.2f}  CAGR={pr['cagr']:+7.2f}%  DD={pr['dd']:5.1f}%")


if __name__ == "__main__":
    main()
