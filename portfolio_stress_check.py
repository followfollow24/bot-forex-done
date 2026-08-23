#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
portfolio_stress_check.py -- two sanity checks before trusting the Sharpe
3.00 / +83%/yr GOLD_LIVE(adx20+adx18)+BTC_aggr combined number:

  1) corr(BTC_cons, BTC_aggr) -- are these two BTC configs actually
     independent bets, or (like ADX20/ADX18 at corr 0.95) just two
     parameterizations of the same signal family?
  2) per-year breakdown of the combined portfolio, OOS 2022-2026 -- does
     Sharpe 3.00 hold up every year, or is it carried by one standout year
     (same failure mode already caught once with Combo LongBias/2022)?
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


def run_btc(adx, sl, tp):
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
print(" STRESS CHECK 1 -- corr(BTC_cons, BTC_aggr): same signal family or independent?")
print("=" * 92)

print(" running gold ADX20, ADX18 ...")
g20 = daily_frac(run_gold(20))
g18 = daily_frac(run_gold(18))
print(" running BTC conservative (ADX15/SL4/TP12) ...")
b_cons = daily_frac(run_btc(15, 4.0, 12.0))
print(" running BTC aggressive (ADX12/SL2.5/TP7.5) ...")
b_aggr = daily_frac(run_btc(12, 2.5, 7.5))

R = pd.DataFrame({"ADX20": g20, "ADX18": g18, "BTC_cons": b_cons, "BTC_aggr": b_aggr})
R_oos = R.loc["2022-01-01":]
idx = pd.date_range(R_oos.index.min(), R_oos.index.max(), freq="D")
R_oos = R_oos.reindex(idx).fillna(0.0)

corr_btc = R_oos["BTC_cons"].corr(R_oos["BTC_aggr"])
corr_gold = R_oos["ADX20"].corr(R_oos["ADX18"])
print(f"\n  corr(BTC_cons, BTC_aggr)  OOS 2022+ = {corr_btc:+.2f}")
print(f"  corr(ADX20, ADX18)        OOS 2022+ = {corr_gold:+.2f}  (reference, already known)")
if corr_btc > 0.6:
    print("  VERDICT: BTC_cons and BTC_aggr are the SAME signal family at different")
    print("  thresholds (like ADX20/ADX18) -- NOT two independent bets. True independent")
    print("  bet count in the 'GOLD_LIVE + BTC_aggr' number = 2 (gold-combined, btc), not 4.")
else:
    print("  VERDICT: BTC_cons/BTC_aggr show meaningfully lower correlation than ADX20/ADX18")
    print("  -- more independent than the gold pair, though still same base architecture.")

print("\n" + "=" * 92)
print(" STRESS CHECK 2 -- per-year breakdown, GOLD_LIVE(ADX20+ADX18) + BTC_aggr, OOS 2022-2026")
print("=" * 92)

gold_live = R_oos["ADX20"] + R_oos["ADX18"]
port = gold_live + R_oos["BTC_aggr"]

print(f"\n  {'year':<6} {'port %ret':>10} {'port Sharpe':>12} {'port MaxDD':>11} "
      f"{'gold_live %ret':>15} {'btc_aggr %ret':>14}")
print("  " + "-" * 72)
overall_mu, overall_sd, overall_sh, overall_mdd = metrics(port)
for yr in range(2022, 2027):
    yp = port[port.index.year == yr]
    yg = gold_live[gold_live.index.year == yr]
    yb = R_oos["BTC_aggr"][R_oos["BTC_aggr"].index.year == yr]
    if len(yp) < 20:
        print(f"  {yr:<6} (partial year, {len(yp)} days -- skip Sharpe)")
        continue
    ret = ((1 + yp).prod() - 1) * 100
    sh = yp.mean() / yp.std() * math.sqrt(BPY) if yp.std() > 0 else 0
    eq = (1 + yp).cumprod(); mdd = -(eq / eq.cummax() - 1).min() * 100
    gret = ((1 + yg).prod() - 1) * 100
    bret = ((1 + yb).prod() - 1) * 100
    print(f"  {yr:<6} {ret:>+9.1f}% {sh:>12.2f} {mdd:>10.1f}% {gret:>+14.1f}% {bret:>+13.1f}%")

print(f"\n  OVERALL (2022-2026, {len(port)} days): Sharpe {overall_sh:.2f}  "
      f"ann {((1+overall_mu)**BPY-1)*100:+.0f}%  MaxDD {overall_mdd*100:.1f}%")

# how many of the individual years actually clear Sharpe ~2+ on their own?
print("\n  VERDICT: check whether Sharpe is consistent across years (WF-style read) or")
print("  concentrated in 1-2 standout years (fragility warning, same pattern as the")
print("  Combo LongBias / 2022 bear-year dependency issue caught earlier).")
print("=" * 92)
