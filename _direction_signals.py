#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_direction_signals.py -- five ways to read which way 19:30 is going,
scored on the same sessions with the same exit and the same account.

The live rule reads direction from the first tick that gets a small
distance from the 19:30 price. On 4 Sep that tick pointed UP one second
before the price fell seventy-seven points, so the question is whether
some other reading of "which way is it going" would have been right.

Only the direction rule changes. Exit is the M15 close, size is the same,
the stop is the same, and the account carries forward identically, so any
difference in the column belongs to the signal and nothing else.

  tick-gate     the live rule: first tick >= gate from the 19:30 price
  tick-gate x4  the same, but demanding four times the distance
  60s-move      net movement over the first sixty seconds
  m5-close      wait for the 19:30 M5 candle to CLOSE and take its
                direction, entering at 19:35. This is the only signal in
                the whole project that beat a random control in all six
                train/TEST cells (z +1.77 to +2.46).
  m5-vs-ema20   the M5 close relative to its own EMA20 -- distance from a
                reference level rather than from the bell price, which is
                what "how far the chart is stretched" actually measures.

Usage:  python _direction_signals.py [symbol] [days] [equity]
"""
import sys
from datetime import datetime, timedelta, timezone

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)"); sys.exit(1)

import numpy as np

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "XAUAUDm"
DAYS = int(sys.argv[2]) if len(sys.argv) > 2 else 30
EQUITY0 = float(sys.argv[3]) if len(sys.argv) > 3 else 43.38
LOT, GATE_USD, SL_ATR, MIN_WAIT, MAX_WAIT, THAI = 0.05, 10.0, 3.0, 1.0, 900, 7


def atr_at(sym, when, n=14):
    r = mt5.copy_rates_range(sym, mt5.TIMEFRAME_H1,
                             when - timedelta(hours=40), when)
    if r is None or len(r) < n + 1:
        return None
    trs = [max(float(r[i]["high"]) - float(r[i]["low"]),
               abs(float(r[i]["high"]) - float(r[i - 1]["close"])),
               abs(float(r[i]["low"]) - float(r[i - 1]["close"])))
           for i in range(1, len(r))]
    return sum(trs[-n:]) / n


def ema(a, n):
    k = 2.0 / (n + 1)
    out = float(a[0])
    for v in a[1:]:
        out = float(v) * k + out * (1 - k)
    return out


def run_from(bars, srv_e, s, entry, sl, spread, per_pt):
    """Hold to the close of the M15 candle containing srv_e."""
    end = (int(srv_e) // 900 + 1) * 900
    exit_px, worst = None, 0.0
    for b in bars:
        bt = int(b["time"])
        if bt + 300 <= srv_e:
            continue
        h, l, c = float(b["high"]), float(b["low"]), float(b["close"])
        worst = min(worst, ((l if s > 0 else h) - entry) * s)
        if ((l <= sl) if s > 0 else (h >= sl)):
            exit_px = sl; break
        if bt + 300 >= end:
            exit_px = c; break
    if exit_px is None:
        exit_px = float(bars[-1]["close"])
    pts = (exit_px - entry) * s - spread
    return pts * per_pt, worst * per_pt


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed"); return 2
    info = mt5.symbol_info(SYMBOL)
    if info is None:
        print(f"[ERROR] {SYMBOL} not found"); return 2
    mt5.symbol_select(SYMBOL, True)
    spread = info.spread * info.point
    tkk = mt5.symbol_info_tick(SYMBOL)
    per_pt = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, SYMBOL, LOT,
                                   tkk.ask, tkk.ask + 1.0) or 0.0
    ac = mt5.account_info()
    ccy = ac.currency if ac else "?"
    gate = GATE_USD / per_pt if per_pt else 2 * spread

    names = ["tick-gate", "tick-gate x4", "60s-move", "m5-close", "m5-vs-ema20"]
    res = {n: [] for n in names}
    sep = {n: [] for n in names}      # (date, side, pnl, worst) for 4 Sep
    today = (datetime.now(timezone.utc) + timedelta(hours=THAI)).date()

    for back in range(DAYS, 0, -1):
        d = today - timedelta(days=back)
        if d.weekday() >= 5:
            continue
        s_utc = datetime(d.year, d.month, d.day, 12, 30, tzinfo=timezone.utc)
        bars = mt5.copy_rates_range(SYMBOL, mt5.TIMEFRAME_M5,
                                    s_utc - timedelta(hours=3),
                                    s_utc + timedelta(minutes=90))
        atr = atr_at(SYMBOL, s_utc)
        if bars is None or len(bars) < 30 or not atr:
            continue
        t0 = int(s_utc.timestamp())
        after = [b for b in bars if int(b["time"]) >= t0]
        before = [b for b in bars if int(b["time"]) < t0]
        if len(after) < 4 or len(before) < 20:
            continue
        bell_bar = after[0]                      # the 19:30-19:35 M5 candle

        t = mt5.copy_ticks_range(SYMBOL, s_utc,
                                 s_utc + timedelta(seconds=MAX_WAIT + 60),
                                 mt5.COPY_TICKS_ALL)
        have_ticks = t is not None and len(t) >= 20
        if have_ticks:
            sec = (t["time_msc"].astype(np.int64) - t0 * 1000) / 1000.0
            bid, ask = t["bid"].astype(float), t["ask"].astype(float)
            mid = np.where(ask > 0, (bid + ask) / 2.0, bid)
            ref = float(mid[0])

        def tick_entry(g):
            w = np.where((sec >= MIN_WAIT) & (np.abs(mid - ref) >= g))[0]
            if len(w) == 0:
                return None
            i = int(w[0])
            s = 1 if mid[i] > ref else -1
            return s, (float(ask[i]) if s > 0 else float(bid[i])), t0 + float(sec[i])

        cands = {}
        if have_ticks:
            cands["tick-gate"] = tick_entry(gate)
            cands["tick-gate x4"] = tick_entry(gate * 4)
            m = sec <= 60.0
            if m.sum() >= 2 and mid[m][-1] != ref:
                s = 1 if mid[m][-1] > ref else -1
                i = int(np.where(m)[0][-1])
                cands["60s-move"] = (s, float(ask[i]) if s > 0 else float(bid[i]),
                                     t0 + 60.0)
        # bar-based signals enter at the OPEN of the next M5 bar (19:35)
        nxt = after[1]
        body = float(bell_bar["close"]) - float(bell_bar["open"])
        if body != 0:
            s = 1 if body > 0 else -1
            cands["m5-close"] = (s, float(nxt["open"]), int(nxt["time"]))
        closes = [float(b["close"]) for b in before[-40:]] + [float(bell_bar["close"])]
        e20 = ema(closes, 20)
        dist = float(bell_bar["close"]) - e20
        if dist != 0:
            s = 1 if dist > 0 else -1
            cands["m5-vs-ema20"] = (s, float(nxt["open"]), int(nxt["time"]))

        for n, c in cands.items():
            if c is None:
                continue
            s, entry, srv_e = c
            pnl, worst = run_from(after, srv_e, s, entry,
                                  entry - s * SL_ATR * atr, spread, per_pt)
            res[n].append((d, s, pnl, worst))

    print("=" * 84)
    print(f" DIRECTION SIGNALS AT 19:30 -- {SYMBOL}  {LOT} lot  exit at the "
          f"M15 close")
    print(f" last {DAYS} days   1 pt = {per_pt:.3f} {ccy}   start {EQUITY0:.2f}"
          f"   account ends at {EQUITY0/per_pt:.1f} pts against")
    print("=" * 84)
    print(f"\n{'signal':>14}{'trades':>8}{'win':>7}{'total':>10}{'best':>9}"
          f"{'worst':>9}{'blowups':>9}{'4 Sep':>16}")
    print("-" * 84)
    for n in names:
        r = res[n]
        if not r:
            print(f"{n:>14}{'-- no data --':>20}")
            continue
        p = np.array([x[2] for x in r])
        eq, blow = EQUITY0, 0
        for x in r:
            if -x[3] >= eq:
                blow += 1
            eq += x[2]
        sep4 = [x for x in r if x[0].month == 9 and x[0].day == 4]
        s4 = (f"{'BUY' if sep4[0][1] > 0 else 'SELL'} {sep4[0][2]:+.0f}"
              if sep4 else "--")
        print(f"{n:>14}{len(p):>8}{100.0*np.mean(p > 0):>6.0f}%"
              f"{p.sum():>+10.0f}{p.max():>+9.0f}{p.min():>+9.0f}"
              f"{blow:>9}{s4:>16}")
    print("-" * 84)
    print("  'blowups' counts sessions whose worst moment exceeded the equity")
    print("  standing before that trade. '4 Sep' is the day the operator")
    print("  circled -- the live rule bought one second before a 77 point fall.")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
