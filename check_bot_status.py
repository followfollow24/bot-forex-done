#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_bot_status.py -- fetch real account status from MT5:
- Current equity, balance, drawdown
- Trade history since 4 Jul 2026 (account open)
- Win/loss count, win rate
- Last 20 trades detail
- Bot PIDs (if bots still running)
Run on VPS.
ASCII-only.
"""
import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime

print("=" * 80)
print(" REAL ACCOUNT STATUS (cent account 160075275)")
print("=" * 80)

if not mt5.initialize():
    print("ERR: MT5 init failed")
    exit(1)

acc = mt5.account_info()
print(f"\nACCOUNT INFO:")
print(f"  Login: {acc.login} | Server: {acc.server} | Currency: {acc.currency}")
print(f"  Balance: {acc.balance:,.2f} | Equity: {acc.equity:,.2f} | Free margin: {acc.margin_free:,.2f}")
print(f"  Drawdown: {100*(1 - acc.equity/acc.balance if acc.balance > 0 else 1):.1f}%")

# Load ALL deals since 4 Jul 2026
deals = mt5.history_deals_get(0, datetime(2026, 7, 4))
if not deals:
    print("\nNo trades found since 4 Jul 2026")
    mt5.shutdown()
    exit(0)

df = pd.DataFrame(list(deals))
df['time'] = pd.to_datetime(df['time'], unit='s')
df = df.sort_values('time')

# Summary
wins = len(df[df['profit'] > 0])
losses = len(df[df['profit'] < 0])
draws = len(df[df['profit'] == 0])
wr = 100 * wins / len(df) if len(df) > 0 else 0

print(f"\nTRADE SUMMARY (4 Jul 2026 - {df['time'].max().date()}):")
print(f"  Total trades: {len(df)} | Wins: {wins} | Losses: {losses} | Draws: {draws}")
print(f"  Win rate: {wr:.1f}% | Total P&L: {df['profit'].sum():,.2f} USD")

# Group by magic (bot ID)
print(f"\nBY BOT:")
for magic in df['magic'].unique():
    sub = df[df['magic'] == magic]
    w = len(sub[sub['profit'] > 0])
    l = len(sub[sub['profit'] < 0])
    bot_name = {555053: "adx20tp7", 555083: "adx18tp7"}.get(magic, f"magic_{magic}")
    print(f"  {bot_name:12s}: {len(sub):3d} trades | {w:2d}W {l:2d}L ({100*w/len(sub) if len(sub) else 0:5.1f}%) | P&L {sub['profit'].sum():+8,.0f}")

# Last 20 trades
print(f"\nLAST 20 TRADES:")
print(f"  {'Time':<20} {'Bot':<12} {'Symbol':<10} {'Type':<6} {'Vol':<5} {'Entry':<8} {'Exit':<8} {'P&L':<8}")
print("  " + "-" * 76)
for _, row in df.tail(20).iterrows():
    bot_name = {555053: "adx20tp7", 555083: "adx18tp7"}.get(row['magic'], f"m{row['magic']}")
    ttype = "BUY" if row['type'] == 0 else "SELL"
    print(f"  {str(row['time']):<20} {bot_name:<12} {row['symbol']:<10} {ttype:<6} {row['volume']:<5.2f} {row['price_open']:<8.2f} {row['price_current']:<8.2f} {row['profit']:+8.0f}")

# Consecutive losing streak (current)
df['losing'] = df['profit'] < 0
losing_streaks = []
cur_streak = 0
for v in df['losing'].values:
    if v:
        cur_streak += 1
    else:
        if cur_streak > 0:
            losing_streaks.append(cur_streak)
        cur_streak = 0
if cur_streak > 0:
    losing_streaks.append(cur_streak)

max_streak = max(losing_streaks) if losing_streaks else 0
cur_streak_end = (df.iloc[-1]['losing'] and sum([1 for i in range(len(df)-1, -1, -1) if df.iloc[i]['losing']]) or 0)

print(f"\nLOSING STREAK:")
print(f"  Max consecutive losses: {max_streak}")
print(f"  Current streak (if any): {cur_streak_end}")

print("\n" + "=" * 80)
mt5.shutdown()
