#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 backtest_portfolio_lock.py  —  Portfolio-Level Profit/Loss Lock Backtest
================================================================================
 Simulates 3 bots (adx20tp7 M15, m5tp7 M5, adx18tp7 M15) running simultaneously.
 Individual-position SL=3xATR / TP=7xATR remain intact as broker-level safety net.
 Portfolio-level overlay checks combined floating P&L every M5 bar:
   +lock_tp_usd  → close ALL positions immediately (profit-lock)
   -lock_sl_usd  → close ALL positions immediately (loss-lock)
 After a portfolio-level close, all engines resume taking new signals normally.

 Usage (runs 4 scenarios: baseline, TP-only, Sym A, Asym B):
   python3 backtest_portfolio_lock.py \\
       --m5-csv  download/xauusd-m5-bid-2013-01-01-2026-06-01.csv \\
       --m15-csv download/xauusd-m15-bid-2013-01-01-2026-06-10.csv \\
       --lock-usd 100 --lock-sl-usd 200 \\
       --start-cash 10000 --risk 0.30

 Design:
   - M5 bars are the master timeline; M15 engines step only at M15 bar closes.
   - Portfolio lock/stop checks run on every M5 bar using M5 close price.
   - SL/TP for M15 positions are resolved at M15 bar resolution (consistent
     with how the live bot polls).
   - Each bot has independent equity, identical to live setup.
   - Exit reasons: "PortfolioLockTP" | "PortfolioLockSL" | "TP" | "SL" | "EndOfData"
================================================================================
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import time
from typing import List, Optional, Dict, NamedTuple

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forex_config import ForexConfig
from backtest_forex import (DataLoader, prepare_data, BacktestEngine,
                             FastHybridTrendPullback, compute_metrics,
                             save_trades_csv)

# ── Variant definitions ────────────────────────────────────────────────────────
VARIANTS = [
    dict(name="adx20tp7", tf="M15", adx_min=20, sl_atr=3.0, tp_atr=7.0),
    dict(name="m5tp7",    tf="M5",  adx_min=20, sl_atr=3.0, tp_atr=7.0),
    dict(name="adx18tp7", tf="M15", adx_min=18, sl_atr=3.0, tp_atr=7.0),
]


# =============================================================================
# Data structures
# =============================================================================

class ScenarioResult(NamedTuple):
    label:         str                      # e.g. "Baseline", "TP+100", "Sym A"
    engines:       Dict[str, BacktestEngine]
    equity_curve:  List[float]              # combined portfolio curve (empty for baseline)
    lock_tp_events: int
    lock_sl_events: int


# =============================================================================
# Helpers
# =============================================================================

def load_ohlcv(csv_path: str, symbol: str) -> pd.DataFrame:
    """Load CSV → DataFrame with columns: timestamp(datetime), open, high, low, close, volume."""
    loader = DataLoader(log_fn=lambda *a, **k: None)
    cfg_tmp = ForexConfig()
    df, _ = loader.load(symbol, 99.0, cfg_tmp, csv_path=csv_path, allow_synthetic=False)
    return df


def build_config(start_cash: float, risk_pct: float) -> ForexConfig:
    """ForexConfig for tp7 variants — partial TP and trailing disabled."""
    cfg = ForexConfig()
    cfg.total_capital_usd    = start_cash
    cfg.risk_per_trade_pct   = risk_pct
    cfg.partial_tp_atr       = 999.0
    cfg.partial_tp_frac      = 0.0
    cfg.move_sl_to_breakeven = False
    return cfg


def make_strategy(adx_min: int, sl_atr: float, tp_atr: float) -> FastHybridTrendPullback:
    s = FastHybridTrendPullback()
    s.ADX_MIN              = adx_min
    s.sl_atr               = sl_atr
    s.tp_atr               = tp_atr
    s.trail_atr_mult       = 999.0
    s.trail_activation_atr = 999.0
    return s


def floating_pnl(engine: BacktestEngine, current_price: float) -> float:
    """Floating P&L of the open position at current_price."""
    pos = engine.position
    if pos is None:
        return 0.0
    lot  = pos.lot_remaining if pos.partial_closed else pos.lot
    pips = (current_price - pos.entry) / engine.pip_size * pos.direction
    return pips * engine.pip_value * lot


