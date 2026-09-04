#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_replay_bot_day.py -- run clock_scalp_bot's EXACT logic over one past day
and show, bar by bar, where it would have entered and where it would have
taken profit.

The operator's exit is "when the chart stops flowing" -- the lower red
circle on their 4 Sep screenshot, where the drop ended and the candles
turned small and mixed. The bot's mechanical version of that is: leave
once two consecutive M5 bars close against the position. This script
checks the two agree, on the day they circled, instead of asserting it.

Nothing here is a simplification of the bot. Same reference tick, same
+3s decision, same spread gate, same 3xATR stop, same two-bar exit. If
this disagrees with the live bot, the live bot is what is wrong.

Output includes the M5 bars in a compact table so the same candles can be
drawn and checked against the operator's own chart.

Usage:  python _replay_bot_day.py [symbol] [YYYY-MM-DD] [decide_s] [gate_x] [sl_atr]
"""
import sys
from datetime import datetime, timedelta, timezone

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)"); sys.exit(1)

import numpy as np

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "XAUAUDm"
DAY = sys.argv[2] if len(sys.argv) > 2 else "2026-09-04"
DECIDE = float(sys.argv[3]) if len(sys.argv) > 3 else 3.0
GATE_X = float(sys.argv[4]) if len(sys.argv) > 4 else 3.0
SL_ATR = float(sys.argv[5]) if len(sys.argv) > 5 else 3.0
PATIENCE, LOT, THAI, TARGET = 2, 0.05, 7, (19, 30)


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

    d = datetime.strptime(DAY, "%Y-%m-%d").date()
    start_utc = datetime(d.year, d.month, d.day, TARGET[0] - THAI, TARGET[1],
                         tzinfo=timezone.utc)
    start_srv = start_utc + timedelta(hours=off)

    ticks = mt5.copy_ticks_range(SYMBOL, start_srv, start_srv + timedelta(seconds=30),
                                 mt5.COPY_TICKS_ALL)
    if ticks is None or len(ticks) < 2:
        print(f"[ERROR] no ticks ({mt5.last_error()})"); return 2

    # ---- ATR(H1) as the bot sees it: last CLOSED H1 bar before the bell ----
    h1 = mt5.copy_rates_range(SYMBOL, mt5.TIMEFRAME_H1,
                              start_srv - timedelta(hours=40), start_srv)
    trs = [max(float(h1[i]["high"]) - float(h1[i]["low"]),
               abs(float(h1[i]["high"]) - float(h1[i - 1]["close"])),
               abs(float(h1[i]["low"]) - float(h1[i - 1]["close"])))
           for i in range(1, len(h1))]
    atr = sum(trs[-14:]) / 14.0

    t0 = int(start_srv.timestamp() * 1000)
    ms = ticks["time_msc"].astype(np.int64) - t0
    bid, ask = ticks["bid"].astype(float), ticks["ask"].astype(float)
    mid = np.where(ask > 0, (bid + ask) / 2.0, bid)
    ref = float(mid[0])
    gate = GATE_X * spread

    print("=" * 84)
    print(f" BOT REPLAY -- {SYMBOL}  {d}  19:30 Thai   ATR(H1) {atr:.2f}")
    print(f" decide +{DECIDE}s   gate {GATE_X}x spread = {gate:.2f}   "
          f"SL {SL_ATR}xATR = {SL_ATR*atr:.2f} pts   lot {LOT}")
    print("=" * 84)

    m = ms <= DECIDE * 1000
    moved = float(mid[m][-1] - ref) if m.sum() >= 2 else 0.0
    print(f"\n 19:30:00.000  reference {ref:.3f}")
    print(f" +{DECIDE}s        price {float(mid[m][-1]):.3f}  moved {moved:+.2f} pts"
          f"  ({abs(moved)/spread:.1f}x spread)")
    if abs(moved) < gate:
        print(f"\n GATE SHUT -- {abs(moved):.2f} < {gate:.2f}, no trade this day")
        mt5.shutdown(); return 0
    s = 1 if moved > 0 else -1
    i_e = int(np.where(m)[0][-1])
    entry = float(ask[i_e]) if s > 0 else float(bid[i_e])
    sl = entry - s * SL_ATR * atr
    print(f" GATE OPEN -> {'BUY' if s > 0 else 'SELL'} at {entry:.3f}"
          f"   SL {sl:.3f}")

    # ---- exit: two consecutive M5 bars closing against -------------------
    bars = mt5.copy_rates_range(SYMBOL, mt5.TIMEFRAME_M5, start_srv,
                                start_srv + timedelta(hours=4))
    print(f"\n M5 BARS FROM 19:30 -- the bot leaves after {PATIENCE} closes against\n")
    print(f"   {'Thai':>8}{'open':>10}{'high':>10}{'low':>10}{'close':>10}"
          f"{'body':>9}{'against':>9}  note")
    against, exit_px, exit_t, why = 0, None, None, ""
    for b in bars:
        th = datetime.fromtimestamp(int(b["time"]) - off * 3600 + THAI * 3600,
                                    timezone.utc)
        o, h, l, c = (float(b["open"]), float(b["high"]),
                      float(b["low"]), float(b["close"]))
        body = c - o
        note = ""
        if exit_px is None:
            hit_sl = (l <= sl) if s > 0 else (h >= sl)
            if hit_sl:
                exit_px, exit_t, why = sl, th, "STOP-LOSS hit"
                note = "<-- SL"
            else:
                if body * s < 0:
                    against += 1
                else:
                    against = 0
                if against >= PATIENCE:
                    exit_px, exit_t, why = c, th, f"{PATIENCE} bars against"
                    note = "<-- TP: it stopped flowing"
        print(f"   {th:%H:%M}{o:>10.2f}{h:>10.2f}{l:>10.2f}{c:>10.2f}"
              f"{body:>+9.2f}{against:>9}  {note}")
        if exit_px is not None and note:
            break

    if exit_px is None:
        print("\n never triggered an exit inside 4h")
        mt5.shutdown(); return 0

    pts = (exit_px - entry) * s
    money = mt5.order_calc_profit(
        mt5.ORDER_TYPE_BUY if s > 0 else mt5.ORDER_TYPE_SELL,
        SYMBOL, LOT, entry, exit_px)
    print("\n" + "-" * 84)
    print(f" ENTRY  {'BUY' if s > 0 else 'SELL'} {LOT} at {entry:.3f}  "
          f"(19:30:0{int(DECIDE)} Thai)")
    print(f" EXIT   {exit_px:.3f} at {exit_t:%H:%M} Thai   -- {why}")
    print(f" RESULT {pts:+.2f} points = {float(money):+.2f} "
          f"{info.currency_profit} on {LOT} lot")
    print("-" * 84)
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
