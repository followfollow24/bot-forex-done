#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_1930_wait.py -- wait AT LEAST 3 seconds, then keep watching until the
chart shows its hand, and enter whenever that is.

I had this wrong. The bot reads direction at exactly +3s and skips the
day if the move is small, which is why it missed 77-84% of the sessions
that went on to run: a move covering 11-25 points over a quarter hour
usually does not announce itself in its first three seconds. The
operator's rule is different -- three seconds is a MINIMUM wait, not a
verdict. Keep watching; enter when it is clear; the clock time of entry
is whatever it turns out to be.

That should fix the miss rate by construction, so what this measures is
what it costs. Two things move in opposite directions as the gate rises:

    a low gate triggers almost every day but enters on noise
    a high gate enters on real moves but arrives late, after the part
    of the move that would have paid for the trade

The sweep is over gate size AND how long you are willing to wait, and
the median trigger delay is reported beside the money, because "it
entered at +14 minutes" and "it entered at +4 seconds" are different
trades wearing the same name.

Entry is at the tick that crosses the gate. Exit is the M15 candle close
the operator chose. Stop 3xATR. Controls are 20 random-direction draws
on the same triggered sessions, so the gate is held constant and only
the direction call is tested.

Usage:  python _1930_wait.py [symbol] [days] [min_wait_s] [weekends:0|1]
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
MIN_WAIT = float(sys.argv[3]) if len(sys.argv) > 3 else 3.0
WEEKENDS = bool(int(sys.argv[4])) if len(sys.argv) > 4 else False
THAI, TARGET_H, TARGET_M = 7, 19, 30
SL_ATR, LOT, N_CTRL = 3.0, 0.05, 20
GATES = [1.0, 2.0, 3.0, 5.0, 10.0]        # x spread
MAXWAITS = [5, 15, 30]                    # minutes


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
    """Out at the close of the M15 candle holding the entry; stop first
    if it was touched on the way."""
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
    tkn = mt5.symbol_info_tick(SYMBOL)
    off = int(round((tkn.time - datetime.now(timezone.utc).timestamp()) / 3600.0))
    _a = mt5.account_info()
    ccy = _a.currency if _a else "?"
    per_pt = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, SYMBOL, LOT,
                                   tkn.ask, tkn.ask + 1.0) or 0.0

    sessions = []
    today = (datetime.now(timezone.utc) + timedelta(hours=THAI)).date()
    for back in range(DAYS, 0, -1):
        d = today - timedelta(days=back)
        if d.weekday() >= 5 and not WEEKENDS:
            continue
        s_utc = datetime(d.year, d.month, d.day, TARGET_H - THAI, TARGET_M,
                         tzinfo=timezone.utc)
        s_srv = s_utc + timedelta(hours=off)
        t = mt5.copy_ticks_range(SYMBOL, s_srv,
                                 s_srv + timedelta(minutes=max(MAXWAITS) + 1),
                                 mt5.COPY_TICKS_ALL)
        if t is None or len(t) < 20:
            continue
        atr = h1_atr_at(SYMBOL, s_srv)
        if not atr:
            continue
        bars = mt5.copy_rates_range(SYMBOL, mt5.TIMEFRAME_M5, s_srv,
                                    s_srv + timedelta(minutes=90))
        if bars is None or len(bars) < 6:
            continue
        t0 = int(s_srv.timestamp())
        sec = (t["time_msc"].astype(np.int64) - t0 * 1000) / 1000.0
        bid, ask = t["bid"].astype(float), t["ask"].astype(float)
        mid = np.where(ask > 0, (bid + ask) / 2.0, bid)
        sessions.append((d, t0, sec, mid, bid, ask, atr, bars))

    n = len(sessions)
    print("=" * 92)
    print(f" WAIT-THEN-ENTER -- {SYMBOL}   min wait {MIN_WAIT:.0f}s, "
          f"exit at the M15 close, SL {SL_ATR}xATR, lot {LOT}")
    print(f" {n} sessions with tick history   spread {spread:.3f}   "
          f"1.0 pt = {per_pt:.2f} {ccy}")
    print("=" * 92)
    if n < 30:
        print(" not enough sessions"); mt5.shutdown(); return 0
    half = n // 2

    print(f"\n{'gate':>7}{'wait<=':>8}{'half':>7}{'fired':>7}{'n':>5}"
          f"{'entered':>9}{'win':>6}{'avg pt':>9}{'total':>9}{'ctl':>8}{'z':>7}")
    print("-" * 92)
    for g in GATES:
        thr = g * spread
        for mw in MAXWAITS:
            for tag, lo, hi in (("train", 0, half), ("TEST", half, n)):
                res, delays, ctl = [], [], [[] for _ in range(N_CTRL)]
                rngs = [np.random.default_rng(k) for k in range(1, N_CTRL + 1)]
                tried = 0
                for (d, t0, sec, mid, bid, ask, atr, bars) in sessions[lo:hi]:
                    tried += 1
                    ref = float(mid[0])
                    w = np.where((sec >= MIN_WAIT) & (sec <= mw * 60)
                                 & (np.abs(mid - ref) >= thr))[0]
                    if len(w) == 0:
                        continue
                    i = int(w[0])
                    s = 1 if mid[i] > ref else -1
                    entry = float(ask[i]) if s > 0 else float(bid[i])
                    srv_entry = t0 + float(sec[i])
                    sl = entry - s * SL_ATR * atr
                    res.append(exit_at_m15(bars, srv_entry, s, entry, sl) - spread)
                    delays.append(float(sec[i]))
                    for k, rg in enumerate(rngs):
                        rs = int(rg.choice((1, -1)))
                        e2 = float(ask[i]) if rs > 0 else float(bid[i])
                        ctl[k].append(
                            exit_at_m15(bars, srv_entry, rs, e2,
                                        e2 - rs * SL_ATR * atr) - spread)
                if len(res) < 25:
                    print(f"{g:>6.0f}x{mw:>7}m{tag:>7}"
                          f"{100.0*len(res)/max(tried,1):>6.0f}%{len(res):>5}"
                          f"   -- too few --")
                    continue
                a = np.array(res)
                cs = [float(np.mean(x)) for x in ctl]
                mu, sd = float(np.mean(cs)), float(np.std(cs, ddof=1))
                z = (a.mean() - mu) / sd if sd > 0 else 0.0
                dly = np.median(delays)
                dtxt = f"{dly:.0f}s" if dly < 120 else f"{dly/60:.0f}m"
                print(f"{g:>6.0f}x{mw:>7}m{tag:>7}"
                      f"{100.0*len(a)/tried:>6.0f}%{len(a):>5}{dtxt:>9}"
                      f"{100.0*np.mean(a > 0):>5.0f}%{a.mean():>+9.2f}"
                      f"{a.sum()*per_pt:>+9.0f}{mu:>+8.2f}{z:>+7.2f}"
                      f"{'  <<<' if abs(z) >= 2 else ''}")
        print("-" * 92)
    print("\n  'entered' is the median delay after 19:30:00. A cell that only")
    print("  works by entering 12 minutes late is a different trade from the")
    print("  one being described, whatever its P&L says.")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
