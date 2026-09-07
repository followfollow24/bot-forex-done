#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_fast_day_sizing.py -- bigger size on the days the gate clears instantly.

The operator's idea: a gate cleared in one or two seconds means a violent
move, so trade those days at 0.05 lot and the ordinary ones at 0.01.

The idea has a real basis -- the fastest days here were 4 Sep, 7 Aug,
3 Sep and 12 Aug, and those were the largest moves of the period. The
question is whether the ACCOUNT can hold the bigger size on exactly those
days, because a move violent enough to cross the gate in a second is also
violent enough to swing hard against the entry before it pays.

So the report is not a P&L. It is: for each fast day, how far the trade
went against you at 0.05 lot, against the equity standing at that moment.
A day that blows the account is not a day you collect the upside on, and
the equity curve stops there.

FAST_SEC is swept, since "instantly" is not defined -- one second, five,
ten and thirty are all defensible readings of the same intuition.

Usage:  python _fast_day_sizing.py [symbol] [days] [gate_pts] [equity]
"""
import sys
from datetime import datetime, timedelta, timezone

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)"); sys.exit(1)

import numpy as np

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "XAUAUDm"
DAYS = int(sys.argv[2]) if len(sys.argv) > 2 else 95
GATE_PTS = float(sys.argv[3]) if len(sys.argv) > 3 else 14.0
EQUITY0 = float(sys.argv[4]) if len(sys.argv) > 4 else 43.38
BIG, SMALL = 0.05, 0.01
HOLD_MIN, SL_ATR, MIN_WAIT, MAXW, THAI = 30.0, 3.0, 1.0, 900, 7
FAST_GRID = [1, 2, 5, 10, 30]


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


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed"); return 2
    info = mt5.symbol_info(SYMBOL)
    if info is None:
        print(f"[ERROR] {SYMBOL} not found"); return 2
    mt5.symbol_select(SYMBOL, True)
    spread = info.spread * info.point
    tkn = mt5.symbol_info_tick(SYMBOL)
    pp = {}
    for lot in (BIG, SMALL):
        v = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, SYMBOL, lot,
                                  tkn.ask, tkn.ask + 1.0)
        if not v:
            print("[ERROR] the broker would not price this symbol -- MT5 may "
                  "have lost its connection or the market is shut")
            mt5.shutdown(); return 2
        pp[lot] = float(v)
    ac = mt5.account_info()
    ccy = ac.currency if ac else "?"

    trades = []
    today = (datetime.now(timezone.utc) + timedelta(hours=THAI)).date()
    for back in range(DAYS, 0, -1):
        d = today - timedelta(days=back)
        if d.weekday() >= 5:
            continue
        s_utc = datetime(d.year, d.month, d.day, 12, 30, tzinfo=timezone.utc)
        t = mt5.copy_ticks_range(SYMBOL, s_utc,
                                 s_utc + timedelta(seconds=MAXW + 60),
                                 mt5.COPY_TICKS_ALL)
        bars = mt5.copy_rates_range(SYMBOL, mt5.TIMEFRAME_M5, s_utc,
                                    s_utc + timedelta(minutes=120))
        atr = atr_at(SYMBOL, s_utc)
        if t is None or len(t) < 20 or bars is None or len(bars) < 6 or not atr:
            continue
        t0 = int(s_utc.timestamp())
        sec = (t["time_msc"].astype(np.int64) - t0 * 1000) / 1000.0
        bid, ask = t["bid"].astype(float), t["ask"].astype(float)
        mid = np.where(ask > 0, (bid + ask) / 2.0, bid)
        ref = float(mid[0])
        w = np.where((sec >= MIN_WAIT) & (np.abs(mid - ref) >= GATE_PTS))[0]
        if len(w) == 0:
            continue
        i = int(w[0])
        s = 1 if mid[i] > ref else -1
        entry = float(ask[i]) if s > 0 else float(bid[i])
        sl = entry - s * SL_ATR * atr
        srv_e = t0 + float(sec[i])
        end = srv_e + HOLD_MIN * 60
        exit_px, worst_pts = None, 0.0
        for b in bars:
            bt = int(b["time"])
            if bt + 300 <= srv_e:
                continue
            h, l, c = float(b["high"]), float(b["low"]), float(b["close"])
            worst_pts = min(worst_pts, ((l if s > 0 else h) - entry) * s)
            if ((l <= sl) if s > 0 else (h >= sl)):
                exit_px = sl; break
            if bt + 300 >= end:
                exit_px = c; break
        if exit_px is None:
            exit_px = float(bars[-1]["close"])
        pts = (exit_px - entry) * s - spread
        trades.append((d, float(sec[i]), s, pts, worst_pts))

    print("=" * 90)
    print(f" BIG LOT ON FAST DAYS -- {SYMBOL}   gate {GATE_PTS:.0f} pts   "
          f"hold {HOLD_MIN:.0f} min")
    print(f" {len(trades)} trades   fast day = {BIG} lot, ordinary = {SMALL}"
          f"   start {EQUITY0:.2f} {ccy}")
    print(f" 1 pt = {pp[SMALL]:.3f} at {SMALL}, {pp[BIG]:.3f} at {BIG}"
          f"   -> account ends at {EQUITY0/pp[BIG]:.1f} pts on a {BIG} lot, "
          f"{EQUITY0/pp[SMALL]:.0f} on {SMALL}")
    print("=" * 90)
    if not trades:
        print(" no trades"); mt5.shutdown(); return 0

    print(f"\nTHE FAST DAYS (gate cleared inside 10s) AT {BIG} LOT\n")
    print(f"  {'date':>13}{'at':>8}{'side':>6}{'pts':>9}{'P/L':>10}"
          f"{'went against':>14}{'vs ' + format(EQUITY0, '.2f'):>12}")
    for (d, sc, s, pts, wp) in trades:
        if sc > 10:
            continue
        print(f"  {d:%Y-%m-%d}{sc:>7.1f}s{'BUY' if s > 0 else 'SELL':>6}"
              f"{pts:>+9.1f}{pts*pp[BIG]:>+10.0f}{wp*pp[BIG]:>+14.0f}"
              + ("   BLOWS UP" if -wp * pp[BIG] >= EQUITY0 else "   survives"))

    print(f"\nSWEEP -- what counts as 'fast'\n")
    print(f"{'fast <=':>9}{'big days':>10}{'small days':>12}{'total':>10}"
          f"{'blowups':>9}{'ends at':>10}   note")
    print("-" * 74)
    for fs in FAST_GRID:
        eq, blow, nb = EQUITY0, 0, 0
        for (d, sc, s, pts, wp) in trades:
            lot = BIG if sc <= fs else SMALL
            if lot == BIG:
                nb += 1
            if -wp * pp[lot] >= eq:
                blow += 1
            eq += pts * pp[lot]
        note = "account survives" if blow == 0 else f"first blow-up kills it"
        print(f"{fs:>8}s{nb:>10}{len(trades)-nb:>12}{eq-EQUITY0:>+10.0f}"
              f"{blow:>9}{eq:>10.0f}   {note}")
    # all-small baseline
    eq, blow = EQUITY0, 0
    for (d, sc, s, pts, wp) in trades:
        if -wp * pp[SMALL] >= eq:
            blow += 1
        eq += pts * pp[SMALL]
    print(f"{'never':>9}{0:>10}{len(trades):>12}{eq-EQUITY0:>+10.0f}"
          f"{blow:>9}{eq:>10.0f}   all {SMALL} lot, for comparison")
    print("-" * 74)
    print("  A blow-up is not a drawdown you sit through: the broker closes the")
    print("  position and there is no account left, so every later row in that")
    print("  column is a trade that could never have been placed.")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
