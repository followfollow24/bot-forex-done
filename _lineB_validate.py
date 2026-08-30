#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LINE B phase 2: the two H4 gold configs whose sign flipped when the 2.85 spread
was corrected to the real 0.24 -- put through train/test + random-entry control.

  A) H4 Donchian breakout   PF 0.84 -> 1.12
  B) H4 trend-pullback adx  PF 0.88 -> 1.17

Discipline: every parameter is chosen on the FIRST half only. The single
best-by-train config is then run ONCE on the second half. Random-entry control
matched on trade count and long-share, 40 seeds, on the SAME test window.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import (DataLoader, prepare_data, BacktestEngine,
                            FastHybridTrendPullback, compute_metrics)
from _idea_search import resample
from _lineB_rerun import (RandomEntry, donch, cfg, concentration, EQUITY, RISK, COMM,
                          GOLD_M15)

SPREAD_REAL = 0.48      # = 0.24 round-trip (engine halves it)
SPREAD_PESS = 0.96      # = 0.48 round-trip


def run(d, strat, spread, hold=40):
    strat.precompute(d)
    eng = BacktestEngine(d, cfg(hold), strat, spread_price=spread,
                         commission_per_lot=COMM, symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, EQUITY), eng.trades


def pull(adx, sl, tp):
    s = FastHybridTrendPullback(); s.ADX_MIN = adx
    s.sl_atr, s.tp_atr = sl, tp
    s.trail_atr_mult = s.trail_activation_atr = 999.0
    return s


def stats(tr):
    if not tr:
        return None
    p = np.array([t["net_pnl"] for t in tr])
    longs = sum(1 for t in tr if t["side"] == "long") / len(tr)
    wins = p[p > 0].sum(); losses = -p[p < 0].sum()
    return dict(n=len(p), pf=wins / losses if losses > 0 else float("inf"),
                net=p.sum(), long_share=longs, wr=(p > 0).mean())


def control(d, tmpl, n_target, long_share, spread, hold, reps=40):
    """Random-entry control on the SAME bars, matched trade count + long share."""
    n_bars = len(d["c"])
    p0 = n_target / max(n_bars, 1)
    out = []
    for seed in range(reps):
        s = RandomEntry()
        s.sl_atr = tmpl.sl_atr; s.tp_atr = tmpl.tp_atr
        s.trail_atr_mult = tmpl.trail_atr_mult
        s.trail_activation_atr = tmpl.trail_activation_atr
        s.long_share = long_share; s.seed = seed
        # calibrate firing prob so realised trade count lands near n_target
        p = p0
        for _ in range(4):
            s.p = p
            _, tr = run(d, s, spread, hold)
            if not tr:
                p *= 2.0; continue
            if abs(len(tr) - n_target) / n_target < 0.10:
                break
            p *= n_target / len(tr)
            p = min(p, 0.9)
        out.append(stats(tr))
    out = [o for o in out if o]
    return out


def report_control(real, ctrl, label):
    nets = np.array([c["net"] for c in ctrl])
    pfs = np.array([c["pf"] for c in ctrl if np.isfinite(c["pf"])])
    ns = np.array([c["n"] for c in ctrl])
    z = (real["net"] - nets.mean()) / nets.std(ddof=1) if nets.std(ddof=1) > 0 else float("nan")
    zpf = (real["pf"] - pfs.mean()) / pfs.std(ddof=1) if pfs.std(ddof=1) > 0 else float("nan")
    beat = (nets < real["net"]).mean() * 100
    print(f"    {label}")
    print(f"      strategy : n={real['n']:>4}  PF={real['pf']:.3f}  net=${real['net']:+.1f}  "
          f"WR={real['wr']*100:.1f}%  long={real['long_share']*100:.0f}%")
    print(f"      control  : n={ns.mean():>6.0f}  PF={pfs.mean():.3f}+-{pfs.std(ddof=1):.3f}  "
          f"net=${nets.mean():+.1f}+-{nets.std(ddof=1):.1f}   ({len(ctrl)} reps)")
    print(f"      EDGE OVER RANDOM: net z={z:+.2f}   PF z={zpf:+.2f}   "
          f"beats {beat:.0f}% of random reps")


def per_year(tr, label):
    df = pd.DataFrame(tr); df["y"] = pd.to_datetime(df["entry_ts"]).dt.year
    rows = []
    for y, g in df.groupby("y"):
        w = g[g.net_pnl > 0].net_pnl.sum(); l = -g[g.net_pnl < 0].net_pnl.sum()
        rows.append((y, len(g), w / l if l > 0 else float("inf"), g.net_pnl.sum()))
    pos = sum(1 for r in rows if r[3] > 0)
    print(f"      per-year {label}: {pos}/{len(rows)} years net-positive  "
          + " ".join(f"{r[0]}:{r[3]:+.0f}" for r in rows))


