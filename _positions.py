#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_positions.py -- what is actually open on the account right now.

Read-only. Opens nothing, closes nothing, sends nothing.

Written because the operator opened a trade by hand while the bot was
being built, and a hand-placed trade and a bot trade share one thing that
matters more than either of them: the account. They do NOT share a magic
number, so the bot will neither manage nor close a manual position and
its own anti-stacking check will not see one -- but both draw on the same
equity, so the broker's stop-out is decided by the pair, not by each
alone. This prints the pair.
"""
import sys

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)"); sys.exit(1)

BOT_MAGIC = 668003

if not mt5.initialize():
    print(f"[ERROR] MT5 init failed: {mt5.last_error()}"); sys.exit(2)

a = mt5.account_info()
if a is None:
    print("[ERROR] account_info() returned None"); sys.exit(2)

print("=" * 72)
print(f" ACCOUNT {a.login} ({a.server})")
print("=" * 72)
print(f"  balance      {a.balance:>10.2f} {a.currency}")
print(f"  equity       {a.equity:>10.2f} {a.currency}"
      f"   ({a.equity - a.balance:+.2f} floating)")
print(f"  free margin  {a.margin_free:>10.2f}   used {a.margin:.2f}")

ps = mt5.positions_get() or []
print(f"\n  OPEN POSITIONS: {len(ps)}")
if ps:
    print(f"    {'symbol':>10}{'side':>6}{'lots':>7}{'open':>11}{'now':>11}"
          f"{'profit':>9}{'magic':>8}  owner")
    for p in ps:
        tk = mt5.symbol_info_tick(p.symbol)
        now = (tk.bid if p.type == 0 else tk.ask) if tk else 0.0
        owner = "THE BOT" if p.magic == BOT_MAGIC else "you (by hand)"
        print(f"    {p.symbol:>10}{'BUY' if p.type == 0 else 'SELL':>6}"
              f"{p.volume:>7.2f}{p.price_open:>11.3f}{now:>11.3f}"
              f"{p.profit:>+9.2f}{p.magic:>8}  {owner}")

    # The number that decides everything: how far the WHOLE account can
    # move against the combined book before the broker closes it.
    total = 0.0
    for p in ps:
        v = mt5.order_calc_profit(
            mt5.ORDER_TYPE_BUY if p.type == 0 else mt5.ORDER_TYPE_SELL,
            p.symbol, p.volume, 1000.0, 1000.0 + (1.0 if p.type == 0 else -1.0))
        if v:
            total += abs(float(v))
    if total > 0:
        print(f"\n  the open book loses {total:.2f} {a.currency} per point "
              f"against it")
        print(f"  -> {a.equity/total:.1f} points of adverse movement ends the "
              f"account")

od = mt5.orders_get() or []
print(f"\n  PENDING ORDERS: {len(od)}")
for o in od:
    print(f"    {o.symbol} type {o.type} vol {o.volume_current} "
          f"at {o.price_open} magic {o.magic}")

print(f"\n  the bot only ever touches magic {BOT_MAGIC}. Anything else here")
print(f"  it will not manage, close, or count -- but the equity is shared.")
mt5.shutdown()
