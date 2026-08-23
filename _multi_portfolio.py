#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
The legitimate way to raise returns: more independent edges, not a harder
search over one dataset.

Sharpe scales with sqrt(N) of *uncorrelated* bets. Gold H4 alone gives
~5-6.5%/yr at 0.3% risk. If the same H4 logic also works on BTC and ETH --
different markets, different drivers -- the combined portfolio can carry more
risk for the same drawdown, which is where the extra return legitimately comes
from.

What this does NOT do: search parameters until the number looks good. The H4
configs are frozen exactly as validated on gold (pullback adx18 SL3/TP7,
donchian ch100 SL3/TP7) and applied unchanged to BTC/ETH. If they only work on
the symbol they were found on, that shows up here as a failure, and that is the
useful answer.

Costs per symbol are the real ones already established in this repo:
  gold  spread $2.85 (live-measured), commission $3.50/lot
  BTC   spread $10   (Exness BTCUSDc), commission 0, pip_value 0.01
  ETH   spread $5    (proportionally scaled from BTC), commission 0
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import (DataLoader, prepare_data, BacktestEngine,
                             FastHybridTrendPullback, compute_metrics)
from _idea_search import DonchianBreakout, resample

START = 10_000.0

SYMBOLS = {
    "GOLD": dict(csv="download/xauusd-m15-bid-2013-01-01-2026-06-10.csv",
                 sym="XAUUSD", spread=2.85, comm=3.50, pip_size=None, pip_value=None),
    "BTC":  dict(csv="download/btcusdt-15m-binance-2017-08-17-2026-06-30.csv",
                 sym="BTCUSDc", spread=10.0, comm=0.0, pip_size=1.0, pip_value=0.01),
    "ETH":  dict(csv="download/ethusdt-15m-binance-2017-08-17-2026-06-30.csv",
                 sym="ETHUSDc", spread=5.0, comm=0.0, pip_size=1.0, pip_value=0.01),
}

STRATS = [
    ("pullback18", FastHybridTrendPullback, dict(adx_min=18)),
    ("donch100",   DonchianBreakout,        dict(CHANNEL=100)),
]


def make_cfg(risk, spec, max_hold=32):
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = risk
    c.partial_tp_atr = 999.0
    c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False
    c.max_hold_bars = max_hold
    if spec["pip_size"] is not None:
        c.pip_size[spec["sym"]] = spec["pip_size"]
        c.pip_value_usd_approx[spec["sym"]] = spec["pip_value"]
    return c


def run(cls, d, spec, risk, adx_min=None, **ov):
    s = cls()
    if adx_min is not None and hasattr(s, "ADX_MIN"):
        s.ADX_MIN = adx_min
    for k, v in ov.items():
        setattr(s, k, v)
    s.sl_atr, s.tp_atr = 3.0, 7.0
    s.trail_atr_mult = s.trail_activation_atr = 999.0
    s.precompute(d)
    eng = BacktestEngine(d, make_cfg(risk, spec), s, spread_price=spec["spread"],
                          commission_per_lot=spec["comm"], symbol=spec["sym"])
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START), eng.trades


def monthly(trades):
    rows = []
    for t in trades:
        if not t.get("exit_ts"):
            continue
        pnl = t["net_pnl"]
        # engine records equity AFTER the trade; equity before = after - pnl
        eq_before = t.get("equity_after", START) - pnl
        rows.append((pd.Timestamp(t["exit_ts"]), pnl, eq_before))
    if not rows:
        return pd.Series(dtype=float)
    df = pd.DataFrame(rows, columns=["ts", "pnl", "eqb"])
    # normalise to fixed notional so months are comparable and compounding
    # doesn't make later months dominate
    df["r"] = df["pnl"] / df["eqb"].replace(0, np.nan) * 100.0
    return df.set_index("ts")["r"].resample("ME").sum()


