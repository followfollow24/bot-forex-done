#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 mix_a_sensitivity_stress.py
 Analysis trifecta for Mix_A (SL=2.5, TP=7.0):

   Part 1 — Parameter Sensitivity  : SL×TP grid around Mix_A, Calmar heatmap
   Part 2 — Stress Test            : Normal / Tough / Extreme cost scenarios
   Part 3 — Detailed 27-window     : per-window PF, regime, return, DD

 Fix applied: CSV loaded once, H1-trend precomputed once, zero re-reads.
 Last time the bug caused "Date range" to print ~hundreds of times.
 This script is fully serial — no multiprocessing, no fork risk.

 Run:
   python3 mix_a_sensitivity_stress.py \
       --csv download/xauusd-m15-bid-2013-01-01-2026-06-10.csv
================================================================================
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forex_config import ForexConfig
from backtest_forex import (DataLoader, prepare_data, BacktestEngine,
                             FastHybridTrendPullback, compute_metrics)
from walk_forward_regime import (build_windows, gold_return_pct, regime_tag,
                                  window_metrics)

# ── Constants ─────────────────────────────────────────────────────────────────
START         = 10_000.0
RISK_PCT      = 0.30
BASE_SPREAD   = 0.50    # realistic XAUUSD spread in USD/oz (live avg ~0.4-0.6)
BASE_COMM     = 3.50    # USD per lot per side
WINDOW_MONTHS = 6

MIX_A_SL = 2.5
MIX_A_TP = 7.0

# ── Part 1: Sensitivity grid ──────────────────────────────────────────────────
#   5 SL values × 5 TP values = 25 combos
SL_GRID = [round(v, 1) for v in np.arange(2.3, 2.8, 0.1)]   # 2.3 2.4 2.5 2.6 2.7
TP_GRID = [round(v, 1) for v in np.arange(6.6, 7.5, 0.2)]   # 6.6 6.8 7.0 7.2 7.4

# ── Part 2: Stress scenarios ──────────────────────────────────────────────────
#   slippage is simulated by adding it to effective spread (worst-side fill)
SCENARIOS = [
    dict(name="Normal  (sprd=0.50 slip=0.00)", spread=0.50, slippage=0.00),
    dict(name="Tough   (sprd=0.75 slip=0.10)", spread=0.75, slippage=0.10),
    dict(name="Extreme (sprd=1.25 slip=0.30)", spread=1.25, slippage=0.30),
]


# =============================================================================
# Helpers
# =============================================================================
def _cfg():
    c = ForexConfig()
    c.total_capital_usd    = START
    c.risk_per_trade_pct   = RISK_PCT
    c.partial_tp_atr       = 999.0
    c.partial_tp_frac      = 0.0
    c.move_sl_to_breakeven = False
    return c


def _run(d, strat, sl, tp, spread, comm):
    strat.sl_atr               = sl
    strat.tp_atr               = tp
    strat.trail_atr_mult       = 999.0
    strat.trail_activation_atr = 999.0
    eng = BacktestEngine(d, _cfg(), strat,
                         spread_price=spread, commission_per_lot=comm,
                         symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)
    return eng.trades, eng.equity_curve


def _overall(trades, eq):
    m   = compute_metrics(trades, eq, START)
    ret = (m.get("final_equity", START) - START) / START * 100
    dd  = m.get("max_dd_pct", 0) or 0.01
    return dict(
        pf       = m.get("profit_factor", 0),
        ret      = ret,
        dd       = m.get("max_dd_pct", 0),
        calmar   = ret / dd,
        trades   = m.get("trades", 0),
        win_rate = m.get("win_rate", 0),
    )


def _prep_trades(trades):
    """Add normalised PnL and parsed timestamp to each trade dict."""
    eq_before = START
    for t in trades:
        t["_norm_pnl"] = (t["net_pnl"] * (START / eq_before)) if eq_before > 0 else t["net_pnl"]
        t["_entry_dt"] = pd.Timestamp(t["entry_ts"])
        eq_before = t["equity_after"]


