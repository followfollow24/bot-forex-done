#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_exit_modes.py -- how should "the chart stopped running" be detected?

The 4 Sep replay held until 19:45. That was not a choice about that day;
it is what two M5 bars closing against ALWAYS produces -- a floor of 15
minutes, whether the move died at minute three or minute fourteen. The
operator's objection is correct and it is a flaw in the rule, not in the
day.

What they actually describe is: while price keeps pushing, stay; the
moment it stops pushing hard, get out. That is a statement about the
move's own extreme, not about candle colours on an arbitrary 5-minute
grid, so it is measured on M1 here and several definitions are compared
on IDENTICAL entries:

  stall N   no new favourable extreme for N minutes -> the push is over.
            The closest match to what they said, and it exits at minute
            four on a day that died at minute three.
  against N N consecutive M1 closes against the position.
  m5x2      the current rule, kept so the change is measured rather than
            assumed better.
  speed     leave when the last minute's movement falls under a fraction
            of the first minute's -- explicitly "it stopped running HARD"
            rather than "it stopped running".

Entries are the live bot's: reference tick at 19:30:00, direction at +3s,
gate at 3x spread. Only the exit varies, so any difference in the table
is caused by the exit and nothing else.

SAMPLE SIZE WARNING, UP FRONT: the entry needs tick history and the exit
needs M1, and this terminal holds far less M1 than M5. The number of days
that satisfy both is printed before the results. If it is under ~100 the
table ranks candidates; it does not prove one.

Usage:  python _exit_modes.py [symbol] [days] [gate_x] [decide_s]
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
GATE_X = float(sys.argv[3]) if len(sys.argv) > 3 else 3.0
DECIDE = float(sys.argv[4]) if len(sys.argv) > 4 else 3.0
SL_ATR, LOT, THAI, TARGET = 3.0, 0.05, 7, (19, 30)
CAP_MIN = 120


def h1_atr_at(sym, when_srv, n=14):
    r = mt5.copy_rates_range(sym, mt5.TIMEFRAME_H1,
                             when_srv - timedelta(hours=40), when_srv)
    if r is None or len(r) < n + 1:
        return None
    trs = [max(float(r[i]["high"]) - float(r[i]["low"]),
               abs(float(r[i]["high"]) - float(r[i - 1]["close"])),
               abs(float(r[i]["low"]) - float(r[i - 1]["close"])))
           for i in range(1, len(r))]
    return sum(trs[-n:]) / n


