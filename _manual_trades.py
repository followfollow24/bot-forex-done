#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_manual_trades.py -- what does the human actually do, and does it work?

The account-wide exit audit found the operator's hand-closed trades
averaged +26.15 while strategy-closed trades averaged -31.36. That is the
single largest measured difference on the account, and nobody has ever
looked at WHAT the human was doing -- only that it beat the bots.

This pulls every position the human opened themselves (magic 0 -- an EA
always stamps its own magic, so magic 0 is a human click) and asks:

  - with or against the prevailing trend at entry?  (H4 EMA50/200, the
    same filter the live bots use, evaluated on bars that had CLOSED
    before the entry -- no lookahead)
  - how long held, and did that differ between winners and losers?
  - what the payoff profile is: WR, avg win vs avg loss, profit factor
  - concentration: what fraction of profit is the single best trade

Ownership resolves through position_id, never per-deal magic: a
broker-side stop writes its closing deal with magic 0 too, so filtering
deals individually would mix bot stops into the "manual" bucket.

Usage (on the VPS):  python _manual_trades.py [days]
"""
import sys
from collections import defaultdict
from datetime import datetime, timedelta

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)")
    sys.exit(1)

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 3650

REASON = {0: "CLIENT", 1: "MOBILE", 2: "WEB", 3: "EXPERT", 4: "SL",
          5: "TP", 6: "STOPOUT", 7: "ROLLOVER", 8: "VMARGIN", 9: "SPLIT"}
HUMAN_OPEN = {"CLIENT", "MOBILE", "WEB"}


def ema(vals, n):
    if len(vals) < n:
        return None
    k = 2.0 / (n + 1)
    out = vals[0]
    for v in vals[1:]:
        out = v * k + out * (1 - k)
    return out


def trend_at(symbol, when):
    """H4 EMA50 vs EMA200 using only bars CLOSED before `when`."""
    rates = mt5.copy_rates_from(symbol, mt5.TIMEFRAME_H4, when, 400)
    if rates is None or len(rates) < 210:
        return None
    cutoff = when.timestamp()
    closes = [float(r["close"]) for r in rates
              if r["time"] + 4 * 3600 <= cutoff]      # fully closed only
    if len(closes) < 210:
        return None
    f, s = ema(closes[-210:], 50), ema(closes[-210:], 200)
    if f is None or s is None:
        return None
    return "up" if f > s else "down"


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed")
        return 2
    acct = mt5.account_info()
    if acct is None:
        print("[ERROR] account_info() returned None")
        mt5.shutdown()
        return 2

    frm = datetime.now() - timedelta(days=DAYS)
    deals = mt5.history_deals_get(frm, datetime.now() + timedelta(days=1)) or []

    pos = {}
    for d in deals:
        if d.position_id == 0:
            continue
        p = pos.setdefault(d.position_id, {
            "magic": 0, "net": 0.0, "sym": d.symbol,
            "open": None, "close": None, "vol": 0.0,
            "open_reason": None, "close_reason": None,
            "open_px": None, "close_px": None, "side": None})
        p["net"] += d.profit + d.swap + d.commission
        if d.magic and not p["magic"]:
            p["magic"] = d.magic
        if d.entry == mt5.DEAL_ENTRY_IN:
            p["open"] = datetime.fromtimestamp(d.time)
            p["vol"] = d.volume
            p["open_reason"] = REASON.get(d.reason, str(d.reason))
            p["open_px"] = d.price
            p["side"] = "long" if d.type == mt5.DEAL_TYPE_BUY else "short"
        elif d.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY):
            p["close"] = datetime.fromtimestamp(d.time)
            p["close_reason"] = REASON.get(d.reason, str(d.reason))
            p["close_px"] = d.price

    # a human-opened position: no EA magic AND the opening deal came from a
    # human-facing terminal
    manual = [p for p in pos.values()
              if p["magic"] == 0 and p["close"] is not None
              and p["open_reason"] in HUMAN_OPEN]

    print("=" * 78)
    print(f" MANUAL TRADES -- account {acct.login} ({acct.server}) {acct.currency}")
    print(f" closed positions since {frm:%Y-%m-%d}")
    print("=" * 78)
    if not manual:
        print("  no human-opened closed positions found on this account")
        mt5.shutdown()
        return 0

    manual.sort(key=lambda p: p["open"])
    print(f"{'#':>3} {'symbol':<10}{'side':<6}{'vol':>6}{'opened':>17}"
          f"{'held':>9}{'trend':>7}{'w/trend':>8}{'exit':>8}{'net':>10}")
    print("-" * 78)
    wins = losses = withtrend = withtrend_win = 0
    tot = 0.0
    durations_w, durations_l = [], []
    for i, p in enumerate(manual, 1):
        held_h = (p["close"] - p["open"]).total_seconds() / 3600.0
        tr = trend_at(p["sym"], p["open"])
        aligned = "" if tr is None else (
            "yes" if (tr == "up") == (p["side"] == "long") else "no")
        if aligned == "yes":
            withtrend += 1
            if p["net"] > 0:
                withtrend_win += 1
        if p["net"] > 0:
            wins += 1
            durations_w.append(held_h)
        else:
            losses += 1
            durations_l.append(held_h)
        tot += p["net"]
        print(f"{i:>3} {p['sym']:<10}{p['side']:<6}{p['vol']:>6.2f}"
              f"{p['open']:%m-%d %H:%M}"[:34].rjust(0) +
              f"{'':>1}{held_h:>7.1f}h{(tr or '?'):>7}{aligned:>8}"
              f"{(p['close_reason'] or '?'):>8}{p['net']:>+10.2f}")

    n = len(manual)
    print("-" * 78)
    print(f"  trades {n}   win {wins}  loss {losses}   "
          f"WR {100.0*wins/n:.0f}%   net {tot:+,.2f} {acct.currency}")
    gw = sum(p["net"] for p in manual if p["net"] > 0)
    gl = -sum(p["net"] for p in manual if p["net"] <= 0)
    if wins:
        print(f"  avg win  {gw/wins:+,.2f}   avg hold {sum(durations_w)/len(durations_w):.1f}h")
    if losses:
        print(f"  avg loss {-gl/losses:+,.2f}   avg hold {sum(durations_l)/len(durations_l):.1f}h")
    if gl > 0:
        print(f"  profit factor {gw/gl:.2f}")
    if withtrend:
        print(f"  with-trend entries {withtrend}/{n} "
              f"({100.0*withtrend/n:.0f}%)   of those, {withtrend_win} won "
              f"({100.0*withtrend_win/withtrend:.0f}%)")
    against = n - withtrend
    aw = wins - withtrend_win
    if against:
        print(f"  against-trend      {against}/{n} "
              f"({100.0*against/n:.0f}%)   of those, {aw} won "
              f"({100.0*aw/against:.0f}%)")
    best = max(manual, key=lambda p: p["net"])
    if tot > 0:
        print(f"  single best trade {best['net']:+,.2f} = "
              f"{100.0*best['net']/tot:.0f}% of net profit")
    print()
    print("  Trend = H4 EMA50 vs EMA200 on bars closed BEFORE entry (the same")
    print("  filter the live bots use). 'w/trend yes' means the direction the")
    print("  human chose matched it.")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
