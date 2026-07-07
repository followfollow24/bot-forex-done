#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
List every CLOSED round-trip trade on the REAL cent account, deal by deal.
Reconstructs positions from history_deals by position_id (ENTRY_IN -> ENTRY_OUT),
so each row is one real trade with entry/exit price, profit, exit reason (from the
closing deal comment) and which bot opened it (opening deal comment / magic).
ASCII-only. Runs on the VPS from the repo dir.
"""
import MetaTrader5 as mt5
from datetime import datetime, timezone
from collections import defaultdict

if not mt5.initialize():
    print("initialize() FAILED:", mt5.last_error())
    raise SystemExit(1)

ai = mt5.account_info()
print("REAL account login=%s  currency=%s  balance=%.2f  equity=%.2f"
      % (ai.login, ai.currency, ai.balance, ai.equity))

frm = datetime(2000, 1, 1, tzinfo=timezone.utc)
to = datetime.now(timezone.utc)
deals = sorted(mt5.history_deals_get(frm, to), key=lambda d: d.time)

# group trade deals by position_id
pos = defaultdict(lambda: {"in": [], "out": []})
for d in deals:
    if d.type not in (mt5.DEAL_TYPE_BUY, mt5.DEAL_TYPE_SELL):
        continue  # skip balance ops
    if d.entry == mt5.DEAL_ENTRY_IN:
        pos[d.position_id]["in"].append(d)
    elif d.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_INOUT):
        pos[d.position_id]["out"].append(d)

rows = []
for pid, legs in pos.items():
    if not legs["out"]:
        continue  # still open
    i = legs["in"][0] if legs["in"] else None
    o = legs["out"][-1]
    pnl = sum(x.profit + x.swap + x.commission for x in legs["out"])
    side = "BUY " if (i and i.type == mt5.DEAL_TYPE_BUY) else "SELL"
    rows.append({
        "close_t": o.time, "open_t": i.time if i else o.time,
        "sym": o.symbol, "side": side, "vol": o.volume,
        "open_p": i.price if i else 0.0, "close_p": o.price,
        "pnl": pnl, "open_c": (i.comment if i else ""), "close_c": o.comment,
        "magic": (i.magic if i else o.magic),
    })

rows.sort(key=lambda r: r["close_t"])
print("=" * 100)
print("CLOSED REAL TRADES (%d)  -- oldest first, newest LAST" % len(rows))
print("=" * 100)
print("%-3s %-16s %-16s %-9s %-5s %-6s %9s %9s %9s  %s"
      % ("#", "opened(UTC)", "closed(UTC)", "symbol", "side", "vol",
         "openP", "closeP", "PnL(USC)", "open->close comment"))
print("-" * 100)
run_pnl = 0.0
for n, r in enumerate(rows, 1):
    run_pnl += r["pnl"]
    ot = datetime.utcfromtimestamp(r["open_t"]).strftime("%m-%d %H:%M")
    ct = datetime.utcfromtimestamp(r["close_t"]).strftime("%m-%d %H:%M")
    tag = "WIN " if r["pnl"] > 0 else ("LOSS" if r["pnl"] < 0 else "flat")
    print("%-3d %-16s %-16s %-9s %-5s %-6.2f %9.2f %9.2f %9.2f %s [%s|%s] m=%s"
          % (n, ot, ct, r["sym"], r["side"], r["vol"],
             r["open_p"], r["close_p"], r["pnl"], tag,
             (r["open_c"] or "-"), (r["close_c"] or "-"), r["magic"]))

wins = [r for r in rows if r["pnl"] > 0]
losses = [r for r in rows if r["pnl"] < 0]
print("-" * 100)
print("summary: %d trades  %dW / %dL  win-rate %.0f%%   net %.2f USC (= $%.2f)"
      % (len(rows), len(wins), len(losses),
         100.0*len(wins)/len(rows) if rows else 0,
         run_pnl, run_pnl/100.0))
if wins and losses:
    aw = sum(r["pnl"] for r in wins)/len(wins)
    al = sum(r["pnl"] for r in losses)/len(losses)
    gp = sum(r["pnl"] for r in wins); gl = -sum(r["pnl"] for r in losses)
    print("  avg win %.2f  avg loss %.2f  reward:risk %.2f  profit-factor %.2f"
          % (aw, al, aw/abs(al) if al else 0, gp/gl if gl else 0))
# streaks
cur = 0; worst = 0
for r in rows:
    if r["pnl"] < 0:
        cur += 1; worst = max(worst, cur)
    else:
        cur = 0
print("  longest losing streak: %d in a row" % worst)
print("=" * 100)
mt5.shutdown()
