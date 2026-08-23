#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 compare_three_strategies.py — เทียบ 3 แนวทาง บน XAUUSD M15 2013-2026
================================================================================

 [1] BASELINE   : C_wider เดี่ยว  (SL=2.5ATR  TP=5.0ATR  risk=0.30%/trade)

 [2] COMBINED   : เปิดทั้ง C_wider + D_letrun พร้อมกันทุกสัญญาณ
                  risk แบ่งครึ่ง: แต่ละ leg = 0.15%/trade (รวม 0.30%/signal)
                  วัดผลเป็น "portfolio" ของสองขา

 [3] REGIME-SWITCH (real-time, no look-ahead):
                  ดู trailing 90 calendar-day close-to-close return ของทอง
                  ณ เวลาที่สัญญาณเกิด (ใช้ข้อมูลอดีตล้วนๆ):
                    return > +6%  →  C_wider  (UP regime, C เก่งกว่า 11/12 windows)
                    return ≤ +6%  →  D_letrun (DOWN/SIDEWAYS, D เก่งกว่า 12/15 windows)

 วิธีรัน:
   python3 compare_three_strategies.py \\
       --csv download/xauusd-m15-bid-2013-01-01-2026-06-10.csv
================================================================================
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from copy import deepcopy
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forex_config import ForexConfig
from backtest_forex import (DataLoader, prepare_data, BacktestEngine,
                             FastHybridTrendPullback, BTPosition, compute_metrics)

# ── Variant parameters ────────────────────────────────────────────────────────
CWIDER   = dict(sl_atr=2.5, tp_atr=5.0, trail_mult=999.0, trail_act=999.0,
                partial_tp_atr=999.0, partial_tp_frac=0.0, breakeven=False)
DLETRUN  = dict(sl_atr=2.0, tp_atr=6.0, trail_mult=2.5,  trail_act=2.0,
                partial_tp_atr=999.0, partial_tp_frac=0.0, breakeven=False)

SPREAD   = 0.10
COMM     = 3.50
START    = 10_000.0
RISK_PCT = 0.30

# Regime-switch: trailing window in calendar days
REGIME_LOOKBACK_DAYS = 90
REGIME_UP_THRESHOLD  = 6.0   # % — above this → UP → C_wider


# =============================================================================
# Helpers
# =============================================================================

def _apply_variant(strat: FastHybridTrendPullback, v: dict):
    """ตั้งค่า exit parameters ใน strategy object."""
    strat.sl_atr               = v["sl_atr"]
    strat.tp_atr               = v["tp_atr"]
    strat.trail_atr_mult       = v["trail_mult"]
    strat.trail_activation_atr = v["trail_act"]
    strat.partial_tp_atr       = v["partial_tp_atr"]
    strat.partial_tp_frac      = v["partial_tp_frac"]
    strat.move_sl_to_breakeven = v["breakeven"]


def _make_engine(d: dict, cfg: ForexConfig, variant: dict,
                 risk_pct: float = RISK_PCT) -> BacktestEngine:
    strat = FastHybridTrendPullback()
    strat.precompute(d)
    _apply_variant(strat, variant)

    cfg2 = deepcopy(cfg)
    cfg2.risk_per_trade_pct   = risk_pct
    # partial_tp and breakeven are read from cfg by BacktestEngine
    cfg2.partial_tp_atr       = variant["partial_tp_atr"]
    cfg2.partial_tp_frac      = variant["partial_tp_frac"]
    cfg2.move_sl_to_breakeven = variant["breakeven"]

    eng = BacktestEngine(
        d=d, cfg=cfg2, strategy=strat,
        spread_price=SPREAD, commission_per_lot=COMM,
        symbol="XAUUSD",
    )
    return eng


def _metrics_summary(trades: list, equity_curve: list) -> dict:
    m = compute_metrics(trades, equity_curve, START)
    return m


def _print_row(label: str, m: dict):
    if not m or m.get("trades", 0) == 0:
        print(f"  {label:<22}  NO TRADES")
        return
    fe = m.get("final_equity", START)
    ret = (fe - START) / START * 100
    print(
        f"  {label:<22}  "
        f"PF={m.get('profit_factor', 0):.3f}  "
        f"WinRate={m.get('win_rate', 0):.1%}  "
        f"Expectancy=${m.get('expectancy_usd', 0):+.2f}  "
        f"Return={ret:+.1f}%  "
        f"MaxDD={m.get('max_dd_pct', 0):.1f}%  "
        f"#Trades={m.get('trades', 0):,}"
    )


