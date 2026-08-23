#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 walk_forward_regime.py — Regime-Stratified Walk-Forward Analysis
================================================================================
 จุดประสงค์: หาว่า exit variants (A_current/B_fixed/C_wider/D_letrun — entry/
 trend logic เหมือนกันทุก variant, ใช้ HybridTrendPullback.signal() ตัวเดียวกัน)
 มี edge "จริง" ในทุก market regime ของทอง (UP / DOWN / SIDEWAYS) หรือกำไรที่
 เห็นเป็นแค่ "gold beta" (กำไรเฉพาะตอนทองขาขึ้น มาจาก LONG เป็นหลัก)

 ไม่ปรับพารามิเตอร์ต่อ window. ไม่ tune ไปหา return target ใดๆ — รายงานความจริง
 แม้ผลจะออกมาว่า "ไม่มี variant ไหน robust" ก็ตาม.

 EFFICIENT APPROACH:
   รันแต่ละ variant "ครั้งเดียว" over full history (ใช้ FastHybridTrendPullback —
   เหมือน HybridTrendPullback ทุก bit แต่ precompute EMA20 array ครั้งเดียว
   แทน O(n^2) ของ original — verified identical output) แล้วเก็บทุก trade
   (entry_ts, side, net_pnl, equity_after, ...) จากนั้นแบ่ง trades ออกเป็น
   window ปฏิทินละ N เดือน (default 6) — ไม่ re-run sim ต่อ window.

 FIXED-NOTIONAL PER-WINDOW METRICS:
   single full run มี equity compound จริง (lot size โตขึ้นเรื่อยๆ ตาม equity)
   เพื่อไม่ให้ window หลังๆ (equity โตแล้ว) ดู "ใหญ่" เกินจริงเทียบ window แรกๆ
   เรา normalize net_pnl ของแต่ละ trade กลับไปที่ momentum ของ $10k เริ่มต้น:
       norm_pnl = net_pnl * (start_cash / equity_before_trade)
   แล้วใช้ norm_pnl ในการคำนวณ PF / expectancy / win-rate / max-DD / long-short
   ของแต่ละ window — windows จึงเทียบกันได้ตรงๆ โดยไม่ถูกบิดจาก compounding.

 REGIME TAG (gold close-to-close return ตลอด window):
   UP        : > +6%
   DOWN      : < -6%
   SIDEWAYS  : อื่นๆ

 HONEST VERDICT:
   - "ROBUST EDGE"            : PF>1 ใน majority ของ windows ทั้งหมด *และ*
                                 ใน majority ของ DOWN+SIDEWAYS windows ด้วย
   - "GOLD BETA, NOT STRATEGY EDGE" : PF>1 ส่วนใหญ่อยู่ใน UP windows และกำไร
                                 รวมเกือบทั้งหมดมาจาก LONG (ไม่ใช่ SHORT)
   - อื่นๆ                     : รายงานตามจริง (mixed / no edge)

 วิธีรัน:
   python3 walk_forward_regime.py \\
       --csv download/xauusd-m15-bid-2013-01-01-2026-06-10.csv \\
       --start-cash 10000 --spread-price 0.10 --commission-per-lot 3.50
================================================================================
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from multiprocessing import Pool
from typing import Optional

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forex_config import ForexConfig
from backtest_forex import (DataLoader, prepare_data, BacktestEngine,
                             FastHybridTrendPullback, compute_metrics)


