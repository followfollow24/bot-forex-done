#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_tick_replay.py -- second by second through 19:30 Thai on one day.

The operator asks how long today's drop actually took. Minute bars
cannot answer that: the 19:30 M1 bar on 4 Sep moved -70.28 as a single
object, and whether that took two seconds or fifty is invisible inside
it. This replays the raw ticks.

It is also the direct test of the bot being built: if the direction at
+2 to +5 seconds does not match where the move ended up, then the entry
window that bot uses would have been on the wrong side of the biggest
candle of the week.

Usage:  python _tick_replay.py [symbol] [YYYY-MM-DD] [minutes]
"""
import sys
from datetime import datetime, timedelta, timezone

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)"); sys.exit(1)

import numpy as np

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "XAUAUDm"
DAY = sys.argv[2] if len(sys.argv) > 2 else "today"
MINUTES = int(sys.argv[3]) if len(sys.argv) > 3 else 5
THAI, TARGET = 7, (19, 30)


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed"); return 2
    info = mt5.symbol_info(SYMBOL)
    if info is None:
        print(f"[ERROR] {SYMBOL} not found"); return 2
    mt5.symbol_select(SYMBOL, True)
    spread = info.spread * info.point

    tk_now = mt5.symbol_info_tick(SYMBOL)
    off = int(round((tk_now.time - datetime.now(timezone.utc).timestamp()) / 3600.0))

    if DAY == "today":
        d = (datetime.now(timezone.utc) + timedelta(hours=THAI)).date()
    else:
        d = datetime.strptime(DAY, "%Y-%m-%d").date()
    # 19:30 Thai -> UTC -> server
    start_utc = datetime(d.year, d.month, d.day, TARGET[0] - THAI, TARGET[1],
                         tzinfo=timezone.utc)
    start_srv = start_utc + timedelta(hours=off)

    ticks = mt5.copy_ticks_range(SYMBOL, start_srv,
                                 start_srv + timedelta(minutes=MINUTES),
                                 mt5.COPY_TICKS_ALL)
    if ticks is None or len(ticks) == 0:
        print(f"[ERROR] no ticks ({mt5.last_error()})"); return 2

    t0 = int(start_srv.timestamp() * 1000)
    ms = ticks["time_msc"].astype(np.int64) - t0
    bid = ticks["bid"].astype(float)
    ask = ticks["ask"].astype(float)
    mid = np.where(ask > 0, (bid + ask) / 2.0, bid)
    ref = float(mid[0])

    print("=" * 78)
    print(f" TICK REPLAY -- {SYMBOL}   {d}  19:30:00 Thai "
          f"(= {start_srv:%H:%M:%S} server, UTC{off:+d})")
    print(f" {len(ticks)} ticks in {MINUTES} min   reference {ref:.3f}   "
          f"quoted spread now {spread:.2f}")
    print("=" * 78)

    print("\nFIRST 20 TICKS -- this is what a seconds-scale entry actually sees\n")
    print(f"   {'+seconds':>10}{'bid':>11}{'ask':>11}{'spread':>9}"
          f"{'move from 19:30:00':>21}")
    for i in range(min(20, len(ticks))):
        sp = ask[i] - bid[i] if ask[i] else float("nan")
        print(f"   {ms[i]/1000.0:>10.3f}{bid[i]:>11.3f}{ask[i]:>11.3f}"
              f"{sp:>9.2f}{mid[i]-ref:>+21.3f}")

    print("\nWHERE PRICE STOOD AT EACH SECOND\n")
    print(f"   {'+sec':>6}{'ticks so far':>14}{'move':>10}{'vs spread':>11}"
          f"{'direction':>11}")
    for sec in [1, 2, 3, 4, 5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 300]:
        m = ms <= sec * 1000
        if m.sum() == 0:
            print(f"   {sec:>6}{0:>14}   -- no ticks yet --")
            continue
        mv = float(mid[m][-1] - ref)
        sp_now = float(ask[m][-1] - bid[m][-1]) if ask[m][-1] else spread
        print(f"   {sec:>6}{int(m.sum()):>14}{mv:>+10.2f}"
              f"{abs(mv)/sp_now if sp_now else 0:>10.1f}x"
              f"{('DOWN' if mv < 0 else 'UP' if mv > 0 else 'flat'):>11}")

    # how long to reach each distance
    print("\nHOW LONG THE MOVE TOOK TO COVER EACH DISTANCE\n")
    print(f"   {'points':>8}{'reached after':>16}{'ticks':>8}")
    lo = np.minimum.accumulate(mid)
    for dist in [5, 10, 20, 30, 50, 70, 90]:
        hit = np.where(ref - lo >= dist)[0]
        if len(hit) == 0:
            print(f"   {dist:>8}   never reached in {MINUTES} min")
            continue
        i = int(hit[0])
        print(f"   {dist:>8}{ms[i]/1000.0:>15.2f}s{i+1:>8}")

    lowest = float(np.min(mid))
    i_low = int(np.argmin(mid))
    print(f"\n   lowest point {lowest:.3f} = {lowest-ref:+.2f} from 19:30:00, "
          f"reached at +{ms[i_low]/1000.0:.1f}s "
          f"({ms[i_low]/60000.0:.1f} min)")

    # the question the bot depends on
    print("\nDOES THE 2-5 SECOND READ AGREE WITH WHERE IT ENDED UP?\n")
    final = float(mid[-1] - ref)
    for sec in (2, 3, 4, 5):
        m = ms <= sec * 1000
        if m.sum() < 2:
            print(f"   +{sec}s: fewer than 2 ticks -- no direction to read")
            continue
        early = float(mid[m][-1] - ref)
        agree = (early > 0) == (final > 0) if early and final else False
        print(f"   +{sec}s said {'UP' if early > 0 else 'DOWN' if early < 0 else 'FLAT'}"
              f" ({early:+.2f})   ... {MINUTES}min later it was "
              f"{final:+.2f}   -> {'AGREES' if agree else 'WRONG WAY'}")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
