#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_move_vs_gate.py -- "only 31 trades? how many times a year does the
chart ACTUALLY run at 19:30?"

Fair challenge, and the two numbers are not the same thing. 31 a year is
how often the FIRST THREE SECONDS moved far enough to open the gate. How
often a real move happens at 19:30 is a different question: a session can
run 25 points over the next quarter hour while its first three seconds
sat still, and the gate would have skipped it.

So this measures both and lines them up:

  A. HOW MANY REAL MOVES per year -- sessions whose 19:30 move over
     5/15/30/60 minutes cleared N times the spread. Measured on every
     session with M5 history, which is far more than have ticks.
  B. WHAT THE 3-SECOND GATE CAUGHT of them -- on the sessions that have
     both, how many real moves the gate let through, and how many it
     skipped. The miss rate is the number that matters: a gate that
     blocks most of the good days is not selective, it is blind.

Everything is in spread multiples rather than percent, because the
question is whether a move is big enough to pay for crossing the spread.

Usage:  python _move_vs_gate.py [symbol] [days] [decide_s] [weekends:0|1]
"""
import sys
from datetime import datetime, timedelta, timezone

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)"); sys.exit(1)

import numpy as np

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "XAUAUDm"
DAYS = int(sys.argv[2]) if len(sys.argv) > 2 else 400
DECIDE = float(sys.argv[3]) if len(sys.argv) > 3 else 3.0
WEEKENDS = bool(int(sys.argv[4])) if len(sys.argv) > 4 else False
THAI, TARGET_H, TARGET_M = 7, 19, 30
HORIZONS = [5, 15, 30, 60]
MULTS = [3, 5, 10, 20, 40]
GATE_X = 3.0


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed"); return 2
    info = mt5.symbol_info(SYMBOL)
    if info is None:
        print(f"[ERROR] {SYMBOL} not found"); return 2
    mt5.symbol_select(SYMBOL, True)
    spread = info.spread * info.point
    tkn = mt5.symbol_info_tick(SYMBOL)
    off = int(round((tkn.time - datetime.now(timezone.utc).timestamp()) / 3600.0))

    rows = []          # (date, move3s or None, {hz: abs move in points})
    today = (datetime.now(timezone.utc) + timedelta(hours=THAI)).date()
    for back in range(DAYS, 0, -1):
        d = today - timedelta(days=back)
        if d.weekday() >= 5 and not WEEKENDS:
            continue
        s_utc = datetime(d.year, d.month, d.day, TARGET_H - THAI, TARGET_M,
                         tzinfo=timezone.utc)
        s_srv = s_utc + timedelta(hours=off)

        bars = mt5.copy_rates_range(SYMBOL, mt5.TIMEFRAME_M5, s_srv,
                                    s_srv + timedelta(minutes=max(HORIZONS) + 5))
        if bars is None or len(bars) < max(HORIZONS) // 5:
            continue
        o0 = float(bars[0]["open"])
        far = {}
        for hz in HORIZONS:
            k = hz // 5
            if k < len(bars):
                far[hz] = abs(float(bars[k]["close"]) - o0)
        if len(far) < len(HORIZONS):
            continue

        m3 = None
        t = mt5.copy_ticks_range(SYMBOL, s_srv, s_srv + timedelta(seconds=12),
                                 mt5.COPY_TICKS_ALL)
        if t is not None and len(t) >= 2:
            t0 = int(s_srv.timestamp() * 1000)
            ms = t["time_msc"].astype(np.int64) - t0
            bid, ask = t["bid"].astype(float), t["ask"].astype(float)
            mid = np.where(ask > 0, (bid + ask) / 2.0, bid)
            sel = ms <= DECIDE * 1000
            if sel.sum() >= 2:
                m3 = abs(float(mid[sel][-1] - mid[0]))
        rows.append((d, m3, far))

    n = len(rows)
    withtick = sum(1 for r in rows if r[1] is not None)
    print("=" * 80)
    print(f" REAL MOVES vs THE 3-SECOND GATE -- {SYMBOL}   spread {spread:.3f}")
    print(f" {n} sessions with M5 history; {withtick} of them also have ticks")
    print("=" * 80)
    if n < 30:
        print(" not enough sessions"); mt5.shutdown(); return 0

    print(f"\nA. HOW OFTEN DOES 19:30 ACTUALLY RUN?   sessions per YEAR whose")
    print(f"   move cleared N x spread ({spread:.2f} points)\n")
    print(f"   {'':>8}" + "".join(f"{f'>={m}x':>10}" for m in MULTS))
    print(f"   {'':>8}" + "".join(f"{f'({m*spread:.0f}pt)':>10}" for m in MULTS))
    print("   " + "-" * 58)
    for hz in HORIZONS:
        line = f"   {hz:>5}min "
        for m in MULTS:
            k = sum(1 for r in rows if r[2][hz] >= m * spread)
            line += f"{k/n*252:>10.0f}"
        print(line)
    print("   (252 = trading days in a year)")

    sub = [r for r in rows if r[1] is not None]
    if len(sub) >= 30:
        print(f"\nB. OF THOSE REAL MOVES, WHAT DID THE {GATE_X:.0f}x GATE CATCH?")
        print(f"   on the {len(sub)} sessions that have both\n")
        print(f"   {'real move':>22}{'days':>7}{'gate let in':>13}"
              f"{'gate skipped':>14}{'missed':>9}")
        print("   " + "-" * 63)
        for hz in (15, 30):
            for m in (5, 10, 20):
                big = [r for r in sub if r[2][hz] >= m * spread]
                if not big:
                    continue
                took = sum(1 for r in big if r[1] >= GATE_X * spread)
                print(f"   {f'>={m}x over {hz}min':>22}{len(big):>7}{took:>13}"
                      f"{len(big)-took:>14}{100.0*(len(big)-took)/len(big):>8.0f}%")
        allgate = sum(1 for r in sub if r[1] >= GATE_X * spread)
        print(f"\n   the gate opened on {allgate} of {len(sub)} sessions "
              f"({100.0*allgate/len(sub):.0f}%)")
        print("   'missed' is the share of genuinely big sessions the gate")
        print("   skipped. A gate that misses most of them is not selecting")
        print("   the good days, it is just trading rarely.")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