# =============================================================================
# Variant definitions — entry/trend IDENTICAL (HybridTrendPullback.signal()),
# ต่างกันแค่ exit rules. (ตรงกับ oos_test.py)
# =============================================================================
VARIANTS = {
    "A_current": dict(
        desc="partial TP@1.5ATR(50%) + breakeven + ATR trail (baseline ปัจจุบัน)",
        sl_atr=1.5, tp_atr=3.0,
        partial_tp_atr=1.5, partial_tp_frac=0.5, move_sl_to_breakeven=True,
        trail_atr_mult=2.5, trail_activation_atr=0.5,
    ),
    "B_fixed": dict(
        desc="ไม่มี partial, ไม่มี trail — SL=1.5ATR TP=3.0ATR (1:2 สะอาด)",
        sl_atr=1.5, tp_atr=3.0,
        partial_tp_atr=999.0, partial_tp_frac=0.0, move_sl_to_breakeven=False,
        trail_atr_mult=999.0, trail_activation_atr=999.0,
    ),
    "C_wider": dict(
        desc="ไม่มี partial, ไม่มี trail — SL=2.5ATR TP=5.0ATR (1:2 กว้างขึ้น)",
        sl_atr=2.5, tp_atr=5.0,
        partial_tp_atr=999.0, partial_tp_frac=0.0, move_sl_to_breakeven=False,
        trail_atr_mult=999.0, trail_activation_atr=999.0,
    ),
    "D_letrun": dict(
        desc="ไม่มี partial — SL=2.0ATR TP=6.0ATR (1:3), trail เริ่มหลัง +2ATR",
        sl_atr=2.0, tp_atr=6.0,
        partial_tp_atr=999.0, partial_tp_frac=0.0, move_sl_to_breakeven=False,
        trail_atr_mult=2.5, trail_activation_atr=2.0,
    ),
}


# =============================================================================
# Worker — รัน variant เดียว ONCE over full history
# =============================================================================
def _run_variant(task: dict) -> dict:
    variant_name = task["variant"]
    vparams      = VARIANTS[variant_name]

    loader = DataLoader(log_fn=lambda *a, **k: None)
    df, _ = loader.load(task["symbol"], 99.0, ForexConfig(),
                         csv_path=task["csv"], allow_synthetic=True)
    d = prepare_data(df)

    cfg = ForexConfig()
    cfg.total_capital_usd    = task["start_cash"]
    cfg.risk_per_trade_pct   = task["risk"]
    cfg.symbols              = [task["symbol"]]
    cfg.partial_tp_atr       = vparams["partial_tp_atr"]
    cfg.partial_tp_frac      = vparams["partial_tp_frac"]
    cfg.move_sl_to_breakeven = vparams["move_sl_to_breakeven"]

    strategy = FastHybridTrendPullback()
    strategy.sl_atr               = vparams["sl_atr"]
    strategy.tp_atr               = vparams["tp_atr"]
    strategy.trail_atr_mult       = vparams["trail_atr_mult"]
    strategy.trail_activation_atr = vparams["trail_activation_atr"]

    engine = BacktestEngine(d, cfg, strategy,
                             spread_price=task["spread_price"],
                             commission_per_lot=task["commission_per_lot"],
                             symbol=task["symbol"])
    engine.run(quiet=True)   # full history, ONE pass

    overall = compute_metrics(engine.trades, engine.equity_curve, cfg.total_capital_usd)

    return dict(
        variant=variant_name,
        trades=engine.trades,
        reconcile_diff=engine.reconcile_diff,
        n_trades=len(engine.trades),
        skipped_risk_cap=engine.skipped_risk_cap,
        overall=overall,
    )


# =============================================================================
# Window helpers
# =============================================================================
def build_windows(ts_start: pd.Timestamp, ts_end: pd.Timestamp,
                   months: int) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """สร้าง consecutive calendar windows ขนาด `months` เดือน
    เริ่มจากวันที่ 1 ของเดือนแรกของข้อมูล จนถึง ts_end (window สุดท้ายอาจสั้นกว่า)."""
    cur = pd.Timestamp(year=ts_start.year, month=ts_start.month, day=1)
    windows = []
    while cur < ts_end:
        nxt = cur + pd.DateOffset(months=months)
        windows.append((cur, nxt))
        cur = nxt
    return windows


def gold_return_pct(df: pd.DataFrame, w_start: pd.Timestamp,
                     w_end: pd.Timestamp) -> Optional[float]:
    mask = (df["timestamp"] >= w_start) & (df["timestamp"] < w_end)
    sub = df.loc[mask]
    if len(sub) < 2:
        return None
    p0 = float(sub["close"].iloc[0])
    p1 = float(sub["close"].iloc[-1])
    if p0 == 0:
        return None
    return (p1 - p0) / p0 * 100.0


def regime_tag(ret_pct: Optional[float]) -> str:
    if ret_pct is None:
        return "N/A"
    if ret_pct > 6.0:
        return "UP"
    if ret_pct < -6.0:
        return "DOWN"
    return "SIDEWAYS"


