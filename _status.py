#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""What the BROKER says, independent of what the bot thinks.

The bot's own log is its account of itself. This asks the terminal
instead, because the two can disagree -- a process can be alive and
looping on a dead IPC link while its log still looks reasonable.
"""
import sys
from datetime import datetime, timedelta, timezone

try:
    import MetaTrader5 as mt5
except ImportError:
    print("  BROKER    cannot check -- MetaTrader5 module missing"); sys.exit(1)

MAGIC = 668003
THAI = 7


def main():
    if not mt5.initialize():
        print(f"  BROKER    NO CONNECTION to the terminal ({mt5.last_error()})")
        print("            The bot cannot trade in this state even if its")
        print("            process is alive.")
        return 1
    ti = mt5.terminal_info()
    ac = mt5.account_info()
    if ti is not None:
        print(f"  TERMINAL  connected={ti.connected}  "
              f"AutoTrading={'ON' if ti.trade_allowed else 'OFF -- orders will be REJECTED'}")
    if ac is not None:
        print(f"  ACCOUNT   {ac.login} ({ac.server})  equity {ac.equity:,.2f} "
              f"{ac.currency}  free margin {ac.margin_free:,.2f}")
        print(f"            fast tier {'ARMS' if ac.equity >= 180 else 'is HELD BACK'} "
              f"at this equity (floor 180)")

    total = 0
    for sym in ("XAUAUDm",):
        try:
            res = mt5.positions_get(symbol=sym)
        except Exception as exc:
            print(f"  POSITION  cannot read {sym}: {exc!r}")
            continue
        if res is None:
            # None is an error, not an empty account -- the distinction
            # that a bug in the bot used to erase.
            print(f"  POSITION  UNREADABLE for {sym} ({mt5.last_error()}) -- "
                  f"this is not the same as 'none open'")
            continue
        ours = [p for p in res if getattr(p, "magic", None) == MAGIC]
        total += len(ours)
        for p in ours:
            when = datetime.fromtimestamp(p.time, timezone.utc) + timedelta(hours=THAI)
            print(f"  POSITION  {sym} {'BUY' if p.type == 0 else 'SELL'} "
                  f"{p.volume} @ {p.price_open:.3f}  SL {p.sl:.3f}  "
                  f"opened {when:%d %b %H:%M} Thai  P/L {p.profit:+.2f}")
    if total == 0:
        print("  POSITION  none of ours open (magic 668003)")

    bell = datetime.now(timezone.utc).replace(hour=12, minute=30, second=0,
                                              microsecond=0)
    if bell <= datetime.now(timezone.utc):
        bell += timedelta(days=1)
    hrs = (bell - datetime.now(timezone.utc)).total_seconds() / 3600.0
    print(f"  NEXT BELL {bell + timedelta(hours=THAI):%a %d %b %H:%M} Thai "
          f"-- in {hrs:.1f} hours")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
