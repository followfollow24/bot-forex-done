#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Independent adversarial re-implementation of the "Combo LongBias" BTC daily-scale
trend combo, written from the SPEC text only (not the finder's code).

Spec:
  1H bars (Binance 15m resampled). Day-params x 24.
  (a) Trend long/flat: 1 if EMA(close,25d)>EMA(close,120d) else 0
  (b) TSMOM: sign(close/close.shift(40d)-1)
  (c) Donchian 20d-entry/10d-exit stop-and-reverse on close vs PRIOR-bar channel
  combo = (a+b+c)/3, clipped >=0 for longbias
  signal at bar-i close -> held from bar i+1 (pos.shift(1)); ret = pos_held * close pct change
  costs: half-spread (0.016%/2 per unit turnover), swap -6.9%/365 per daily
         rollover while ANY long is held (full charge even on fractional longs)
Validation:
  full-sample Sharpe (monthly), half-split Sharpe, yearly expanding walk-forward
  (train picks params+mode by train MAR from the stated grid), neighbor fragility.
"""
import os, sys, time
import numpy as np
import pandas as pd

DATA = "/Users/follow/Desktop/outputs/bot forex/download/btcusdt-15m-binance-2017-08-17-2026-06-30.csv"
H = 24                      # day-param -> 1H bars
SPREAD_RT = 0.00016         # $10 / ~$63k round trip
HALF = SPREAD_RT / 2.0
SWAP_DAY = 0.069 / 365.0    # long swap per daily rollover; short = 0

def load():
    df = pd.read_csv(DATA, parse_dates=["timestamp"]).set_index("timestamp")
    c = df["close"].resample("1h").last().dropna()
    return c

def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()

# ---------- sub-signals (all causal: value at bar i uses bars <= i) ----------
def sig_trend(close, fd, sd):
    a = (ema(close, fd * H) > ema(close, sd * H)).astype(float)
    a.iloc[: sd * H] = 0.0            # conservative warm-up: no trade
    return a

def sig_tsmom(close, ld):
    b = np.sign(close / close.shift(ld * H) - 1.0)
    return pd.Series(b, index=close.index).fillna(0.0)

def sig_donch(close, ne_d, nx_d):
    ne, nx = ne_d * H, nx_d * H
    hi_e = close.rolling(ne).max().shift(1).values   # prior-bar channel
    lo_e = close.rolling(ne).min().shift(1).values
    hi_x = close.rolling(nx).max().shift(1).values
    lo_x = close.rolling(nx).min().shift(1).values
    c = close.values
    pos = np.zeros(len(c))
    p = 0.0
    for i in range(len(c)):
        if np.isnan(hi_e[i]):
            pos[i] = 0.0
            continue
        if p == 0.0:
            if c[i] > hi_e[i]:
                p = 1.0
            elif c[i] < lo_e[i]:
                p = -1.0
        elif p == 1.0:
            if c[i] < lo_x[i]:
                p = -1.0 if c[i] < lo_e[i] else 0.0
        else:  # p == -1
            if c[i] > hi_x[i]:
                p = 1.0 if c[i] > hi_e[i] else 0.0
        pos[i] = p
    return pd.Series(pos, index=close.index)

# ---------- combo + PnL ----------
def combo_pos(A, B, C, mode):
    raw = (A + B + C) / 3.0
    if mode == "longbias":
        raw = raw.clip(lower=0.0)
    return raw

def net_returns(close, pos):
    ret_cc = close.pct_change().fillna(0.0)
    pos_held = pos.shift(1).fillna(0.0)          # signal at i close -> held from i+1
    gross = pos_held * ret_cc
    turnover = (pos - pos.shift(1)).abs().fillna(0.0)
    cost = turnover.shift(1).fillna(0.0) * HALF  # paid when the fill happens (bar i+1)
    # swap: charged on each daily rollover while any long is held (full charge)
    dates = pos.index.normalize()
    newday = pd.Series(dates != np.roll(dates, 1), index=pos.index)
    newday.iloc[0] = False
    swap = np.where(newday.values & (pos_held.values > 0), SWAP_DAY, 0.0)
    return gross - cost - pd.Series(swap, index=pos.index)

# ---------- metrics ----------
def to_monthly_ret(net):
    return net.resample("MS").apply(lambda x: (1.0 + x).prod() - 1.0)

def sharpe_monthly(net):
    m = to_monthly_ret(net)
    m = m[m.index >= net.index[0].normalize()]
    if len(m) < 6 or m.std() == 0:
        return np.nan
    return m.mean() / m.std() * np.sqrt(12)

def mar(net):
    eq = (1.0 + net).cumprod()
    yrs = (net.index[-1] - net.index[0]).total_seconds() / (365.25 * 24 * 3600)
    if yrs <= 0 or eq.iloc[-1] <= 0:
        return -9.0
    cagr = eq.iloc[-1] ** (1.0 / yrs) - 1.0
    dd = (eq / eq.cummax() - 1.0).min()
    if dd == 0:
        return -9.0
    return cagr / abs(dd)

def total_ret(net):
    return (1.0 + net).prod() - 1.0

# ---------- main ----------
def main():
    close = load()
    print(f"bars: {len(close)}  {close.index[0]} .. {close.index[-1]}")

    # grid from spec
    TRENDS = [(15, 80), (20, 100), (25, 120), (30, 150)]
    TSMOMS = [20, 30, 40]
    DONCHS = [(20, 10), (30, 15), (40, 20)]
    MODES = ["longbias", "withshort"]

    # neighbor values for fragility test (fixed config EMA25/120 TS40 DC20/10)
    TREND_EXTRA = [(20, 120), (30, 120), (25, 100), (25, 150)]
    TSMOM_EXTRA = [35, 45, 50]
    DONCH_EXTRA = [(15, 8), (25, 12), (20, 8), (20, 12)]

    t0 = time.time()
    trend_cache = {p: sig_trend(close, *p) for p in TRENDS + TREND_EXTRA}
    ts_cache = {p: sig_tsmom(close, p) for p in TSMOMS + TSMOM_EXTRA}
    dc_cache = {p: sig_donch(close, *p) for p in DONCHS + DONCH_EXTRA}
    print(f"signals computed in {time.time()-t0:.1f}s")

    def net_for(tr, ts, dc, mode):
        pos = combo_pos(trend_cache[tr], ts_cache[ts], dc_cache[dc], mode)
        return net_returns(close, pos)

    # ===== 1) fixed config full-sample =====
    FIX = ((25, 120), 40, (20, 10), "longbias")
    net_fix = net_for(*FIX)
    bh = close.pct_change().fillna(0.0)
    print("\n=== FIXED CONFIG EMA25/120 TS40 DC20/10 longbias ===")
    print(f"full Sharpe(monthly): {sharpe_monthly(net_fix):.2f}   (claim 1.09)")
    print(f"full total return: {total_ret(net_fix)*100:.0f}%  MAR: {mar(net_fix):.2f}")

    # half-split
    mid = close.index[len(close) // 2]
    print(f"\nhalf split at {mid}")
    print(f"first-half Sharpe:  {sharpe_monthly(net_fix[net_fix.index < mid]):.2f}")
    print(f"second-half Sharpe: {sharpe_monthly(net_fix[net_fix.index >= mid]):.2f}  (claim OOS-half 0.75)")

    # yearly returns fixed config (full sample) + B&H
    print("\nyear | fixed-config ret | B&H ret")
    for y in range(2017, 2027):
        m = net_fix.index.year == y
        if m.sum() < 24 * 20:
            continue
        print(f"{y}: {total_ret(net_fix[m])*100:+7.0f}%   {total_ret(bh[m])*100:+7.0f}%")

    # ===== 2) yearly expanding walk-forward =====
    print("\n=== WALK-FORWARD (train=all prior data, pick by train MAR) ===")
    all_nets = {}
    for tr in TRENDS:
        for ts in TSMOMS:
            for dc in DONCHS:
                for md in MODES:
                    all_nets[(tr, ts, dc, md)] = net_for(tr, ts, dc, md)
    print(f"grid of {len(all_nets)} configs computed ({time.time()-t0:.1f}s elapsed)")

    wf_rows = []
    for y in range(2021, 2027):
        tr_mask = net_fix.index.year < y
        te_mask = net_fix.index.year == y
        if te_mask.sum() == 0:
            continue
        best_cfg, best_mar = None, -1e9
        for cfg, net in all_nets.items():
            m = mar(net[tr_mask])
            if m > best_mar:
                best_mar, best_cfg = m, cfg
        net_te = all_nets[best_cfg][te_mask]
        r = total_ret(net_te) * 100
        rb = total_ret(bh[te_mask]) * 100
        wf_rows.append((y, best_cfg, r, rb))
        print(f"{y}: pick={best_cfg} trainMAR={best_mar:.2f} -> test {r:+.0f}% (B&H {rb:+.0f}%)")

    pos_years = sum(1 for _, _, r, _ in wf_rows if r > 0)
    beat = sum(1 for _, _, r, rb in wf_rows if r > rb)
    lb_picks = sum(1 for _, c, _, _ in wf_rows if c[3] == "longbias")
    print(f"WF positive years: {pos_years}/{len(wf_rows)}  beat B&H: {beat}/{len(wf_rows)}  longbias picked: {lb_picks}/{len(wf_rows)}")

    # ===== 3) neighbor fragility around the fixed config =====
    print("\n=== NEIGHBORS (full-sample Sharpe, one param moved at a time) ===")
    base = sharpe_monthly(net_fix)
    print(f"base: {base:.2f}")
    for tr in [(20, 100), (30, 150), (20, 120), (30, 120), (25, 100), (25, 150)]:
        s = sharpe_monthly(net_for(tr, 40, (20, 10), "longbias"))
        print(f"  TREND {tr}: {s:.2f}")
    for ts in [30, 35, 45, 50]:
        s = sharpe_monthly(net_for((25, 120), ts, (20, 10), "longbias"))
        print(f"  TSMOM {ts}: {s:.2f}")
    for dc in [(15, 8), (25, 12), (30, 15), (20, 8), (20, 12)]:
        s = sharpe_monthly(net_for((25, 120), 40, dc, "longbias"))
        print(f"  DONCH {dc}: {s:.2f}")

    # ===== 4) save monthly returns of the fixed config =====
    m = to_monthly_ret(net_fix)
    out = pd.DataFrame({"month": m.index.strftime("%Y-%m"), "ret_pct": (m.values * 100)})
    path = "/private/tmp/claude-501/-Users-follow-Desktop-outputs/5c1c9348-7d96-4348-98bf-858abc3aadf3/scratchpad/btc_combo_longbias_monthly.csv"
    out.to_csv(path, index=False)
    print(f"\nmonthly returns saved: {path}")
    print(f"total elapsed {time.time()-t0:.1f}s")

if __name__ == "__main__":
    main()
