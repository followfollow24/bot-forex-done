#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gold AMD scalping playbook, implemented to the user's written spec
(2026-08-02) and tested honestly.

THE SPEC (all times THAI, UTC+7 -- exactly as written)
------------------------------------------------------
  Asian  06:00-13:00  = ACCUMULATION. Builds the Asian Range box. Stops pile
                        up above (BSL) and below (SSL) it.
  London 14:00-18:00  = MANIPULATION. Judas swing sweeps one side of the box.
  NY     19:00-04:00  = DISTRIBUTION. Real move runs the other way.

  Killzones (highest expected win rate):
    London KZ 14:00-16:00   |   New York KZ 19:00-21:00

  Entry: price sweeps out of the Asian box, prints a REJECTION WICK, and
         CLOSES BACK INSIDE the box -> enter opposite the sweep.
  FRVP:  a Fixed-Range profile is drawn over the ACCUMULATION BOX ONLY
         (never over the prior trend) -> POC / VAH / VAL of that box.
         Optional confluence: the sweep must reach the box POC/VAL region.
  SL:    beyond the sweep wick + buffer.
  TP:    opposite side of the box (the un-swept liquidity).

WHAT IS NEW VS THE EARLIER AMD TEST
-----------------------------------
The generic AMD test (_amd_sweep_tpo_test.py) scored PF 0.32 on gold M15,
but it had NONE of the three refinements this spec adds:
    1. killzone time filter (only ~4 hours/day are tradeable, not 9)
    2. FRVP POC/VAL confluence from the accumulation box itself
    3. rejection-wick + close-back-inside confirmation
Those are real, testable differences, so this is re-tested from scratch
rather than assumed to fail.

VOLUME CAVEAT: no data file here has a volume column, so the "FRVP" is a
TPO (time-at-price) fixed-range profile. Same POC/VA concepts, time-weighted.
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

THAI_OFFSET = 7 * 3600


def _epoch(ts):
    return pd.to_datetime(pd.Series(ts)).astype("datetime64[s]").astype("int64").to_numpy()


def _profile(lows, highs, bins=24, value_frac=0.70):
    lo, hi = float(np.min(lows)), float(np.max(highs))
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        return (float("nan"),) * 3
    edges = np.linspace(lo, hi, bins + 1)
    centers = (edges[:-1] + edges[1:]) / 2.0
    counts = np.zeros(bins)
    for bl, bh in zip(lows, highs):
        i0 = max(np.searchsorted(edges, bl, side="right") - 1, 0)
        i1 = min(np.searchsorted(edges, bh, side="left"), bins)
        if i1 > i0:
            counts[i0:i1] += 1.0
    tot = counts.sum()
    if tot <= 0:
        return (float("nan"),) * 3
    p = int(np.argmax(counts)); lo_i = hi_i = p; acc = counts[p]
    while acc < tot * value_frac and (lo_i > 0 or hi_i < bins - 1):
        a = counts[lo_i - 1] if lo_i > 0 else -1.0
        b = counts[hi_i + 1] if hi_i < bins - 1 else -1.0
        if b >= a: hi_i += 1; acc += b
        else:      lo_i -= 1; acc += a
    return float(centers[p]), float(centers[hi_i]), float(centers[lo_i])