def _window_rows(trades, win_info):
    """Return list of per-window dicts with metrics."""
    rows = []
    for wi in win_info:
        wt = [t for t in trades if wi["start"] <= t["_entry_dt"] < wi["end"]]
        m  = window_metrics(wt, START)
        ret_pct = (sum(t["_norm_pnl"] for t in wt) / START * 100) if wt else None
        rows.append(dict(
            start    = wi["start"],
            end      = wi["end"],
            regime   = wi["regime"],
            gold_ret = wi["gold_ret"],
            trades   = m["trades"],
            pf       = m["pf"],
            win_rate = m["win_rate"],
            ret_pct  = ret_pct,
            dd_pct   = m["max_dd_pct"],
        ))
    return rows


def _pf_counts(win_rows):
    ok    = sum(1 for r in win_rows if r["trades"] > 0 and _pf_ok(r["pf"]))
    n     = sum(1 for r in win_rows if r["trades"] > 0)
    ds_ok = sum(1 for r in win_rows if r["trades"] > 0
                and r["regime"] in ("DOWN", "SIDEWAYS") and _pf_ok(r["pf"]))
    n_ds  = sum(1 for r in win_rows if r["trades"] > 0
                and r["regime"] in ("DOWN", "SIDEWAYS"))
    return ok, n, ds_ok, n_ds


def _pf_ok(pf):
    return pf is not None and (math.isinf(pf) or pf > 1.0)


# =============================================================================
# Part 1 — Sensitivity grid
# =============================================================================
def run_sensitivity(d, strat, win_info):
    combos = [(sl, tp) for sl in SL_GRID for tp in TP_GRID]
    N = len(combos)
    print(f"\n  รัน {N} combos (SL {SL_GRID[0]}–{SL_GRID[-1]} × "
          f"TP {TP_GRID[0]}–{TP_GRID[-1]}) ด้วย spread={BASE_SPREAD} ...\n")

    results = []
    for idx, (sl, tp) in enumerate(combos, 1):
        tag = " ← Mix_A" if sl == MIX_A_SL and tp == MIX_A_TP else ""
        print(f"  [{idx:>2}/{N}] SL={sl} TP={tp}{tag}", end="", flush=True)
        t0 = time.time()
        trades, eq = _run(d, strat, sl, tp, BASE_SPREAD, BASE_COMM)
        _prep_trades(trades)
        ov = _overall(trades, eq)
        rows = _window_rows(trades, win_info)
        ok, n, ds_ok, n_ds = _pf_counts(rows)
        results.append(dict(sl=sl, tp=tp,
                            pf=ov["pf"], ret=ov["ret"],
                            dd=ov["dd"], calmar=ov["calmar"],
                            win_ok=ok, n_all=n, ds_ok=ds_ok, n_ds=n_ds))
        print(f"  PF={ov['pf']:.3f}  Calmar={ov['calmar']:.1f}  "
              f"win={ok}/{n}  ({time.time()-t0:.1f}s)", flush=True)

    return results


