#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
portfolio_path_03.py -- concrete path toward 0.3-0.5%/day using the TWO REAL
validated strategies combined (gold ADX20_TP7 live config + BTC HF ADX12/SL2.5/
TP7.5), both from the real SL/TP engine with real costs. Measures the combined
portfolio Sharpe, then solves for the risk-scale needed to hit 0.3 / 0.5 %/day
and the drawdown that scale implies. Includes an honest live-discount scenario.
ASCII-only.
"""
import os, math
import numpy as np, pandas as pd
import btc_walkforward as W
from forex_config import ForexConfig
from backtest_forex import DataLoader, prepare_data, BacktestEngine, FastHybridTrendPullback

DL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "download")
BPY = 365


def daily_frac(trades):
    """fractional daily returns (net_pnl / equity_before_trade), by exit date."""
    prev = 10000.0
    rows = []
    for t in trades:
        rows.append((pd.Timestamp(t["exit_ts"]).normalize(), t["net_pnl"] / prev if prev > 0 else 0))
        prev = t["equity_after"]
    s = pd.Series([r[1] for r in rows], index=[r[0] for r in rows])
    return s.groupby(s.index).sum()


def run_gold():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = 10000.0
    df, _ = loader.load("XAUUSD", 99.0, c0,
                        csv_path=os.path.join(DL, "xauusd-m15-bid-2013-01-01-2026-06-10.csv"),
                        allow_synthetic=False)
    d = prepare_data(df)
    s = FastHybridTrendPullback(); s.ADX_MIN = 20; s.precompute(d)
    s.sl_atr = 3.0; s.tp_atr = 7.0; s.trail_atr_mult = 999; s.trail_activation_atr = 999
    cfg = ForexConfig(); cfg.total_capital_usd = 10000.0; cfg.risk_per_trade_pct = 0.30
    cfg.partial_tp_atr = 999.0; cfg.partial_tp_frac = 0.0; cfg.move_sl_to_breakeven = False
    e = BacktestEngine(d, cfg, s, spread_price=0.28, commission_per_lot=3.50, symbol="XAUUSD")
    e.run(quiet=True, do_precompute=False)
    return e.trades


def run_btc():
    d, df = W.load_prepared(os.path.join(DL, "btcusdt-15m-binance-2017-08-17-2026-06-30.csv"), resample_h1=False)
    s = FastHybridTrendPullback(); s.ADX_MIN = 12; s.precompute(d)
    s.sl_atr = 2.5; s.tp_atr = 7.5; s.trail_atr_mult = 999; s.trail_activation_atr = 999
    e = W.BTCEngine(d, W.make_cfg(), s, spread_price=10.0, commission_per_lot=0.0, symbol="BTCUSDc")
    e.run(quiet=True, do_precompute=False)
    return e.trades


def metrics(daily):
    d = daily.dropna()
    mu = d.mean(); sd = d.std()
    sh = mu / sd * math.sqrt(BPY) if sd > 0 else 0
    eq = (1 + d).cumprod(); mdd = -(eq / eq.cummax() - 1).min()
    return mu, sd, sh, mdd


print("=" * 84)
print(" PATH TO 0.3-0.5%/day  --  gold(ADX20_TP7) + BTC-HF(ADX12/SL2.5/TP7.5), REAL engines")
print("=" * 84)
print(" running gold real engine ...")
g = daily_frac(run_gold())
print(" running BTC-HF real engine ...")
b = daily_frac(run_btc())

# align on OOS window (both have data), 2022+ = hardest OOS
for lbl, start in [("FULL overlap", None), ("OOS 2022+", "2022-01-01")]:
    R = pd.DataFrame({"GOLD": g, "BTC_HF": b})
    if start:
        R = R.loc[start:]
    idx = pd.date_range(R.index.min(), R.index.max(), freq="D")
    R = R.reindex(idx).fillna(0.0)
    port = R["GOLD"] + R["BTC_HF"]           # both at 0.30% risk, shared account
    mu, sd, sh, mdd = metrics(port)
    gmu, _, gsh, gmdd = metrics(R["GOLD"])
    bmu, _, bsh, bmdd = metrics(R["BTC_HF"])
    corr = R["GOLD"].corr(R["BTC_HF"])
    print(f"\n--- {lbl}  ({R.index.min().date()}..{R.index.max().date()}) ---")
    print(f"  GOLD   : {gmu*100:+.4f}%/day  Sharpe {gsh:.2f}  MaxDD {gmdd*100:.1f}%")
    print(f"  BTC_HF : {bmu*100:+.4f}%/day  Sharpe {bsh:.2f}  MaxDD {bmdd*100:.1f}%")
    print(f"  corr(GOLD,BTC_HF) = {corr:+.2f}")
    print(f"  COMBINED (both 0.30% risk): {mu*100:+.4f}%/day  ann {((1+mu)**BPY-1)*100:+.0f}%  "
          f"Sharpe {sh:.2f}  MaxDD {mdd*100:.1f}%")
    # scale to targets
    for tgt in (0.30, 0.50):
        k = (tgt/100.0) / mu if mu > 0 else float('inf')
        print(f"    -> to hit {tgt:.1f}%/day: scale risk x{k:.1f}  "
              f"(=> ~{0.30*k:.2f}% risk/trade each, est MaxDD ~{mdd*100*k:.0f}%)")
    # half-Kelly ceiling from Sharpe
    hk = math.exp(3*sh**2/8/BPY) - 1
    print(f"  half-Kelly ceiling at Sharpe {sh:.2f}: {hk*100:.3f}%/day  "
          f"(theoretical max growth, ~{ (math.exp(3*sh**2/8)-1)*100:.0f}%/yr, high DD)")

print("\n" + "=" * 84)
print(" HONEST LIVE-DISCOUNT: backtest OOS Sharpe overstates live. If live = 65% of OOS:")
R = pd.DataFrame({"GOLD": g, "BTC_HF": b}).loc["2022-01-01":]
idx = pd.date_range(R.index.min(), R.index.max(), freq="D"); R = R.reindex(idx).fillna(0.0)
port = R["GOLD"] + R["BTC_HF"]; mu, sd, sh, mdd = metrics(port)
sh_live = sh * 0.65
hk_live = math.exp(3*sh_live**2/8/BPY) - 1
print(f"  OOS Sharpe {sh:.2f} -> live ~{sh_live:.2f}; half-Kelly ~{hk_live*100:.3f}%/day")
print(f"  => 0.3-0.5%/day needs Sharpe ~1.7-2.2; with 2 edges live~{sh_live:.2f} we are "
      f"{'THERE' if sh_live>=1.7 else 'SHORT -> need a 3rd/4th uncorrelated edge'}.")
print("=" * 84)