def window_metrics(wtrades: list[dict], start_cash: float) -> dict:
    """คำนวณ metrics ของ window จาก norm_pnl (fixed-notional, ไม่ compound)."""
    n = len(wtrades)
    if n == 0:
        return dict(trades=0, pf=None, expectancy=None, win_rate=None,
                     max_dd_pct=0.0, long_pnl=0.0, short_pnl=0.0)

    pnls = [t["_norm_pnl"] for t in wtrades]
    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    sum_wins = sum(wins)
    sum_loss = abs(sum(losses))

    if sum_loss > 0:
        pf = sum_wins / sum_loss
    elif sum_wins > 0:
        pf = float("inf")
    else:
        pf = 0.0

    win_rate = len(wins) / n
    expectancy = sum(pnls) / n

    eq = start_cash
    peak = start_cash
    max_dd_pct = 0.0
    for p in pnls:
        eq += p
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak * 100.0 if peak > 0 else 0.0
        max_dd_pct = max(max_dd_pct, dd)

    long_pnl  = sum(t["_norm_pnl"] for t in wtrades if t["side"] == "long")
    short_pnl = sum(t["_norm_pnl"] for t in wtrades if t["side"] == "short")

    return dict(trades=n, pf=pf, expectancy=expectancy, win_rate=win_rate,
                 max_dd_pct=max_dd_pct, long_pnl=long_pnl, short_pnl=short_pnl)


def fmt_pf(pf: Optional[float]) -> str:
    if pf is None:
        return "  n/a "
    if math.isinf(pf):
        return "  inf "
    return f"{pf:6.3f}"


