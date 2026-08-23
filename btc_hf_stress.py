#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
btc_hf_stress.py -- stress + robustness for the WF-picked BTC higher-frequency
config (M15, ADX15 / SL4 / TP12, real HybridTrendPullback + real SL/TP engine +
asymmetric swap). Answers the three questions that decide real-vs-fragile:

  1. SPREAD STRESS: $10 is TODAY's spread. Re-run OOS at $10/$20/$30 -- does the
     edge survive a 2-3x spread blowout / early-history uncertainty?
  2. LONG vs SHORT decomposition: is the edge bull-beta (longs riding BTC's
     secular bull) or genuinely 2-sided (shorts pay their way in bears)?
  3. CORRELATION to the existing sleeves (gold-trend, BTC-combo LongBias):
     additive third edge, or a (better) substitute for the BTC combo?

Everything OOS = entry date >= 2022-01-01 (the held-out test half of WF-B).
All numbers include $10 spread + asymmetric swap unless a stress row says otherwise.
ASCII-only.
"""
from __future__ import annotations

import os
import math
import numpy as np
import pandas as pd

import btc_walkforward as W
from backtest_forex import FastHybridTrendPullback
from backtest_btc import s_trend_longflat, s_tsmom, s_donchian

DL   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "download")
BTC  = os.path.join(DL, "btcusdt-15m-binance-2017-08-17-2026-06-30.csv")
GOLD = os.path.join(DL, "xauusd-m15-bid-2013-01-01-2026-06-10.csv")
SPLIT = pd.Timestamp("2022-01-01")
CFG   = dict(adx=15, sl=4.0, tp=12.0)   # WF-B train-picked (locked)
BPY   = 365


# ── run the locked config at a given spread; return trades ───────────────────
def run_locked(d, spread):
    s = FastHybridTrendPullback()
    s.ADX_MIN = CFG["adx"]; s.precompute(d)
    s.sl_atr = CFG["sl"]; s.tp_atr = CFG["tp"]
    s.trail_atr_mult = 999.0; s.trail_activation_atr = 999.0
    eng = W.BTCEngine(d, W.make_cfg(), s, spread_price=spread,
                      commission_per_lot=0.0, symbol="BTCUSDc")
    eng.run(quiet=True, do_precompute=False)
    return W.normalize_trades(eng.trades)


def oos(trades):
    return [t for t in trades if t["_entry_dt"] >= SPLIT]


def sleeve_daily(trades):
    """calendar-daily fixed-notional return series from a trade list (by exit date)."""
    s = pd.Series([t["_norm_pnl"] for t in trades],
                  index=[pd.Timestamp(t["exit_ts"]).normalize() for t in trades])
    s = s.groupby(s.index).sum()
    full = pd.date_range(s.index.min(), s.index.max(), freq="D")
    return (s.reindex(full, fill_value=0.0) / W.START)


def m(trades):
    return W.slice_metrics(trades)


def show(tag, mm):
    print("  %-26s PF %5s  ret %+7.1f%%  MaxDD %4.1f%%  Sharpe %5.2f  n %5d  win %2.0f%%"
          % (tag, W.pf_str(mm["pf"]), mm["ret"], mm["dd"], mm["sh"], mm["n"], mm["wr"] * 100))


# =============================================================================
print("=" * 88)
print(" BTC HIGHER-FREQ  M15  ADX15 / SL4 / TP12  --  STRESS + ROBUSTNESS (OOS >= 2022)")
print("=" * 88)

print("\n[load M15] ...")
d, df = W.load_prepared(BTC, resample_h1=False)

# ── 1. SPREAD STRESS ─────────────────────────────────────────────────────────
print("\n1) SPREAD STRESS -- $10 is today's; does the OOS edge survive 2-3x?")
print("-" * 88)
base_trades = None
for sp in (10.0, 20.0, 30.0):
    tr = run_locked(d, sp)
    if sp == 10.0:
        base_trades = tr
    show("OOS spread $%.0f" % sp, m(oos(tr)))
print("  (all include asymmetric swap; only the spread differs)")

# ── 2. LONG vs SHORT decomposition ───────────────────────────────────────────
print("\n2) LONG vs SHORT -- bull-beta, or genuinely 2-sided?")
print("-" * 88)
ot = oos(base_trades)
longs  = W.normalize_trades([t for t in ot if t["side"] == "long"])
shorts = W.normalize_trades([t for t in ot if t["side"] == "short"])
show("OOS all", m(ot))
show("OOS longs only",  m(longs))
show("OOS shorts only", m(shorts))
# per-year long/short net to see 2022 bear specifically
print("  by year (normalized net %, OOS):")
for yr in range(2022, 2027):
    yl = [t["_norm_pnl"] for t in ot if t["_entry_dt"].year == yr and t["side"] == "long"]
    ys = [t["_norm_pnl"] for t in ot if t["_entry_dt"].year == yr and t["side"] == "short"]
    print("    %d  long %+6.1f%% (n%3d)   short %+6.1f%% (n%3d)"
          % (yr, sum(yl)/W.START*100, len(yl), sum(ys)/W.START*100, len(ys)))

# ── 3. CORRELATION to gold-trend + BTC-combo sleeves ─────────────────────────
print("\n3) CORRELATION to existing sleeves (daily, OOS >= 2022)")
print("-" * 88)


def load_h1_csv(path):
    x = pd.read_csv(path); ts = x["timestamp"]
    x["timestamp"] = pd.to_datetime(ts) if not pd.api.types.is_numeric_dtype(ts) \
        else pd.to_datetime(ts, unit="ms")
    x = x.set_index("timestamp")
    o = x["open"].resample("1h").first(); h = x["high"].resample("1h").max()
    l = x["low"].resample("1h").min();  c = x["close"].resample("1h").last()
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c}).dropna()


def ema(s, n): return s.ewm(span=n, adjust=False).mean()


def vec_daily(df_, pos, spread_side, swap_l=0.0, swap_s=0.0):
    ret = df_["close"].pct_change().fillna(0).values
    p = pos.shift(1).fillna(0).values
    tc = spread_side * np.abs(np.diff(p, prepend=0.0))
    day = df_.index.normalize().values
    roll = (day != np.roll(day, 1)); roll[0] = False
    swap = np.where(roll & (p > 0), swap_l/365, 0.0) + np.where(roll & (p < 0), swap_s/365, 0.0)
    n = pd.Series(p*ret - tc - swap, index=df_.index)
    return (1+n).groupby(n.index.normalize()).prod()-1


g = load_h1_csv(GOLD)
b = load_h1_csv(BTC)
gold_d  = vec_daily(g, (ema(g["close"], 50) > ema(g["close"], 200)).astype(float),
                    (0.28/2)/g["close"].mean(), 0.02, 0.02)
combo_pos = ((s_trend_longflat(b, 25*24, 120*24) + s_tsmom(b, 40*24)
              + s_donchian(b, 20*24, 10*24))/3).clip(lower=0)
combo_d = vec_daily(b, combo_pos, 0.00008, 0.069, 0.0)
hf_d    = sleeve_daily(ot)   # our new BTC HF sleeve, OOS

R = pd.DataFrame({"GOLD": gold_d, "BTC_combo": combo_d, "BTC_HF": hf_d}).loc[SPLIT:].fillna(0.0)
print("  daily-return correlation (OOS 2022+):")
print(R.corr().round(2).to_string().replace("\n", "\n  "))


def stats(dd):
    dd = dd[dd != 0] if (dd != 0).any() else dd
    eq = (1+dd).cumprod()
    sh = dd.mean()/dd.std()*math.sqrt(BPY) if dd.std() > 0 else 0
    mdd = -(eq/eq.cummax()-1).min()
    return sh, mdd*100


print("\n  standalone OOS Sharpe / MaxDD (each sleeve, daily):")
for nm in ["GOLD", "BTC_combo", "BTC_HF"]:
    sh, mdd = stats(R[nm])
    print("    %-10s Sharpe %5.2f  MaxDD %4.1f%%" % (nm, sh, mdd))

# inverse-vol portfolios: with combo vs with HF-instead-of-combo
def port_sharpe(cols):
    sub = R[cols]; vol = sub.std(); w = (1/vol)/(1/vol).sum()
    p = (sub*w).sum(axis=1)
    return p.mean()/p.std()*math.sqrt(BPY) if p.std() > 0 else 0

print("\n  inverse-vol portfolio OOS Sharpe:")
print("    GOLD + BTC_combo            : %.2f" % port_sharpe(["GOLD", "BTC_combo"]))
print("    GOLD + BTC_HF (swap combo)  : %.2f" % port_sharpe(["GOLD", "BTC_HF"]))
print("    GOLD + BTC_combo + BTC_HF   : %.2f" % port_sharpe(["GOLD", "BTC_combo", "BTC_HF"]))
print("\n  READ: if corr(BTC_HF,BTC_combo) is high (~0.5+), HF is a SUBSTITUTE for the")
print("        combo (use the better one), not an additive 3rd edge. If low, it's additive.")
print("=" * 88)