def print_sensitivity(results):
    print()
    print("=" * 90)
    print(" PART 1 — PARAMETER SENSITIVITY  (27-window, spread=0.50, sorted by Calmar)")
    print(f"  SL range: {SL_GRID}   TP range: {TP_GRID}")
    print("=" * 90)
    print(f"  {'SL':>5} {'TP':>5} | {'PF':>6} {'Ret%':>8} {'MaxDD%':>7} {'Calmar':>8} "
          f"| {'win/27':>7} {'D+S':>6}")
    print("  " + "-" * 78)
    for r in sorted(results, key=lambda x: x["calmar"], reverse=True):
        star = " ⭐ Mix_A" if r["sl"] == MIX_A_SL and r["tp"] == MIX_A_TP else ""
        print(f"  {r['sl']:>5.1f} {r['tp']:>5.1f} | {r['pf']:>6.3f} "
              f"{r['ret']:>+7.0f}% {r['dd']:>6.1f}% {r['calmar']:>8.1f} "
              f"| {r['win_ok']:>3}/{r['n_all']:<3} {r['ds_ok']:>3}/{r['n_ds']:<3}{star}")
    print("=" * 90)

    # Plateau summary: how many combos within 5% of Mix_A Calmar?
    mix_a = next((r for r in results if r["sl"] == MIX_A_SL and r["tp"] == MIX_A_TP), None)
    if mix_a:
        threshold = mix_a["calmar"] * 0.95
        plateau = [r for r in results if r["calmar"] >= threshold]
        print(f"\n  Plateau (Calmar ≥ {threshold:.1f} = 95% ของ Mix_A {mix_a['calmar']:.1f}): "
              f"{len(plateau)}/{len(results)} combos")
        if len(plateau) >= 3:
            print("  ✅ Mix_A ตั้งอยู่บน plateau กว้าง — ไม่ใช่ overfit จุดเดียว")
        else:
            print("  ⚠️  Mix_A ตั้งอยู่ใกล้ยอดแหลม — ระวัง parameter sensitivity สูง")


# =============================================================================
# Part 2 — Stress test
# =============================================================================
def run_stress(d, strat, win_info):
    results = []
    N = len(SCENARIOS)
    print(f"\n  รัน {N} scenarios สำหรับ Mix_A (SL={MIX_A_SL}, TP={MIX_A_TP}) ...\n")
    for idx, sc in enumerate(SCENARIOS, 1):
        eff_spread = sc["spread"] + sc["slippage"]   # slippage = wider effective spread
        print(f"  [{idx}/{N}] {sc['name']}  (effective spread={eff_spread:.2f})", end="", flush=True)
        t0 = time.time()
        trades, eq = _run(d, strat, MIX_A_SL, MIX_A_TP, eff_spread, BASE_COMM)
        _prep_trades(trades)
        ov = _overall(trades, eq)
        rows = _window_rows(trades, win_info)
        ok, n, ds_ok, n_ds = _pf_counts(rows)
        results.append(dict(
            name   = sc["name"],
            spread = sc["spread"],
            slip   = sc["slippage"],
            eff    = eff_spread,
            pf=ov["pf"], ret=ov["ret"], dd=ov["dd"], calmar=ov["calmar"],
            win_ok=ok, n_all=n, ds_ok=ds_ok, n_ds=n_ds,
        ))
        print(f"  PF={ov['pf']:.3f}  Ret={ov['ret']:+.0f}%  Calmar={ov['calmar']:.1f}  "
              f"win={ok}/{n}  ({time.time()-t0:.1f}s)", flush=True)
    return results


def print_stress(results):
    print()
    print("=" * 90)
    print(" PART 2 — STRESS TEST  (Mix_A SL=2.5 TP=7.0 — 3 cost scenarios)")
    print("=" * 90)
    print(f"  {'Scenario':<36} | {'PF':>6} {'Ret%':>8} {'MaxDD%':>7} {'Calmar':>8} "
          f"| {'win/27':>7} {'D+S':>6}")
    print("  " + "-" * 84)
    for r in results:
        print(f"  {r['name']:<36} | {r['pf']:>6.3f} {r['ret']:>+7.0f}% "
              f"{r['dd']:>6.1f}% {r['calmar']:>8.1f} "
              f"| {r['win_ok']:>3}/{r['n_all']:<3} {r['ds_ok']:>3}/{r['n_ds']:<3}")
    print("=" * 90)

    if len(results) >= 2:
        base = results[0]
        print("\n  Degradation vs Normal:")
        for r in results[1:]:
            dpf  = r["pf"]    - base["pf"]
            dret = r["ret"]   - base["ret"]
            dcal = r["calmar"]- base["calmar"]
            dwin = r["win_ok"]- base["win_ok"]
            still_edge = "✅ PF>1" if r["pf"] > 1.0 else "❌ PF≤1 — edge หายแล้ว"
            print(f"    {r['name']}: ΔPF={dpf:+.3f}  ΔRet={dret:+.0f}%  "
                  f"ΔCalmar={dcal:+.1f}  Δwin={dwin:+d}  → {still_edge}")
    print()