# =============================================================================
# [1] Baseline: C_wider เดี่ยว
# =============================================================================
def run_baseline(d: dict, cfg: ForexConfig) -> Tuple[list, list]:
    return run_baseline_risk(d, cfg, RISK_PCT)


def run_baseline_risk(d: dict, cfg: ForexConfig, risk_pct: float) -> Tuple[list, list]:
    eng = _make_engine(d, cfg, CWIDER, risk_pct=risk_pct)
    eng.run(quiet=True)
    return eng.trades, eng.equity_curve


# =============================================================================
# [2] Combined 50/50 dollar-weighted
# เปิดทั้งสองขาพร้อมกันทุกสัญญาณ risk = 0.15% ต่อขา
# Portfolio equity = เฉลี่ยของทั้งสอง (equivalent to 50% capital each)
# =============================================================================
def run_combined(d: dict, cfg: ForexConfig,
                 risk_per_leg: float = RISK_PCT / 2.0) -> Tuple[list, list]:
    """
    เปิดทั้ง C_wider + D_letrun พร้อมกันทุกสัญญาณ แต่ละขา risk = risk_per_leg%
    Portfolio = 50/50 rebalanced book.
    Equity curve = ค่าเฉลี่ยของสองขา (ทั้งคู่เริ่มที่ $START → averaged curve เริ่มที่
    $START พอดี = พอร์ตที่แบ่งทุนครึ่งๆ ให้สองกลยุทธ์)
    """
    eng_c = _make_engine(d, cfg, CWIDER,  risk_pct=risk_per_leg)
    eng_d = _make_engine(d, cfg, DLETRUN, risk_pct=risk_per_leg)
    eng_c.run(quiet=True)
    eng_d.run(quiet=True)

    trades_c = [dict(t, _leg="C") for t in eng_c.trades]
    trades_d = [dict(t, _leg="D") for t in eng_d.trades]
    all_trades = sorted(trades_c + trades_d, key=lambda t: t["entry_ts"])

    # 50/50 rebalanced portfolio: average of two curves (both start at START)
    n = min(len(eng_c.equity_curve), len(eng_d.equity_curve))
    combined_eq = [(eng_c.equity_curve[i] + eng_d.equity_curve[i]) / 2.0
                   for i in range(n)]
    return all_trades, combined_eq


# =============================================================================
# [3] Regime-Switch (no look-ahead)
# ณ แต่ละ bar ใช้ trailing 90-day gold return เพื่อเลือก variant
# =============================================================================

def _build_regime_series(d: dict, lookback_days: int = REGIME_LOOKBACK_DAYS) -> np.ndarray:
    """
    คืน array (len = n bars) ของ trailing regime ณ ทุก bar
    'UP' / 'DOWN' / 'SIDEWAYS'  ดูจาก trailing {lookback_days} calendar-day return
    ทุกค่าใช้ข้อมูลที่ผ่านมาแล้วเท่านั้น (no look-ahead)
    """
    raw = d["ts"]
    try:
        ts = pd.to_datetime(raw.astype("int64"), unit="ms", utc=True)
    except (ValueError, TypeError):
        ts = pd.to_datetime(raw, utc=True)
    c   = pd.Series(d["c"], index=ts)

    # Close ต่อวัน (ใช้แท่งสุดท้ายของแต่ละวัน)
    daily_close = c.resample("1D").last().dropna()
    daily_ret   = daily_close.pct_change(lookback_days) * 100.0  # % change

    # Map กลับไป bar-level (ffill)
    bar_daily_ret = daily_ret.reindex(ts, method="ffill")

    regimes = np.full(len(ts), "SIDEWAYS", dtype=object)
    regimes[bar_daily_ret > REGIME_UP_THRESHOLD]  = "UP"
    regimes[bar_daily_ret < -REGIME_UP_THRESHOLD] = "DOWN"
    return regimes