class GoldAMDScalp:
    name = "Gold AMD Scalp (Asian box + killzone sweep + FRVP)"
    short_name = "GoldAMDScalp"

    ACC_START_TH = 6      # Asian session start, Thai hour
    ACC_END_TH = 13       # Asian session end
    KILLZONES = ((14, 16), (19, 21))   # London KZ, NY KZ (Thai hours)
    USE_KILLZONE = True
    REQUIRE_POC_TOUCH = True   # sweep must reach box POC/VAL(VAH) region
    REQUIRE_WICK = True        # rejection wick >= WICK_MULT x body
    WICK_MULT = 1.0
    SWEEP_MIN_ATR = 0.10
    SL_BUFFER_ATR = 0.30
    ONE_PER_DAY = True

    sl_atr = 2.0; tp_atr = 4.0
    trail_atr_mult = 999.0; trail_activation_atr = 999.0
    max_spread_atr_ratio = 1.0
    MIN_BARS = 120
    _built = None

    def precompute(self, d):
        ep = _epoch(d["ts"]) + THAI_OFFSET
        n = len(d["c"])
        day = ep // 86400
        hr = (ep % 86400) // 3600
        h, l = d["h"], d["l"]

        in_acc = (hr >= self.ACC_START_TH) & (hr < self.ACC_END_TH)
        self._after_acc = hr >= self.ACC_END_TH
        if self.USE_KILLZONE:
            kz = np.zeros(n, bool)
            for a, b in self.KILLZONES:
                kz |= (hr >= a) & (hr < b)
            self._kz = kz
        else:
            self._kz = self._after_acc.copy()

        df = pd.DataFrame({"day": day, "in_acc": in_acc, "h": h, "l": l})
        acc = df[df["in_acc"]]
        self._box_hi = df["day"].map(acc.groupby("day")["h"].max()).to_numpy(dtype=float)
        self._box_lo = df["day"].map(acc.groupby("day")["l"].min()).to_numpy(dtype=float)

        # FRVP over the ACCUMULATION BOX ONLY (per spec)
        poc_m, vah_m, val_m = {}, {}, {}
        for dy, g in acc.groupby("day"):
            p, vh, vl = _profile(g["l"].to_numpy(), g["h"].to_numpy())
            if np.isfinite(p):
                poc_m[dy], vah_m[dy], val_m[dy] = p, vh, vl
        self._poc = df["day"].map(poc_m).to_numpy(dtype=float)
        self._vah = df["day"].map(vah_m).to_numpy(dtype=float)
        self._val = df["day"].map(val_m).to_numpy(dtype=float)

        # running sweep flags, reset each Thai day
        sh = np.zeros(n, bool); sl_ = np.zeros(n, bool)
        cur = None; a = b = False
        for i in range(n):
            if day[i] != cur:
                cur = day[i]; a = b = False
            if self._after_acc[i]:
                if np.isfinite(self._box_hi[i]) and h[i] > self._box_hi[i]: a = True
                if np.isfinite(self._box_lo[i]) and l[i] < self._box_lo[i]: b = True
            sh[i] = a; sl_[i] = b
        self._swept_hi, self._swept_lo = sh, sl_
        self._day = day
        self._traded_day = set()
        self._built = n

    def _ensure(self, d):
        if self._built != len(d["c"]):
            self.precompute(d)

    def signal(self, d, i):
        if i < self.MIN_BARS: return Signal()
        self._ensure(d)
        if not self._kz[i] or not self._after_acc[i]: return Signal()
        if self.ONE_PER_DAY and self._day[i] in self._traded_day: return Signal()

        atr = d["atr"][i]
        if np.isnan(atr) or atr <= 0: return Signal()
        bh, bl = self._box_hi[i], self._box_lo[i]
        poc, vah, val = self._poc[i], self._vah[i], self._val[i]
        if not (np.isfinite(bh) and np.isfinite(bl) and np.isfinite(poc)): return Signal()

        o, h, l, c = d["o"][i], d["h"][i], d["l"][i], d["c"][i]
        body = abs(c - o)
        up_wick = h - max(o, c)
        dn_wick = min(o, c) - l
        m = self.SWEEP_MIN_ATR * atr

        # BUY: swept SSL below box, rejection wick, close back inside box
        if self._swept_lo[i] and l < bl - m and c > bl:
            if (not self.REQUIRE_WICK) or dn_wick >= self.WICK_MULT * max(body, 1e-9):
                if (not self.REQUIRE_POC_TOUCH) or (l <= val):
                    self.sl_atr = max((c - l) / atr + self.SL_BUFFER_ATR, 0.5)
                    self.tp_atr = max((bh - c) / atr, 0.5)      # TP = opposite side (BSL)
                    self._traded_day.add(self._day[i])
                    return Signal("BUY", f"AMD sweep-SSL box[{bl:.2f},{bh:.2f}] poc={poc:.2f}")

        # SELL: swept BSL above box, rejection wick, close back inside box
        if self._swept_hi[i] and h > bh + m and c < bh:
            if (not self.REQUIRE_WICK) or up_wick >= self.WICK_MULT * max(body, 1e-9):
                if (not self.REQUIRE_POC_TOUCH) or (h >= vah):
                    self.sl_atr = max((h - c) / atr + self.SL_BUFFER_ATR, 0.5)
                    self.tp_atr = max((c - bl) / atr, 0.5)
                    self._traded_day.add(self._day[i])
                    return Signal("SELL", f"AMD sweep-BSL box[{bl:.2f},{bh:.2f}] poc={poc:.2f}")
        return Signal()