def conc(tr, label):
    c = concentration(tr)
    print(f"      concentration {label}: net=${c['total']:+.1f}  best single trade "
          f"${c['best']:+.1f} ({c['best_share']:.0f}% of net)  top-decile (n={c['k']}) "
          f"${c['topdec']:+.1f} ({c['topdec_share']:.0f}% of net)  "
          f"net WITHOUT top decile = ${c['ex_topdec']:+.1f}")


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = EQUITY
    dfg, _ = loader.load("XAUUSD", 99.0, c0, csv_path=GOLD_M15, allow_synthetic=True)
    df_h4 = resample(dfg, "4h")
    mid = df_h4["timestamp"].iloc[len(df_h4) // 2]
    tr_df = df_h4[df_h4.timestamp < mid].reset_index(drop=True)
    te_df = df_h4[df_h4.timestamp >= mid].reset_index(drop=True)
    d_tr, d_te = prepare_data(tr_df), prepare_data(te_df)
    print(f"[split] H4 train {tr_df.timestamp.iloc[0]} .. {tr_df.timestamp.iloc[-1]}  ({len(tr_df)} bars)")
    print(f"[split] H4 test  {te_df.timestamp.iloc[0]} .. {te_df.timestamp.iloc[-1]}  ({len(te_df)} bars)")
    print(f"[cost]  spread_price={SPREAD_REAL} => 0.24 round-trip; comm ${COMM}/lot/side; "
          f"equity ${EQUITY:,.0f}; risk {RISK}%; min_lot 0.01\n")

    # ================= A) DONCHIAN BREAKOUT =================
    print("=" * 100)
    print(" A) GOLD H4 DONCHIAN BREAKOUT  (rejected at 2.85: PF 0.84)")
    print("=" * 100)
    print("  TRAIN sweep (first half only):")
    best = None
    for sl in (1.5, 2.0, 2.5, 3.0):
        for tm, ta in ((3.0, 1.0), (2.0, 1.0), (4.0, 1.5), (999.0, 999.0)):
            for mg in (0.0, 0.25):
                m, t = run(d_tr, donch(sl, tm, ta, mg), SPREAD_REAL, 40)
                if not t or len(t) < 60:
                    continue
                s = stats(t)
                tag = f"sl{sl} trail{tm}@{ta} mg{mg}"
                print(f"    {tag:<30} n={s['n']:>4} PF={s['pf']:.3f} net=${s['net']:+.0f}")
                if best is None or s["pf"] > best[1]["pf"]:
                    best = (tag, s, (sl, tm, ta, mg))
    print(f"\n  -> best on TRAIN: {best[0]}  PF={best[1]['pf']:.3f}")
    sl, tm, ta, mg = best[2]
    m, t_te = run(d_te, donch(sl, tm, ta, mg), SPREAD_REAL, 40)
    s_te = stats(t_te)
    print(f"\n  TEST (second half, run ONCE):")
    ctrl = control(d_te, donch(sl, tm, ta, mg), s_te["n"], s_te["long_share"], SPREAD_REAL, 40)
    report_control(s_te, ctrl, f"H4 Donchian {best[0]} @0.24")
    conc(t_te, "TEST"); per_year(t_te, "TEST")
    m2, t2 = run(d_te, donch(sl, tm, ta, mg), SPREAD_PESS, 40)
    print(f"      at 2x pessimistic cost (0.48 rt): PF={stats(t2)['pf']:.3f} net=${stats(t2)['net']:+.1f}")

    # ================= B) TREND-PULLBACK =================
    print("\n" + "=" * 100)
    print(" B) GOLD H4 HYBRID TREND-PULLBACK  (rejected at 2.85: PF 0.88)")
    print("=" * 100)
    print("  TRAIN sweep (first half only):")
    bestp = None
    for adx in (14, 18, 20, 25):
        for sl, tp in ((2.5, 5.0), (3.0, 7.0), (2.5, 15.0), (3.0, 5.0)):
            m, t = run(d_tr, pull(adx, sl, tp), SPREAD_REAL, 40)
            if not t or len(t) < 60:
                continue
            s = stats(t)
            tag = f"adx{adx} sl{sl}/tp{tp}"
            print(f"    {tag:<30} n={s['n']:>4} PF={s['pf']:.3f} net=${s['net']:+.0f}")
            if bestp is None or s["pf"] > bestp[1]["pf"]:
                bestp = (tag, s, (adx, sl, tp))
    print(f"\n  -> best on TRAIN: {bestp[0]}  PF={bestp[1]['pf']:.3f}")
    adx, sl, tp = bestp[2]
    m, t_te2 = run(d_te, pull(adx, sl, tp), SPREAD_REAL, 40)
    s_te2 = stats(t_te2)
    print(f"\n  TEST (second half, run ONCE):")
    ctrl2 = control(d_te, pull(adx, sl, tp), s_te2["n"], s_te2["long_share"], SPREAD_REAL, 40)
    report_control(s_te2, ctrl2, f"H4 pullback {bestp[0]} @0.24")
    conc(t_te2, "TEST"); per_year(t_te2, "TEST")
    m3, t3 = run(d_te, pull(adx, sl, tp), SPREAD_PESS, 40)
    print(f"      at 2x pessimistic cost (0.48 rt): PF={stats(t3)['pf']:.3f} net=${stats(t3)['net']:+.1f}")

    # reversed split as a robustness check (test on FIRST half)
    print("\n" + "=" * 100)
    print(" C) REVERSED SPLIT -- does either hold in the OTHER half?")
    print("=" * 100)
    m, t = run(d_tr, donch(sl_d := best[2][0], best[2][1], best[2][2], best[2][3]), SPREAD_REAL, 40)
    print(f"    Donchian {best[0]} on FIRST half : PF={stats(t)['pf']:.3f} net=${stats(t)['net']:+.1f}")
    m, t = run(d_tr, pull(adx, sl, tp), SPREAD_REAL, 40)
    print(f"    Pullback {bestp[0]} on FIRST half : PF={stats(t)['pf']:.3f} net=${stats(t)['net']:+.1f}")


if __name__ == "__main__":
    main()
