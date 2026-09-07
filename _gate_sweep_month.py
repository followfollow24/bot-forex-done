#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_gate_sweep_month.py -- what does the gate buy, across its whole range?

The operator noticed that at 11.1 points more sessions lose than win, and
asked which gate would win every day.

None will. A rule that never has a losing session would mean the next
half hour is knowable, and it is not. What the gate actually trades is
win RATE against trade COUNT, and the honest answer is the shape of that
trade-off rather than a number that does not exist.

Reported per gate: how many days it fires, how many of those win, the
month's total, the worst single session, and how many sessions would have
ended the account. Win rate and total are printed side by side on purpose
-- a high win rate on three trades is not better than a lower one on
eleven, and only reading them together shows that.

Usage:  python _gate_sweep_month.py [symbol] [days] [equity] [lot] [hold_min]
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
LOT = float(sys.argv[4]) if len(sys.argv) > 4 else 0.01
HOLD = float(sys.argv[5]) if len(sys.argv) > 5 else 30.0
SL_ATR, MIN_WAIT, MAXW, THAI = 3.0, 1.0, 900, 7
GATES = [3, 5, 8, 11, 14, 17, 20, 25, 30, 40, 50]


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
    per_pt = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, SYMBOL, LOT,
                                   tkn.ask, tkn.ask + 1.0) or 0.0
    if not per_pt:
        print("[ERROR] the broker would not price a {} lot of {} "
              "-- order_calc_profit returned nothing. That usually\n"
              "means the MT5 terminal has lost its connection, or the\n"
              "market for this symbol is shut. Check MT5 before trusting\n"
              "anything else.".format(LOT if "LOT" in dir() else "?", SYMBOL))
        mt5.shutdown(); return 2
    ac = mt5.account_info()
    ccy = ac.currency if ac else "?"

    # load every session once, then score all gates against it
    sess = []
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
        sess.append((d, t0, sec, mid, bid, ask, atr, bars))

    print("=" * 92)
    print(f" GATE SWEEP OVER THE MONTH -- {SYMBOL}  {LOT} lot  hold {HOLD:.0f} min"
          f"  SL {SL_ATR}xATR")
    print(f" {len(sess)} sessions   1 pt = {per_pt:.3f} {ccy}   start "
          f"{EQUITY0:.2f}   account ends at {EQUITY0/per_pt:.0f} pts against")
    print("=" * 92)
    print(f"\n{'gate':>7}{'in USD':>9}{'fires':>8}{'trades':>8}{'wins':>7}"
          f"{'win %':>8}{'total':>9}{'best':>8}{'worst':>8}{'blowups':>9}")
    print("-" * 84)

    for g in GATES:
        res, worsts = [], []
        eq, blow = EQUITY0, 0
        for (d, t0, sec, mid, bid, ask, atr, bars) in sess:
            ref = float(mid[0])
            w = np.where((sec >= MIN_WAIT) & (np.abs(mid - ref) >= g))[0]
            if len(w) == 0:
                continue
            i = int(w[0])
            s = 1 if mid[i] > ref else -1
            entry = float(ask[i]) if s > 0 else float(bid[i])
            sl = entry - s * SL_ATR * atr
            srv_e = t0 + float(sec[i])
            end = srv_e + HOLD * 60
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
            pl = ((exit_px - entry) * s - spread) * per_pt
            if -worst * per_pt >= eq:
                blow += 1
            eq += pl
            res.append(pl); worsts.append(worst * per_pt)
        if not res:
            print(f"{g:>6}p{g*per_pt:>9.2f}{0:>8}   -- never fires --")
            continue
        a = np.array(res)
        wins = int(np.sum(a > 0))
        print(f"{g:>6}p{g*per_pt:>9.2f}{100.0*len(a)/len(sess):>7.0f}%"
              f"{len(a):>8}{wins:>7}{100.0*wins/len(a):>7.0f}%"
              f"{a.sum():>+9.0f}{a.max():>+8.0f}{a.min():>+8.0f}{blow:>9}")

    print("-" * 84)
    print("  No gate wins every session, and one that did would mean the next")
    print("  half hour is knowable. Read win % and total together: a high rate")
    print("  on three trades is not better than a lower one on eleven.")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