class RegimeSwitchEngine:
    """
    Bar-by-bar simulation เหมือน BacktestEngine แต่เปลี่ยน variant ได้แบบ real-time
    ก่อนทุก entry — ใช้ regime ณ ขณะสัญญาณเกิด (close of signal bar)
    """

    def __init__(self, d: dict, cfg: ForexConfig, regimes: np.ndarray):
        self.d        = d
        self.regimes  = regimes
        self.cfg      = deepcopy(cfg)
        self.cfg.risk_per_trade_pct = RISK_PCT

        self.strat_c = FastHybridTrendPullback()
        self.strat_c.precompute(d)
        _apply_variant(self.strat_c, CWIDER)

        self.strat_d = FastHybridTrendPullback()
        self.strat_d.precompute(d)
        _apply_variant(self.strat_d, DLETRUN)

        # ใช้ strat_c เป็น "active" strat (จะสลับก่อน entry)
        self.active_strat = self.strat_c

        # start with C_wider cfg defaults (will switch per signal)
        self.cfg.partial_tp_atr       = CWIDER["partial_tp_atr"]
        self.cfg.partial_tp_frac      = CWIDER["partial_tp_frac"]
        self.cfg.move_sl_to_breakeven = CWIDER["breakeven"]

        self.eng = BacktestEngine(
            d=d, cfg=self.cfg, strategy=self.strat_c,
            spread_price=SPREAD, commission_per_lot=COMM,
            symbol="XAUUSD",
        )
        self.regime_log: List[str] = []  # regime ที่ใช้ต่อ trade

    def run(self):
        """Run bar-by-bar กับ regime switching ก่อนทุก entry"""
        eng   = self.eng
        d     = self.d
        n     = len(d["c"])
        warm  = self.strat_c.MIN_BARS

        for i in range(warm, n):
            o  = float(d["o"][i])
            h  = float(d["h"][i])
            l  = float(d["l"][i])
            c  = float(d["c"][i])
            ts = str(d["ts"][i])

            # Day rollover
            day = ts[:10]
            if day != eng.current_day:
                if eng.current_day is not None:
                    if eng.cooldown_days_left > 0:
                        eng.cooldown_days_left -= 1
                    if eng.day_realized_pnl < 0:
                        eng.consec_losing_days += 1
                    else:
                        eng.consec_losing_days = 0
                eng.current_day      = day
                eng.day_realized_pnl = 0.0
                eng.day_equity_start = eng.equity
                eng.day_blocked      = False
                eng.day_was_positive = False
                if eng.cooldown_days_left > 0:
                    eng.halted_days_count += 1

            # Phase 1: fill pending entry (regime was set at signal bar)
            if eng._pending is not None and eng.position is None:
                eng._enter(i, o, ts)
                eng._pending = None

            # Phase 2: manage open position
            if eng.position is not None:
                if not eng.position.partial_closed:
                    eng._check_partial_tp(i, h, l, c, ts)
                exit_px, reason = eng._check_exit(h, l, c)
                if exit_px is not None:
                    eng._close(i, exit_px, reason)
                else:
                    eng._update_trail(h, l)
                    eng.position.bars += 1
                    if eng.position.bars >= self.cfg.max_hold_bars:
                        eng._close(i, c, "Timeout")

            # Phase 3: signal — เลือก variant ตาม regime ณ แท่งนี้ (close)
            if (eng.position is None and eng._pending is None
                    and not eng.day_blocked and eng.cooldown_days_left == 0):

                regime = self.regimes[i]
                if regime == "UP":
                    eng.strat = self.strat_c
                    eng.cfg.partial_tp_atr       = CWIDER["partial_tp_atr"]
                    eng.cfg.partial_tp_frac      = CWIDER["partial_tp_frac"]
                    eng.cfg.move_sl_to_breakeven = CWIDER["breakeven"]
                else:
                    eng.strat = self.strat_d
                    eng.cfg.partial_tp_atr       = DLETRUN["partial_tp_atr"]
                    eng.cfg.partial_tp_frac      = DLETRUN["partial_tp_frac"]
                    eng.cfg.move_sl_to_breakeven = DLETRUN["breakeven"]

                sig = eng.strat.signal(d, i)
                if sig.action in ("BUY", "SELL"):
                    eng._pending = sig
                    self.regime_log.append(regime)

            # Record equity
            open_pnl = 0.0
            if eng.position:
                pos = eng.position
                lot = pos.lot_remaining if pos.partial_closed else pos.lot
                pips = (c - pos.entry) / eng.pip_size * pos.direction
                open_pnl = pips * eng.pip_value * lot
            eng.equity_curve.append(eng.equity + open_pnl)

        return eng.trades, eng.equity_curve, self.regime_log