def simulate(bars, s, entry, sl, mode, param):
    """bars: M1 rows from the entry minute onward. Returns (points, minutes)."""
    o = bars["open"].astype(float); c = bars["close"].astype(float)
    h = bars["high"].astype(float); l = bars["low"].astype(float)
    best = entry
    since_extreme = 0
    against = 0
    first_move = None
    for j in range(len(bars)):
        if (l[j] <= sl) if s > 0 else (h[j] >= sl):
            return (sl - entry) * s, j + 1
        ext = h[j] if s > 0 else l[j]
        improved = (ext > best) if s > 0 else (ext < best)
        if improved:
            best = ext
            since_extreme = 0
        else:
            since_extreme += 1
        body = c[j] - o[j]
        against = against + 1 if body * s < 0 else 0
        move = abs(c[j] - o[j])
        if first_move is None and move > 0:
            first_move = move

        if mode == "stall" and since_extreme >= param:
            return (c[j] - entry) * s, j + 1
        if mode == "against" and against >= param:
            return (c[j] - entry) * s, j + 1
        if mode == "speed" and first_move and j > 0 and move < first_move * param:
            return (c[j] - entry) * s, j + 1
        if j >= CAP_MIN - 1:
            break
    j = min(len(bars), CAP_MIN) - 1
    return (c[j] - entry) * s, j + 1


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
    per_pt = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, SYMBOL, LOT,
                                   tkn.ask, tkn.ask + 1.0) or 0.0

    trades = []           # (date, direction, entry, sl, M1 bars)
    checked = gated = 0
    today = (datetime.now(timezone.utc) + timedelta(hours=THAI)).date()
    for back in range(DAYS, 0, -1):
        d = today - timedelta(days=back)
        if d.weekday() >= 5:
            continue
        checked += 1
        s_utc = datetime(d.year, d.month, d.day, TARGET[0] - THAI, TARGET[1],
                         tzinfo=timezone.utc)
        s_srv = s_utc + timedelta(hours=off)
        tk = mt5.copy_ticks_range(SYMBOL, s_srv, s_srv + timedelta(seconds=int(DECIDE) + 2),
                                  mt5.COPY_TICKS_ALL)
        if tk is None or len(tk) < 3:
            continue
        t0 = int(s_srv.timestamp() * 1000)
        ms = tk["time_msc"].astype(np.int64) - t0
        bid = tk["bid"].astype(float); ask = tk["ask"].astype(float)
        mid = np.where(ask > 0, (bid + ask) / 2.0, bid)
        m = ms <= DECIDE * 1000
        if m.sum() < 2:
            continue
        moved = float(mid[m][-1] - mid[0])
        if abs(moved) < GATE_X * spread:
            continue
        atr = h1_atr_at(SYMBOL, s_srv)
        if not atr:
            continue
        i_e = int(np.where(m)[0][-1])
        s = 1 if moved > 0 else -1
        entry = float(ask[i_e]) if s > 0 else float(bid[i_e])
        bars = mt5.copy_rates_range(SYMBOL, mt5.TIMEFRAME_M1, s_srv,
                                    s_srv + timedelta(minutes=CAP_MIN + 5))
        if bars is None or len(bars) < 20:
            continue
        gated += 1
        trades.append((d, s, entry, entry - s * SL_ATR * atr, bars))

    print("=" * 90)
    print(f" EXIT COMPARISON -- {SYMBOL}   entry: +{DECIDE}s, gate {GATE_X}x spread")
    print(f" {checked} weekdays scanned, {gated} had BOTH tick and M1 history "
          f"and opened the gate")
    print(f" spread {spread:.2f}   1.0 pt on {LOT} lot = {per_pt:.2f} "
          f"{info.currency_profit}")
    if gated < 100:
        print(" *** under 100 trades: this RANKS the exits, it does not prove one")
    print("=" * 90)
    if gated < 15:
        print(" not enough to compare"); mt5.shutdown(); return 0

    half = gated // 2
    print(f"\n{'exit rule':>14}{'half':>7}{'n':>5}{'held':>8}{'win':>7}"
          f"{'avg pt':>9}{'total$':>9}{'maxDD$':>9}{'best':>8}{'worst':>8}")
    print("-" * 90)
    modes = [("stall 1min", "stall", 1), ("stall 2min", "stall", 2),
             ("stall 3min", "stall", 3), ("stall 5min", "stall", 5),
             ("against 1", "against", 1), ("against 2", "against", 2),
             ("speed <50%", "speed", 0.5), ("speed <25%", "speed", 0.25),
             ("m5x2 (now)", "against", 10)]
    for label, mode, param in modes:
        for tag, sub in (("train", trades[:half]), ("TEST", trades[half:])):
            res, holds = [], []
            for (_, s, entry, sl, bars) in sub:
                pts, mins = simulate(bars, s, entry, sl, mode, param)
                res.append(pts - spread); holds.append(mins)
            a = np.array(res)
            eq = np.cumsum(a)
            dd = float(np.max(np.maximum.accumulate(eq) - eq))
            print(f"{label:>14}{tag:>7}{len(a):>5}{np.mean(holds):>7.0f}m"
                  f"{100.0*np.mean(a>0):>6.0f}%{a.mean():>+9.2f}"
                  f"{a.sum()*per_pt:>+9.0f}{dd*per_pt:>9.0f}"
                  f"{a.max()*per_pt:>+8.0f}{a.min()*per_pt:>+8.0f}")
        print("-" * 90)
    print("\n  'held' is the average minutes in the trade -- the operator's")
    print("  objection was that 15 minutes is too slow, so this column")
    print("  matters as much as the money column.")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
