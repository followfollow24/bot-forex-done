#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
portfolio_real_live_setup.py -- combine the backtest exactly matching what is
ACTUALLY running live: both gold bots (adx20tp7 + adx18tp7, run concurrently,
each at 0.30% risk/trade -- NOT deduped) plus BTC-HF (backtest only, not yet
live). This differs from portfolio_path_03.py, which only used ONE gold sleeve
(ADX20_TP7) as a simplification -- but the real account runs BOTH bots at once,
so real gold exposure is the SUM of both, and they are known near-duplicate
signals (see project_real_cent_account_start.md), not truly diversified.

All real engines (FastHybridTrendPullback + real BacktestEngine), same costs
as live: spread=0.28 gold / $10 BTC, commission $3.50/lot gold / $0 BTC,
risk=0.30%/trade each bot independently. OOS 2022+ is the honest window.
ASCII-only.
"""
import os, sys, math
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import DataLoader, prepare_data, BacktestEngine, FastHybridTrendPullback
import btc_walkforward as W

DL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "download")
BPY = 365
START = 10_000.0


def daily_frac(trades):
    """fractional daily returns (net_pnl / equity_before_trade), by exit date."""
    prev = START
    rows = []
    for t in trades:
        rows.append((pd.Timestamp(t["exit_ts"]).normalize(),
                      t["net_pnl"] / prev if prev > 0 else 0))
        prev = t["equity_after"]
    s = pd.Series([r[1] for r in rows], index=[r[0] for r in rows])
    return s.groupby(s.index).sum()


def run_gold(adx_min):
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START
    df, _ = loader.load("XAUUSD", 99.0, c0,
                        csv_path=os.path.join(DL, "xauusd-m15-bid-2013-01-01-2026-06-10.csv"),
                        allow_synthetic=False)
    d = prepare_data(df)
    s = FastHybridTrendPullback(); s.ADX_MIN = adx_min; s.precompute(d)
    s.sl_atr = 3.0; s.tp_atr = 7.0; s.trail_atr_mult = 999; s.trail_activation_atr = 999
    cfg = ForexConfig(); cfg.total_capital_usd = START; cfg.risk_per_trade_pct = 0.30
    cfg.partial_tp_atr = 999.0; cfg.partial_tp_frac = 0.0; cfg.move_sl_to_breakeven = False
    e = BacktestEngine(d, cfg, s, spread_price=0.28, commission_per_lot=3.50, symbol="XAUUSD")
    e.run(quiet=True, do_precompute=False)
    return e.trades


def run_btc(adx, sl, tp, label):
    d, df = W.load_prepared(os.path.join(DL, "btcusdt-15m-binance-2017-08-17-2026-06-30.csv"),
                             resample_h1=False)
    s = FastHybridTrendPullback(); s.ADX_MIN = adx; s.precompute(d)
    s.sl_atr = sl; s.tp_atr = tp; s.trail_atr_mult = 999; s.trail_activation_atr = 999
    e = W.BTCEngine(d, W.make_cfg(), s, spread_price=10.0, commission_per_lot=0.0, symbol="BTCUSDc")
    e.run(quiet=True, do_precompute=False)
    return e.trades


def metrics(daily):
    d = daily.dropna()
    mu = d.mean(); sd = d.std()
    sh = mu / sd * math.sqrt(BPY) if sd > 0 else 0
    eq = (1 + d).cumprod(); mdd = -(eq / eq.cummax() - 1).min()
    return mu, sd, sh, mdd


print("=" * 92)
print(" REAL LIVE SETUP -- adx20tp7 + adx18tp7 (BOTH running now, undeduped) + BTC-HF (backtest)")
print("=" * 92)

print(" running adx20tp7 (ADX_MIN=20) real engine ...")
g20 = daily_frac(run_gold(20))
print(" running adx18tp7 (ADX_MIN=18) real engine ...")
g18 = daily_frac(run_gold(18))
print(" running BTC-HF conservative (ADX15/SL4/TP12) real engine ...")
b_cons = daily_frac(run_btc(15, 4.0, 12.0, "cons"))
print(" running BTC-HF aggressive (ADX12/SL2.5/TP7.5) real engine ...")
b_aggr = daily_frac(run_btc(12, 2.5, 7.5, "aggr"))

for lbl, start in [("FULL overlap", None), ("OOS 2022+", "2022-01-01")]:
    R = pd.DataFrame({"ADX20": g20, "ADX18": g18, "BTC_cons": b_cons, "BTC_aggr": b_aggr})
    if start:
        R = R.loc[start:]
    idx = pd.date_range(R.index.min(), R.index.max(), freq="D")
    R = R.reindex(idx).fillna(0.0)

    gold_live = R["ADX20"] + R["ADX18"]   # BOTH bots run concurrently, undeduped -- matches real account

    print(f"\n--- {lbl}  ({R.index.min().date()}..{R.index.max().date()}) ---")
    print("  individual sleeves:")
    for nm in ["ADX20", "ADX18", "BTC_cons", "BTC_aggr"]:
        mu, sd, sh, mdd = metrics(R[nm])
        print(f"    {nm:<10}: {mu*100:+.4f}%/day  Sharpe {sh:5.2f}  MaxDD {mdd*100:5.1f}%")

    corr_gg = R["ADX20"].corr(R["ADX18"])
    print(f"\n  corr(ADX20,ADX18) = {corr_gg:+.2f}  <- near-duplicate signal warning check")

    mu_gl, sd_gl, sh_gl, mdd_gl = metrics(gold_live)
    print(f"\n  GOLD_LIVE (ADX20+ADX18 summed, as actually run): {mu_gl*100:+.4f}%/day  "
          f"Sharpe {sh_gl:.2f}  MaxDD {mdd_gl*100:.1f}%")

    for btc_lbl, btc_series in [("BTC_cons (ADX15/SL4/TP12)", b_cons if not start else R["BTC_cons"]),
                                  ("BTC_aggr (ADX12/SL2.5/TP7.5)", b_aggr if not start else R["BTC_aggr"])]:
        btc_s = R[btc_lbl.split()[0]]
        corr = gold_live.corr(btc_s)
        port = gold_live + btc_s
        mu, sd, sh, mdd = metrics(port)
        print(f"\n  + {btc_lbl}:")
        print(f"    corr(GOLD_LIVE,{btc_lbl.split()[0]}) = {corr:+.2f}")
        print(f"    COMBINED (GOLD_LIVE + {btc_lbl.split()[0]}, real equity curve): "
              f"{mu*100:+.4f}%/day  ann {((1+mu)**BPY-1)*100:+.0f}%  Sharpe {sh:.2f}  MaxDD {mdd*100:.1f}%")

print("\n" + "=" * 92)
print(" NOTE: GOLD_LIVE = ADX20 + ADX18 summed (both real bots run concurrently on the")
print(" SAME account right now, each independently risking 0.30%/trade -- this is 2x gold")
print(" exposure on near-duplicate signals, NOT diversification; see project_real_cent_account_start.md")
print("=" * 92)