def cfg(risk=0.5, hold=32):
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = risk
    c.partial_tp_atr = 999.0; c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False; c.max_hold_bars = hold
    return c


def run(d, spread=2.85, comm=3.5, risk=0.5, **kw):
    s = GoldAMDScalp()
    for k, v in kw.items(): setattr(s, k, v)
    s.precompute(d)
    eng = BacktestEngine(d, cfg(risk), s, spread_price=spread,
                         commission_per_lot=comm, symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START), eng.trades


def line(m, tr, label, yrs):
    if not m or m.get("trades", 0) < 20:
        print(f"    {label:<40} n={m.get('trades',0) if m else 0:>5}  too few"); return
    p = perf(to_monthly(tr)); sh = p["sharpe"] if p else float("nan")
    tot = m["total_return_pct"]
    cg = -100.0 if tot <= -100 else ((1+tot/100)**(1/yrs)-1)*100
    print(f"    {label:<40} n={m['trades']:>5} ({m['trades']/yrs:>4.0f}/yr)  "
          f"win%={m['win_rate']*100:>5.1f}  PF={m['profit_factor']:>5.2f}  "
          f"Sharpe={sh:>5.2f}  CAGR={cg:>+7.2f}%  DD={m['max_dd_pct']:>5.1f}%")


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START
    dfg, _ = loader.load("XAUUSD", 99.0, c0,
                         csv_path="download/xauusd-m15-bid-2013-01-01-2026-06-10.csv",
                         allow_synthetic=True)
    yrs = (dfg["timestamp"].iloc[-1] - dfg["timestamp"].iloc[0]).days / 365.25
    d15 = prepare_data(dfg)

    print("=" * 104)
    print(" GOLD AMD SCALP -- built to spec (Thai sessions), M15, real cost $2.85 + $3.5/lot")
    print("=" * 104)
    print(f"\n  full history {yrs:.1f}y -- ablation: which spec element actually matters?")
    line(*run(d15), "full spec (KZ+POC+wick, 1/day)", yrs) if False else None

    variants = [
        ("full spec (KZ + POC + wick, 1/day)", dict()),
        ("no killzone filter",                 dict(USE_KILLZONE=False)),
        ("no POC/VA confluence",               dict(REQUIRE_POC_TOUCH=False)),
        ("no wick requirement",                dict(REQUIRE_WICK=False)),
        ("no 1-per-day cap",                   dict(ONE_PER_DAY=False)),
        ("bare (no KZ/POC/wick/cap)",          dict(USE_KILLZONE=False, REQUIRE_POC_TOUCH=False,
                                                    REQUIRE_WICK=False, ONE_PER_DAY=False)),
        ("London KZ only",                     dict(KILLZONES=((14, 16),))),
        ("NY KZ only",                         dict(KILLZONES=((19, 21),))),
        ("wider KZ (14-18, 19-22)",            dict(KILLZONES=((14, 18), (19, 22)))),
    ]
    for label, kw in variants:
        m, tr = run(d15, **kw)
        line(m, tr, label, yrs)

    # OOS on the full spec
    mid = dfg["timestamp"].iloc[len(dfg)//2]
    tr_df = dfg[dfg["timestamp"] <= mid].reset_index(drop=True)
    te_df = dfg[dfg["timestamp"] >  mid].reset_index(drop=True)
    y_tr = (tr_df["timestamp"].iloc[-1]-tr_df["timestamp"].iloc[0]).days/365.25
    y_te = (te_df["timestamp"].iloc[-1]-te_df["timestamp"].iloc[0]).days/365.25
    print(f"\n  OOS split (full spec, nothing tuned)")
    m, tr = run(prepare_data(tr_df)); line(m, tr, "1st half (train)", y_tr)
    m, tr = run(prepare_data(te_df)); line(m, tr, "2nd half (OOS)", y_te)

    # M5 version (spec mentions M5 confirmation)
    print(f"\n  same spec on M5 bars")
    df5 = resample(dfg, "5min")
    y5 = (df5["timestamp"].iloc[-1]-df5["timestamp"].iloc[0]).days/365.25
    m, tr = run(prepare_data(df5)); line(m, tr, "M5 full spec", y5)


if __name__ == "__main__":
    main()
