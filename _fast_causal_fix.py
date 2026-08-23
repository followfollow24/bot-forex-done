#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fast, vectorized, TRUE-causal replacement for _build_h1_trend_array's bucket
expansion.

The bug: _build_h1_trend_array computes per-H1-bucket trend values from
h1_c[k] = d["c"][k*H1_BARS + H1_BARS - 1] (bucket k's own last bar), which is
correct -- but then expands with `out[i] = h1_trend[k]` where k = i // H1_BARS.
For i inside bucket k but BEFORE that bucket's last bar has actually occurred
(e.g. i = k*H1_BARS, the FIRST bar of the bucket), this assigns a value that
depends on bars 1-3 steps in i's future. Confirmed empirically: 133/2150 bars
(6.2%) get a different trend sign than the causal _h1_trend(d, i) fallback,
and signal counts differ by ~9% on a BTC M15 sample.

The fix needs no per-bar recomputation (which is what made the honest O(n)
causal check too slow to run over real history). Bucket k is only KNOWN once
its own last bar (index k*H1_BARS + H1_BARS - 1) has occurred. So the causal
value at bar i is whichever bucket most recently COMPLETED at or before i:
    k_known(i) = (i - (H1_BARS - 1)) // H1_BARS   (bars before the first
                                                     completed bucket -> -1)
This is a pure index remap of the SAME already-computed h1_trend per-bucket
array -- O(n), same cost as the original (buggy) expansion.

Verifies against the slow, definitely-correct causal loop on a small sample,
then re-validates the fresh-filter configs at full speed using the corrected
array.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import DataLoader, prepare_data, BacktestEngine, compute_metrics
from forex_hybrid_strategy import HybridTrendPullback, FreshTrendFilterMixin
from gold_regime_live_strategy import RegimeFilteredHybridLive
from _idea_search import resample
from _all_paths import to_monthly, perf, START

GOLD_M15 = "download/xauusd-m15-bid-2013-01-01-2026-06-10.csv"
BTC_CSV  = "download/btcusdt-15m-binance-2017-08-17-2026-06-30.csv"
ETH_CSV  = "download/ethusdt-15m-binance-2017-08-17-2026-06-30.csv"


