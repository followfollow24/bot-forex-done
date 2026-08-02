#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AMD (Accumulation-Manipulation-Distribution) + Liquidity Sweep + TPO Profile.

WHAT THIS IS
------------
The ICT/SMC-style setup the user asked for, made mechanical:

  A - ACCUMULATION : the Asian session (00:00-ACC_END_H UTC) builds a range.
                     This is the "accumulation" box where liquidity (stops)
                     piles up above the high and below the low.
  M - MANIPULATION : during the London window, price SWEEPS one side of that
                     range (takes out the high or low = grabs the liquidity)
                     and then CLOSES BACK INSIDE the range. That failed
                     breakout is the manipulation leg.
  D - DISTRIBUTION : the real move goes the OTHER way. Entry is taken in the
                     direction opposite the sweep, targeting the far side /
                     the prior day's value area.

  + TPO CONFLUENCE : the entry must also be on the correct side of the prior
                     day's POC (point of control), so we only fade a sweep
                     when the profile agrees with the direction.

IMPORTANT -- "VOLUME PROFILE" CAVEAT
------------------------------------
Every price file in download/ is timestamp,open,high,low,close -- there is
NO VOLUME COLUMN anywhere in this project's data (verified 2026-08-01), so a
true volume profile (POC/VAH/VAL weighted by traded volume) is IMPOSSIBLE to
compute here and nothing in this file pretends otherwise.

What is used instead is a genuine TPO (Time-Price-Opportunity) profile --
Steidlmayer's ORIGINAL Market Profile, which counts how many bars/how much
TIME price spent at each level rather than how much volume traded there. It
is computable from OHLC alone and yields the same POC / value-area concepts.
It is a well-defined substitute, NOT a fake volume profile. If real volume
data is obtained later, swapping the TPO weight for volume is a one-line
change in _build_profile().

CAUSALITY
---------
Every level is built from COMPLETED prior periods only:
  - the accumulation range is only usable AFTER its window closes,
  - the TPO profile is the PRIOR day's, never the forming day's.
Same discipline as the H1/H4 bucket fix earlier this session.
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


