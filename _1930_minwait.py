#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_1930_minwait.py -- how early can the bot be allowed to act?

The wait-then-enter rule holds fire until the move clears the gate, so
lowering the MINIMUM wait cannot make it enter on noise -- the gate still
has to be cleared. What it changes is the days where the gate clears
very early: on 4 Sep price covered 30 points in 1.37 seconds, so a
minimum wait of 3s entered well after the level that triggered it.

So the question is only whether earlier permission buys a better fill,
and how often it applies. Both are measured here directly: the share of
sessions whose gate opens before each threshold, and the price
difference between acting at the crossing and waiting to +3s.

THE PHYSICAL FLOOR, stated because a sweep will happily report numbers
below it: the bot polls ticks every 50 ms, and an order still has to
reach the broker. Anything under roughly 0.2 s is not reachable in live
trading no matter what this table says, and a row that only wins there
is describing a fill nobody can get.

Usage:  python _1930_minwait.py [symbol] [days] [gate_x] [weekends:0|1]
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
GATE_X = float(sys.argv[3]) if len(sys.argv) > 3 else 2.0
WEEKENDS = bool(int(sys.argv[4])) if len(sys.argv) > 4 else False
THAI, TARGET_H, TARGET_M = 7, 19, 30
SL_ATR, LOT, N_CTRL, MAXWAIT = 3.0, 0.05, 20, 900
MINWAITS = [0.0, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0]
REACHABLE = 0.2


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