# =============================================================================
# Main
# =============================================================================
def main():
    ap = argparse.ArgumentParser(
        description="Regime-stratified walk-forward analysis (UP/DOWN/SIDEWAYS)")
    ap.add_argument("--csv", required=True, help="CSV path (M15 OHLCV, real data)")
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--start-cash", type=float, default=10_000.0)
    ap.add_argument("--risk", type=float, default=0.30, help="Risk %% per trade")
    ap.add_argument("--spread-price", type=float, default=0.10,
                    help="Real Exness spread (price units, e.g. $0.10 for XAUUSD)")
    ap.add_argument("--commission-per-lot", type=float, default=3.50,
                    help="Real Exness commission per lot per side ($)")
    ap.add_argument("--window-months", type=int, default=6)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    # ── Load once in main process for banner / windows / gold returns ──
    loader = DataLoader(log_fn=print)
    df, data_source = loader.load(args.symbol, 99.0, ForexConfig(),
                                   csv_path=args.csv, allow_synthetic=True)
    n = len(df)

    print()
    print("=" * 96)
    print(" REGIME-STRATIFIED WALK-FORWARD ANALYSIS (UP / DOWN / SIDEWAYS)")
    print("=" * 96)
    print(f"  DATA SOURCE : {data_source}")
    print(f"  Symbol      : {args.symbol}")
    print(f"  Bars        : {n:,}")
    print(f"  Date range  : {df['timestamp'].iloc[0]}  ->  {df['timestamp'].iloc[-1]}")
    print(f"  Cost model  : spread={args.spread_price}  "
          f"commission=${args.commission_per_lot}/lot/side  "
          f"risk={args.risk}%/trade  cash=${args.start_cash:,.0f}")
    print(f"  Window size : {args.window_months} months  "
          f"(REGIME: UP>+6%, DOWN<-6%, else SIDEWAYS — gold close-to-close)")
    print("=" * 96)

    windows = build_windows(df["timestamp"].iloc[0], df["timestamp"].iloc[-1],
                             args.window_months)

    tasks = [dict(variant=v, csv=args.csv, symbol=args.symbol,
                   start_cash=args.start_cash, risk=args.risk,
                   spread_price=args.spread_price,
                   commission_per_lot=args.commission_per_lot)
             for v in VARIANTS]

    print(f"\n  Running {len(tasks)} variants (full-history, single pass each) "
          f"with {args.workers} workers ...\n")

    with Pool(processes=args.workers) as pool:
        results = pool.map(_run_variant, tasks)
    results_by = {r["variant"]: r for r in results}

    # ── Pre-compute window gold returns / regime tags (shared across variants) ──
    win_info = []
    for (w_start, w_end) in windows:
        ret = gold_return_pct(df, w_start, w_end)
        win_info.append(dict(start=w_start, end=w_end, gold_ret=ret,
                              regime=regime_tag(ret)))

    overall_verdicts = {}

    for variant_name, vparams in VARIANTS.items():
        r = results_by[variant_name]
        trades = r["trades"]

        print("-" * 96)
        print(f" Variant {variant_name}: {vparams['desc']}")
        print(f"   SL={vparams['sl_atr']}xATR  TP={vparams['tp_atr']}xATR  "
              f"partial_tp_atr={vparams['partial_tp_atr']} "
              f"frac={vparams['partial_tp_frac']} "
              f"breakeven={vparams['move_sl_to_breakeven']}  "
              f"trail_mult={vparams['trail_atr_mult']} "
              f"trail_act={vparams['trail_activation_atr']}")
        ov = r["overall"]
        print(f"   Total trades (full history): {r['n_trades']:,}   "
              f"[RECONCILE] diff={r['reconcile_diff']:.6f}   "
              f"skipped_risk_cap={r['skipped_risk_cap']}")
        if ov.get("trades", 0) > 0:
            print(f"   FULL-HISTORY (compounded, single run): "
                  f"PF={ov['profit_factor']:.3f}  "
                  f"WinRate={ov['win_rate']:.1%}  "
                  f"Expectancy=${ov['expectancy_usd']:+.2f}/trade  "
                  f"TotalReturn={ov['total_return_pct']:+.1f}%  "
                  f"MaxDD={ov['max_dd_pct']:.1f}%  "
                  f"Final Equity=${ov['final_equity']:,.2f}")
        print("-" * 96)

        if not trades:
            print("   [WARN] ไม่มี trade เลยตลอดประวัติศาสตร์ — ข้าม variant นี้")
            print()
            continue

        # ── normalize each trade's PnL to fixed $start_cash notional ──
        equity_before = args.start_cash
        for t in trades:
            t["_equity_before"] = equity_before
            t["_norm_pnl"] = (t["net_pnl"] * (args.start_cash / equity_before)
                              if equity_before > 0 else t["net_pnl"])
            t["_entry_dt"] = pd.Timestamp(t["entry_ts"])
            equity_before = t["equity_after"]

        # ── header ──
        print(f"   {'Window':23} {'GoldRet%':>9} {'Regime':>8} {'PF':>7} "
              f"{'Expectncy':>10} {'WinRate':>8} {'MaxDD%':>8} {'#Trades':>8} "
              f"{'LongPnL':>10} {'ShortPnL':>10}")

        rows = []
        for wi in win_info:
            wtrades = [t for t in trades if wi["start"] <= t["_entry_dt"] < wi["end"]]
            m = window_metrics(wtrades, args.start_cash)
            rows.append((wi, m))

            label = f"{wi['start'].date()}~{wi['end'].date()}"
            gr = wi["gold_ret"]
            gr_s = f"{gr:+.1f}%" if gr is not None else "n/a"
            exp_s = f"${m['expectancy']:+.2f}" if m["expectancy"] is not None else "   n/a"
            wr_s  = f"{m['win_rate']:.1%}" if m["win_rate"] is not None else "  n/a"
            print(f"   {label:23} {gr_s:>9} {wi['regime']:>8} {fmt_pf(m['pf']):>7} "
                  f"{exp_s:>10} {wr_s:>8} {m['max_dd_pct']:7.1f}% {m['trades']:>8} "
                  f"${m['long_pnl']:>+9.2f} ${m['short_pnl']:>+9.2f}")

        # ── per-variant summary ──
        print()
        regime_counts  = {"UP": 0, "DOWN": 0, "SIDEWAYS": 0}
        regime_pf_gt1  = {"UP": 0, "DOWN": 0, "SIDEWAYS": 0}
        ds_long_total  = 0.0
        ds_short_total = 0.0
        up_long_total  = 0.0
        up_short_total = 0.0
        for wi, m in rows:
            if m["trades"] == 0 or wi["regime"] == "N/A":
                continue
            reg = wi["regime"]
            regime_counts[reg] += 1
            pf = m["pf"]
            if pf is not None and pf > 1.0:
                regime_pf_gt1[reg] += 1
            if reg in ("DOWN", "SIDEWAYS"):
                ds_long_total  += m["long_pnl"]
                ds_short_total += m["short_pnl"]
            else:  # UP
                up_long_total  += m["long_pnl"]
                up_short_total += m["short_pnl"]

        total_n   = sum(regime_counts.values())
        total_win = sum(regime_pf_gt1.values())
        ds_n      = regime_counts["DOWN"] + regime_counts["SIDEWAYS"]
        ds_win    = regime_pf_gt1["DOWN"] + regime_pf_gt1["SIDEWAYS"]

        print(f"   Windows with trades: {total_n}   "
              f"(UP={regime_counts['UP']}, DOWN={regime_counts['DOWN']}, "
              f"SIDEWAYS={regime_counts['SIDEWAYS']})")
        print(f"   Windows with PF>1  : {total_win}/{total_n}   "
              f"(UP={regime_pf_gt1['UP']}/{regime_counts['UP']}, "
              f"DOWN={regime_pf_gt1['DOWN']}/{regime_counts['DOWN']}, "
              f"SIDEWAYS={regime_pf_gt1['SIDEWAYS']}/{regime_counts['SIDEWAYS']})")
        print(f"   DOWN+SIDEWAYS windows: {ds_n}   PF>1 in {ds_win}/{ds_n}")
        print(f"   DOWN+SIDEWAYS PnL (norm $10k):  LONG=${ds_long_total:+,.2f}   "
              f"SHORT=${ds_short_total:+,.2f}")
        print(f"   UP windows PnL       (norm $10k):  LONG=${up_long_total:+,.2f}   "
              f"SHORT=${up_short_total:+,.2f}")

        # ── per-variant verdict ──
        majority_all = total_n > 0 and total_win > total_n / 2.0
        majority_ds  = ds_n > 0 and ds_win > ds_n / 2.0
        total_long   = ds_long_total + up_long_total
        total_short  = ds_short_total + up_short_total
        total_profit = total_long + total_short

        if majority_all and majority_ds and ds_n > 0:
            verdict = "✅ ROBUST EDGE — PF>1 in majority of ALL windows, " \
                      "including DOWN+SIDEWAYS"
        elif majority_all and not majority_ds:
            long_dominates = (total_profit > 0 and total_long > 0 and
                              total_long >= 0.8 * total_profit and
                              up_long_total > 0)
            if long_dominates:
                verdict = "⚠️  GOLD BETA, NOT STRATEGY EDGE — PF>1 mostly in UP " \
                          "windows, profit driven by LONG during gold uptrends"
            else:
                verdict = "🟡 MIXED — PF>1 in majority overall but NOT in " \
                          "DOWN+SIDEWAYS majority (regime-dependent, not robust)"
        elif ds_n == 0:
            verdict = "🟡 INCONCLUSIVE — no DOWN/SIDEWAYS windows in this dataset " \
                      "to test against"
        else:
            verdict = "❌ NO EDGE — PF<=1 in majority of windows overall"

        overall_verdicts[variant_name] = dict(
            majority_all=majority_all, majority_ds=majority_ds, ds_n=ds_n,
            total_n=total_n, total_win=total_win, ds_win=ds_win,
            verdict=verdict)

        print(f"   VERDICT: {verdict}")
        print()

    # ── overall honest summary ──
    print("=" * 96)
    print(" HONEST VERDICT — ALL VARIANTS")
    print("=" * 96)
    any_robust = False
    for variant_name, v in overall_verdicts.items():
        print(f"  {variant_name:12} windows(all)={v['total_win']}/{v['total_n']}  "
              f"windows(DOWN+SIDEWAYS)={v['ds_win']}/{v['ds_n']}")
        print(f"               -> {v['verdict']}")
        if v["verdict"].startswith("✅"):
            any_robust = True
    print("-" * 96)
    if any_robust:
        print("  >>> At least one variant shows PF>1 across the majority of windows,")
        print("      INCLUDING down/sideways gold regimes. This is the strongest")
        print("      evidence so far of a real (if thin) edge — still validate with")
        print("      demo forward-testing before risking real capital.")
    else:
        print("  >>> NO variant achieved a PF>1 majority across DOWN+SIDEWAYS regimes.")
        print("      Any apparent profitability is regime-dependent (concentrated in")
        print("      gold uptrend windows) — i.e. likely 'gold beta', not a robust")
        print("      strategy edge. Report this honestly before proceeding to demo.")
    print("=" * 96)


if __name__ == "__main__":
    main()