class AMDSweepTPO:
    name = "AMD + Liquidity Sweep + TPO Profile"
    short_name = "AMD-Sweep-TPO"

    ACC_END_H = 7          # accumulation window = 00:00 .. 07:00 UTC (Asian)
    HUNT_END_H = 16        # sweep must happen before this hour (London window)
    SWEEP_MIN_ATR = 0.10   # sweep must exceed the range edge by >= this x ATR
    TPO_BINS = 30          # price bins for the prior-day TPO profile
    USE_TPO_FILTER = True  # require POC agreement

    sl_atr = 2.0
    tp_atr = 999.0
    trail_atr_mult = 3.0
    trail_activation_atr = 1.0
    max_spread_atr_ratio = 0.5
    MIN_BARS = 200

    _acc_hi = None
    _acc_lo = None
    _acc_ready = None
    _in_hunt = None
    _prev_poc = None
    _swept_hi = None
    _swept_lo = None

    @staticmethod
    def _epoch_seconds(ts) -> np.ndarray:
        return pd.to_datetime(pd.Series(ts)).astype("datetime64[s]").astype("int64").to_numpy()

    def precompute(self, d: dict):
        epoch = self._epoch_seconds(d["ts"])
        n = len(d["c"])
        day_id = epoch // 86400
        sec_of_day = epoch % 86400
        hour = sec_of_day // 3600

        h, l, c = d["h"], d["l"], d["c"]
        in_acc = hour < self.ACC_END_H
        self._acc_ready = (hour >= self.ACC_END_H)
        self._in_hunt = (hour >= self.ACC_END_H) & (hour < self.HUNT_END_H)

        df = pd.DataFrame({"day": day_id, "in_acc": in_acc, "h": h, "l": l, "c": c})

        # --- A: accumulation range, per day, from that day's own Asian window ---
        acc = df[df["in_acc"]]
        acc_hi_by_day = acc.groupby("day")["h"].max()
        acc_lo_by_day = acc.groupby("day")["l"].min()
        self._acc_hi = df["day"].map(acc_hi_by_day).to_numpy()
        self._acc_lo = df["day"].map(acc_lo_by_day).to_numpy()

        # --- TPO profile of the PRIOR day (causal: shift by one day) ---
        poc_by_day = {}
        for day, grp in df.groupby("day"):
            lo, hi = grp["l"].min(), grp["h"].max()
            if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
                continue
            edges = np.linspace(lo, hi, self.TPO_BINS + 1)
            centers = (edges[:-1] + edges[1:]) / 2.0
            counts = np.zeros(self.TPO_BINS)
            # each bar adds 1 TPO to every bin its range covers (time at price)
            for bl, bh in zip(grp["l"].to_numpy(), grp["h"].to_numpy()):
                i0 = np.searchsorted(edges, bl, side="right") - 1
                i1 = np.searchsorted(edges, bh, side="left")
                i0 = max(i0, 0); i1 = min(i1, self.TPO_BINS)
                if i1 > i0:
                    counts[i0:i1] += 1.0
            if counts.sum() > 0:
                poc_by_day[day] = centers[int(np.argmax(counts))]

        days_sorted = sorted(poc_by_day.keys())
        prev_poc_map = {}
        for k in range(1, len(days_sorted)):
            prev_poc_map[days_sorted[k]] = poc_by_day[days_sorted[k - 1]]
        self._prev_poc = df["day"].map(prev_poc_map).to_numpy(dtype=float)

        # --- M: has this day already swept a side? (causal running flags) ---
        swept_hi = np.zeros(n, dtype=bool)
        swept_lo = np.zeros(n, dtype=bool)
        cur_day = -1
        hi_done = lo_done = False
        for i in range(n):
            if day_id[i] != cur_day:
                cur_day = day_id[i]
                hi_done = lo_done = False
            if self._acc_ready[i]:
                ah, al = self._acc_hi[i], self._acc_lo[i]
                if np.isfinite(ah) and h[i] > ah:
                    hi_done = True
                if np.isfinite(al) and l[i] < al:
                    lo_done = True
            swept_hi[i] = hi_done
            swept_lo[i] = lo_done
        self._swept_hi = swept_hi
        self._swept_lo = swept_lo

    def signal(self, d: dict, i: int) -> Signal:
        if i < self.MIN_BARS or not self._in_hunt[i]:
            return Signal()
        atr = d["atr"][i]
        if np.isnan(atr) or atr <= 0:
            return Signal()
        ah, al = self._acc_hi[i], self._acc_lo[i]
        if not np.isfinite(ah) or not np.isfinite(al):
            return Signal()

        h, l, c = d["h"][i], d["l"][i], d["c"][i]
        margin = self.SWEEP_MIN_ATR * atr
        poc = self._prev_poc[i]
        if self.USE_TPO_FILTER and not np.isfinite(poc):
            return Signal()

        # M leg: swept the HIGH (grabbed buy-side liquidity) and closed back
        # INSIDE the range -> distribution is DOWN -> SELL.
        if self._swept_hi[i] and h > ah + margin and c < ah:
            if (not self.USE_TPO_FILTER) or (c < poc):
                return Signal("SELL", f"AMD sweep-high acc_hi={ah:.2f} poc={poc:.2f}")

        # mirror: swept the LOW (sell-side liquidity) and closed back inside -> BUY
        if self._swept_lo[i] and l < al - margin and c > al:
            if (not self.USE_TPO_FILTER) or (c > poc):
                return Signal("BUY", f"AMD sweep-low acc_lo={al:.2f} poc={poc:.2f}")

        return Signal()


def cfg(sym, ps=None, pv=None, risk=1.0, hold=48):
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


def run(d, sym, spread, comm=0.0, ps=1.0, pv=0.01, risk=1.0, use_tpo=True,
        acc_end=7, hunt_end=16, sweep_atr=0.10):
    s = AMDSweepTPO()
    s.USE_TPO_FILTER = use_tpo
    s.ACC_END_H = acc_end
    s.HUNT_END_H = hunt_end
    s.SWEEP_MIN_ATR = sweep_atr
    s.precompute(d)
    eng = BacktestEngine(d, cfg(sym, ps, pv, risk=risk), s, spread_price=spread,
                          commission_per_lot=comm, symbol=sym)
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START), eng.trades


