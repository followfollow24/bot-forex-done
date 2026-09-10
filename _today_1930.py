#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_today_1930.py -- replay ONE day at 19:30 Thai with the CURRENT live rules.

The operator saw another big 19:30 move and wants to know whether the rule
would have made money on it. That is the right question, and it is worth
more than any backtest here: a day that happens AFTER the testing is
finished cannot have been selected, tuned to, or seen in advance.

Exactly the configured rules, nothing simplified:
    reference   the first tick at or after 19:30:00.000 Thai (12:30 UTC)
    earliest    entry may not fire before +1.0s
    gate        14.0 points, fixed distance from the reference
    give up     after 900s with the gate never cleared
    exit        30 minutes after entry, flat
    stop        3 x ATR(H1), attached

It also prints what the window did overall -- the largest excursion each
way and the close -- so the size of the move the operator SAW can be
compared against what the rule actually caught. Those are usually
different numbers, and the difference is the whole argument.

Usage:  python _today_1930.py [symbol] [YYYY-MM-DD]
        python _today_1930.py XAUAUDm 2026-09-10
"""
import sys
from datetime import datetime, timedelta, timezone

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)"); sys.exit(1)

import numpy as np

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "XAUAUDm"
DAY = sys.argv[2] if len(sys.argv) > 2 else None
GATE, MIN_WAIT, MAXW, HOLD_MIN, SL_ATR, THAI = 14.0, 1.0, 900, 30.0, 3.0, 7
LOTS = (0.01, 0.05)


def atr_h1(sym, when, n=14):
    r = mt5.copy_rates_range(sym, mt5.TIMEFRAME_H1,
                             when - timedelta(hours=40), when)
    if r is None or len(r) < n + 1:
        return None
    trs = [max(float(r[i]["high"]) - float(r[i]["low"]),
               abs(float(r[i]["high"]) - float(r[i - 1]["close"])),
               abs(float(r[i]["low"]) - float(r[i - 1]["close"])))
           for i in range(1, len(r))]
    return sum(trs[-n:]) / n


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed"); return 2
    si = mt5.symbol_info(SYMBOL)
    if si is None:
        print(f"[ERROR] {SYMBOL} not found"); mt5.shutdown(); return 2
    mt5.symbol_select(SYMBOL, True)
    spread = si.spread * si.point

    if DAY:
        d = datetime.strptime(DAY, "%Y-%m-%d").date()
    else:
        d = (datetime.now(timezone.utc) + timedelta(hours=THAI)).date()
    bell = datetime(d.year, d.month, d.day, 12, 30, tzinfo=timezone.utc)

    t = mt5.copy_ticks_range(SYMBOL, bell,
                             bell + timedelta(seconds=MAXW + HOLD_MIN * 60 + 120),
                             mt5.COPY_TICKS_ALL)
    atr = atr_h1(SYMBOL, bell)
    if t is None or len(t) < 20 or not atr:
        print(f"[ERROR] no tick data for {d} -- the market may have been "
              f"shut, or the broker may not keep ticks this far back")
        mt5.shutdown(); return 2

    tk = mt5.symbol_info_tick(SYMBOL)
    pp = {}
    for lot in LOTS:
        v = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, SYMBOL, lot,
                                  tk.ask, tk.ask + 1.0) if tk else None
        pp[lot] = float(v) if v else 0.0

    t0 = int(bell.timestamp())
    sec = (t["time_msc"].astype(np.int64) - t0 * 1000) / 1000.0
    bid, ask = t["bid"].astype(float), t["ask"].astype(float)
    mid = np.where(ask > 0, (bid + ask) / 2.0, bid)
    ref = float(mid[0])

    print("=" * 74)
    print(f" 19:30 THAI ON {d}  --  {SYMBOL}   (rules as configured, "
          f"nothing tuned)")
    print(f" reference {ref:.3f} at +{sec[0]:.3f}s   spread {spread:.3f}"
          f"   ATR(H1) {atr:.3f}   gate {GATE:.0f} pts")
    print("=" * 74)

    win = sec <= MAXW
    up = float(mid[win].max() - ref)
    dn = float(mid[win].min() - ref)
    print(f"\n WHAT THE WINDOW DID (first {MAXW/60:.0f} min)")
    print(f"   furthest UP   {up:+8.2f} pts    furthest DOWN {dn:+8.2f} pts")
    print(f"   total range   {up - dn:8.2f} pts    at the 15-min mark"
          f" {float(mid[win][-1]) - ref:+8.2f}")
    hit_up = float(np.argmax(np.abs(mid[win] - ref) >= GATE)) if (np.abs(mid[win] - ref) >= GATE).any() else None

    w = np.where((sec >= MIN_WAIT) & (np.abs(mid - ref) >= GATE))[0]
    if len(w) == 0:
        peak = float(np.abs(mid[win] - ref).max())
        print(f"\n THE RULE: NO TRADE")
        print(f"   closest it came was {peak:.2f} pts = {peak/GATE*100:.0f}%"
              f" of the {GATE:.0f}-point gate")
        print(f"\n   The move was real and the rule did not take it. That is")
        print(f"   the 62% of days the gate is designed to sit out.")
        mt5.shutdown(); return 0

    i = int(w[0])
    side = 1 if mid[i] > ref else -1
    entry = float(ask[i]) if side > 0 else float(bid[i])
    sl = entry - side * SL_ATR * atr
    t_in = float(sec[i])
    end = t_in + HOLD_MIN * 60
    after = (sec >= t_in) & (sec <= end)
    if not after.any():
        print("\n [ERROR] no ticks after entry -- cannot price the exit")
        mt5.shutdown(); return 2
    path = mid[after]
    worst = float(((path.min() if side > 0 else path.max()) - entry) * side)
    best = float(((path.max() if side > 0 else path.min()) - entry) * side)
    stopped = worst <= -SL_ATR * atr
    exit_px = sl if stopped else float(path[-1])
    pts = (exit_px - entry) * side - spread

    print(f"\n THE RULE: {'BUY' if side > 0 else 'SELL'} at +{t_in:.2f}s")
    print(f"   entry {entry:.3f}   stop {sl:.3f} ({SL_ATR}xATR = "
          f"{SL_ATR*atr:.1f} pts)")
    print(f"   exit  {exit_px:.3f}   "
          + ("STOPPED OUT" if stopped else f"after {HOLD_MIN:.0f} min"))
    print(f"\n   result {pts:+.2f} pts"
          + "".join(f"   |  {lot} lot {pts*pp[lot]:+.2f} USD"
                    for lot in LOTS if pp[lot]))
    print(f"   best it went  {best:+8.2f} pts")
    print(f"   worst it went {worst:+8.2f} pts"
          + "".join(f"   ({worst*pp[lot]:+.2f} USD at {lot})"
                    for lot in LOTS if pp[lot]))
    print(f"\n   The window moved {up-dn:.1f} points in total and the rule")
    print(f"   captured {pts:+.1f}. Those two numbers are not the same thing,")
    print(f"   and the gap is what a chart cannot show you.")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
