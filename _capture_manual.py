#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_capture_manual.py -- turn a day of hand-placed trades into bot parameters.

The operator will trade 19:30 by hand as a worked example. This reads what
they actually did from the broker's own deal history and expresses it in
the bot's own units -- seconds after the bell, points from the 19:30
price, direction, hold time -- so the demonstration can be compared with
the bot's rules line for line instead of described.

OWNERSHIP IS RESOLVED BY POSITION, NOT BY DEAL. A broker-side close --
a stop-loss, a stop-out -- carries magic 0 exactly like a hand-placed
trade does, so filtering deals on magic would count the bot's own
liquidations as manual trades. Every deal is grouped by position_id and
the ENTRY deal's magic decides who owns the position.

What it cannot do is say whether the method works: one session is one to
five trades. What it can do is calibrate -- if the entry was 18 points
out and the bot's gate is 11, that is a number worth changing, and it
needs no statistics at all.

Usage:  python _capture_manual.py [symbol] [YYYY-MM-DD]
"""
import sys
from datetime import datetime, timedelta, timezone

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)"); sys.exit(1)

import numpy as np

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "XAUAUDm"
DAY = sys.argv[2] if len(sys.argv) > 2 else None
BOT_MAGIC, THAI = 668003, 7
BOT_GATE_PTS, BOT_HOLD_MIN = 11.1, 30


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed"); return 2
    if DAY:
        d = datetime.strptime(DAY, "%Y-%m-%d").date()
    else:
        d = (datetime.now(timezone.utc) + timedelta(hours=THAI)).date()
    bell = datetime(d.year, d.month, d.day, 12, 30, tzinfo=timezone.utc)

    frm = bell - timedelta(hours=13)
    to = bell + timedelta(hours=11)
    deals = mt5.history_deals_get(frm, to)
    if deals is None:
        print(f"[ERROR] history_deals_get: {mt5.last_error()}"); return 2

    # group by position, decide ownership from the ENTRY deal
    pos = {}
    for x in deals:
        pos.setdefault(x.position_id, []).append(x)
    manual, botpos = [], []
    for pid, ds in pos.items():
        ds.sort(key=lambda x: x.time_msc)
        opening = next((x for x in ds if x.entry == 0), None)
        if opening is None or opening.symbol != SYMBOL:
            continue
        (botpos if opening.magic == BOT_MAGIC else manual).append((pid, ds))

    print("=" * 86)
    print(f" HAND-PLACED TRADES ON {d} -- {SYMBOL}")
    print(f" bell 19:30 Thai = {bell:%Y-%m-%d %H:%M} UTC")
    print("=" * 86)
    if not manual:
        print("\n  no hand-placed positions on this symbol that day.")
        if botpos:
            print(f"  ({len(botpos)} position(s) belonged to the bot, magic "
                  f"{BOT_MAGIC})")
        mt5.shutdown(); return 0

    # the 19:30 reference price, the same one the bot would take
    tk = mt5.copy_ticks_range(SYMBOL, bell, bell + timedelta(seconds=30),
                              mt5.COPY_TICKS_ALL)
    ref = None
    if tk is not None and len(tk):
        a0 = float(tk["ask"][0]); b0 = float(tk["bid"][0])
        ref = (a0 + b0) / 2.0 if a0 > 0 else b0
        print(f"\n  19:30:00 reference price: {ref:.3f}")
    print(f"  the bot's rules for comparison: enter at {BOT_GATE_PTS} pts "
          f"from that price, hold {BOT_HOLD_MIN} min\n")

    print(f"  {'#':>2}{'side':>6}{'lots':>7}{'entered':>12}{'at price':>11}"
          f"{'pts from ref':>14}{'held':>9}{'exit':>11}{'P/L':>9}")
    print("  " + "-" * 82)
    for i, (pid, ds) in enumerate(sorted(manual, key=lambda p: p[1][0].time_msc), 1):
        op = next(x for x in ds if x.entry == 0)
        cl = [x for x in ds if x.entry in (1, 2)]
        t_in = datetime.fromtimestamp(op.time_msc / 1000.0, timezone.utc)
        secs = (t_in - bell).total_seconds()
        dist = (op.price - ref) if ref else float("nan")
        pl = sum(x.profit + x.commission + x.swap for x in ds)
        if cl:
            t_out = datetime.fromtimestamp(cl[-1].time_msc / 1000.0, timezone.utc)
            held = (t_out - t_in).total_seconds() / 60.0
            outp = f"{cl[-1].price:.2f}"
        else:
            held, outp = float("nan"), "still open"
        when = (f"+{secs:.0f}s" if abs(secs) < 120 else f"+{secs/60:.1f}m")
        print(f"  {i:>2}{'BUY' if op.type == 0 else 'SELL':>6}{op.volume:>7.2f}"
              f"{when:>12}{op.price:>11.2f}{dist:>+14.2f}"
              f"{held:>8.1f}m{outp:>11}{pl:>+9.2f}")

    print("  " + "-" * 82)
    firsts = sorted(manual, key=lambda p: p[1][0].time_msc)[0][1]
    op = next(x for x in firsts if x.entry == 0)
    secs = (datetime.fromtimestamp(op.time_msc / 1000.0, timezone.utc)
            - bell).total_seconds()
    dist = abs(op.price - ref) if ref else float("nan")
    total = sum(sum(x.profit + x.commission + x.swap for x in ds)
                for _, ds in manual)
    print(f"\n  FIRST ENTRY: {secs:.0f}s after the bell, {dist:.2f} points from "
          f"the 19:30 price")
    print(f"  the bot would have waited for {BOT_GATE_PTS} points -- "
          + ("you were EARLIER than the bot" if dist < BOT_GATE_PTS
             else "you were LATER than the bot"))
    print(f"  {len(manual)} hand-placed position(s), net {total:+.2f}")
    if botpos:
        bt = sum(sum(x.profit + x.commission + x.swap for x in ds)
                 for _, ds in botpos)
        print(f"  the bot also traded that day: {len(botpos)} position(s), "
              f"net {bt:+.2f}")
    print("\n  One session calibrates; it does not validate. What is worth")
    print("  acting on here is the DISTANCE and the HOLD, not the P/L.")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
