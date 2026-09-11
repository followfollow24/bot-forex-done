#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_double_or_bust.py -- 50 USD, a 50 USD target, and a lot big enough that
the account can end. Exactly what the operator asked for, measured.

THE ARITHMETIC FIRST, because it decides the whole thing. Aiming to make
your entire balance means the take-profit distance and the distance that
empties the account are THE SAME NUMBER:

    target points = 50 / point_value
    ruin   points = 50 / point_value

They are the same expression. Changing the lot moves both together -- a
bigger lot brings the target nearer and brings ruin exactly as near. So
this is a symmetric bet: reach +X first and the balance doubles, reach -X
first and there is nothing left, and the spread is paid either way.

Everything therefore rests on ONE number: the probability of touching +X
before -X, after a gate entry at 19:30. Above 50% and repeated bets
compound; at or below 50%, repeated bets end at zero with certainty and
the only question is how many trades it takes.

So this measures that probability from ticks -- which is the only data
that can say which side was touched FIRST when both are a few points away
-- for each lot the 50 USD of margin actually allows, and then reports
what a run of such bets does.

Nothing is optimised. The gate is 11 or 14 as specified, the target is the
balance as specified, and the lot is swept because that is the question.

Usage:  python _double_or_bust.py [symbol] [days] [equity]
"""
import sys
from datetime import datetime, timedelta, timezone

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)"); sys.exit(1)

import numpy as np

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "XAUUSDm"
DAYS = int(sys.argv[2]) if len(sys.argv) > 2 else 365
EQUITY = float(sys.argv[3]) if len(sys.argv) > 3 else 50.0
GATES = [11.0, 14.0]
LOTS = [0.01, 0.02, 0.03, 0.05, 0.08, 0.10]
MIN_WAIT, MAXW, THAI = 1.0, 900, 7
WATCH_H = 12.0            # hours to keep looking for the first touch


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed"); return 2
    si = mt5.symbol_info(SYMBOL)
    if si is None:
        print(f"[ERROR] {SYMBOL} not found"); mt5.shutdown(); return 2
    mt5.symbol_select(SYMBOL, True)
    spread = si.spread * si.point
    tk = mt5.symbol_info_tick(SYMBOL)

    print("=" * 78)
    print(f" DOUBLE OR BUST -- {SYMBOL}   equity {EQUITY:.0f} USD"
          f"   target {EQUITY:.0f} USD   spread {spread:.3f} pts")
    print("=" * 78)
    print(f"\n{'lot':>6}{'1 pt USD':>10}{'target pts':>12}{'ruin pts':>10}"
          f"{'margin':>9}{'fits in ' + str(int(EQUITY)):>12}")
    usable = []
    for lot in LOTS:
        pv = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, SYMBOL, lot,
                                   tk.ask, tk.ask + 1.0) if tk else None
        mg = mt5.order_calc_margin(mt5.ORDER_TYPE_BUY, SYMBOL, lot, tk.ask) \
            if tk else None
        if not pv or mg is None:
            continue
        pv, mg = float(pv), float(mg)
        d = EQUITY / pv
        fits = mg < EQUITY * 0.9
        print(f"{lot:>6.2f}{pv:>10.2f}{d:>12.1f}{d:>10.1f}{mg:>9.2f}"
              f"{'yes' if fits else 'NO -- margin':>12}")
        if fits:
            usable.append((lot, pv, d))
    if not usable:
        print("\n  No lot fits the margin. Nothing to test.")
        mt5.shutdown(); return 0
    print(f"\n  target pts and ruin pts are the same column, by construction.")

    # ---- collect the sessions once ------------------------------------
    print(f"\nloading ticks ...", flush=True)
    sess = {g: [] for g in GATES}
    today = (datetime.now(timezone.utc) + timedelta(hours=THAI)).date()
    for b in range(DAYS, -1, -1):
        d = today - timedelta(days=b)
        if d.weekday() >= 5:
            continue
        bell = datetime(d.year, d.month, d.day, 12, 30, tzinfo=timezone.utc)
        t = mt5.copy_ticks_range(SYMBOL, bell,
                                 bell + timedelta(hours=WATCH_H),
                                 mt5.COPY_TICKS_ALL)
        if t is None or len(t) < 40:
            continue
        t0 = int(bell.timestamp())
        sec = (t["time_msc"].astype(np.int64) - t0 * 1000) / 1000.0
        bid, ask = t["bid"].astype(float), t["ask"].astype(float)
        mid = np.where(ask > 0, (bid + ask) / 2.0, bid)
        ref = float(mid[0])
        for g in GATES:
            w = np.where((sec >= MIN_WAIT) & (sec <= MAXW)
                         & (np.abs(mid - ref) >= g))[0]
            if len(w) == 0:
                continue
            i = int(w[0])
            side = 1 if mid[i] > ref else -1
            entry = float(ask[i]) if side > 0 else float(bid[i])
            path = (mid[i:] - entry) * side - spread
            sess[g].append(path)

    for g in GATES:
        rows = sess[g]
        if len(rows) < 15:
            print(f"\n  gate {g:g}: only {len(rows)} trades -- too few")
            continue
        print(f"\n{'='*78}")
        print(f" GATE {g:g} PTS -- {len(rows)} trades, first touch within"
              f" {WATCH_H:.0f} h")
        print(f"{'='*78}")
        print(f"\n{'lot':>6}{'target':>9}{'doubled':>9}{'busted':>8}"
              f"{'neither':>9}{'P(double)':>11}{'EV per bet':>12}"
              f"{'trades to ruin':>16}")
        for lot, pv, dist in usable:
            win = lose = none = 0
            for path in rows:
                hit_up = np.argmax(path >= dist) if (path >= dist).any() else None
                hit_dn = np.argmax(path <= -dist) if (path <= -dist).any() else None
                if hit_up is not None and (hit_dn is None or hit_up < hit_dn):
                    win += 1
                elif hit_dn is not None:
                    lose += 1
                else:
                    none += 1
            n = win + lose
            p = win / n if n else 0.0
            # a bet that neither resolves is treated as a scratch
            ev = (p * EQUITY - (1 - p) * EQUITY) if n else 0.0
            # expected consecutive wins before the first loss
            ttr = (p / (1 - p)) if p < 1 else float("inf")
            print(f"{lot:>6.2f}{dist:>9.1f}{win:>9}{lose:>8}{none:>9}"
                  f"{p*100:>10.0f}%{ev:>+12.2f}{ttr:>16.1f}")
        print(f"\n  P(double) is the only number that matters. Above 50% and")
        print(f"  repeated bets grow; at or below, they end at zero and the")
        print(f"  last column says how many wins to expect on the way there.")
        print(f"  'neither' resolved inside {WATCH_H:.0f} h -- scratched, not counted in P.")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