def build_h1_trend_array_causal(strat, d: dict) -> np.ndarray:
    """Same per-bucket computation as _build_h1_trend_array, but expands with
    the LAST COMPLETED bucket at each bar instead of the bucket i falls inside
    (which may not be complete yet). Works for both HybridTrendPullback and
    RegimeFilteredHybridLive since it only touches the final expansion loop --
    everything before it is copy-pasted from whichever class's own
    _build_h1_trend_array to keep the per-bucket trend values byte-identical."""
    # Reuse the class's own per-bucket logic by calling its method, then only
    # fix the expansion. We need the per-bucket h1_trend array, which
    # _build_h1_trend_array doesn't expose directly -- so recompute the
    # bucket-level part here using the same formulas (mirrors either class).
    H1_BARS = strat.H1_BARS
    n = len(d["c"])
    n_h1 = n // H1_BARS
    out = np.zeros(n, dtype=np.int8)
    if n_h1 < strat.EMA_H1_SLOW + 5:
        return out

    idx = np.arange(n_h1) * H1_BARS
    h1_c = np.array([d["c"][j + H1_BARS - 1] for j in idx])
    h1_h = np.array([d["h"][j:j + H1_BARS].max() for j in idx])
    h1_l = np.array([d["l"][j:j + H1_BARS].min() for j in idx])

    ema_f = strat._ema(h1_c, strat.EMA_H1_FAST)
    ema_s = strat._ema(h1_c, strat.EMA_H1_SLOW)
    adx_a = strat._adx_array(h1_h, h1_l, h1_c, strat.ADX_PERIOD)

    is_regime = isinstance(strat, RegimeFilteredHybridLive)
    h1_trend = np.zeros(n_h1, dtype=np.int8)
    if is_regime:
        import math
        from gold_regime_live_strategy import REGIME_ADX_MIN, REGIME_GAP_MULT
        prev_c = np.empty(n_h1); prev_c[0] = h1_c[0]; prev_c[1:] = h1_c[:-1]
        tr = np.maximum(h1_h - h1_l, np.maximum(np.abs(h1_h - prev_c), np.abs(h1_l - prev_c)))
        atr_h1 = strat._wilder_smooth(tr, strat.ADX_PERIOD)
        adx_rising = adx_a > np.roll(adx_a, 1); adx_rising[0] = False
        gap_ok = np.abs(ema_f - ema_s) > (REGIME_GAP_MULT * atr_h1)
        for k in range(n_h1):
            ef, es, adx = ema_f[k], ema_s[k], adx_a[k]
            if math.isnan(ef) or math.isnan(es) or math.isnan(adx):
                continue
            if adx < REGIME_ADX_MIN or not adx_rising[k] or not gap_ok[k]:
                continue
            c = h1_c[k]
            if c > ef > es: h1_trend[k] = 1
            elif c < ef < es: h1_trend[k] = -1
    else:
        for k in range(n_h1):
            ef, es, adx = ema_f[k], ema_s[k], adx_a[k]
            if np.isnan(ef) or np.isnan(es):
                continue
            if adx < strat.ADX_MIN:
                continue
            c = h1_c[k]
            if c > ef > es: h1_trend[k] = 1
            elif c < ef < es: h1_trend[k] = -1

    # ---- THE FIX: causal expansion ----
    # bucket k completes at bar (k*H1_BARS + H1_BARS - 1). bar i's causal
    # trend = the last bucket that has already completed at or before i.
    k_known = (np.arange(n) - (H1_BARS - 1)) // H1_BARS   # int floor division
    valid = k_known >= 0
    k_known = np.clip(k_known, 0, n_h1 - 1)
    out[valid] = h1_trend[k_known[valid]]
    return out


def verify_matches_slow_causal():
    """Sanity check against the definitely-correct (but slow) per-bar loop,
    on a small sample."""
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START
    dfb, _ = loader.load("BTCUSDc", 99.0, c0, csv_path=BTC_CSV, allow_synthetic=False)
    d = prepare_data(dfb.tail(1500).reset_index(drop=True))

    s = HybridTrendPullback()
    s.ADX_MIN = 18
    fast_causal = build_h1_trend_array_causal(s, d)

    slow_causal = np.array([s._h1_trend(d, i) for i in range(s.MIN_BARS, len(d["c"]))])
    fast_slice = fast_causal[s.MIN_BARS:len(d["c"])]
    mismatches = (slow_causal != fast_slice).sum()
    print(f"[verify] fast-causal vs slow-causal mismatches: {mismatches}/{len(slow_causal)}")
    return mismatches == 0


class FixedTrendMixin:
    """Drop-in replacement: uses the fast causal-correct expansion instead of
    the buggy one, without touching the original classes."""
    def precompute(self, d):
        self._h1_trend_arr = build_h1_trend_array_causal(self, d)


class FixedPullback(FixedTrendMixin, FreshTrendFilterMixin, HybridTrendPullback):
    pass


class FixedRegime(FixedTrendMixin, FreshTrendFilterMixin, RegimeFilteredHybridLive):
    pass


class FixedPullbackNoFilter(FixedTrendMixin, HybridTrendPullback):
    pass


class FixedRegimeNoFilter(FixedTrendMixin, RegimeFilteredHybridLive):
    pass


def cfg(sym, ps=None, pv=None, hold=64, risk=0.30):
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = risk
    c.partial_tp_atr = 999.0
    c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False
    c.max_hold_bars = hold
    if ps is not None:
        c.pip_size[sym] = ps
        c.pip_value_usd_approx[sym] = pv
    return c