def clip_df_to_range(df: pd.DataFrame,
                     start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    mask = (df["timestamp"] >= start) & (df["timestamp"] <= end)
    return df[mask].reset_index(drop=True)


def calmar(m: dict) -> float:
    dd = m.get("max_dd_pct", 0.0)
    return m.get("ann_return_pct", 0.0) / dd if dd > 0 else 0.0


def combined_equity_from_engines(engines: Dict[str, BacktestEngine],
                                 initial_total: float) -> List[float]:
    """Sum per-engine equity_curves (aligned by shortest length)."""
    curves = [eng.equity_curve for eng in engines.values() if eng.equity_curve]
    if not curves:
        return [initial_total]
    min_len = min(len(c) for c in curves)
    return [sum(c[i] for c in curves) for i in range(min_len)]


# =============================================================================
# Baseline — 3 independent engines, no portfolio overlay
# =============================================================================

def run_baseline(m5_df: pd.DataFrame, m15_df: pd.DataFrame,
                 start_cash: float, risk: float, spread: float,
                 commission: float, symbol: str) -> ScenarioResult:
    engines = {}
    for v in VARIANTS:
        df    = m5_df if v["tf"] == "M5" else m15_df
        d     = prepare_data(df)
        if d is None:
            print(f"  [{v['name']}] not enough data — skip")
            continue
        cfg   = build_config(start_cash, risk)
        strat = make_strategy(v["adx_min"], v["sl_atr"], v["tp_atr"])
        eng   = BacktestEngine(d, cfg, strat,
                               spread_price=spread,
                               commission_per_lot=commission,
                               symbol=symbol)
        print(f"  [{v['name']}] {v['tf']} ADX≥{v['adx_min']} ...")
        eng.run(quiet=True)
        engines[v["name"]] = eng
        print(f"  [{v['name']}] {len(eng.trades)} trades  "
              f"${start_cash:,.0f} → ${eng.equity:,.2f}")
    return ScenarioResult("Baseline", engines, [], 0, 0)


# =============================================================================
# Portfolio Lock Co-simulation
# =============================================================================

class EngineSlot:
    def __init__(self, name: str, tf: str, engine: BacktestEngine):
        self.name   = name
        self.tf     = tf
        self.engine = engine
        self.last_j = 0

    def floating(self, price: float) -> float:
        return floating_pnl(self.engine, price)

    def force_close(self, j: int, price: float, reason: str):
        if self.engine.position is not None:
            self.engine._close(j, price, reason)


def build_m15_boundary_map(m5_ts_dt: pd.DatetimeIndex,
                            m15_ts_dt: pd.DatetimeIndex) -> Dict[int, int]:
    """
    Returns dict: m5_bar_index → m15_bar_index
    Only for M5 bars that are the last bar within a 15-min window (= M15 bar close).
    """
    m15_ts_to_j = {ts: j for j, ts in enumerate(m15_ts_dt)}
    m5_floor    = m5_ts_dt.floor("15min")
    result: Dict[int, int] = {}
    n = len(m5_ts_dt)
    for i in range(n):
        if i == n - 1 or m5_floor[i] != m5_floor[i + 1]:
            j = m15_ts_to_j.get(m5_floor[i])
            if j is not None:
                result[i] = j
    return result


def run_co_simulation(m5_df: pd.DataFrame, m15_df: pd.DataFrame,
                      label: str,
                      lock_tp_usd: Optional[float],
                      lock_sl_usd: Optional[float],
                      start_cash: float, risk: float,
                      spread: float, commission: float,
                      symbol: str) -> ScenarioResult:
    """
    Co-simulate 3 engines on M5 timeline with optional portfolio TP/SL locks.

    lock_tp_usd: close all when combined floating >= +lock_tp_usd  (None = disabled)
    lock_sl_usd: close all when combined floating <= -lock_sl_usd  (None = disabled)
    """
    warm = FastHybridTrendPullback.MIN_BARS

    d_m5  = prepare_data(m5_df)
    d_m15 = prepare_data(m15_df)
    if d_m5 is None or d_m15 is None:
        raise ValueError("Not enough data for co-simulation")

    m5_ts_dt  = pd.to_datetime(d_m5["ts"])
    m15_ts_dt = pd.to_datetime(d_m15["ts"])
    m5_to_m15 = build_m15_boundary_map(m5_ts_dt, m15_ts_dt)

    slots: List[EngineSlot] = []
    for v in VARIANTS:
        d     = d_m5 if v["tf"] == "M5" else d_m15
        cfg   = build_config(start_cash, risk)
        strat = make_strategy(v["adx_min"], v["sl_atr"], v["tp_atr"])
        eng   = BacktestEngine(d, cfg, strat,
                               spread_price=spread,
                               commission_per_lot=commission,
                               symbol=symbol)
        strat.precompute(d)
        slots.append(EngineSlot(v["name"], v["tf"], eng))

    m5_slot   = next(s for s in slots if s.tf == "M5")
    m15_slots = [s for s in slots if s.tf == "M15"]

    n_m5            = len(m5_ts_dt)
    lock_tp_events  = 0
    lock_sl_events  = 0
    equity_curve:   List[float] = []

    tp_str = f"+${lock_tp_usd:.0f}" if lock_tp_usd is not None else "off"
    sl_str = f"-${lock_sl_usd:.0f}" if lock_sl_usd is not None else "off"
    print(f"  [{label}] {n_m5:,} M5 bars | TP-lock={tp_str}  SL-lock={sl_str} ...")
    t0 = time.time()

    for i in range(warm, n_m5 - 1):
        # 1. Step M5 engine
        m5_slot.engine._step(i)

        # 2. Step M15 engines at M15 bar closes
        if i in m5_to_m15:
            j = m5_to_m15[i]
            if j >= warm:
                for s in m15_slots:
                    s.engine._step(j)
                    s.last_j = j

        # 3. Portfolio lock checks (profit-lock takes priority if both triggered)
        m5_close    = float(d_m5["c"][i])
        total_float = sum(s.floating(m5_close) for s in slots)

        if lock_tp_usd is not None and total_float >= lock_tp_usd:
            lock_tp_events += 1
            reason = "PortfolioLockTP"
            m5_slot.force_close(i, m5_close, reason)
            for s in m15_slots:
                s.force_close(max(s.last_j, warm), m5_close, reason)

        elif lock_sl_usd is not None and total_float <= -lock_sl_usd:
            lock_sl_events += 1
            reason = "PortfolioLockSL"
            m5_slot.force_close(i, m5_close, reason)
            for s in m15_slots:
                s.force_close(max(s.last_j, warm), m5_close, reason)

        # 4. Track combined equity
        combined = sum(s.engine.equity + s.floating(m5_close) for s in slots)
        equity_curve.append(combined)

    # Force-close remaining positions at end of data
    last_i   = n_m5 - 2
    last_c   = float(d_m5["c"][last_i])
    last_m15 = len(m15_ts_dt) - 2
    m5_slot.force_close(last_i, last_c, "EndOfData")
    for s in m15_slots:
        s.force_close(min(max(s.last_j, warm), last_m15), last_c, "EndOfData")

    elapsed = time.time() - t0
    total_t = sum(len(s.engine.trades) for s in slots)
    ltp_t   = sum(1 for s in slots for t in s.engine.trades
                  if t["reason"] == "PortfolioLockTP")
    lsl_t   = sum(1 for s in slots for t in s.engine.trades
                  if t["reason"] == "PortfolioLockSL")
    print(f"  [{label}] {total_t} trades | LockTP={ltp_t}({lock_tp_events}ev)  "
          f"LockSL={lsl_t}({lock_sl_events}ev) | {elapsed:.1f}s")

    engines = {s.name: s.engine for s in slots}
    return ScenarioResult(label, engines, equity_curve, lock_tp_events, lock_sl_events)


# =============================================================================
# Reporting
# =============================================================================

def _exit_breakdown(trades: List[dict]) -> Dict[str, tuple]:
    """Returns {reason: (count, sum_pnl)} for all reasons in trades."""
    out: Dict[str, list] = {}
    for t in trades:
        r = t.get("reason", "?")
        if r not in out:
            out[r] = [0, 0.0]
        out[r][0] += 1
        out[r][1] += t["net_pnl"]
    return {k: (v[0], v[1]) for k, v in out.items()}


def _scenario_combined_metrics(sr: ScenarioResult, total_start: float) -> dict:
    all_trades = sorted(
        [t for eng in sr.engines.values() for t in eng.trades],
        key=lambda t: t["entry_ts"]
    )
    total_final = sum(eng.equity for eng in sr.engines.values())
    curve = sr.equity_curve if sr.equity_curve else \
            combined_equity_from_engines(sr.engines, total_start)
    m = compute_metrics(all_trades, curve, total_start)
    return m, all_trades, total_final


def print_report(scenarios: List[ScenarioResult], start_cash: float):
    n_bots      = len(VARIANTS)
    total_start = start_cash * n_bots

    print("\n" + "=" * 90)
    print("  PORTFOLIO LOCK / STOP BACKTEST — MULTI-SCENARIO REPORT")
    print("=" * 90)
    print(f"  Start cash : ${start_cash:,.0f} × {n_bots} bots = ${total_start:,.0f} total")
    print(f"  Scenarios  : {[s.label for s in scenarios]}")

    # ── 1. Per-variant breakdown per scenario ─────────────────────────────────
    print("\n" + "─" * 90)
    print("  PER-VARIANT METRICS")
    print("─" * 90)
    hdr = (f"  {'Variant':<13} {'Scenario':<16} {'Trades':>7} {'Win%':>6} "
           f"{'PF':>6} {'E[$]':>8} {'MaxDD%':>7} {'FinalEq':>10} "
           f"{'LockTP':>7} {'LockSL':>7} {'NormTP':>7} {'NormSL':>7}")
    print(hdr)
    print("  " + "-" * 88)

    for v in VARIANTS:
        name = v["name"]
        for sr in scenarios:
            if name not in sr.engines:
                continue
            eng = sr.engines[name]
            if not eng.trades:
                print(f"  {name:<13} {sr.label:<16} {'0':>7}")
                continue
            m   = compute_metrics(eng.trades, eng.equity_curve, start_cash)
            bd  = _exit_breakdown(eng.trades)
            ltp = bd.get("PortfolioLockTP", (0, 0.0))
            lsl = bd.get("PortfolioLockSL", (0, 0.0))
            ntp = bd.get("TP", (0, 0.0))
            nsl = bd.get("SL", (0, 0.0))
            print(f"  {name:<13} {sr.label:<16} {m['trades']:>7} "
                  f"{m['win_rate']*100:>5.1f}% {m['profit_factor']:>6.2f} "
                  f"${m['expectancy_usd']:>7.2f} {m['max_dd_pct']:>6.2f}% "
                  f"${eng.equity:>9,.0f} "
                  f"{ltp[0]:>7} {lsl[0]:>7} {ntp[0]:>7} {nsl[0]:>7}")
        print()

    # ── 2. Combined portfolio per scenario ────────────────────────────────────
    print("─" * 90)
    print("  COMBINED PORTFOLIO  (all 3 variants summed)")
    print("─" * 90)
    print(f"  {'Scenario':<16} {'Trades':>7} {'Win%':>6} {'PF':>6} "
          f"{'E[$]':>8} {'MaxDD%':>7} {'AnnRet%':>8} {'Calmar':>7} "
          f"{'TotalPnL':>10} {'LockTP-ev':>10} {'LockSL-ev':>10}")
    print("  " + "-" * 88)

    for sr in scenarios:
        m, all_trades, total_final = _scenario_combined_metrics(sr, total_start)
        total_pnl = total_final - total_start
        cal = calmar(m)
        bd  = _exit_breakdown(all_trades)
        ltp = bd.get("PortfolioLockTP", (0, 0.0))
        lsl = bd.get("PortfolioLockSL", (0, 0.0))
        print(f"  {sr.label:<16} {m['trades']:>7} {m['win_rate']*100:>5.1f}% "
              f"{m['profit_factor']:>6.3f} ${m['expectancy_usd']:>7.2f} "
              f"{m['max_dd_pct']:>6.2f}% {m['ann_return_pct']:>7.1f}% "
              f"{cal:>7.2f} ${total_pnl:>+9,.0f} "
              f"{sr.lock_tp_events:>10} {sr.lock_sl_events:>10}")

    # ── 3. Exit-mix breakdown per scenario ────────────────────────────────────
    print("\n─" * 90)
    print("  EXIT MIX BREAKDOWN  (combined, all 3 variants)")
    print("─" * 90)
    reasons_order = ["TP", "SL", "PortfolioLockTP", "PortfolioLockSL", "EndOfData"]
    print(f"  {'Scenario':<16}", end="")
    for r in reasons_order:
        short = r.replace("PortfolioLock", "pLock")
        print(f"  {short:<12}", end="")
    print()
    print("  " + "-" * 88)

    for sr in scenarios:
        all_trades = [t for eng in sr.engines.values() for t in eng.trades]
        bd = _exit_breakdown(all_trades)
        total = len(all_trades)
        print(f"  {sr.label:<16}", end="")
        for r in reasons_order:
            n, s = bd.get(r, (0, 0.0))
            pct  = n / total * 100 if total else 0.0
            print(f"  {n:>4}({pct:>4.1f}%)", end="")
        print()

    print("\n  $ P&L by exit reason (combined):")
    print(f"  {'Scenario':<16}", end="")
    for r in reasons_order:
        short = r.replace("PortfolioLock", "pLock")
        print(f"  {short:>12}", end="")
    print()
    print("  " + "-" * 88)

    for sr in scenarios:
        all_trades = [t for eng in sr.engines.values() for t in eng.trades]
        bd = _exit_breakdown(all_trades)
        print(f"  {sr.label:<16}", end="")
        for r in reasons_order:
            _, pnl = bd.get(r, (0, 0.0))
            print(f"  ${pnl:>+10,.0f}", end="")
        print()

    # ── 4. Asymmetry summary (baseline vs each scenario) ──────────────────────
    baseline = next((s for s in scenarios if s.label == "Baseline"), None)
    if baseline:
        print("\n─" * 90)
        print("  DELTA VS BASELINE  (portfolio - baseline)")
        print("─" * 90)
        mb, _, _ = _scenario_combined_metrics(baseline, total_start)
        for sr in scenarios:
            if sr.label == "Baseline":
                continue
            ms, _, _ = _scenario_combined_metrics(sr, total_start)
            dpf  = ms["profit_factor"]   - mb["profit_factor"]
            ddd  = ms["max_dd_pct"]      - mb["max_dd_pct"]
            de   = ms["expectancy_usd"]  - mb["expectancy_usd"]
            dann = ms["ann_return_pct"]  - mb["ann_return_pct"]
            print(f"  [{sr.label}]  "
                  f"ΔPF={dpf:+.3f}  ΔE=${de:+.2f}  ΔMaxDD={ddd:+.2f}%  "
                  f"ΔAnnRet={dann:+.1f}%  ΔCalmar={calmar(ms)-calmar(mb):+.2f}")


# =============================================================================
# Main — runs baseline + 3 co-simulation scenarios in one pass
# =============================================================================

def main():
    ap = argparse.ArgumentParser(
        description="Portfolio profit/loss lock backtest — 4 scenarios",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Runs 4 scenarios automatically:
  1. Baseline           — no portfolio overlay
  2. TP-only lock       — +lock-usd triggers close, no loss-lock
  3. Scenario A (Sym)   — +lock-usd TP, -lock-usd SL  (symmetric)
  4. Scenario B (Asym)  — +lock-usd TP, -lock-sl-usd SL  (asymmetric)

Example:
  python3 backtest_portfolio_lock.py \\
      --m5-csv download/xauusd-m5-bid-2013-01-01-2026-06-01.csv \\
      --m15-csv download/xauusd-m15-bid-2013-01-01-2026-06-10.csv \\
      --lock-usd 100 --lock-sl-usd 200 \\
      --start 2016-01-01 --end 2017-12-31
"""
    )
    ap.add_argument("--m5-csv",     required=True)
    ap.add_argument("--m15-csv",    required=True)
    ap.add_argument("--symbol",     default="XAUUSD")
    ap.add_argument("--lock-usd",   type=float, default=100.0,
                    help="Portfolio profit-lock threshold USD (default=100)")
    ap.add_argument("--lock-sl-usd", type=float, default=200.0,
                    help="Portfolio loss-lock threshold USD for Scenario B (default=200)")
    ap.add_argument("--start-cash", type=float, default=10000.0)
    ap.add_argument("--risk",       type=float, default=0.30)
    ap.add_argument("--spread",     type=float, default=0.28)
    ap.add_argument("--commission", type=float, default=3.50)
    ap.add_argument("--start",      default=None,
                    help="Start date YYYY-MM-DD (default: data start)")
    ap.add_argument("--end",        default=None,
                    help="End date YYYY-MM-DD (default: data end)")
    ap.add_argument("--no-save",    action="store_true")
    args   = ap.parse_args()
    symbol = args.symbol.upper()

    print("=" * 90)
    print(f"  Portfolio Lock Backtest  |  TP-lock=+${args.lock_usd:.0f}  "
          f"SL-lock=-${args.lock_sl_usd:.0f}  risk={args.risk:.2f}%")
    print("=" * 90)

    # ── Load & clip data ──────────────────────────────────────────────────────
    print("\n[1] Loading data ...")
    m5_df  = load_ohlcv(args.m5_csv,  symbol)
    m15_df = load_ohlcv(args.m15_csv, symbol)

    # Intersect data ranges
    start_common = max(m5_df["timestamp"].iloc[0],  m15_df["timestamp"].iloc[0])
    end_common   = min(m5_df["timestamp"].iloc[-1], m15_df["timestamp"].iloc[-1])

    # Apply user-specified date range on top
    if args.start:
        start_common = max(start_common, pd.Timestamp(args.start))
    if args.end:
        end_common   = min(end_common,   pd.Timestamp(args.end))

    m5_df  = clip_df_to_range(m5_df,  start_common, end_common)
    m15_df = clip_df_to_range(m15_df, start_common, end_common)
    print(f"  Range : {start_common.date()} – {end_common.date()}")
    print(f"  M5={len(m5_df):,}  M15={len(m15_df):,}")

    kw = dict(start_cash=args.start_cash, risk=args.risk,
              spread=args.spread, commission=args.commission, symbol=symbol)

    # ── Scenario 1: Baseline ─────────────────────────────────────────────────
    print("\n[2] Baseline ...")
    baseline = run_baseline(m5_df, m15_df, **kw)

    # ── Scenario 2: TP-lock only ──────────────────────────────────────────────
    print(f"\n[3] TP-lock only (TP=+${args.lock_usd:.0f}) ...")
    tp_only = run_co_simulation(
        m5_df, m15_df,
        label="TP-only",
        lock_tp_usd=args.lock_usd,
        lock_sl_usd=None,
        **kw)

    # ── Scenario 3: Symmetric (TP=+X, SL=-X) ─────────────────────────────────
    print(f"\n[4] Scenario A — Symmetric "
          f"(TP=+${args.lock_usd:.0f}, SL=-${args.lock_usd:.0f}) ...")
    sym_a = run_co_simulation(
        m5_df, m15_df,
        label=f"Sym(±{args.lock_usd:.0f})",
        lock_tp_usd=args.lock_usd,
        lock_sl_usd=args.lock_usd,
        **kw)

    # ── Scenario 4: Asymmetric (TP=+X, SL=-Y where Y>X) ─────────────────────
    print(f"\n[5] Scenario B — Asymmetric "
          f"(TP=+${args.lock_usd:.0f}, SL=-${args.lock_sl_usd:.0f}) ...")
    asym_b = run_co_simulation(
        m5_df, m15_df,
        label=f"Asym(TP{args.lock_usd:.0f}/SL{args.lock_sl_usd:.0f})",
        lock_tp_usd=args.lock_usd,
        lock_sl_usd=args.lock_sl_usd,
        **kw)

    # ── Report ────────────────────────────────────────────────────────────────
    print_report([baseline, tp_only, sym_a, asym_b], args.start_cash)

    # ── Save CSVs ─────────────────────────────────────────────────────────────
    if not args.no_save:
        print("\n[6] Saving trade CSVs ...")
        for sr in [baseline, tp_only, sym_a, asym_b]:
            tag = sr.label.replace("/", "-").replace(" ", "_")
            for name, eng in sr.engines.items():
                fname = save_trades_csv(eng.trades, f"{symbol}_{tag}_{name}")
                print(f"  → {fname}")

    print("\n[DONE]")


if __name__ == "__main__":
    main()