def summarize(mret, label, years=None):
    """mret = monthly % returns series (fixed-notional)."""
    if len(mret) < 12:
        print(f"  {label:<28} insufficient history ({len(mret)} months)")
        return None
    eq = (1 + mret / 100.0).cumprod()
    tot = (eq.iloc[-1] - 1) * 100
    yrs = years if years else len(mret) / 12.0
    cagr = ((eq.iloc[-1]) ** (1 / yrs) - 1) * 100
    dd = ((eq / eq.cummax()) - 1).min() * 100
    sharpe = (mret.mean() / mret.std() * np.sqrt(12)) if mret.std() > 0 else 0
    print(f"  {label:<28} months={len(mret):>3}  TotRet={tot:>+8.1f}%  "
          f"CAGR={cagr:>+6.2f}%/yr  MaxDD={dd:>6.1f}%  Sharpe={sharpe:>5.2f}")
    return dict(cagr=cagr, dd=abs(dd), sharpe=sharpe, mret=mret)


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START

    RISK = 0.30
    print(f"All individual runs at risk/trade = {RISK}%, H4 bars, params frozen from the gold validation\n")

    series = {}
    print("=" * 104)
    print(" INDIVIDUAL EDGES (H4, each symbol, real costs)")
    print("=" * 104)
    for symname, spec in SYMBOLS.items():
        try:
            df, _ = loader.load(spec["sym"], 99.0, c0, csv_path=spec["csv"], allow_synthetic=False)
        except Exception as e:
            print(f"  [{symname}] load failed: {e}")
            continue
        df_h4 = resample(df, "4h")
        d_h4 = prepare_data(df_h4)
        for sname, cls, kw in STRATS:
            m, tr = run(cls, d_h4, spec, RISK, **kw)
            key = f"{symname}-{sname}"
            if m and m.get("trades", 0) > 0:
                mr = monthly(tr)
                if len(mr) >= 12:
                    series[key] = mr
                print(f"  {key:<28} trades={m['trades']:>5}  PF={m['profit_factor']:>5.2f}  "
                      f"win%={m['win_rate']*100:>5.1f}  MaxDD={m['max_dd_pct']:>5.1f}%  "
                      f"TotRet={m['total_return_pct']:>+8.1f}%")
            else:
                print(f"  {key:<28} NO TRADES")

    if len(series) < 2:
        print("\nNot enough working edges to build a portfolio.")
        return

    print("\n" + "=" * 104)
    print(" CORRELATION MATRIX (monthly returns)")
    print("=" * 104)
    allm = pd.concat(series, axis=1).dropna(how="all")
    corr = allm.corr()
    print(corr.round(2).to_string())

    print("\n" + "=" * 104)
    print(" PORTFOLIO (equal weight across all working edges)")
    print("=" * 104)
    common = allm.dropna()
    print(f"  overlapping months across every edge: {len(common)}")
    if len(common) >= 12:
        port = common.mean(axis=1)
        base = summarize(port, "equal-weight portfolio")
        # what the same portfolio looks like scaled up to a target drawdown
        if base and base["dd"] > 0:
            for target_dd in [10.0, 20.0]:
                k = target_dd / base["dd"]
                scaled = port * k
                summarize(scaled, f"  scaled to ~{target_dd:.0f}% DD (x{k:.1f} risk)")

    print("\n  -- individual edges over the same overlapping window, for reference --")
    for c in common.columns:
        summarize(common[c], f"  {c}")

    # portfolio over the union window (uses each edge when it exists)
    print("\n" + "=" * 104)
    print(" PORTFOLIO (union window, equal weight over whichever edges are live)")
    print("=" * 104)
    union = allm.mean(axis=1, skipna=True).dropna()
    ub = summarize(union, "union-window portfolio")
    if ub and ub["dd"] > 0:
        for target_dd in [10.0, 20.0]:
            k = target_dd / ub["dd"]
            summarize(union * k, f"  scaled to ~{target_dd:.0f}% DD (x{k:.1f} risk)")


if __name__ == "__main__":
    main()
