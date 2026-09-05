#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_pyramid_test.py -- does adding to a winner help, on this rule?

The operator wants to add lots while the move keeps running their way.
That is NOT the "fire repeatedly regardless" idea measured earlier and
rejected -- adding only when the trade is already right is a different
payoff shape, and it deserves its own measurement rather than an
assertion.

Two things have to come out of this, and only one of them is about
profit:

  1. Does it earn more? Adding to a winner raises expectancy only if the
     move CONTINUES past the point where you add. The continuation
     studies here found drift of about 0.07 xATR at thirty minutes, so
     the prior is that it does not -- but that was measured on entries,
     not on adds, and the two are different conditional questions.

  2. WOULD IT HAVE KILLED THE ACCOUNT? At 51.46 USD equity and 3.603
     USD per point per 0.05 lot, one position is wiped by a 14.3 point
     move against, two by 7.1, three by 4.8. The typical 30-minute move
     at 19:30 is 8.6 points. So the number that matters is not average
     profit, it is how many individual sessions would have taken the
     whole account -- reported per configuration, in days.

Entry, gate and exit are the live bot's. The only thing added is the
pyramid.

Usage:  python _pyramid_test.py [symbol] [days] [gate_usd] [lot]

An add step of 0 means every entry fills at the same price, which is what
"fire three in the moment it is running" actually is -- three positions
within a few points of each other behave as one position of three times
the size, and the only thing that changes is how far the account can move
against them before the broker closes it.
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
GATE_USD = float(sys.argv[3]) if len(sys.argv) > 3 else 10.0
LOT = float(sys.argv[4]) if len(sys.argv) > 4 else 0.05
SL_ATR, MIN_WAIT, MAXWAIT = 3.0, 1.0, 900
EQUITY = 51.46
ADD_STEPS = [0.0, 3.0, 10.0]        # 0 = all at once, in the moment
MAX_ADDS = [0, 1, 2, 3]


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


