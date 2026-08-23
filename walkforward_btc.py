#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
walkforward_btc.py -- HONEST walk-forward for the BTC combo.

Guards against two forms of data snooping at once:
  (a) parameter overfit  -> params (trend EMA pair, TSMOM lookback, Donchian N/M)
      are SELECTED ON TRAIN only, then applied unchanged to the test year.
  (b) structural snooping -> the long/short decision (cut net-short vs keep it)
      is ALSO a config choice the TRAIN picks; it is NOT hardcoded. This tests
      whether "cut short" is something past data would have told you, or just
      hindsight from seeing the full sample.

Expanding-window folds; each test year is scored using only data before it.
Concatenated test years = a fully out-of-sample equity curve.

DISCLAIMER (always report with these numbers): the sample contains only ~2-3
real BTC bear markets. Even a passing walk-forward here is far less certain than
the gold work (which has many more cycles). Treat any edge as provisional.
"""
import numpy as np
import pandas as pd
from backtest_btc import (load, s_trend_longflat, s_tsmom, s_donchian, s_buyhold,
                          run, BARS_PER_YEAR)

D = 24  # 1H bars per day

TREND = [(15, 80), (20, 100), (25, 120), (30, 150)]
TSMOM = [20, 30, 40]
DONCH = [(20, 10), (30, 15), (40, 20)]
MODES = ["longbias", "withshort"]      # longbias = cut net-short (the decision under test)


def m_from_net(net):
    net = np.asarray(net)
    if len(net) < 10:
        return None
    eq = np.cumprod(1 + net)
    yrs = len(net) / BARS_PER_YEAR
    cagr = eq[-1] ** (1/yrs) - 1
    peak = np.maximum.accumulate(eq)
    dd = -(eq/peak - 1).min()
    sharpe = net.mean()/net.std()*np.sqrt(BARS_PER_YEAR) if net.std() > 0 else 0.0
    mar = cagr/dd if dd > 0 else np.nan
    return dict(cagr=cagr, dd=dd, mar=mar, sharpe=sharpe, tot=eq[-1]-1)


def main():
    df = load()
    idx = df.index
    print(f"loaded {len(df):,} 1H bars  {idx[0].date()} -> {idx[-1].date()}\n")

    # ---- precompute base signals per param ----
    trend_sig = {p: s_trend_longflat(df, p[0]*D, p[1]*D) for p in TREND}
    tsmom_sig = {L: s_tsmom(df, L*D) for L in TSMOM}
    donch_sig = {dd: s_donchian(df, dd[0]*D, dd[1]*D) for dd in DONCH}

    def build_net(cfg):
        tp, tl, dd, mode = cfg
        avg = (trend_sig[tp] + tsmom_sig[tl] + donch_sig[dd]) / 3.0
        if mode == "longbias":
            avg = avg.clip(lower=0)
        net, _, _ = run(df, avg)
        return pd.Series(net, index=idx)

    configs = [(tp, tl, dd, md) for tp in TREND for tl in TSMOM for dd in DONCH for md in MODES]
    net_full = {c: build_net(c) for c in configs}
    bh_net = pd.Series(run(df, s_buyhold(df))[0], index=idx)

    # ================= (A) full-sample sweep: robustness to params =================
    print("=" * 78)
    print("(A) FULL-SAMPLE param sweep -- is the edge broad or a lucky point?")
    print("=" * 78)
    for md in MODES:
        mars = [m_from_net(net_full[c])["mar"] for c in configs if c[3] == md]
        mars = [x for x in mars if x == x]
        print(f"  {md:<10}  MAR across {len(mars)} param sets:  "
              f"min {min(mars):.2f} | median {np.median(mars):.2f} | max {max(mars):.2f}")
    bh = m_from_net(bh_net)
    print(f"  {'Buy&Hold':<10}  MAR {bh['mar']:.2f}  (benchmark)")
    print("  -> if longbias median >> withshort median AND >> B&H, the edge is broad,")
    print("     not a single lucky config.\n")

    # ================= (B) honest walk-forward =================
    print("=" * 78)
    print("(B) WALK-FORWARD -- train selects EVERYTHING (params + short-mode), locked to test")
    print("=" * 78)
    test_years = [2021, 2022, 2023, 2024, 2025, 2026]
    SEL = "mar"     # selection metric on train
    oos_parts, bh_parts, picks = [], [], []
    for ty in test_years:
        tr = idx.year < ty
        te = idx.year == ty
        if tr.sum() < BARS_PER_YEAR or te.sum() < 50:
            continue
        # select best config on TRAIN only
        best, bestv = None, -1e9
        for c in configs:
            mt = m_from_net(net_full[c][tr].values)
            if mt is None:
                continue
            v = mt[SEL]
            if v == v and v > bestv:
                bestv, best = v, c
        test_m = m_from_net(net_full[best][te].values)
        bh_m = m_from_net(bh_net[te].values)
        picks.append((ty, best, bestv, test_m, bh_m))
        oos_parts.append(net_full[best][te])
        bh_parts.append(bh_net[te])

    print(f"{'testYr':<7}{'train-picked config (params, mode)':<38}{'trainMAR':>9}"
          f"{'testRet':>9}{'B&H':>8}{'mode':>10}")
    print("-" * 82)
    for ty, cfg, tv, tm, bm in picks:
        tp, tl, dd, md = cfg
        label = f"EMA{tp[0]}/{tp[1]} TS{tl} DC{dd[0]}/{dd[1]}"
        print(f"{ty:<7}{label:<38}{tv:>9.2f}{tm['tot']*100:>8.0f}%{bm['tot']*100:>7.0f}%{md:>10}")

    oos = pd.concat(oos_parts)
    bh_oos = pd.concat(bh_parts)
    om, bo = m_from_net(oos.values), m_from_net(bh_oos.values)
    print("\n" + "-" * 78)
    print(f"CONCATENATED OUT-OF-SAMPLE ({test_years[0]}-{test_years[-1]}, each yr config chosen from prior data only):")
    print(f"  Walk-forward combo : CAGR {om['cagr']*100:5.1f}%  MaxDD {om['dd']*100:4.1f}%  "
          f"MAR {om['mar']:.2f}  Sharpe {om['sharpe']:.2f}  Tot {om['tot']*100:.0f}%")
    print(f"  Buy&Hold (same span): CAGR {bo['cagr']*100:5.1f}%  MaxDD {bo['dd']*100:4.1f}%  "
          f"MAR {bo['mar']:.2f}  Sharpe {bo['sharpe']:.2f}  Tot {bo['tot']*100:.0f}%")

    # ================= (C) turning the validated edge into more RETURN =================
    # The honest lever: leverage a lower-DD edge up to a chosen risk budget.
    # lev_net = L * oos_net (leverage scales both PnL and costs ~proportionally on a CFD).
    # Also a vol-target version: size = target_vol / trailing_realised_vol (no lookahead), capped.
    print("\n" + "=" * 78)
    print("(C) MORE RETURN via leverage/vol-target on the WALK-FORWARD OOS edge (honest)")
    print("=" * 78)
    oos_r = oos.values
    print(f"{'variant':<26}{'CAGR':>8}{'MaxDD':>8}{'MAR':>7}{'Sharpe':>8}  note")
    print("-" * 78)
    for L in [1.0, 1.5, 2.0]:
        m = m_from_net(L * oos_r)
        note = "same DD as B&H(79%)" if abs(m["dd"]-0.79) < 0.08 else ""
        print(f"{'combo x'+str(L):<26}{m['cagr']*100:>7.1f}%{m['dd']*100:>7.1f}%"
              f"{m['mar']:>7.2f}{m['sharpe']:>8.2f}  {note}")
    # vol-target: annualised target vol, size = tgt/trailing_vol, cap 3x
    for tgt in [0.40, 0.60]:
        r = pd.Series(oos_r)
        trail = r.rolling(24*30).std() * np.sqrt(BARS_PER_YEAR)   # 30d trailing ann. vol
        lev = (tgt / trail).shift(1).clip(0, 3).fillna(0)          # shift -> no lookahead
        m = m_from_net((lev.values * oos_r))
        print(f"{'vol-target '+str(int(tgt*100))+'%':<26}{m['cagr']*100:>7.1f}%{m['dd']*100:>7.1f}%"
              f"{m['mar']:>7.2f}{m['sharpe']:>8.2f}  cap 3x, no-lookahead")
    bh_o = m_from_net(bh_oos.values)
    print(f"{'[ref] Buy&Hold':<26}{bh_o['cagr']*100:>7.1f}%{bh_o['dd']*100:>7.1f}%"
          f"{bh_o['mar']:>7.2f}{bh_o['sharpe']:>8.2f}  benchmark")
    print("  NOTE: leverage on a CFD adds margin/liquidation risk (a 78% DD at 2x can")
    print("  margin-call before recovery). vol-target smooths the path -> safer leverage.")

    modes_picked = [c[3] for _, c, _, _, _ in picks]
    n_lb = modes_picked.count("longbias")
    print("\n" + "-" * 78)
    print(f"STRUCTURAL DECISION TEST: train chose 'longbias' (cut short) in {n_lb}/{len(picks)} folds.")
    if n_lb == len(picks):
        print("  -> 'cut short' was learnable from PAST data every fold (not hindsight).")
    elif n_lb == 0:
        print("  -> train NEVER chose cut-short; the full-sample 'cut short' WAS hindsight.")
    else:
        print("  -> mixed; the short decision is unstable -> treat 'cut short' with caution.")
    print("\nDISCLAIMER: only ~2-3 bear markets in sample; OOS edge is provisional, "
          "lower-confidence than gold. Report this every time.")


if __name__ == "__main__":
    main()
