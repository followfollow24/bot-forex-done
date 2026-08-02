#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Three ICT/SMC tools, each isolated into its OWN standalone strategy so we can
see which one (if any) actually carries signal, instead of only knowing how
the combination behaves.

  1. AMD          -- TIME anchored. Asian session (00:00-ACC_END_H UTC) builds
                     the accumulation range; during the London/NY window price
                     manipulates (breaks the range and closes back inside);
                     distribution is traded in the opposite direction.
                     Nothing but session structure -- no levels, no profile.

  2. LQ SWEEP     -- LEVEL anchored, no session timing at all. Liquidity pools
                     are the PRIOR DAY high/low and recent confirmed swing
                     highs/lows. A sweep = price exceeds the level by
                     >= SWEEP_MIN_ATR x ATR and then closes back inside.
                     Trades the rejection. Works at any hour.

  3. TPO PROFILE  -- PROFILE anchored. Builds the PRIOR day's Time-Price-
                     Opportunity profile: POC plus the 70% value area
                     (VAH/VAL). Classic value-area trade: price accepts
                     outside value, then RE-ENTERS the value area -> trade
                     back toward POC ("value area rejection / return to
                     value").

VOLUME CAVEAT: no price file in this project has a volume column (verified
2026-08-01), so tool 3 uses a genuine TPO profile (time at price --
Steidlmayer's original Market Profile), not a volume profile. Swapping in
real volume would be a one-line change in _day_profile().

CAUSALITY: every level comes from a COMPLETED prior period (prior day, or a
swing confirmed SWING_LOOKBACK bars after the fact, or a session window that
has already closed). Same discipline as the H1/H4 bucket fix this session.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import DataLoader, prepare_data, BacktestEngine, compute_metrics
from forex_indicators import Signal
from _idea_search import resample
from _all_paths import to_monthly, perf, START


def _epoch_seconds(ts) -> np.ndarray:
    return pd.to_datetime(pd.Series(ts)).astype("datetime64[s]").astype("int64").to_numpy()


def _day_profile(lows, highs, bins=30, value_frac=0.70):
    """TPO profile for one day -> (poc, vah, val). Each bar adds 1 TPO to
    every bin its range covers. Value area = smallest contiguous band around
    the POC holding `value_frac` of all TPOs."""
    lo, hi = float(np.min(lows)), float(np.max(highs))
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        return float("nan"), float("nan"), float("nan")
    edges = np.linspace(lo, hi, bins + 1)
    centers = (edges[:-1] + edges[1:]) / 2.0
    counts = np.zeros(bins)
    for bl, bh in zip(lows, highs):
        i0 = max(np.searchsorted(edges, bl, side="right") - 1, 0)
        i1 = min(np.searchsorted(edges, bh, side="left"), bins)
        if i1 > i0:
            counts[i0:i1] += 1.0
    total = counts.sum()
    if total <= 0:
        return float("nan"), float("nan"), float("nan")
    poc_i = int(np.argmax(counts))
    lo_i = hi_i = poc_i
    acc = counts[poc_i]
    target = total * value_frac
    while acc < target and (lo_i > 0 or hi_i < bins - 1):
        take_lo = counts[lo_i - 1] if lo_i > 0 else -1.0
        take_hi = counts[hi_i + 1] if hi_i < bins - 1 else -1.0
        if take_hi >= take_lo:
            hi_i += 1; acc += take_hi
        else:
            lo_i -= 1; acc += take_lo
    return float(centers[poc_i]), float(centers[hi_i]), float(centers[lo_i])


# ══════════════════════════════════════════════════════════════════════════
class ToolAMD:
    """1. AMD -- pure session structure (time anchored)."""
    name = "AMD (Accumulation-Manipulation-Distribution)"
    short_name = "AMD"

    ACC_END_H = 7
    HUNT_END_H = 16
    SWEEP_MIN_ATR = 0.10
    sl_atr = 2.0; tp_atr = 999.0
    trail_atr_mult = 999.0; trail_activation_atr = 999.0
    max_spread_atr_ratio = 0.5
    MIN_BARS = 200
    _built_len = None

    def precompute(self, d):
        epoch = _epoch_seconds(d["ts"]); n = len(d["c"])
        day = epoch // 86400; hour = (epoch % 86400) // 3600
        h, l = d["h"], d["l"]
        in_acc = hour < self.ACC_END_H
        self._ready = hour >= self.ACC_END_H
        self._hunt = (hour >= self.ACC_END_H) & (hour < self.HUNT_END_H)
        df = pd.DataFrame({"day": day, "in_acc": in_acc, "h": h, "l": l})
        acc = df[df["in_acc"]]
        self._hi = df["day"].map(acc.groupby("day")["h"].max()).to_numpy(dtype=float)
        self._lo = df["day"].map(acc.groupby("day")["l"].min()).to_numpy(dtype=float)
        sh = np.zeros(n, bool); sl_ = np.zeros(n, bool)
        cur = None; a = b = False
        for i in range(n):
            if day[i] != cur:
                cur = day[i]; a = b = False
            if self._ready[i]:
                if np.isfinite(self._hi[i]) and h[i] > self._hi[i]: a = True
                if np.isfinite(self._lo[i]) and l[i] < self._lo[i]: b = True
            sh[i] = a; sl_[i] = b
        self._swept_hi, self._swept_lo = sh, sl_
        self._built_len = n

    def _ensure(self, d):
        if self._built_len != len(d["c"]):
            self.precompute(d)

    def signal(self, d, i):
        if i < self.MIN_BARS: return Signal()
        self._ensure(d)
        if i >= len(self._hunt) or not self._hunt[i]: return Signal()
        atr = d["atr"][i]
        if np.isnan(atr) or atr <= 0: return Signal()
        ah, al = self._hi[i], self._lo[i]
        if not np.isfinite(ah) or not np.isfinite(al): return Signal()
        h, l, c = d["h"][i], d["l"][i], d["c"][i]
        m = self.SWEEP_MIN_ATR * atr
        if self._swept_hi[i] and h > ah + m and c < ah:
            return Signal("SELL", f"AMD manip-high {ah:.2f}")
        if self._swept_lo[i] and l < al - m and c > al:
            return Signal("BUY", f"AMD manip-low {al:.2f}")
        return Signal()


# ══════════════════════════════════════════════════════════════════════════
class ToolLQSweep:
    """2. Liquidity Sweep -- level anchored, no session timing."""
    name = "Liquidity Sweep (PDH/PDL + swings)"
    short_name = "LQ-Sweep"

    SWING_LOOKBACK = 5
    SWEEP_MIN_ATR = 0.15
    sl_atr = 2.0; tp_atr = 999.0
    trail_atr_mult = 999.0; trail_activation_atr = 999.0
    max_spread_atr_ratio = 0.5
    MIN_BARS = 200
    _built_len = None

    def precompute(self, d):
        epoch = _epoch_seconds(d["ts"]); n = len(d["c"])
        day = epoch // 86400
        h, l = d["h"], d["l"]
        df = pd.DataFrame({"day": day, "h": h, "l": l})

        # prior-day high/low (classic liquidity pools)
        dh = df.groupby("day")["h"].max(); dl = df.groupby("day")["l"].min()
        days = sorted(dh.index)
        pdh_map = {days[k]: dh[days[k-1]] for k in range(1, len(days))}
        pdl_map = {days[k]: dl[days[k-1]] for k in range(1, len(days))}
        self._pdh = df["day"].map(pdh_map).to_numpy(dtype=float)
        self._pdl = df["day"].map(pdl_map).to_numpy(dtype=float)

        # most recent CONFIRMED swing high/low (confirmed lb bars later)
        lb = self.SWING_LOOKBACK
        sw_hi = np.full(n, np.nan); sw_lo = np.full(n, np.nan)
        cur_hi = cur_lo = np.nan
        for i in range(n):
            k = i - lb
            if k >= lb:
                if h[k] == h[k-lb:k+lb+1].max(): cur_hi = h[k]
                if l[k] == l[k-lb:k+lb+1].min(): cur_lo = l[k]
            sw_hi[i] = cur_hi; sw_lo[i] = cur_lo
        self._sw_hi, self._sw_lo = sw_hi, sw_lo
        self._built_len = n

    def _ensure(self, d):
        if self._built_len != len(d["c"]):
            self.precompute(d)

    def signal(self, d, i):
        if i < self.MIN_BARS: return Signal()
        self._ensure(d)
        atr = d["atr"][i]
        if np.isnan(atr) or atr <= 0: return Signal()
        h, l, c = d["h"][i], d["l"][i], d["c"][i]
        m = self.SWEEP_MIN_ATR * atr

        for lvl in (self._pdh[i], self._sw_hi[i]):
            if np.isfinite(lvl) and h > lvl + m and c < lvl:
                return Signal("SELL", f"LQ sweep-high {lvl:.2f}")
        for lvl in (self._pdl[i], self._sw_lo[i]):
            if np.isfinite(lvl) and l < lvl - m and c > lvl:
                return Signal("BUY", f"LQ sweep-low {lvl:.2f}")
        return Signal()


# ══════════════════════════════════════════════════════════════════════════
class ToolTPOProfile:
    """3. TPO / Volume Profile -- value-area return trade."""
    name = "TPO Profile (value area / POC)"
    short_name = "TPO-Profile"

    TPO_BINS = 30
    VALUE_FRAC = 0.70
    sl_atr = 2.0; tp_atr = 999.0
    trail_atr_mult = 999.0; trail_activation_atr = 999.0
    max_spread_atr_ratio = 0.5
    MIN_BARS = 200
    _built_len = None

    def precompute(self, d):
        epoch = _epoch_seconds(d["ts"]); n = len(d["c"])
        day = epoch // 86400
        df = pd.DataFrame({"day": day, "h": d["h"], "l": d["l"]})
        poc_m, vah_m, val_m = {}, {}, {}
        for dy, g in df.groupby("day"):
            p, vh, vl = _day_profile(g["l"].to_numpy(), g["h"].to_numpy(),
                                     self.TPO_BINS, self.VALUE_FRAC)
            if np.isfinite(p):
                poc_m[dy], vah_m[dy], val_m[dy] = p, vh, vl
        days = sorted(poc_m)
        pp = {days[k]: poc_m[days[k-1]] for k in range(1, len(days))}
        pvh = {days[k]: vah_m[days[k-1]] for k in range(1, len(days))}
        pvl = {days[k]: val_m[days[k-1]] for k in range(1, len(days))}
        self._poc = df["day"].map(pp).to_numpy(dtype=float)
        self._vah = df["day"].map(pvh).to_numpy(dtype=float)
        self._val = df["day"].map(pvl).to_numpy(dtype=float)
        self._built_len = n

    def _ensure(self, d):
        if self._built_len != len(d["c"]):
            self.precompute(d)

    def signal(self, d, i):
        if i < self.MIN_BARS: return Signal()
        self._ensure(d)
        poc, vah, val = self._poc[i], self._vah[i], self._val[i]
        if not (np.isfinite(poc) and np.isfinite(vah) and np.isfinite(val)):
            return Signal()
        c_prev, c = d["c"][i-1], d["c"][i]
        # accepted ABOVE value, now back INSIDE -> revert down toward POC
        if c_prev > vah and val <= c <= vah:
            return Signal("SELL", f"VA re-entry from above vah={vah:.2f} poc={poc:.2f}")
        # accepted BELOW value, now back INSIDE -> revert up toward POC
        if c_prev < val and val <= c <= vah:
            return Signal("BUY", f"VA re-entry from below val={val:.2f} poc={poc:.2f}")
        return Signal()


# ══════════════════════════════════════════════════════════════════════════
def cfg(sym, ps=None, pv=None, risk=1.0, hold=48):
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = risk
    c.partial_tp_atr = 999.0; c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False; c.max_hold_bars = hold
    if ps is not None:
        c.pip_size[sym] = ps; c.pip_value_usd_approx[sym] = pv
    return c


def run(cls, d, sym, spread, comm=0.0, ps=None, pv=None, risk=1.0):
    s = cls(); s.precompute(d)
    eng = BacktestEngine(d, cfg(sym, ps, pv, risk=risk), s,
                         spread_price=spread, commission_per_lot=comm, symbol=sym)
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START), eng.trades


def line(m, tr, label, yrs):
    if not m or m.get("trades", 0) < 20:
        print(f"    {label:<20} n={m.get('trades',0) if m else 0:>5}  too few"); return
    p = perf(to_monthly(tr)); sh = p["sharpe"] if p else float("nan")
    tot = m["total_return_pct"]
    cg = -100.0 if tot <= -100 else ((1+tot/100)**(1/yrs)-1)*100
    print(f"    {label:<20} n={m['trades']:>5} ({m['trades']/yrs/365:.2f}/day)  "
          f"win%={m['win_rate']*100:>5.1f}  PF={m['profit_factor']:>5.2f}  "
          f"Sharpe={sh:>5.2f}  CAGR={cg:>+7.2f}%  DD={m['max_dd_pct']:>5.1f}%")


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START
    dfg, _ = loader.load("XAUUSD", 99.0, c0, csv_path="download/xauusd-m15-bid-2013-01-01-2026-06-10.csv", allow_synthetic=True)
    dfb, _ = loader.load("BTCUSDc", 99.0, c0, csv_path="download/btcusdt-15m-binance-2017-08-17-2026-06-30.csv", allow_synthetic=False)
    dfe, _ = loader.load("ETHUSDc", 99.0, c0, csv_path="download/ethusdt-15m-binance-2017-08-17-2026-06-30.csv", allow_synthetic=False)

    print("=" * 96)
    print(" THREE TOOLS, EACH ISOLATED -- H1 bars, real costs")
    print(" (TPO = time-at-price profile; no volume column exists in this data)")
    print("=" * 96)

    for label, df, sym, sp, comm, ps, pv, risk in [
        ("GOLD H1", resample(dfg, "1h"), "XAUUSD", 2.85, 3.5, None, None, 0.30),
        ("BTC  H1", resample(dfb, "1h"), "BTCUSDc", 10.0, 0.0, 1.0, 0.01, 1.00),
        ("ETH  H1", resample(dfe, "1h"), "ETHUSDc",  1.0, 0.0, 1.0, 0.01, 1.00),
    ]:
        yrs = (df["timestamp"].iloc[-1] - df["timestamp"].iloc[0]).days / 365.25
        d = prepare_data(df)
        print(f"\n  {label}  ({yrs:.1f}y, risk={risk}%)")
        for cls in (ToolAMD, ToolLQSweep, ToolTPOProfile):
            m, tr = run(cls, d, sym, sp, comm, ps, pv, risk)
            line(m, tr, cls.short_name, yrs)


if __name__ == "__main__":
    main()