def session(bars, srv_entry, s, entry, sl, step, max_adds, per_pt):
    """Returns (net USD, worst equity drawdown in USD, lots used).

    Adds are placed at fixed favourable distances from the FIRST entry,
    each at the same lot. Exit is the M15 close for the whole stack, as
    the live bot does. Drawdown is tracked on the running stack, because
    the account is what stops this trade, not the stop-loss.
    """
    end = (int(srv_entry) // 900 + 1) * 900
    fills = [entry]
    worst = 0.0
    for b in bars:
        t = int(b["time"])
        if t + 300 <= srv_entry:
            continue
        h, l, c = float(b["high"]), float(b["low"]), float(b["close"])
        adverse = (l if s > 0 else h)
        favour = (h if s > 0 else l)
        # worst point of the bar, priced on the stack held at that moment
        loss_pts = sum((adverse - f) * s for f in fills)
        worst = min(worst, loss_pts * per_pt)
        if ((l <= sl) if s > 0 else (h >= sl)):
            pts = sum((sl - f) * s for f in fills)
            return pts * per_pt, worst, len(fills) * LOT
        while len(fills) - 1 < max_adds:
            nxt = entry + s * step * len(fills)
            # step 0 puts every entry at the same price: "three in the
            # moment", which is one position of three times the size.
            if step <= 0 or ((favour >= nxt) if s > 0 else (favour <= nxt)):
                fills.append(nxt)
            else:
                break
        if t + 300 >= end:
            pts = sum((c - f) * s for f in fills)
            return pts * per_pt, worst, len(fills) * LOT
    c = float(bars[-1]["close"])
    return sum((c - f) * s for f in fills) * per_pt, worst, len(fills) * LOT


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed"); return 2
    info = mt5.symbol_info(SYMBOL)
    if info is None:
        print(f"[ERROR] {SYMBOL} not found"); return 2
    mt5.symbol_select(SYMBOL, True)
    spread = info.spread * info.point
    tkn = mt5.symbol_info_tick(SYMBOL)
    per_pt = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, SYMBOL, LOT,
                                   tkn.ask, tkn.ask + 1.0) or 0.0
    _a = mt5.account_info()
    ccy = _a.currency if _a else "?"
    gate = GATE_USD / per_pt if per_pt else 2.0 * spread

    sess = []
    today = (datetime.now(timezone.utc) + timedelta(hours=7)).date()
    for back in range(DAYS, 0, -1):
        d = today - timedelta(days=back)
        if d.weekday() >= 5:
            continue
        s_utc = datetime(d.year, d.month, d.day, 12, 30, tzinfo=timezone.utc)
        t = mt5.copy_ticks_range(SYMBOL, s_utc,
                                 s_utc + timedelta(seconds=MAXWAIT + 60),
                                 mt5.COPY_TICKS_ALL)
        if t is None or len(t) < 20:
            continue
        atr = h1_atr_at(SYMBOL, s_utc)
        bars = mt5.copy_rates_range(SYMBOL, mt5.TIMEFRAME_M5, s_utc,
                                    s_utc + timedelta(minutes=90))
        if not atr or bars is None or len(bars) < 6:
            continue
        t0 = int(s_utc.timestamp())
        sec = (t["time_msc"].astype(np.int64) - t0 * 1000) / 1000.0
        bid, ask = t["bid"].astype(float), t["ask"].astype(float)
        mid = np.where(ask > 0, (bid + ask) / 2.0, bid)
        ref = float(mid[0])
        w = np.where((sec >= MIN_WAIT) & (np.abs(mid - ref) >= gate))[0]
        if len(w) == 0:
            continue
        i = int(w[0])
        s = 1 if mid[i] > ref else -1
        entry = float(ask[i]) if s > 0 else float(bid[i])
        sess.append((t0 + float(sec[i]), s, entry, entry - s * SL_ATR * atr, bars))

    n = len(sess)
    print("=" * 84)
    print(f" PYRAMID TEST -- {SYMBOL}   gate {GATE_USD:.0f} {ccy} = "
          f"{gate:.3f} pts   {LOT} lot per entry")
    print(f" {n} sessions   1 pt = {per_pt:.3f} {ccy}   equity {EQUITY:.2f}")
    print("=" * 84)
    if n < 30:
        print(" not enough sessions"); mt5.shutdown(); return 0

    print(f"  account dies at {EQUITY/per_pt:.1f} pts on one {LOT} lot, "
          f"{EQUITY/per_pt/2:.1f} on two, {EQUITY/per_pt/3:.1f} on three")
    print(f"\n{'add every':>11}{'max adds':>10}{'trades':>8}{'avg lots':>10}"
          f"{'win':>6}{'avg $':>9}{'total $':>10}{'worst day $':>13}"
          f"{'BLEW UP':>9}")
    print("-" * 86)
    for step in ADD_STEPS:
        for madd in MAX_ADDS:
            res, worsts, lots = [], [], []
            for (se, s, entry, sl, bars) in sess:
                pnl, worst, lot_used = session(bars, se, s, entry, sl,
                                               step, madd, per_pt)
                res.append(pnl - spread * per_pt * (lot_used / LOT))
                worsts.append(worst)
                lots.append(lot_used)
            a = np.array(res)
            blew = sum(1 for w in worsts if -w >= EQUITY)
            print(f"{step:>10.0f}p{madd:>10}{len(a):>8}{np.mean(lots):>10.3f}"
                  f"{100.0*np.mean(a > 0):>5.0f}%{a.mean():>+9.2f}"
                  f"{a.sum():>+10.0f}{min(worsts):>+13.0f}"
                  f"{blew:>7} d" + ("  <<<" if blew else ""))
        print("-" * 86)
    print(f"\n  'BLEW UP' counts sessions whose worst moment cost more than the")
    print(f"  whole {EQUITY:.2f} {ccy} account. Those are not drawdowns you sit")
    print(f"  through -- the broker closes the position and the account is done,")
    print(f"  so every later row in the table would never have happened.")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