# =============================================================================
# Part 3 — Detailed 27-window breakdown
# =============================================================================
def print_window_detail(trades, win_info, label="Mix_A 2.5/7.0"):
    _prep_trades(trades)
    rows = _window_rows(trades, win_info)

    print()
    print("=" * 100)
    print(f" PART 3 — DETAILED 27-WINDOW BREAKDOWN  [{label}]  spread={BASE_SPREAD}")
    print("=" * 100)
    print(f"  {'#':>3} {'Period':>14}  {'Regime':>8}  {'Gold%':>6}  "
          f"{'#T':>5}  {'PF':>7}  {'Win%':>6}  {'Ret%':>7}  {'MaxDD%':>7}  {'Pass':>5}")
    print("  " + "-" * 92)

    REGIME_SYM = {"UP": "↑ UP  ", "DOWN": "↓ DOWN", "SIDEWAYS": "↔ SWY "}

    for i, r in enumerate(rows, 1):
        if r["trades"] == 0:
            print(f"  {i:>3} {r['start'].strftime('%Y-%m')}→{r['end'].strftime('%Y-%m')}  "
                  f"  {REGIME_SYM.get(r['regime'], r['regime']):>8}  "
                  f"{r['gold_ret']:>+5.1f}%  {'—':>5}  {'—':>7}  {'—':>6}  {'—':>7}  {'—':>7}   skip")
            continue

        pf_s  = ("   ∞  " if r["pf"] is not None and math.isinf(r["pf"])
                 else f"{r['pf']:>7.3f}" if r["pf"] is not None else "   n/a ")
        wr_s  = f"{r['win_rate']:.0%}" if r["win_rate"] is not None else "  n/a"
        ret_s = f"{r['ret_pct']:>+6.1f}" if r["ret_pct"] is not None else "   n/a"
        dd_s  = f"{r['dd_pct']:>6.1f}" if r["dd_pct"] is not None else "  n/a"
        pass_s = ("  ✅" if _pf_ok(r["pf"]) else "  ❌")

        print(f"  {i:>3} {r['start'].strftime('%Y-%m')}→{r['end'].strftime('%Y-%m')}  "
              f"{REGIME_SYM.get(r['regime'], r['regime']):>8}  "
              f"{r['gold_ret']:>+5.1f}%  {r['trades']:>5}  {pf_s}  "
              f"{wr_s:>6}  {ret_s}%  {dd_s}%  {pass_s}")

    ok, n, ds_ok, n_ds = _pf_counts(rows)
    print("  " + "-" * 92)
    print(f"  TOTAL:  PF>1 in {ok}/{n} windows  |  DOWN+SIDEWAYS: {ds_ok}/{n_ds}")

    # Regime sub-summary with ASCII bar
    print()
    print("  Regime sub-summary:")
    by_rg: dict = {}
    for r in rows:
        if r["trades"] == 0:
            continue
        rg = r["regime"]
        by_rg.setdefault(rg, {"ok": 0, "n": 0, "ret": []})
        by_rg[rg]["n"] += 1
        if _pf_ok(r["pf"]):
            by_rg[rg]["ok"] += 1
        if r["ret_pct"] is not None:
            by_rg[rg]["ret"].append(r["ret_pct"])

    for rg in ["UP", "DOWN", "SIDEWAYS"]:
        v = by_rg.get(rg)
        if not v:
            continue
        bar     = "█" * v["ok"] + "░" * (v["n"] - v["ok"])
        avg_ret = sum(v["ret"]) / len(v["ret"]) if v["ret"] else 0
        print(f"    {rg:<10}  {bar}  {v['ok']}/{v['n']} PF>1  avg window ret={avg_ret:+.1f}%")

    print("=" * 100)