def line(m, tr, label, yrs):
    if not m or m.get("trades", 0) < 20:
        n = m.get("trades", 0) if m else 0
        print(f"    {label:<30} n={n:>5}  too few")
        return
    p = perf(to_monthly(tr))
    sh = p["sharpe"] if p else float("nan")
    tot = m["total_return_pct"]
    cg = -100.0 if tot <= -100 else ((1 + tot/100) ** (1/yrs) - 1) * 100
    print(f"    {label:<30} n={m['trades']:>5} ({m['trades']/yrs:>4.0f}/yr {m['trades']/yrs/365:.2f}/day)  "
          f"win%={m['win_rate']*100:>5.1f}  PF={m['profit_factor']:>5.2f}  Sharpe={sh:>5.2f}  "
          f"CAGR={cg:>+7.2f}%  DD={m['max_dd_pct']:>5.1f}%")


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START
    GOLD_M15 = "download/xauusd-m15-bid-2013-01-01-2026-06-10.csv"
    BTC_CSV  = "download/btcusdt-15m-binance-2017-08-17-2026-06-30.csv"
    ETH_CSV  = "download/ethusdt-15m-binance-2017-08-17-2026-06-30.csv"

    print("=" * 104)
    print(" AMD + LIQUIDITY SWEEP + TPO PROFILE  (M15 and H1, real costs)")
    print(" NOTE: TPO = time-at-price profile (Market Profile). No volume column exists in this data.")
    print("=" * 104)

    dfg, _ = loader.load("XAUUSD", 99.0, c0, csv_path=GOLD_M15, allow_synthetic=True)
    dfb, _ = loader.load("BTCUSDc", 99.0, c0, csv_path=BTC_CSV, allow_synthetic=False)
    dfe, _ = loader.load("ETHUSDc", 99.0, c0, csv_path=ETH_CSV, allow_synthetic=False)

    markets = [
        ("GOLD M15", dfg, "XAUUSD", 2.85, 3.5, None, None, 0.30),
        ("BTC  M15", dfb, "BTCUSDc", 10.0, 0.0, 1.0, 0.01, 1.00),
        ("ETH  M15", dfe, "ETHUSDc",  1.0, 0.0, 1.0, 0.01, 1.00),
    ]

    for label, df, sym, sp, comm, ps, pv, risk in markets:
        yrs = (df["timestamp"].iloc[-1] - df["timestamp"].iloc[0]).days / 365.25
        d = prepare_data(df)
        print(f"\n  {label}  (risk={risk}%, {yrs:.1f}y)")
        m, tr = run(d, sym, sp, comm, ps, pv, risk=risk, use_tpo=True)
        line(m, tr, "AMD+sweep+TPO filter", yrs)
        m, tr = run(d, sym, sp, comm, ps, pv, risk=risk, use_tpo=False)
        line(m, tr, "AMD+sweep (no TPO filter)", yrs)

    # H1 variants for gold (M15 gold is cost-crippled -- established this session)
    print(f"\n  GOLD H1")
    dfg_h1 = resample(dfg, "1h")
    yg1 = (dfg_h1["timestamp"].iloc[-1] - dfg_h1["timestamp"].iloc[0]).days / 365.25
    dg1 = prepare_data(dfg_h1)
    m, tr = run(dg1, "XAUUSD", 2.85, 3.5, None, None, risk=0.30, use_tpo=True)
    line(m, tr, "AMD+sweep+TPO filter", yg1)
    m, tr = run(dg1, "XAUUSD", 2.85, 3.5, None, None, risk=0.30, use_tpo=False)
    line(m, tr, "AMD+sweep (no TPO filter)", yg1)

    print(f"\n  BTC H1")
    dfb_h1 = resample(dfb, "1h")
    yb1 = (dfb_h1["timestamp"].iloc[-1] - dfb_h1["timestamp"].iloc[0]).days / 365.25
    db1 = prepare_data(dfb_h1)
    m, tr = run(db1, "BTCUSDc", 10.0, 0.0, 1.0, 0.01, risk=1.00, use_tpo=True)
    line(m, tr, "AMD+sweep+TPO filter", yb1)
    m, tr = run(db1, "BTCUSDc", 10.0, 0.0, 1.0, 0.01, risk=1.00, use_tpo=False)
    line(m, tr, "AMD+sweep (no TPO filter)", yb1)


if __name__ == "__main__":
    main()
