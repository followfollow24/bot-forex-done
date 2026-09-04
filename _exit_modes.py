#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_exit_modes.py -- how should "the chart stopped running" be detected?

The 4 Sep replay held to 19:45. That was not a judgement about that day:
two M5 bars closing against imposes a 15-minute floor on EVERY trade,
whether the move died at minute three or minute fourteen. The operator's
objection is right, and it is a flaw in the exit rule.

What they describe is about the move's own extreme -- stay while price
keeps pushing, leave when it stops pushing hard -- so it is measured at
TICK resolution here, in seconds, rather than on a 5-minute candle grid.

FIRST ATTEMPT AND WHY IT WAS THROWN AWAY
----------------------------------------------------------------------
The first version read the exit off M1 bars and produced SEVEN usable
days: the terminal holds ~72 days of M1 against ~174 of ticks, and the
3x-spread gate then removed most of what was left. Seven trades cannot
rank anything. Ticks fix it -- they are both finer AND deeper here.

The other fix is to stop conflating two questions. WHICH EXIT IS BEST
does not depend on the entry gate, so it is measured on every day that
has ticks (~174) rather than only the gated ones. The gate decides which
days you trade; it is reported separately underneath.

    stall Ns    no new favourable extreme for N seconds -- the closest
                match to "it stopped", and it can leave at minute 2 on a
                day that died at minute 2.
    speed       the last 60s covered less than a fraction of the first
                60s -- explicitly "stopped running HARD".
    fixed Nm    a flat time stop, as a baseline any rule must beat.
    m5x2        the current 15-minute-floor rule, kept so the change is
                measured rather than assumed to be an improvement.

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
WINDOW_MIN = 40                      # ticks pulled per day

MODES = [("stall 30s", "stall", 30), ("stall 60s", "stall", 60),
         ("stall 90s", "stall", 90), ("stall 120s", "stall", 120),
         ("stall 180s", "stall", 180),
         ("speed <50%", "speed", 0.50), ("speed <25%", "speed", 0.25),
         ("fixed 5min", "fixed", 300), ("fixed 15min", "fixed", 900),
         ("m5x2 floor", "m5x2", 0)]


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


def run_exit(sec, px, s, entry, sl, mode, param):
    """sec: seconds since 19:30 for each tick. Returns (points, seconds held)."""
    best = entry
    best_t = sec[0]
    first_win = None
    for k in range(len(sec)):
        p = float(px[k]); t = float(sec[k])
        if ((p <= sl) if s > 0 else (p >= sl)):
            return (sl - entry) * s, t
        if (p > best) if s > 0 else (p < best):
            best, best_t = p, t
        if mode == "stall" and (t - best_t) >= param:
            return (p - entry) * s, t
        if mode == "fixed" and t >= param:
            return (p - entry) * s, t
        if mode == "m5x2":
            # the current rule's real behaviour: a 15-minute floor
            if t >= 900:
                return (p - entry) * s, t
        if mode == "speed":
            if first_win is None and t >= 60:
                first_win = abs(p - entry)
            elif first_win:
                m = sec >= t - 60
                seg = px[m]
                if len(seg) > 1 and (seg.max() - seg.min()) < first_win * param:
                    return (p - entry) * s, t
    return (float(px[-1]) - entry) * s, float(sec[-1])


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

    rows = []          # (date, gated, {label: (pts, secs)})
    today = (datetime.now(timezone.utc) + timedelta(hours=THAI)).date()
    scanned = 0
    for back in range(DAYS, 0, -1):
        d = today - timedelta(days=back)
        if d.weekday() >= 5:
            continue
        s_utc = datetime(d.year, d.month, d.day, TARGET[0] - THAI, TARGET[1],
                         tzinfo=timezone.utc)
        s_srv = s_utc + timedelta(hours=off)
        tk = mt5.copy_ticks_range(SYMBOL, s_srv,
                                  s_srv + timedelta(minutes=WINDOW_MIN),
                                  mt5.COPY_TICKS_ALL)
        if tk is None or len(tk) < 50:
            continue
        t0 = int(s_srv.timestamp() * 1000)
        sec = (tk["time_msc"].astype(np.int64) - t0) / 1000.0
        bid = tk["bid"].astype(float); ask = tk["ask"].astype(float)
        mid = np.where(ask > 0, (bid + ask) / 2.0, bid)
        m = sec <= DECIDE
        if m.sum() < 2:
            continue
        scanned += 1
        moved = float(mid[m][-1] - mid[0])
        if moved == 0:
            continue
        atr = h1_atr_at(SYMBOL, s_srv)
        if not atr:
            continue
        s = 1 if moved > 0 else -1
        i_e = int(np.where(m)[0][-1])
        entry = float(ask[i_e]) if s > 0 else float(bid[i_e])
        sl = entry - s * SL_ATR * atr
        fwd = slice(i_e, None)
        out = {}
        for label, mode, param in MODES:
            out[label] = run_exit(sec[fwd], mid[fwd], s, entry, sl, mode, param)
        rows.append((d, abs(moved) >= GATE_X * spread, out))

    print("=" * 92)
    print(f" EXIT COMPARISON -- {SYMBOL}   entry +{DECIDE}s, SL {SL_ATR}xATR, "
          f"lot {LOT}")
    print(f" {scanned} days had tick history; {len(rows)} produced a trade; "
          f"{sum(1 for r in rows if r[1])} also cleared the {GATE_X}x gate")
    print(f" spread {spread:.2f}   1.0 pt on {LOT} lot = {per_pt:.2f} "
          f"{info.currency_profit}")
    print("=" * 92)
    if len(rows) < 30:
        print(" not enough days"); mt5.shutdown(); return 0

    def table(title, subset):
        if len(subset) < 20:
            print(f"\n{title}: only {len(subset)} trades -- skipped")
            return
        half = len(subset) // 2
        print(f"\n{title}   (n={len(subset)})")
        print(f"{'exit rule':>13}{'half':>7}{'n':>5}{'held':>8}{'win':>7}"
              f"{'avg pt':>9}{'total$':>9}{'maxDD$':>9}{'worst$':>9}")
        print("-" * 78)
        for label, _, _ in MODES:
            for tag, part in (("train", subset[:half]), ("TEST", subset[half:])):
                a = np.array([r[2][label][0] - spread for r in part])
                hs = np.array([r[2][label][1] for r in part]) / 60.0
                eq = np.cumsum(a)
                dd = float(np.max(np.maximum.accumulate(eq) - eq))
                print(f"{label:>13}{tag:>7}{len(a):>5}{hs.mean():>7.1f}m"
                      f"{100.0*np.mean(a > 0):>6.0f}%{a.mean():>+9.2f}"
                      f"{a.sum()*per_pt:>+9.0f}{dd*per_pt:>9.0f}"
                      f"{a.min()*per_pt:>+9.0f}")
            print("-" * 78)

    table("EVERY DAY (no gate) -- this is what ranks the exits", rows)
    table(f"ONLY DAYS THAT CLEARED THE {GATE_X}x GATE",
          [r for r in rows if r[1]])
    print("\n  'held' is average minutes in the trade. Being too slow was the")
    print("  complaint, so a rule that earns more by sitting an hour has not")
    print("  answered it.")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
