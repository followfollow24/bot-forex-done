#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Account-level P&L SINCE THE CENT ACCOUNT WAS OPENED.
Pulls the FULL MT5 deal history (history_deals_get from year 2000 to now),
which is the authoritative source -- not the per-bot fills_log CSVs.

Separates:
  - balance operations (deposits / withdrawals / credits)  -> money you put in
  - trade deals (buy/sell out)                              -> realized trading P&L
and reconciles them against the live balance/equity so nothing is missing.
ASCII-only. Runs on the VPS from Desktop.
"""
import MetaTrader5 as mt5
from datetime import datetime, timezone
from collections import defaultdict

if not mt5.initialize():
    print("initialize() FAILED:", mt5.last_error())
    raise SystemExit(1)

ai = mt5.account_info()
print("=" * 66)
print("ACCOUNT  login=%s  server=%s" % (ai.login, ai.server))
print("  currency=%s  leverage=1:%s  name=%s" % (ai.currency, ai.leverage, ai.name))
print("  balance=%.2f  equity=%.2f  profit(floating)=%.2f  margin_free=%.2f"
      % (ai.balance, ai.equity, ai.profit, ai.margin_free))
print("=" * 66)

frm = datetime(2000, 1, 1, tzinfo=timezone.utc)
to = datetime.now(timezone.utc)
deals = mt5.history_deals_get(frm, to)
if deals is None:
    print("history_deals_get returned None:", mt5.last_error())
    mt5.shutdown()
    raise SystemExit(1)

deals = sorted(deals, key=lambda d: d.time)
print("total deals in history: %d" % len(deals))
if deals:
    print("first deal: %s   last deal: %s"
          % (datetime.utcfromtimestamp(deals[0].time),
             datetime.utcfromtimestamp(deals[-1].time)))

# DEAL_TYPE_BALANCE=2 (deposit/withdraw/credit corrections), others are trades
deposits = 0.0        # positive balance ops
withdrawals = 0.0     # negative balance ops
trade_profit = 0.0
trade_swap = 0.0
trade_comm = 0.0
n_trade_out = 0
by_symbol = defaultdict(lambda: [0, 0.0])   # symbol -> [count_out, net_pnl]

for d in deals:
    if d.type == mt5.DEAL_TYPE_BALANCE or d.entry == mt5.DEAL_ENTRY_OUT and d.symbol == "":
        if d.profit >= 0:
            deposits += d.profit
        else:
            withdrawals += d.profit
        continue
    # trading deal
    trade_swap += d.swap
    trade_comm += d.commission
    trade_profit += d.profit
    if d.entry == mt5.DEAL_ENTRY_OUT or d.entry == mt5.DEAL_ENTRY_INOUT:
        n_trade_out += 1
        by_symbol[d.symbol][0] += 1
        by_symbol[d.symbol][1] += d.profit + d.swap + d.commission

net_trading = trade_profit + trade_swap + trade_comm
net_deposits = deposits + withdrawals

print("\n" + "-" * 66)
print("MONEY IN/OUT (balance operations):")
print("  deposits (+)        : %+.2f %s" % (deposits, ai.currency))
print("  withdrawals (-)     : %+.2f %s" % (withdrawals, ai.currency))
print("  net deposited       : %+.2f %s" % (net_deposits, ai.currency))
print("\nREALIZED TRADING P&L SINCE INCEPTION (all closed deals):")
print("  gross profit/loss   : %+.2f %s" % (trade_profit, ai.currency))
print("  swap                : %+.2f %s" % (trade_swap, ai.currency))
print("  commission          : %+.2f %s" % (trade_comm, ai.currency))
print("  ------------------------------------")
print("  NET realized trading: %+.2f %s   (%d closing deals)"
      % (net_trading, ai.currency, n_trade_out))

print("\nPER-SYMBOL net realized (profit+swap+comm):")
for sym, (cnt, pnl) in sorted(by_symbol.items(), key=lambda kv: kv[1][1]):
    print("  %-12s %4d deals   %+.2f %s" % (sym or "(none)", cnt, pnl, ai.currency))

print("\n" + "-" * 66)
print("RECONCILE:")
print("  net deposited + realized trading = %+.2f" % (net_deposits + net_trading))
print("  live balance                     = %+.2f" % ai.balance)
print("  (+ floating on open positions)   = %+.2f" % ai.profit)
print("  live equity                      = %+.2f" % ai.equity)
if ai.currency.upper().startswith("USC") or ai.currency.upper() == "USC":
    print("\nNOTE: currency is USC (US cent) -> divide by 100 for real USD.")
    print("  net deposited   ~ $%.2f" % (net_deposits / 100.0))
    print("  realized P&L    ~ $%.2f" % (net_trading / 100.0))
    print("  equity now      ~ $%.2f" % (ai.equity / 100.0))
    if net_deposits > 0:
        print("  total return since inception = %+.1f%%"
              % (100.0 * (ai.equity - net_deposits) / net_deposits))
print("=" * 66)

mt5.shutdown()
