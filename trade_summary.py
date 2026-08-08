#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
trade_summary.py -- win/loss summary per live variant since a given date.
Run ON THE VPS (needs MetaTrader5 + a live terminal connection).

Usage: python trade_summary.py [YYYY-MM-DD]
Default start date: 2026-07-29 (when the current 9-bot H1-manual family
first appeared in the logs).

[2026-08-06] Resolves magic via POSITION_ID, not the closing deal's own
magic field. When a --manual-exit bot's position is closed by the user
by hand in the MT5 terminal (not by the bot's own order path), the CLOSING
deal is recorded with magic=0 even though the position's OPENING deal
correctly carries the bot's real magic -- an MT5 record-keeping quirk for
manual closes, confirmed against a real case: btc_h1_manual (magic 666120)
had 1 SL-hit close correctly tagged, but 6 other closes across the 9-bot
family all showed magic=0 despite being opened by these bots (user-confirmed
2026-08-06). Fix: look back further than `start` for the ENTRY deal of each
position (a position can open before `start` and close after), build
position_id -> magic from every ENTRY deal (entry==0) seen, and use that to
resolve any closing deal whose own magic is 0 or missing.
"""
import sys
from datetime import datetime, timedelta
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
    668001: "funding_contrarian",
    668002: "btc_combo_lb",
}

# Per-bot actual start date (when it started running its CURRENT config) --
# used only for trades/day; falls back to the CLI start date if absent.
BOT_START = {
    668001: "2026-08-08",
    668002: "2026-08-08",
}

start_arg = sys.argv[1] if len(sys.argv) > 1 else "2026-07-29"
start = datetime.strptime(start_arg, "%Y-%m-%d")
# entry-deal lookback: a position open before `start` can still close after
# it, so scan further back for ENTRY deals specifically (60 days is generous
# given this account has been live since 2026-07-04).
lookback_start = start - timedelta(days=60)

if not mt5.initialize():
    print("ERR: MT5 init failed")
    sys.exit(1)

wide_deals = mt5.history_deals_get(lookback_start, datetime.now()) or []
entries = [d for d in wide_deals if d.entry == 0]  # DEAL_ENTRY_IN
pos_magic = {}
pos_symbol = {}
pos_open_time = {}
for d in entries:
    if d.magic:  # only trust a nonzero magic from the entry side
        pos_magic[d.position_id] = d.magic
    pos_symbol[d.position_id] = d.symbol
    pos_open_time[d.position_id] = d.time

deals = [d for d in wide_deals if d.time >= start.timestamp()]
closes = [d for d in deals if d.entry == 1]  # DEAL_ENTRY_OUT

resolved = []
unresolved_zero = 0
for d in closes:
    magic = d.magic if d.magic else pos_magic.get(d.position_id, 0)
    if not magic:
        unresolved_zero += 1
    resolved.append((d, magic))

by_magic = {}
for d, magic in resolved:
    by_magic.setdefault(magic, []).append(d)

print("=" * 88)
print(f" TRADE SUMMARY since {start_arg}  (closing deals, magic resolved via position_id)")
print("=" * 88)

# show every bot even with 0 closed trades yet (e.g. brand-new daily sleeves)
all_magics = sorted(set(MAGIC_LABEL) | set(by_magic), key=lambda m: MAGIC_LABEL.get(m, "zz"))

tot_n = tot_win = 0
tot_pnl = 0.0
now = datetime.now()
for magic in all_magics:
    ds = by_magic.get(magic, [])
    label = MAGIC_LABEL.get(magic, f"magic={magic}")
    n = len(ds)
    wins = sum(1 for d in ds if d.profit > 0)
    losses = sum(1 for d in ds if d.profit < 0)
    pnl = sum(d.profit + d.swap + d.commission for d in ds)
    wr = 100 * wins / n if n else 0
    bot_start = datetime.strptime(BOT_START.get(magic, start_arg), "%Y-%m-%d")
    days = max((now - max(bot_start, start)).total_seconds() / 86400.0, 0.001)
    tpd = n / days
    print(f"  {label:<22} n={n:>3}  win={wins:>3} loss={losses:>3}  "
          f"win%={wr:5.1f}  net_pnl={pnl:+9.2f}  trades/day={tpd:5.2f}"
          f"  (over {days:.1f}d)")
    tot_n += n
    tot_win += wins
    tot_pnl += pnl

print("-" * 88)
wr_tot = 100 * tot_win / tot_n if tot_n else 0
print(f"  {'TOTAL':<22} n={tot_n:>3}  win={tot_win:>3}  win%={wr_tot:5.1f}  "
      f"net_pnl={tot_pnl:+9.2f}")
if unresolved_zero:
    print(f"  (still unresolved magic=0 after position_id lookup: {unresolved_zero})")

print("\n" + "=" * 88)
print(" PER-TRADE DETAIL")
print("=" * 88)
print(f"  {'bot':<20}{'symbol':<10}{'opened':<17}{'closed':<17}{'lot':>6}{'pnl':>10}")
rows = sorted(resolved, key=lambda x: x[0].time)
for d, magic in rows:
    label = MAGIC_LABEL.get(magic, f"magic={magic}")
    opened = pos_open_time.get(d.position_id)
    opened_s = datetime.fromtimestamp(opened).strftime("%m-%d %H:%M") if opened else "?"
    closed_s = datetime.fromtimestamp(d.time).strftime("%m-%d %H:%M")
    net = d.profit + d.swap + d.commission
    print(f"  {label:<20}{d.symbol:<10}{opened_s:<17}{closed_s:<17}{d.volume:>6.2f}{net:>+10.2f}")

acc = mt5.account_info()
if acc:
    print(f"\n  current balance={acc.balance:,.2f}  equity={acc.equity:,.2f}")

mt5.shutdown()