# =============================================================================
# Part 4 — Heatmap
# =============================================================================
def plot_heatmap(results):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

    except ImportError:
        print("\n  [heatmap] matplotlib ไม่ได้ติดตั้ง — ข้าม")
        return

    sl_vals = sorted(set(r["sl"] for r in results))
    tp_vals = sorted(set(r["tp"] for r in results))
    ni, nj  = len(sl_vals), len(tp_vals)

    metrics = [
        ("calmar", "Calmar Ratio", "RdYlGn"),
        ("pf",     "Profit Factor", "RdYlGn"),
        ("win_ok", "Windows PF>1 (of 27)", "Blues"),
    ]

    fig, axes = plt.subplots(1, 3, figsize=(18, 5))

    for ax, (met, label, cmap) in zip(axes, metrics):
        mat = np.full((ni, nj), float("nan"))
        for r in results:
            i = sl_vals.index(r["sl"])
            j = tp_vals.index(r["tp"])
            mat[i][j] = r[met]

        vmin = np.nanpercentile(mat, 5)
        vmax = np.nanpercentile(mat, 95)
        im = ax.imshow(mat, aspect="auto", cmap=cmap,
                       vmin=vmin, vmax=vmax, origin="upper")
        fig.colorbar(im, ax=ax, shrink=0.85)

        ax.set_xticks(range(nj))
        ax.set_xticklabels([f"{v:.1f}" for v in tp_vals], fontsize=9)
        ax.set_yticks(range(ni))
        ax.set_yticklabels([f"{v:.1f}" for v in sl_vals], fontsize=9)
        ax.set_xlabel("TP (× ATR)", fontsize=11)
        ax.set_ylabel("SL (× ATR)", fontsize=11)
        ax.set_title(label, fontsize=12)

        for i in range(ni):
            for j in range(nj):
                val = mat[i][j]
                if math.isnan(val):
                    continue
                is_mix_a = (sl_vals[i] == MIX_A_SL and tp_vals[j] == MIX_A_TP)
                text = f"★\n{val:.1f}" if is_mix_a else f"{val:.1f}"
                color = "black"
                ax.text(j, i, text, ha="center", va="center",
                        fontsize=8, color=color,
                        fontweight="bold" if is_mix_a else "normal")

        # Highlight Mix_A cell border
        mix_i = sl_vals.index(MIX_A_SL)
        mix_j = tp_vals.index(MIX_A_TP)
        rect = plt.Rectangle((mix_j - 0.5, mix_i - 0.5), 1, 1,
                              linewidth=2.5, edgecolor="blue", facecolor="none")
        ax.add_patch(rect)

    fig.suptitle(
        f"Mix_A Neighbourhood Heatmap — XAUUSD M15 (27-window walk-forward, IS 2013-2026)\n"
        f"SL {SL_GRID[0]}–{SL_GRID[-1]}, TP {TP_GRID[0]}–{TP_GRID[-1]}, "
        f"spread={BASE_SPREAD}, risk={RISK_PCT}%  |  ★ = Mix_A (2.5/7.0)",
        fontsize=11, y=1.02,
    )
    fig.tight_layout()

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "mix_a_sensitivity_heatmap.png")
    fig.savefig(out, dpi=150, bbox_inches="tight")
    print(f"\n  [heatmap] บันทึกแล้ว → {out}")
    plt.close(fig)