def run(cls, d, sym, spread, adx, comm=3.5, ps=None, pv=None, maxmat=None, risk=0.30):
    s = cls()
    s.ADX_MIN = adx
    if maxmat is not None and hasattr(s, "MAX_MATURITY"):
        s.MAX_MATURITY = maxmat
    s.sl_atr, s.tp_atr = 3.0, 999.0
    s.trail_atr_mult = s.trail_activation_atr = 999.0
    s.precompute(d)
    eng = BacktestEngine(d, cfg(sym, ps, pv, risk=risk), s, spread_price=spread,
                          commission_per_lot=comm, symbol=sym)
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START), eng.trades


def line(m, tr, label, yrs):
    if not m or m.get("trades", 0) < 15:
        print(f"    {label:<30} n={m.get('trades',0) if m else 0:>5}  too few")
        return
    p = perf(to_monthly(tr))
    sh = p["sharpe"] if p else float("nan")
    tot = m["total_return_pct"]
    cg = -100.0 if tot <= -100 else ((1+tot/100)**(1/yrs)-1)*100
    print(f"    {label:<30} n={m['trades']:>5}  PF={m['profit_factor']:>5.2f}  "
          f"Sharpe={sh:>5.2f}  CAGR={cg:>+7.2f}%  DD={m['max_dd_pct']:>5.1f}%")


def main():
    ok = verify_matches_slow_causal()
    print(f"[verify] {'PASSED -- fast causal array is correct' if ok else 'FAILED -- do not trust results below'}\n")
    if not ok:
        return

    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START

    dfg, _ = loader.load("XAUUSD", 99.0, c0, csv_path=GOLD_M15, allow_synthetic=True)
    dfg_h1 = resample(dfg, "1h")
    yg = (dfg_h1["timestamp"].iloc[-1] - dfg_h1["timestamp"].iloc[0]).days / 365.25
    dg = prepare_data(dfg_h1)

    dfb, _ = loader.load("BTCUSDc", 99.0, c0, csv_path=BTC_CSV, allow_synthetic=False)
    yb = (dfb["timestamp"].iloc[-1] - dfb["timestamp"].iloc[0]).days / 365.25
    db = prepare_data(dfb)

    dfe, _ = loader.load("ETHUSDc", 99.0, c0, csv_path=ETH_CSV, allow_synthetic=False)
    ye = (dfe["timestamp"].iloc[-1] - dfe["timestamp"].iloc[0]).days / 365.25
    de = prepare_data(dfe)

    print("=" * 100)
    print(" FULL HISTORY, TRUE CAUSAL (no look-ahead), real costs")
    print("=" * 100)

    print(f"\n  GOLD H1 regime22, risk=0.30% ({yg:.1f}y)")
    m, tr = run(FixedRegimeNoFilter, dg, "XAUUSD", 2.85, 22, risk=0.30)
    line(m, tr, "no fresh-filter (baseline)", yg)
    m, tr = run(FixedRegime, dg, "XAUUSD", 2.85, 22, maxmat=10, risk=0.30)
    line(m, tr, "+fresh<=10", yg)

    print(f"\n  BTC M15, risk=1.00% ({yb:.1f}y)")
    m, tr = run(FixedPullbackNoFilter, db, "BTCUSDc", 10.0, 18, comm=0.0, ps=1.0, pv=0.01, risk=1.00)
    line(m, tr, "no fresh-filter (baseline)", yb)
    m, tr = run(FixedPullback, db, "BTCUSDc", 10.0, 18, comm=0.0, ps=1.0, pv=0.01, maxmat=5, risk=1.00)
    line(m, tr, "+fresh<=5", yb)

    print(f"\n  ETH M15, risk=1.00% ({ye:.1f}y)")
    m, tr = run(FixedPullbackNoFilter, de, "ETHUSDc", 1.0, 18, comm=0.0, ps=1.0, pv=0.01, risk=1.00)
    line(m, tr, "no fresh-filter (baseline)", ye)
    m, tr = run(FixedPullback, de, "ETHUSDc", 1.0, 18, comm=0.0, ps=1.0, pv=0.01, maxmat=3, risk=1.00)
    line(m, tr, "+fresh<=3", ye)


if __name__ == "__main__":
    main()
