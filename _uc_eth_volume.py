#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_uc_eth_volume.py -- ENTRY-FILTER test: real traded volume / trade-count activity
on ETHUSDc H1 trend-pullback (frozen live config).

Family battery:
  rv   = volume / SMA(volume,168)            thresholds >0.8/>1.0/>1.2/>1.5, <1.0/<1.5, band
  rn   = n_trades / SMA(n_trades,168)        >1.0 / <1.0
  OBV 24-bar slope agrees with trade direction (per-direction) + inverse + 12/48 neighbors
  volume regime SMA24 vs SMA168 (up / down)

All rolling stats end at bar i (bar i's own volume known at close; entry fills at
i+1 open => causal). Warm-up NaNs => mask True (allow), and every warm-up ends
well before the strategy's own EMA200-H4 warm-up, so battery windows == baseline window.
"""
import sys, os
sys.path.insert(0, os.getcwd())
import numpy as np, pandas as pd
from forex_config import ForexConfig
from backtest_forex import DataLoader, prepare_data, BacktestEngine, FastHybridTrendPullback, compute_metrics
from forex_indicators import Signal
from _all_paths import to_monthly, perf, START

class Filtered(FastHybridTrendPullback):
    _mask_long = None
    _mask_short = None
    def signal(self, d, i):
        s = super().signal(d, i)
        if s.action == "BUY" and self._mask_long is not None and not self._mask_long[i]:
            return Signal()
        if s.action == "SELL" and self._mask_short is not None and not self._mask_short[i]:
            return Signal()
        return s

def cfg(risk, sym, ps=None, pv=None):
    c = ForexConfig(); c.total_capital_usd = START; c.risk_per_trade_pct = risk
    c.partial_tp_atr = 999.0; c.partial_tp_frac = 0.0; c.move_sl_to_breakeven = False; c.max_hold_bars = 64
    if ps is not None: c.pip_size[sym] = ps; c.pip_value_usd_approx[sym] = pv
    return c

def run(h1df, sym, spread, comm, risk, mlong=None, mshort=None, ps=None, pv=None):
    d = prepare_data(h1df[["timestamp", "open", "high", "low", "close"]].copy())
    s = Filtered(); s.ADX_MIN = 10; s.TIMEFRAME_SECONDS = 3600; s.TOUCH_TOLERANCE = 0.012
    s.sl_atr = 3.0; s.tp_atr = 999.0; s.trail_atr_mult = 999.0; s.trail_activation_atr = 999.0
    s.precompute(d); s._mask_long = mlong; s._mask_short = mshort
    eng = BacktestEngine(d, cfg(risk, sym, ps, pv), s, spread_price=spread, commission_per_lot=comm, symbol=sym)
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START), eng.trades

def line(m, tr, yrs):
    p = perf(to_monthly(tr)); sh = p["sharpe"] if p else float("nan")
    t = m["total_return_pct"]; cg = -100.0 if t <= -100 else ((1 + t / 100) ** (1 / yrs) - 1) * 100
    return "n=%d win%%=%.1f PF=%.2f Sharpe=%.2f CAGR=%+.2f%% DD=%.1f%%" % (
        m["trades"], m["win_rate"] * 100, m["profit_factor"], sh, cg, m["max_dd_pct"])

def stats(m, tr, yrs):
    p = perf(to_monthly(tr)); sh = p["sharpe"] if p else float("nan")
    t = m["total_return_pct"]; cg = -100.0 if t <= -100 else ((1 + t / 100) ** (1 / yrs) - 1) * 100
    return sh, cg, m["max_dd_pct"]

SYM, SPREAD, COMM, RISK, PS, PV = "ETHUSDc", 1.0, 0.0, 1.00, 1.0, 0.01

# ---------------- data ----------------
m15 = pd.read_csv("download/ethusdt-15m-vol.csv")
m15["timestamp"] = pd.to_datetime(m15["timestamp"], format="mixed")
h1 = (m15.set_index("timestamp").resample("1h")
      .agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"),
           volume=("volume", "sum"), n_trades=("n_trades", "sum"), taker_buy_vol=("taker_buy_vol", "sum"))
      .dropna(subset=["open"]).reset_index())
print("H1 bars: %d  %s .. %s" % (len(h1), h1["timestamp"].iloc[0], h1["timestamp"].iloc[-1]))

vol = h1["volume"].astype(float)
ntr = h1["n_trades"].astype(float)
close = h1["close"].astype(float)

rv = (vol / vol.rolling(168).mean()).to_numpy()          # window ends at i (causal)
rn = (ntr / ntr.rolling(168).mean()).to_numpy()
obv = (np.sign(close.diff().fillna(0.0)) * vol).cumsum()
obv_sl = {w: (obv - obv.shift(w)).to_numpy() for w in (12, 24, 48)}
v24 = vol.rolling(24).mean().to_numpy()
v168 = vol.rolling(168).mean().to_numpy()

def allow(cond_arr):
    """NaN warm-up -> allow (equivalent to baseline; strategy warm-up is longer anyway)."""
    a = np.asarray(cond_arr)
    nan = ~np.isfinite(a) if a.dtype.kind == "f" else np.zeros(len(a), bool)
    out = np.where(nan, True, a.astype(bool))
    return out

def sym_mask(cond):
    m = allow(cond); return m, m

VARIANTS = {}  # name -> (mlong, mshort, allowfrac_note)
def add(name, mlong, mshort):
    VARIANTS[name] = (mlong, mshort)

add("rv>0.8",  *sym_mask(rv > 0.8))
add("rv>1.0",  *sym_mask(rv > 1.0))
add("rv>1.2",  *sym_mask(rv > 1.2))
add("rv>1.5",  *sym_mask(rv > 1.5))
add("rv<1.0",  *sym_mask(rv < 1.0))
add("rv<1.5",  *sym_mask(rv < 1.5))
add("rv_band_0.7-1.5", *sym_mask((rv > 0.7) & (rv < 1.5)))
add("rn>1.0",  *sym_mask(rn > 1.0))
add("rn<1.0",  *sym_mask(rn < 1.0))
add("obv24_agree",   allow(obv_sl[24] > 0), allow(obv_sl[24] < 0))
add("obv24_inverse", allow(obv_sl[24] < 0), allow(obv_sl[24] > 0))
add("obv12_agree",   allow(obv_sl[12] > 0), allow(obv_sl[12] < 0))
add("obv48_agree",   allow(obv_sl[48] > 0), allow(obv_sl[48] < 0))
add("volreg_up", *sym_mask(v24 > v168))
add("volreg_dn", *sym_mask(v24 < v168))

yrs_full = (h1["timestamp"].iloc[-1] - h1["timestamp"].iloc[0]).days / 365.25

print("\n========== FULL WINDOW (%.2f yrs) ==========" % yrs_full)
bm, btr = run(h1, SYM, SPREAD, COMM, RISK, ps=PS, pv=PV)
bsh, bcg, bdd = stats(bm, btr, yrs_full)
print("%-18s %s" % ("BASELINE", line(bm, btr, yrs_full)))

results = {}
for name, (ml, ms) in VARIANTS.items():
    m, tr = run(h1, SYM, SPREAD, COMM, RISK, ml, ms, PS, PV)
    sh, cg, dd = stats(m, tr, yrs_full)
    results[name] = (m, tr, sh, cg, dd)
    frac = 0.5 * (ml.mean() + ms.mean())
    print("%-18s %s  [allow=%.0f%%]" % (name, line(m, tr, yrs_full), frac * 100))

def improved(sh, cg, dd):
    return (sh >= bsh + 0.10 and cg >= bcg) or (sh >= bsh and dd <= 0.75 * bdd and cg >= 0.9 * bcg)

winners = [n for n, (_, _, sh, cg, dd) in results.items() if improved(sh, cg, dd)]
print("\nBeats-baseline (criterion 4):", winners if winners else "NONE")

if not winners:
    # near-misses: nominally >= baseline Sharpe but under the +0.10 bar -- run OOS anyway
    winners = [n for n, (_, _, sh, cg, dd) in results.items() if sh >= bsh and n not in ("volreg_dn",)]
    print("Near-miss candidates for OOS diligence:", winners)

# ---------------- OOS half-split for winners (each half vs OWN-half baseline) ----------------
if winners:
    mid = len(h1) // 2
    halves = [("H1(first)", h1.iloc[:mid].reset_index(drop=True), slice(0, mid)),
              ("H2(second)", h1.iloc[mid:].reset_index(drop=True), slice(mid, len(h1)))]
    for hname, hdf, sl in halves:
        hyrs = (hdf["timestamp"].iloc[-1] - hdf["timestamp"].iloc[0]).days / 365.25
        hbm, hbtr = run(hdf, SYM, SPREAD, COMM, RISK, ps=PS, pv=PV)
        print("\n---- %s (%.2f yrs) ----" % (hname, hyrs))
        print("%-18s %s" % ("baseline", line(hbm, hbtr, hyrs)))
        for name in winners:
            ml, ms = VARIANTS[name]
            m, tr = run(hdf, SYM, SPREAD, COMM, RISK, ml[sl], ms[sl], PS, PV)
            print("%-18s %s" % (name, line(m, tr, hyrs)))
print("\nDONE")
