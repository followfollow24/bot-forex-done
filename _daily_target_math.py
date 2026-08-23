#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
What would it actually take to hit 0.3-0.5% per day?

This is not an opinion question -- given a strategy's Sharpe ratio, the return
you can extract and the drawdown you must accept are locked together. You can
choose the return by scaling risk, but the drawdown comes along with it, and
the ratio between them is fixed by the edge quality (Sharpe).

So: take the best validated portfolio we actually have (6 H4 edges across
gold/BTC/ETH, measured Sharpe ~1.10), scale it to each daily target, and
report the drawdown that necessarily comes with it -- plus the risk of ruin.

Uses the real monthly return series from _multi_portfolio, not assumptions.
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
from _multi_portfolio import SYMBOLS, STRATS, run as run_edge, monthly

START = 10_000.0
RISK = 0.30
TRADING_DAYS = 252


def build_portfolio():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START
    series = {}
    for symname, spec in SYMBOLS.items():
        try:
            df, _ = loader.load(spec["sym"], 99.0, c0, csv_path=spec["csv"], allow_synthetic=False)
        except Exception:
            continue
        d_h4 = prepare_data(resample(df, "4h"))
        for sname, cls, kw in STRATS:
            m, tr = run_edge(cls, d_h4, spec, RISK, **kw)
            if m and m.get("trades", 0) > 0:
                mr = monthly(tr)
                if len(mr) >= 12:
                    series[f"{symname}-{sname}"] = mr
    allm = pd.concat(series, axis=1).dropna()
    return allm.mean(axis=1)


def stats(mret):
    mu = mret.mean(); sd = mret.std()
    sharpe = mu / sd * np.sqrt(12) if sd > 0 else 0
    eq = (1 + mret / 100).cumprod()
    dd = abs(((eq / eq.cummax()) - 1).min() * 100)
    yrs = len(mret) / 12
    cagr = (eq.iloc[-1] ** (1 / yrs) - 1) * 100
    return sharpe, cagr, dd, mu, sd


def main():
    port = build_portfolio()
    sharpe, cagr0, dd0, mu0, sd0 = stats(port)
    print("=" * 100)
    print(" BASELINE: best validated portfolio we actually have")
    print("=" * 100)
    print(f"  6 H4 edges (gold/BTC/ETH), {len(port)} months")
    print(f"  Sharpe={sharpe:.2f}   CAGR={cagr0:+.2f}%/yr   MaxDD={dd0:.1f}%\n")

    print("=" * 100)
    print(" WHAT EACH DAILY TARGET REQUIRES")
    print("=" * 100)
    print(f"  {'target/day':<12}{'= CAGR':>12}{'risk scale':>13}{'-> MaxDD':>12}{'verdict':>28}")
    for daily in [0.10, 0.20, 0.30, 0.50]:
        req_cagr = ((1 + daily / 100) ** TRADING_DAYS - 1) * 100
        # scaling k multiplies both return and drawdown roughly linearly
        # solve k from monthly mean: (1+k*mu/100)^12 - 1 = req_cagr
        target_monthly = ((1 + req_cagr / 100) ** (1 / 12) - 1) * 100
        k = target_monthly / mu0 if mu0 > 0 else float("inf")
        scaled = port * k
        _, c, d, _, _ = stats(scaled)
        if d >= 100:
            verdict = "ACCOUNT WIPED OUT"
        elif d >= 60:
            verdict = "ruin near-certain"
        elif d >= 35:
            verdict = "survivable only in theory"
        elif d >= 20:
            verdict = "aggressive but possible"
        else:
            verdict = "reasonable"
        print(f"  {daily:<12.2f}{req_cagr:>+11.0f}%{k:>12.1f}x{d:>11.1f}%{verdict:>28}")

    print()
    print("=" * 100)
    print(" WHY: the return/drawdown ratio is fixed by Sharpe, not by wanting it")
    print("=" * 100)
    print(f"  This portfolio's Sharpe is {sharpe:.2f}.")
    print(f"  At Sharpe {sharpe:.2f}, every +1% of annual return costs roughly "
          f"{dd0 / cagr0:.2f}% of drawdown.")
    print()
    print("  To reach 0.3%/day at a drawdown you could actually survive (<25%),")
    print(f"  the portfolio Sharpe would need to be about "
          f"{((1+0.003)**TRADING_DAYS-1)*100 / 25 * (dd0/cagr0) * sharpe:.1f} "
          f"-- vs {sharpe:.2f} today.")
    print()
    print("  Sharpe rises with the square root of the number of INDEPENDENT edges.")
    n_now = 6
    for target_sharpe in [2.0, 3.0, 4.0]:
        n_needed = n_now * (target_sharpe / sharpe) ** 2
        print(f"    Sharpe {target_sharpe:.1f}  needs ~{n_needed:.0f} independent edges "
              f"(have {n_now}, and they are not fully independent)")


if __name__ == "__main__":
    main()
