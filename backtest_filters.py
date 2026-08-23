#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest_filters.py — RSI / MACD entry filters on ADX20_TP7 M15
Tests 4 variants: BASE / RSI_only / MACD_only / RSI+MACD

Key question: do RSI & MACD improve signal quality or just reduce trades?
"""
from __future__ import annotations
import os, sys, time, math
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forex_config import ForexConfig
from backtest_forex import (DataLoader, prepare_data, BacktestEngine,
                             FastHybridTrendPullback, compute_metrics)
from forex_indicators import Signal
from walk_forward_regime import build_windows, gold_return_pct, regime_tag, window_metrics
from backtest_m5 import windows_pf, period_slice, _cfg

# ── Config ────────────────────────────────────────────────────────────────────
CSV15    = "download/xauusd-m15-bid-2013-01-01-2026-06-10.csv"
SPREAD   = 0.10
COMM     = 3.50
START    = 10_000.0
RISK_PCT = 0.30

ADX_MIN  = 20
SL_ATR   = 3.0
TP_ATR   = 7.0

WINDOW_MONTHS = 6
PF_THRESHOLD  = 1.05
WIN_THRESHOLD = 15

IS_FROM  = "2013-01-01"; IS_TO  = "2020-01-01"
VAL_FROM = "2020-01-01"; VAL_TO = "2022-01-01"
OOS_FROM = "2022-01-01"; OOS_TO = "2026-06-10"

RSI_PERIOD   = 14
MACD_FAST    = 12
MACD_SLOW    = 26
MACD_SIGNAL  = 9


# =============================================================================
# RSI / MACD helpers (vectorised, computed once)
# =============================================================================
def _ema_arr(prices: np.ndarray, span: int) -> np.ndarray:
    """Pandas-style EMA with adjust=False."""
    alpha = 2.0 / (span + 1)
    out   = np.empty_like(prices, dtype=float)
    out[0] = prices[0]
    for k in range(1, len(prices)):
        out[k] = alpha * prices[k] + (1.0 - alpha) * out[k - 1]
    return out


def compute_rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    """Wilder RSI — returns array same length as closes."""
    n    = len(closes)
    rsi  = np.full(n, np.nan)
    if n < period + 1:
        return rsi

    deltas = np.diff(closes)
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    # Wilder: seed = simple average of first period values
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])

    for k in range(period, n):
        g = gains[k - 1]
        l = losses[k - 1]
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
        if avg_loss == 0:
            rsi[k] = 100.0
        else:
            rsi[k] = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    return rsi


def compute_macd_hist(closes: np.ndarray,
                      fast: int = 12, slow: int = 26, sig: int = 9) -> np.ndarray:
    """MACD histogram (MACD_line - signal_line)."""
    ema_f    = _ema_arr(closes, fast)
    ema_s    = _ema_arr(closes, slow)
    macd_ln  = ema_f - ema_s
    sig_ln   = _ema_arr(macd_ln, sig)
    return macd_ln - sig_ln


# =============================================================================
# Filter strategy subclasses
# =============================================================================
class FilteredStrategy(FastHybridTrendPullback):
    """Base for filter variants — precomputes RSI and MACD arrays once."""

    use_rsi:  bool = False
    use_macd: bool = False

    # populated by precompute()
    _rsi_arr:  Optional[np.ndarray] = None
    _macd_arr: Optional[np.ndarray] = None

    # counter for filtered signals
    _n_base_signals: int = 0
    _n_filtered_out: int = 0

    def precompute(self, d: dict):
        super().precompute(d)
        closes = d["c"]
        if self.use_rsi:
            self._rsi_arr  = compute_rsi(closes, RSI_PERIOD)
        if self.use_macd:
            self._macd_arr = compute_macd_hist(closes, MACD_FAST, MACD_SLOW, MACD_SIGNAL)
        self._n_base_signals = 0
        self._n_filtered_out = 0

    def signal(self, d: dict, i: int) -> Signal:
        base = super().signal(d, i)
        if base.action == "HOLD":
            return base   # no signal from base — nothing to filter

        self._n_base_signals += 1
        action = base.action   # "BUY" or "SELL"

        # ── RSI filter ───────────────────────────────────────────────────────
        if self.use_rsi and self._rsi_arr is not None:
            rsi_val = self._rsi_arr[i]
            if np.isnan(rsi_val):
                self._n_filtered_out += 1
                return Signal()
            if action == "BUY"  and rsi_val <= 50:
                self._n_filtered_out += 1
                return Signal()
            if action == "SELL" and rsi_val >= 50:
                self._n_filtered_out += 1
                return Signal()

        # ── MACD histogram filter ────────────────────────────────────────────
        if self.use_macd and self._macd_arr is not None:
            hist_val = self._macd_arr[i]
            if np.isnan(hist_val):
                self._n_filtered_out += 1
                return Signal()
            if action == "BUY"  and hist_val <= 0:
                self._n_filtered_out += 1
                return Signal()
            if action == "SELL" and hist_val >= 0:
                self._n_filtered_out += 1
                return Signal()

        return base


def make_strat(use_rsi: bool, use_macd: bool) -> FilteredStrategy:
    s = FilteredStrategy()
    s.use_rsi              = use_rsi
    s.use_macd             = use_macd
    s.ADX_MIN              = ADX_MIN
    s.sl_atr               = SL_ATR
    s.tp_atr               = TP_ATR
    s.trail_atr_mult       = 999.0
    s.trail_activation_atr = 999.0
    return s


# =============================================================================
# Runner
# =============================================================================
def run_full(d, strat):
    strat.precompute(d)
    eng = BacktestEngine(d, _cfg(), strat, spread_price=SPREAD,
                         commission_per_lot=COMM, symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)
    ov  = compute_metrics(eng.trades, eng.equity_curve, START)
    return eng.trades, ov, strat._n_base_signals, strat._n_filtered_out


def load_m15(csv_path):
    loader = DataLoader(log_fn=lambda *a, **k: None)
    cfg0   = ForexConfig(); cfg0.total_capital_usd = START
    df, _  = loader.load("XAUUSD", 99.0, cfg0, csv_path=csv_path, allow_synthetic=True)
    return df


# =============================================================================
# main
# =============================================================================
def main():
    t0  = time.time()
    W   = 102
    DIR = os.path.dirname(os.path.abspath(__file__))
    csv = os.path.join(DIR, CSV15)

    print()
    print("=" * W)
    print(f"  ADX20_TP7 M15  —  RSI/MACD entry filter test")
    print(f"  RSI({RSI_PERIOD}) threshold=50  |  MACD({MACD_FAST},{MACD_SLOW},{MACD_SIGNAL}) histogram>0/<0")
    print("=" * W)

    # ── Load M15 ─────────────────────────────────────────────────────────────
    print(f"\n  Loading M15 ...", flush=True)
    t1 = time.time()
    df = load_m15(csv)
    d  = prepare_data(df)
    print(f"  {len(df):,} bars  {df['timestamp'].iloc[0].date()} → {df['timestamp'].iloc[-1].date()}  ({time.time()-t1:.1f}s)")

    windows_list = build_windows(df["timestamp"].iloc[0], df["timestamp"].iloc[-1], WINDOW_MONTHS)
    win_info = [dict(start=ws, end=we,
                     gold_ret=gold_return_pct(df, ws, we),
                     regime=regime_tag(gold_return_pct(df, ws, we)))
                for ws, we in windows_list]
    print(f"  Walk-forward: {len(windows_list)} windows × {WINDOW_MONTHS}mo\n")

    # ── Run 4 variants ────────────────────────────────────────────────────────
    VARIANTS = [
        ("BASE",      False, False),
        ("RSI_only",  True,  False),
        ("MACD_only", False, True),
        ("RSI+MACD",  True,  True),
    ]

    results = {}
    for name, use_rsi, use_macd in VARIANTS:
        strat = make_strat(use_rsi, use_macd)
        print(f"  [{name:<10}] running ...", end="", flush=True)
        t_ = time.time()
        trades, ov, n_base, n_filt = run_full(d, strat)
        win_ok, n_all = windows_pf(trades, win_info)
        results[name] = dict(trades=trades, ov=ov, win_ok=win_ok, n_all=n_all,
                              n_base=n_base, n_filt=n_filt)
        pct = n_filt / n_base * 100 if n_base else 0
        print(f"  {len(trades):,} trades  filtered {n_filt}/{n_base} ({pct:.0f}%)  ({time.time()-t_:.1f}s)")

    # ── Summary table ─────────────────────────────────────────────────────────
    base_oos = period_slice(results["BASE"]["trades"], OOS_FROM, OOS_TO)
    base_cal = base_oos["calmar"] if base_oos else 0

    print()
    print("=" * W)
    print("  FULL-PERIOD SUMMARY  (2013–2026)")
    print(f"  {'Variant':<12} {'PF':>6} {'Calmar':>8} {'Win/27':>7} {'Trades':>7} "
          f"{'OOS Cal':>9} {'vs BASE':>8}  {'Filtered':>12}  {'%filt':>6}")
    print("  " + "-" * (W - 2))

    for name, *_ in VARIANTS:
        r = results[name]
        trades, ov, win_ok, n_all = r["trades"], r["ov"], r["win_ok"], r["n_all"]
        n_base, n_filt = r["n_base"], r["n_filt"]
        ret  = (ov.get("final_equity", START) - START) / START * 100
        dd   = ov.get("max_dd_pct", 0) or 0.001
        cal  = ret / dd
        pf   = ov.get("profit_factor", 0) or 0
        m_oos = period_slice(trades, OOS_FROM, OOS_TO)
        oos_cal = m_oos["calmar"] if m_oos else 0
        vs_base = oos_cal - base_cal
        pct_filt = n_filt / n_base * 100 if n_base else 0
        vs_str  = f"{vs_base:+.1f}" if name != "BASE" else "—"
        icon    = ("✅" if vs_base > 2 else "🟡" if vs_base > -2 else "❌") if name != "BASE" else " "
        print(f"  {name:<12} {pf:>6.3f} {cal:>8.1f} {win_ok:>4}/{n_all:<3} "
              f"{len(trades):>7,} {oos_cal:>9.1f} {vs_str:>8}  "
              f"{n_filt:>5}/{n_base:<5} {pct_filt:>5.0f}%  {icon}")

    # ── Period breakdown ──────────────────────────────────────────────────────
    PERIODS = [
        ("IS  2013–2019", IS_FROM,  IS_TO),
        ("VAL 2020–2021", VAL_FROM, VAL_TO),
        ("OOS 2022–2026", OOS_FROM, OOS_TO),
    ]
    print()
    print("=" * W)
    print("  PERIOD BREAKDOWN")
    for plabel, pfrom, pto in PERIODS:
        print(f"\n  [{plabel}]")
        print(f"  {'Variant':<12} {'Trades':>7} {'PF':>6} {'Calmar':>8} {'MaxDD%':>7} "
              f"{'Win%':>6} {'Ret%':>8}")
        print("  " + "-" * 60)
        for name, *_ in VARIANTS:
            m = period_slice(results[name]["trades"], pfrom, pto)
            if not m:
                print(f"  {name:<12}  —"); continue
            flag = ""
            if plabel.startswith("OOS"):
                flag = " ✅" if m["calmar"] >= 20 else (" 🟡" if m["calmar"] >= 10 else " ❌")
            print(f"  {name:<12} {m['n']:>7,} {m['pf']:>6.3f} {m['calmar']:>8.1f} "
                  f"{m['dd']:>6.1f}% {m['win_pct']:>5.1f}% {m['ret']:>+7.0f}%{flag}")

    # ── Year-by-year for 2014 & 2018 ─────────────────────────────────────────
    print()
    print("=" * W)
    print("  YEAR DETAIL: 2014, 2018 (stress test years)")
    from backtest_m10 import year_slice
    for yr in [2014, 2018]:
        print(f"\n  [{yr}]  MaxDD% / Ret% / Trades:")
        for name, *_ in VARIANTS:
            m = year_slice(results[name]["trades"], yr)
            if not m:
                print(f"  {name:<12}  —"); continue
            safe = "✅" if m["dd"] < 15 else ("🟡" if m["dd"] < 20 else "⚠️ ")
            print(f"  {name:<12}  DD={m['dd']:5.1f}%  ret={m['ret']:+6.0f}%  "
                  f"n={m['n']:4d}  {safe}")

    # ── Diagnose: filter quality ──────────────────────────────────────────────
    print()
    print("=" * W)
    print("  FILTER QUALITY ANALYSIS")
    print(f"\n  Base signals generated: {results['BASE']['n_base']:,}")
    print(f"  (signals are entry attempts after H1 trend + EMA20 pullback confirmed)\n")
    for name, *_ in VARIANTS[1:]:
        r = results[name]
        n_base, n_filt = r["n_base"], r["n_filt"]
        n_pass = n_base - n_filt
        trades_base  = len(results["BASE"]["trades"])
        trades_this  = len(r["trades"])
        kept_ratio   = trades_this / trades_base * 100 if trades_base else 0
        oos_b = period_slice(results["BASE"]["trades"],  OOS_FROM, OOS_TO)
        oos_f = period_slice(r["trades"],                OOS_FROM, OOS_TO)
        pf_base = oos_b["pf"] if oos_b else 0
        pf_filt = oos_f["pf"] if oos_f else 0
        pf_delta = pf_filt - pf_base
        pct_filt = n_filt / n_base * 100 if n_base else 0
        verdict  = "improves quality" if pf_delta > 0.02 else ("neutral" if pf_delta > -0.02 else "hurts quality")
        print(f"  {name:<12}  filtered {pct_filt:.0f}% of signals  "
              f"→ {kept_ratio:.0f}% of base trades kept  "
              f"OOS PF: {pf_base:.3f}→{pf_filt:.3f} (Δ{pf_delta:+.3f})  [{verdict}]")

    # ── Final verdict ─────────────────────────────────────────────────────────
    print()
    print("=" * W)
    print("  VERDICT")
    oos_vals = {name: period_slice(results[name]["trades"], OOS_FROM, OOS_TO)
                for name, *_ in VARIANTS}
    best = max(oos_vals, key=lambda n: oos_vals[n]["calmar"] if oos_vals[n] else 0)
    best_cal = oos_vals[best]["calmar"] if oos_vals[best] else 0
    print(f"\n  Best OOS Calmar: {best} = {best_cal:.1f}")
    base_oos_cal = oos_vals["BASE"]["calmar"] if oos_vals["BASE"] else 0
    for name, *_ in VARIANTS[1:]:
        m = oos_vals[name]
        cal = m["calmar"] if m else 0
        delta = cal - base_oos_cal
        if delta > 2:
            print(f"  {name}: OOS Calmar {cal:.1f} vs BASE {base_oos_cal:.1f}  (+{delta:.1f})  → WORTH ADDING ✅")
        elif delta > -2:
            print(f"  {name}: OOS Calmar {cal:.1f} vs BASE {base_oos_cal:.1f}  ({delta:+.1f})  → NEUTRAL (trade-count reduction only)")
        else:
            print(f"  {name}: OOS Calmar {cal:.1f} vs BASE {base_oos_cal:.1f}  ({delta:+.1f})  → HURTS ❌ (filters good trades)")

    print(f"\n  Total elapsed: {time.time()-t0:.1f}s")
    print("=" * W)


if __name__ == "__main__":
    main()
