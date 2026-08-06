#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
trade_summary.py -- win/loss summary per live variant since a given date.
Run ON THE VPS (needs MetaTrader5 + a live terminal connection).

Usage: python trade_summary.py [YYYY-MM-DD]
Default start date: 2026-07-29 (when the current 9-bot H1-manual family
first appeared in the logs).
"""
import sys
from datetime import datetime
import MetaTrader5 as mt5

# magic -> variant_tag, taken from forex_live_bot_gold_cwider.py's
# SYMBOL_MAGIC + VARIANT_MAGIC_OFFSET tables (base + offset).
MAGIC_LABEL = {
    555143: "gold_h1_manual",
    555153: "gold_daily_breakout",
    555073: "gold_momentum_rsi",
    666120: "btc_h1_manual",
    666020: "btc_h1_breakout",
    666040: "btc_amd",
    666050: "btc_lqsweep",
    666060: "btc_tpo",
    667130: "eth_h1_manual",
}

start_arg = sys.argv[1] if len(sys.argv) > 1 else "2026-07-29"
start = datetime.strptime(start_arg, "%Y-%m-%d")

if not mt5.initialize():
    print("ERR: MT5 init failed")
    sys.exit(1)

deals = mt5.history_deals_get(start, datetime.now()) or []
# only closing deals carry realized P&L; entries have profit == 0
closes = [d for d in deals if d.entry == 1]  # DEAL_ENTRY_OUT

by_magic = {}
for d in closes:
    by_magic.setdefault(d.magic, []).append(d)

print("=" * 78)
print(f" TRADE SUMMARY since {start_arg}  (closing deals only)")
print("=" * 78)

tot_n = tot_win = 0
tot_pnl = 0.0
for magic, ds in sorted(by_magic.items()):
    label = MAGIC_LABEL.get(magic, f"magic={magic}")
    n = len(ds)
    wins = sum(1 for d in ds if d.profit > 0)
    losses = sum(1 for d in ds if d.profit < 0)
    pnl = sum(d.profit + d.swap + d.commission for d in ds)
    wr = 100 * wins / n if n else 0
    print(f"  {label:<22} n={n:>3}  win={wins:>3} loss={losses:>3}  "
          f"win%={wr:5.1f}  net_pnl={pnl:+9.2f}")
    tot_n += n
    tot_win += wins
    tot_pnl += pnl

unlabeled = {m: len(ds) for m, ds in by_magic.items() if m not in MAGIC_LABEL}
if unlabeled:
    print(f"\n  (unlabeled magics seen: {unlabeled} -- add to MAGIC_LABEL if real)")

print("-" * 78)
wr_tot = 100 * tot_win / tot_n if tot_n else 0
print(f"  {'TOTAL':<22} n={tot_n:>3}  win={tot_win:>3}  win%={wr_tot:5.1f}  "
      f"net_pnl={tot_pnl:+9.2f}")

acc = mt5.account_info()
if acc:
    print(f"\n  current balance={acc.balance:,.2f}  equity={acc.equity:,.2f}")

mt5.shutdown()
