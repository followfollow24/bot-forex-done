#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 grid_search_oos.py — หา exit params ที่ดีกว่า C_wider แบบ honest (IS/OOS split)
================================================================================

 วิธีการ (กัน overfitting):
   IN-SAMPLE  : 2013-01 → 2020-12  (8 ปี)  ← ใช้ "ค้นหา" parameter ที่ดีสุด
   OUT-SAMPLE : 2021-01 → 2026-06  (5.5 ปี) ← ห้ามแตะตอนค้นหา, ใช้ "พิสูจน์"

 เกณฑ์: parameter จะ "ดีกว่า C_wider จริง" ต่อเมื่อ
   ชนะ C_wider (2.5/5.0) ทั้งบน IS *และ* OOS (ไม่ใช่แค่ IS)
   ถ้าตัวที่ดีสุดบน IS แพ้ OOS = overfit, ทิ้ง.

 Grid: SL_ATR × TP_ATR (entry/trend logic เหมือน C_wider ทุกอย่าง)

 วิธีรัน:
   python3 grid_search_oos.py --csv download/xauusd-m15-bid-2013-01-01-2026-06-10.csv
================================================================================
"""
from __future__ import annotations

import argparse
import os
import sys
from copy import deepcopy
from multiprocessing import Pool
from typing import Optional

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forex_config import ForexConfig
from backtest_forex import (DataLoader, prepare_data, BacktestEngine,
                             FastHybridTrendPullback, compute_metrics)

SPREAD   = 0.10
COMM     = 3.50
START    = 10_000.0
RISK_PCT = 0.30
SPLIT    = "2021-01-01"   # IS < SPLIT <= OOS

SL_GRID = [1.5, 2.0, 2.5, 3.0, 3.5]
TP_GRID = [3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
BENCH   = (2.5, 5.0)      # C_wider

# ── per-worker globals (load data once per process) ──────────────────────────
_D = None
_SPLIT_IDX = None


def _init(csv: str):
    global _D, _SPLIT_IDX
    loader = DataLoader(log_fn=lambda *a, **k: None)
    cfg = ForexConfig(); cfg.total_capital_usd = START
    df, _ = loader.load("XAUUSD", 99.0, cfg, csv_path=csv, allow_synthetic=True)
    _D = prepare_data(df)
    # find first bar index at/after SPLIT (ts strings are ISO-sortable)
    ts = _D["ts"]
    idx = int(np.searchsorted(ts.astype(str), SPLIT))
    _SPLIT_IDX = idx


def _cfg(risk=RISK_PCT):
    c = ForexConfig()
    c.total_capital_usd    = START
    c.risk_per_trade_pct   = risk
    c.partial_tp_atr       = 999.0   # no partial (เหมือน C/B/D)
    c.partial_tp_frac      = 0.0
    c.move_sl_to_breakeven = False
    return c


def _strat(sl, tp):
    s = FastHybridTrendPullback()
    s.sl_atr = sl
    s.tp_atr = tp
    s.trail_atr_mult       = 999.0
    s.trail_activation_atr = 999.0
    s.precompute(_D)
    return s


def _run_segment(sl, tp, start_i, end_i):
    eng = BacktestEngine(_D, _cfg(), _strat(sl, tp),
                         spread_price=SPREAD, commission_per_lot=COMM,
                         symbol="XAUUSD")
    eng.run(start_i=start_i, end_i=end_i, quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START)


def _run_combo(task):
    sl, tp = task
    m_is  = _run_segment(sl, tp, None, _SPLIT_IDX)
    m_oos = _run_segment(sl, tp, _SPLIT_IDX, None)
    return dict(sl=sl, tp=tp, is_=m_is, oos=m_oos)


def _g(m, k, default=0.0):
    return m.get(k, default) if m else default


def calmar(m):
    ret = (_g(m, "final_equity", START) - START) / START * 100
    dd  = _g(m, "max_dd_pct", 99) or 0.01
    return ret / dd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--workers", type=int, default=5)
    args = ap.parse_args()

    tasks = [(sl, tp) for sl in SL_GRID for tp in TP_GRID]

    print()
    print("=" * 104)
    print(" GRID SEARCH with IN-SAMPLE / OUT-OF-SAMPLE split — หาที่ดีกว่า C_wider แบบ honest")
    print("=" * 104)
    print(f"  IS  : 2013-01 → {SPLIT}   |   OOS : {SPLIT} → 2026-06")
    print(f"  Grid: SL∈{SL_GRID} × TP∈{TP_GRID}  ({len(tasks)} combos)")
    print(f"  Cost: spread={SPREAD} comm=${COMM}/lot/side risk={RISK_PCT}%  | Benchmark C_wider={BENCH}")
    print("=" * 104)
    print(f"  Running {len(tasks)} combos × 2 segments with {args.workers} workers ...\n")

    with Pool(processes=args.workers, initializer=_init, initargs=(args.csv,)) as pool:
        results = pool.map(_run_combo, tasks)

    # benchmark
    bench = next(r for r in results if (r["sl"], r["tp"]) == BENCH)
    b_is_pf, b_oos_pf = _g(bench["is_"], "profit_factor"), _g(bench["oos"], "profit_factor")
    b_is_cal, b_oos_cal = calmar(bench["is_"]), calmar(bench["oos"])

    # ── table sorted by IS PF ────────────────────────────────────────────────
    results.sort(key=lambda r: _g(r["is_"], "profit_factor"), reverse=True)

    print(f"  {'SL':>4} {'TP':>4} | {'IS_PF':>6} {'IS_Ret':>8} {'IS_DD':>6} {'IS_Cal':>7} "
          f"| {'OOS_PF':>6} {'OOS_Ret':>8} {'OOS_DD':>6} {'OOS_Cal':>7} | flag")
    print("  " + "-" * 100)
    for r in results:
        sl, tp = r["sl"], r["tp"]
        mi, mo = r["is_"], r["oos"]
        is_pf, oos_pf = _g(mi, "profit_factor"), _g(mo, "profit_factor")
        is_ret = (_g(mi, "final_equity", START) - START) / START * 100
        oos_ret = (_g(mo, "final_equity", START) - START) / START * 100
        is_n, oos_n = _g(mi, "trades", 0), _g(mo, "trades", 0)

        tag = ""
        if (sl, tp) == BENCH:
            tag = "← C_wider"
        elif is_pf > b_is_pf and oos_pf > b_oos_pf and oos_n >= 100:
            tag = "✅ BEATS both"
        elif is_pf > b_is_pf and oos_pf <= b_oos_pf:
            tag = "⚠ overfit (IS↑ OOS↓)"

        print(f"  {sl:>4.1f} {tp:>4.1f} | {is_pf:>6.3f} {is_ret:>+7.0f}% "
              f"{_g(mi,'max_dd_pct'):>5.1f}% {calmar(mi):>7.1f} "
              f"| {oos_pf:>6.3f} {oos_ret:>+7.0f}% {_g(mo,'max_dd_pct'):>5.1f}% "
              f"{calmar(mo):>7.1f} | {tag}")

    # ── verdict ──────────────────────────────────────────────────────────────
    print()
    print("=" * 104)
    print(" VERDICT")
    print("=" * 104)
    print(f"  C_wider (2.5/5.0)  IS: PF={b_is_pf:.3f} Calmar={b_is_cal:.1f}  |  "
          f"OOS: PF={b_oos_pf:.3f} Calmar={b_oos_cal:.1f}")

    beats = [r for r in results
             if (r["sl"], r["tp"]) != BENCH
             and _g(r["is_"], "profit_factor")  > b_is_pf
             and _g(r["oos"], "profit_factor")  > b_oos_pf
             and _g(r["oos"], "trades", 0) >= 100]

    # IS-optimum: ตัวที่ดีสุดบน IS → ดูว่ารอด OOS ไหม
    is_best = max(results, key=lambda r: _g(r["is_"], "profit_factor"))
    ib_oos_pf = _g(is_best["oos"], "profit_factor")
    print(f"  IS-optimum = SL{is_best['sl']}/TP{is_best['tp']}  "
          f"(IS_PF={_g(is_best['is_'],'profit_factor'):.3f}) → "
          f"OOS_PF={ib_oos_pf:.3f} "
          f"{'(ยังชนะ benchmark OOS)' if ib_oos_pf > b_oos_pf else '⚠ แพ้ benchmark OOS = overfit'}")
    print()
    if beats:
        print(f"  ✅ พบ {len(beats)} combo ที่ชนะ C_wider ทั้ง IS และ OOS:")
        for r in sorted(beats, key=lambda r: _g(r["oos"], "profit_factor"), reverse=True):
            print(f"     SL{r['sl']}/TP{r['tp']}  "
                  f"OOS_PF={_g(r['oos'],'profit_factor'):.3f}  "
                  f"OOS_Calmar={calmar(r['oos']):.1f}")
        print("     → คุ้มที่จะทดสอบต่อด้วย walk-forward เต็ม + demo")
    else:
        print("  ❌ ไม่มี combo ไหนชนะ C_wider ทั้ง IS และ OOS")
        print("     → C_wider (2.5/5.0) robust จริง — exit-param space ไม่มีของดีกว่าที่ generalize ได้")
        print("     → ของที่ดูดีกว่าบน IS ล้วน overfit (แพ้ OOS)")
    print("=" * 104)
    print()


if __name__ == "__main__":
    main()