def exit_at_m15(bars, srv_entry, s, entry, sl):
    end = (int(srv_entry) // 900 + 1) * 900
    for b in bars:
        t = int(b["time"])
        if t + 300 <= srv_entry:
            continue
        h, l, c = float(b["high"]), float(b["low"]), float(b["close"])
        if (l <= sl) if s > 0 else (h >= sl):
            return (sl - entry) * s
        if t + 300 >= end:
            return (c - entry) * s
    return (float(bars[-1]["close"]) - entry) * s


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed"); return 2
    info = mt5.symbol_info(SYMBOL)
    if info is None:
        print(f"[ERROR] {SYMBOL} not found"); return 2
    mt5.symbol_select(SYMBOL, True)
    spread = info.spread * info.point
    thr = GATE_X * spread
    tkn = mt5.symbol_info_tick(SYMBOL)
    off = int(round((tkn.time - datetime.now(timezone.utc).timestamp()) / 3600.0))
    _a = mt5.account_info()
    ccy = _a.currency if _a else "?"
    per_pt = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, SYMBOL, LOT,
                                   tkn.ask, tkn.ask + 1.0) or 0.0

    sess = []
    today = (datetime.now(timezone.utc) + timedelta(hours=THAI)).date()
    for back in range(DAYS, 0, -1):
        d = today - timedelta(days=back)
        if d.weekday() >= 5 and not WEEKENDS:
            continue
        s_utc = datetime(d.year, d.month, d.day, TARGET_H - THAI, TARGET_M,
                         tzinfo=timezone.utc)
        s_srv = s_utc + timedelta(hours=off)
        t = mt5.copy_ticks_range(SYMBOL, s_srv,
                                 s_srv + timedelta(seconds=MAXWAIT + 60),
                                 mt5.COPY_TICKS_ALL)
        if t is None or len(t) < 20:
            continue
        atr = h1_atr_at(SYMBOL, s_srv)
        bars = mt5.copy_rates_range(SYMBOL, mt5.TIMEFRAME_M5, s_srv,
                                    s_srv + timedelta(minutes=90))
        if not atr or bars is None or len(bars) < 6:
            continue
        t0 = int(s_srv.timestamp())
        sec = (t["time_msc"].astype(np.int64) - t0 * 1000) / 1000.0
        bid, ask = t["bid"].astype(float), t["ask"].astype(float)
        mid = np.where(ask > 0, (bid + ask) / 2.0, bid)
        sess.append((t0, sec, mid, bid, ask, atr, bars))

    n = len(sess)
    print("=" * 88)
    print(f" MINIMUM WAIT SWEEP -- {SYMBOL}   gate {GATE_X:.0f}x spread = "
          f"{thr:.3f}   exit at M15 close")
    print(f" {n} sessions   spread {spread:.3f}   1.0 pt on {LOT} lot = "
          f"{per_pt:.2f} {ccy}")
    print("=" * 88)
    if n < 30:
        print(" not enough sessions"); mt5.shutdown(); return 0

    # when does the gate first open, ignoring any minimum wait?
    first = []
    for (t0, sec, mid, bid, ask, atr, bars) in sess:
        ref = float(mid[0])
        w = np.where((sec <= MAXWAIT) & (np.abs(mid - ref) >= thr))[0]
        first.append(float(sec[w[0]]) if len(w) else None)
    got = [f for f in first if f is not None]
    print(f"\nWHEN DOES THE GATE FIRST OPEN?  (no minimum wait at all)\n")
    print(f"  it opens at some point on {len(got)} of {n} sessions "
          f"({100.0*len(got)/n:.0f}%)")
    for cut in (0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 30.0, 60.0):
        k = sum(1 for f in got if f <= cut)
        print(f"    within {cut:>5.2f}s : {k:>4} sessions "
              f"({100.0*k/n:>3.0f}% of all days)")

    half = n // 2
    print(f"\nDOES ACTING EARLIER PAY?\n")
    print(f"{'min wait':>10}{'half':>7}{'n':>5}{'entered':>9}{'vs +3s':>9}"
          f"{'win':>6}{'avg pt':>9}{'total':>9}{'ctl':>8}{'z':>7}")
    print("-" * 79)
    for mwt in MINWAITS:
        for tag, lo, hi in (("train", 0, half), ("TEST", half, n)):
            res, dly, gain, ctl = [], [], [], [[] for _ in range(N_CTRL)]
            rngs = [np.random.default_rng(k) for k in range(1, N_CTRL + 1)]
            for (t0, sec, mid, bid, ask, atr, bars) in sess[lo:hi]:
                ref = float(mid[0])
                w = np.where((sec >= mwt) & (sec <= MAXWAIT)
                             & (np.abs(mid - ref) >= thr))[0]
                if len(w) == 0:
                    continue
                i = int(w[0])
                s = 1 if mid[i] > ref else -1
                entry = float(ask[i]) if s > 0 else float(bid[i])
                srv_e = t0 + float(sec[i])
                sl = entry - s * SL_ATR * atr
                res.append(exit_at_m15(bars, srv_e, s, entry, sl) - spread)
                dly.append(float(sec[i]))
                # what the +3s rule would have paid on this same session
                w3 = np.where((sec >= 3.0) & (sec <= MAXWAIT)
                              & (np.abs(mid - ref) >= thr))[0]
                if len(w3):
                    j = int(w3[0])
                    e3 = float(ask[j]) if s > 0 else float(bid[j])
                    gain.append((e3 - entry) * s)
                for k, rg in enumerate(rngs):
                    rs = int(rg.choice((1, -1)))
                    e2 = float(ask[i]) if rs > 0 else float(bid[i])
                    ctl[k].append(exit_at_m15(bars, srv_e, rs, e2,
                                              e2 - rs * SL_ATR * atr) - spread)
            if len(res) < 25:
                print(f"{mwt:>9.2f}s{tag:>7}{len(res):>5}   -- too few --")
                continue
            a = np.array(res)
            cs = [float(np.mean(x)) for x in ctl]
            mu, sd = float(np.mean(cs)), float(np.std(cs, ddof=1))
            z = (a.mean() - mu) / sd if sd > 0 else 0.0
            md = float(np.median(dly))
            dt = f"{md:.1f}s" if md < 120 else f"{md/60:.0f}m"
            gt = f"{np.mean(gain):+.2f}" if gain else "--"
            flag = "   <- below what a live bot can reach" \
                if mwt < REACHABLE else ""
            print(f"{mwt:>9.2f}s{tag:>7}{len(a):>5}{dt:>9}{gt:>9}"
                  f"{100.0*np.mean(a > 0):>5.0f}%{a.mean():>+9.2f}"
                  f"{a.sum()*per_pt:>+9.0f}{mu:>+8.2f}{z:>+7.2f}{flag}")
        print("-" * 79)
    print(f"\n  'vs +3s' is the price advantage over waiting to 3 seconds,")
    print(f"  in points, positive meaning the earlier entry was better.")
    print(f"  Rows under {REACHABLE}s are unreachable live: 50 ms polling plus")
    print(f"  the round trip to the broker. They bound the prize, not the plan.")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