# =============================================================================
# Main
# =============================================================================
def main():
    global BASE_SPREAD, BASE_COMM
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--spread", type=float, default=0.50,
                    help="Base spread in USD/oz (default 0.50)")
    ap.add_argument("--commission", type=float, default=3.50,
                    help="Commission per lot per side (default 3.50)")
    ap.add_argument("--skip-sensitivity", action="store_true",
                    help="ข้าม Part 1 (sensitivity grid)")
    ap.add_argument("--skip-stress", action="store_true",
                    help="ข้าม Part 2 (stress test)")
    ap.add_argument("--no-heatmap", action="store_true",
                    help="ไม่ plot heatmap (Part 1 ยังรัน)")
    args = ap.parse_args()

    BASE_SPREAD = args.spread
    BASE_COMM   = args.commission

    t_total = time.time()
    print()
    print("=" * 90)
    print(" mix_a_sensitivity_stress.py — Mix_A (SL=2.5, TP=7.0) Deep Analysis")
    print("=" * 90)
    print(f"  Base cost : spread={BASE_SPREAD}  comm=${BASE_COMM}/lot/side  risk={RISK_PCT}%")
    print(f"  Grid      : SL {SL_GRID} × TP {TP_GRID}  ({len(SL_GRID)*len(TP_GRID)} combos)")
    print(f"  Scenarios : {len(SCENARIOS)} (Normal / Tough / Extreme)")
    print(f"  Windows   : {WINDOW_MONTHS}-month × 27")

    # ── Load CSV once ─────────────────────────────────────────────────────────
    print("\n  [load] อ่าน CSV ครั้งเดียว ...", flush=True)
    loader = DataLoader(log_fn=lambda *a, **k: None)
    cfg0 = ForexConfig(); cfg0.total_capital_usd = START
    t0 = time.time()
    df, _ = loader.load("XAUUSD", 99.0, cfg0, csv_path=args.csv, allow_synthetic=True)
    print(f"  [load] {len(df):,} bars  "
          f"{df['timestamp'].iloc[0]} → {df['timestamp'].iloc[-1]}  "
          f"({time.time()-t0:.1f}s)", flush=True)

    d = prepare_data(df)
    if d is None:
        print("[ERROR] prepare_data failed"); sys.exit(1)

    # ── Windows + regimes ─────────────────────────────────────────────────────
    windows  = build_windows(df["timestamp"].iloc[0], df["timestamp"].iloc[-1], WINDOW_MONTHS)
    win_info = []
    for (ws, we) in windows:
        ret = gold_return_pct(df, ws, we)
        win_info.append(dict(start=ws, end=we, gold_ret=ret, regime=regime_tag(ret)))
    regime_counts = {}
    for wi in win_info:
        regime_counts[wi["regime"]] = regime_counts.get(wi["regime"], 0) + 1
    print(f"  [windows] {len(windows)} windows  "
          + "  ".join(f"{k}:{v}" for k, v in sorted(regime_counts.items())),
          flush=True)

    # ── Precompute H1-trend/EMA20 once ───────────────────────────────────────
    print("  [precompute] H1-trend + EMA20 ครั้งเดียว ...", flush=True)
    strat = FastHybridTrendPullback()
    strat.precompute(d)
    print("  [precompute] เสร็จ", flush=True)
    print()

    # ── Part 1: Sensitivity ───────────────────────────────────────────────────
    sens_results = []
    if not args.skip_sensitivity:
        print("─" * 90)
        print(" PART 1 — Parameter Sensitivity")
        print("─" * 90)
        sens_results = run_sensitivity(d, strat, win_info)
        print_sensitivity(sens_results)
        if not args.no_heatmap:
            plot_heatmap(sens_results)

    # ── Part 2: Stress test ───────────────────────────────────────────────────
    if not args.skip_stress:
        print()
        print("─" * 90)
        print(" PART 2 — Stress Test")
        print("─" * 90)
        stress_results = run_stress(d, strat, win_info)
        print_stress(stress_results)

    # ── Part 3: Detailed 27-window for Mix_A ─────────────────────────────────
    print("─" * 90)
    print(" PART 3 — Detailed 27-Window Breakdown  [Mix_A SL=2.5 TP=7.0]")
    print("─" * 90)
    trades_detail, _ = _run(d, strat, MIX_A_SL, MIX_A_TP, BASE_SPREAD, BASE_COMM)
    print_window_detail(trades_detail, win_info, f"Mix_A  SL={MIX_A_SL} TP={MIX_A_TP}")

    print(f"\n  เสร็จทั้งหมดใน {time.time()-t_total:.1f}s")
    print("  ⚠️  IN-SAMPLE ทั้งชุด — ต้องผ่าน OOS split + demo forward-test ก่อนใช้จริง")
    print()


if __name__ == "__main__":
    main()
