#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_exit_reasons.py -- who actually closed each trade, per bot?

_live_r_multiples.py found that all 8 of btc_h1_manual's winners were
closed with DEAL_REASON_MOBILE -- by hand, from the MT5 phone app -- at an
average of +0.42R, while all 5 losers ran to their stop for -1.01R. That
single fact invalidates the bot's live P&L as evidence about its strategy:
8 of 13 trades never reached an exit the strategy chose.

The obvious next question is whether that is one bot or the whole fleet.
If manual closes are widespread, the account's -2,758 says much less about
the strategies than it appears to, and any code "fix" derived from those
numbers would be aimed at something that is not broken.

So: every closed position on the account, grouped by owning bot, tallied
by who closed it and what it paid.

Ownership is resolved through position_id, never the deal's own magic: a
broker-side stop writes its closing deal with magic 0, so filtering deals
individually silently drops exactly the trades being counted.

Usage (on the VPS):  python _exit_reasons.py [days]
"""
import sys
from collections import defaultdict
from datetime import datetime, timedelta

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)")
    sys.exit(1)

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 45

MAGIC_LABEL = {
    555143: "gold_h1_manual", 555153: "gold_daily_breakout",
    555073: "gold_momentum_rsi", 666120: "btc_h1_manual",
    666020: "btc_h1_breakout", 666040: "btc_amd", 666050: "btc_lqsweep",
    666060: "btc_tpo", 667130: "eth_h1_manual",
    668001: "funding_contrarian", 668002: "btc_combo_lb",
    669001: "news_gemini", 671001: "chart_ai_trader",
}

# Only SL, TP and EXPERT are the strategy running its own course. CLIENT,
# MOBILE and WEB are a human overriding it -- the distinction this exists
# to surface.
REASON = {0: "CLIENT", 1: "MOBILE", 2: "WEB", 3: "EXPERT", 4: "SL",
          5: "TP", 6: "STOPOUT", 7: "ROLLOVER", 8: "VMARGIN", 9: "SPLIT"}
MANUAL = {"CLIENT", "MOBILE", "WEB"}


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed")
        sys.exit(1)
    deals = mt5.history_deals_get(datetime.now() - timedelta(days=DAYS),
                                  datetime.now() + timedelta(days=1))
    mt5.shutdown()
    if not deals:
        print("[ERROR] no deals returned")
        return

    pos = defaultdict(list)
    for d in deals:
        pos[d.position_id].append(d)

    # magic comes from whichever deal in the position carries one
    tally = defaultdict(lambda: defaultdict(lambda: [0, 0.0]))
    for pid, ds in pos.items():
        ds.sort(key=lambda d: d.time)
        outs = [d for d in ds if d.entry == 1]
        if not outs or not [d for d in ds if d.entry == 0]:
            continue                       # still open
        magic = next((d.magic for d in ds if d.magic), 0)
        reason = REASON.get(outs[-1].reason, f"code{outs[-1].reason}")
        profit = sum(d.profit + d.swap + d.commission for d in ds)
        cell = tally[magic][reason]
        cell[0] += 1
        cell[1] += profit

    print("=" * 88)
    print(f" EXIT REASONS BY BOT -- last {DAYS} days")
    print(" SL / TP / EXPERT = the strategy ran its course")
    print(" CLIENT / MOBILE / WEB = a human closed it by hand")
    print("=" * 88)
    print(f"{'bot':<22}{'reason':<12}{'n':>5}{'net $':>12}{'avg $':>10}")
    print("-" * 88)

    grand_manual = grand_auto = 0
    manual_pnl = auto_pnl = 0.0
    for magic in sorted(tally, key=lambda m: MAGIC_LABEL.get(m, f"zz{m}")):
        label = MAGIC_LABEL.get(magic, f"magic={magic}")
        first = True
        for reason, (n, pnl) in sorted(tally[magic].items(),
                                       key=lambda kv: -kv[1][0]):
            print(f"{(label if first else ''):<22}{reason:<12}{n:>5}"
                  f"{pnl:>+12.2f}{pnl/n:>+10.2f}")
            first = False
            if reason in MANUAL:
                grand_manual += n
                manual_pnl += pnl
            else:
                grand_auto += n
                auto_pnl += pnl
        print()

    tot = grand_manual + grand_auto
    print("-" * 88)
    print(f"  closed by the STRATEGY : {grand_auto:>4} trades   "
          f"{auto_pnl:>+10.2f}   avg {auto_pnl/max(grand_auto,1):+.2f}")
    print(f"  closed BY HAND         : {grand_manual:>4} trades   "
          f"{manual_pnl:>+10.2f}   avg {manual_pnl/max(grand_manual,1):+.2f}")
    if tot:
        print(f"  -> {100*grand_manual/tot:.0f}% of all closed trades were "
              f"exited by hand")
    print()
    if grand_manual and auto_pnl < 0 < manual_pnl:
        print("  The hand-closed trades are the profitable ones and the")
        print("  strategy-closed ones carry the losses. That is the signature")
        print("  of taking winners early and leaving losers to the stop -- and")
        print("  it means the account's P&L is not measuring the strategies.")
    elif grand_manual:
        print("  Read the per-bot rows: any bot whose winners are all manual")
        print("  has not actually been tested, whatever its P&L says.")


if __name__ == "__main__":
    main()