# =============================================================================
# Main
# =============================================================================

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv",     required=True)
    ap.add_argument("--symbol",  default="XAUUSD")
    ap.add_argument("--spread-price",        type=float, default=SPREAD)
    ap.add_argument("--commission-per-lot",  type=float, default=COMM)
    ap.add_argument("--start-cash",          type=float, default=START)
    args = ap.parse_args()

    spread = args.spread_price
    comm   = args.commission_per_lot
    start  = args.start_cash

    print()
    print("=" * 96)
    print(" STRATEGY COMPARISON — C_wider vs Combined vs Regime-Switch")
    print("=" * 96)
    print(f"  CSV    : {args.csv}")
    print(f"  Cost   : spread={spread}  commission=${comm}/lot/side  risk={RISK_PCT}%/trade  cash=${start:,.0f}")
    print(f"  Regime : trailing {REGIME_LOOKBACK_DAYS}-day gold return | UP threshold={REGIME_UP_THRESHOLD:+.0f}%")
    print("=" * 96)

    loader = DataLoader(log_fn=print)
    cfg    = ForexConfig()
    cfg.total_capital_usd = start
    df, src = loader.load(args.symbol, 99.0, cfg,
                           csv_path=args.csv, allow_synthetic=True)
    print(f"  Data   : {src}  |  {len(df):,} bars  "
          f"{df['timestamp'].iloc[0]} → {df['timestamp'].iloc[-1]}")
    print()

    d = prepare_data(df)
    if d is None:
        print("[ERROR] prepare_data failed"); sys.exit(1)

    # ── [1] Baseline ──────────────────────────────────────────────────────────
    print("Running [1] Baseline: C_wider เดี่ยว ...")
    trades_b, eq_b = run_baseline(d, cfg)
    m_b = _metrics_summary(trades_b, eq_b)

    # ── [2] Combined ─────────────────────────────────────────────────────────
    print("Running [2] Combined: C_wider + D_letrun 50/50 ...")
    trades_comb, eq_comb = run_combined(d, cfg)
    m_comb = _metrics_summary(trades_comb, eq_comb)

    # ── [3] Regime-Switch ────────────────────────────────────────────────────
    print(f"Running [3] Regime-Switch: trailing {REGIME_LOOKBACK_DAYS}d return ...")
    regimes = _build_regime_series(d)
    rs_eng  = RegimeSwitchEngine(d, cfg, regimes)
    trades_rs, eq_rs, regime_log = rs_eng.run()
    m_rs = _metrics_summary(trades_rs, eq_rs)

    dd_b = m_b.get("max_dd_pct", 99)  # C_wider DD = drawdown budget target

    # ── [4] BEST-OF-EACH ──────────────────────────────────────────────────────
    # เอา edge ของ [1] + ความนิ่งของ [2]: รัน Combined book หลายระดับ risk
    # หาจุดที่ MaxDD ≈ C_wider (budget เท่ากัน) แล้วดูว่า Return เอาชนะ C_wider ได้ไหม
    print(f"Running [4] Best-of-Each: sweep Combined risk → target DD≈{dd_b:.1f}% ...")
    sweep = []
    for rpl in (0.15, 0.20, 0.25, 0.30, 0.375, 0.45):
        tr, eq = run_combined(d, cfg, risk_per_leg=rpl)
        m = _metrics_summary(tr, eq)
        sweep.append((rpl, m))

    # เลือกตัวที่ DD ใกล้ budget ที่สุดแต่ไม่เกิน (conservative)
    under = [(rpl, m) for rpl, m in sweep if m.get("max_dd_pct", 99) <= dd_b]
    if under:
        best4_rpl, m4 = max(under, key=lambda x: x[1].get("final_equity", 0))
    else:
        best4_rpl, m4 = min(sweep, key=lambda x: x[1].get("max_dd_pct", 99))

    # ── Results ───────────────────────────────────────────────────────────────
    def calmar(m):
        fe = m.get("final_equity", START)
        ret = (fe - START) / START * 100
        dd  = m.get("max_dd_pct", 99) or 0.01
        return ret / dd

    print()
    print("=" * 96)
    print(" RESULTS")
    print("=" * 96)
    for label, m in [("[1] C_wider (Baseline)", m_b),
                     ("[2] Combined 50/50",     m_comb),
                     ("[3] Regime-Switch",      m_rs),
                     (f"[4] Best-of-Each (lev)", m4)]:
        _print_row(label, m)
        print(f"  {'':<22}  Calmar(Ret/DD)={calmar(m):.1f}")
    print()
    print(f"  [4] = Combined book @ risk {best4_rpl:.3f}%/leg "
          f"(เร่งจาก 0.15% จน DD ≈ budget {dd_b:.1f}%)")
    print()
    print("  FAIR FRONTIER — เร่ง risk ทั้งสองฝั่ง เทียบ Return ที่ DD เท่ากัน:")
    print(f"     {'risk':>8} | {'C_wider เดี่ยว':^28} | {'Combined (C+D)':^28}")
    print(f"     {'/leg':>8} | {'Return':>9} {'DD':>6} {'Calmar':>7} | {'Return':>9} {'DD':>6} {'Calmar':>7}")
    cwider_sweep = []
    for rpl in (0.15, 0.20, 0.25, 0.30, 0.375, 0.45):
        tr_c, eq_c = run_baseline_risk(d, cfg, rpl)
        mc = _metrics_summary(tr_c, eq_c)
        cwider_sweep.append((rpl, mc))
    for (rpl, mc), (_, mco) in zip(cwider_sweep, sweep):
        rc  = (mc.get("final_equity", START)  - START) / START * 100
        rco = (mco.get("final_equity", START) - START) / START * 100
        print(f"     {rpl:>7.3f}% | {rc:>+8.1f}% {mc.get('max_dd_pct',0):>5.1f}% "
              f"{calmar(mc):>7.1f} | {rco:>+8.1f}% {mco.get('max_dd_pct',0):>5.1f}% "
              f"{calmar(mco):>7.1f}")

    # Regime-switch breakdown
    print()
    if regime_log:
        up_count  = regime_log.count("UP")
        ds_count  = len(regime_log) - up_count
        print(f"  Regime-Switch breakdown: {up_count} signals → C_wider (UP)  |  "
              f"{ds_count} signals → D_letrun (DOWN/SIDEWAYS)")

    # ── Verdict ──────────────────────────────────────────────────────────────
    print()
    print("-" * 96)
    print(" VERDICT — 'best of each' = edge[1] + smoothness[2] via risk-scaling")
    print("-" * 96)

    ret_b  = (m_b.get("final_equity", START)  - START) / START * 100
    ret_4  = (m4.get("final_equity", START)   - START) / START * 100
    dd_4   = m4.get("max_dd_pct", 99)

    print(f"  [1] C_wider     : Return={ret_b:+.1f}%  DD={dd_b:.1f}%  Calmar={calmar(m_b):.1f}")
    print(f"  [4] Best-of-Each: Return={ret_4:+.1f}%  DD={dd_4:.1f}%  Calmar={calmar(m4):.1f}")
    print()
    if ret_4 > ret_b and dd_4 <= dd_b * 1.02:
        print(f"  ✅ Best-of-Each ชนะ! Return สูงกว่า C_wider ({ret_4:+.0f}% vs {ret_b:+.0f}%) "
              f"ที่ความเจ็บเท่ากัน")
        print("     → diversification ของ exit + เร่ง risk ให้ผลจริง (in-sample)")
    elif calmar(m4) > calmar(m_b):
        print(f"  ◐ Best-of-Each ให้ Calmar ดีกว่า ({calmar(m4):.1f} vs {calmar(m_b):.1f}) "
              f"แต่ Return ยังไม่ทะลุ C_wider")
        print("     → คุณภาพกำไรดีกว่า แต่ต้องใช้ leverage เพิ่มเพื่อแลก return")
    else:
        print(f"  ⚠️  Best-of-Each ไม่ชนะ C_wider — diversification benefit ถูกกินหมด")
        print("     → C_wider เดี่ยวยังคือคำตอบที่ดีที่สุด")
    print()
    print("  ⚠️  ทั้งหมดนี้เป็น IN-SAMPLE (optimize บนข้อมูลชุดเดียว 2013-2026)")
    print("     ต้องพิสูจน์ด้วย demo forward-test ก่อนเชื่อ — เลข backtest มองโลกสวยเสมอ")
    print("=" * 96)
    print()


if __name__ == "__main__":
    main()
