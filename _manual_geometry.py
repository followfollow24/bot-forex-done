#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_manual_geometry.py -- reverse-engineer the operator's own trade rules.

_manual_trades.py established the human's hand-opened trades are the best
performing thing on this account: 26 trades, WR 58%, PF 1.84, +216.13 USD,
cutting losers in half the time they hold winners. The operator asked to
turn that into a bot.

You cannot copy a discretionary trader by guessing. This measures the
actual geometry so the rules come from the tape, not from a story:

  - TP and SL distance per trade, in price AND in H1-ATR units (so the
    rule scales with volatility instead of hardcoding dollars)
  - the realised R:R and whether the brackets were symmetric
  - entry hour-of-day (UTC) -- a scalper usually lives in one session
  - direction split and win rate within each
  - how often price was already extended vs mean-reverting at entry

Exit reason is taken from the CLOSING deal, so TP/SL here means the
broker actually filled the bracket, not that one was merely attached.

Usage (on the VPS):  python _manual_geometry.py [days]
"""
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)")
    sys.exit(1)

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 3650
REASON = {0: "CLIENT", 1: "MOBILE", 2: "WEB", 3: "EXPERT", 4: "SL",
          5: "TP", 6: "STOPOUT"}
HUMAN_OPEN = {"CLIENT", "MOBILE", "WEB"}


def atr_h1(symbol, when, n=14):
    """ATR14 on H1 bars closed before `when`."""
    rates = mt5.copy_rates_from(symbol, mt5.TIMEFRAME_H1, when, n * 4)
    if rates is None or len(rates) < n + 1:
        return None
    cutoff = when.timestamp()
    rs = [r for r in rates if r["time"] + 3600 <= cutoff]
    if len(rs) < n + 1:
        return None
    trs = []
    for i in range(1, len(rs)):
        h, l, pc = float(rs[i]["high"]), float(rs[i]["low"]), float(rs[i-1]["close"])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-n:]) / n if len(trs) >= n else None


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed")
        return 2
    acct = mt5.account_info()
    frm = datetime.now() - timedelta(days=DAYS)
    deals = mt5.history_deals_get(frm, datetime.now() + timedelta(days=1)) or []

    pos = {}
    for d in deals:
        if d.position_id == 0:
            continue
        p = pos.setdefault(d.position_id, {
            "magic": 0, "net": 0.0, "sym": d.symbol, "vol": 0.0,
            "open": None, "close": None, "open_px": None, "close_px": None,
            "side": None, "open_reason": None, "close_reason": None})
        p["net"] += d.profit + d.swap + d.commission
        if d.magic and not p["magic"]:
            p["magic"] = d.magic
        if d.entry == mt5.DEAL_ENTRY_IN:
            p.update(open=datetime.fromtimestamp(d.time), vol=d.volume,
                     open_px=d.price, open_reason=REASON.get(d.reason, str(d.reason)),
                     side="long" if d.type == mt5.DEAL_TYPE_BUY else "short")
        elif d.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY):
            p.update(close=datetime.fromtimestamp(d.time), close_px=d.price,
                     close_reason=REASON.get(d.reason, str(d.reason)))

    manual = [p for p in pos.values()
              if p["magic"] == 0 and p["close"] and p["open_reason"] in HUMAN_OPEN]
    manual.sort(key=lambda p: p["open"])

    print("=" * 80)
    print(f" MANUAL TRADE GEOMETRY -- account {acct.login} ({acct.currency})")
    print("=" * 80)
    if not manual:
        print("  none found")
        mt5.shutdown()
        return 0

    print(f"{'#':>3} {'sym':<9}{'side':<6}{'entry':>10}{'exit':>10}"
          f"{'move':>8}{'xATR':>7}{'exit':>7}{'hourUTC':>8}{'net':>9}")
    print("-" * 80)
    tp_dists, sl_dists, atrs = [], [], []
    hours = Counter()
    by_dir = defaultdict(lambda: [0, 0])
    for i, p in enumerate(manual, 1):
        a = atr_h1(p["sym"], p["open"])
        atrs.append(a)
        sign = 1 if p["side"] == "long" else -1
        move = (p["close_px"] - p["open_px"]) * sign
        xatr = (abs(move) / a) if a else None
        cr = p["close_reason"]
        if cr == "TP":
            tp_dists.append((abs(move), xatr))
        elif cr == "SL":
            sl_dists.append((abs(move), xatr))
        hours[p["open"].hour] += 1
        by_dir[p["side"]][0] += 1
        if p["net"] > 0:
            by_dir[p["side"]][1] += 1
        print(f"{i:>3} {p['sym']:<9}{p['side']:<6}{p['open_px']:>10.2f}"
              f"{p['close_px']:>10.2f}{move:>+8.2f}"
              f"{(f'{xatr:.2f}' if xatr else '?'):>7}{(cr or '?'):>7}"
              f"{p['open'].hour:>8}{p['net']:>+9.2f}")

    print("-" * 80)

    def summarise(label, rows):
        if not rows:
            print(f"  {label}: none")
            return
        px = [r[0] for r in rows]
        xa = [r[1] for r in rows if r[1]]
        px.sort()
        med = px[len(px) // 2]
        line = (f"  {label}: n={len(px)}  median {med:.2f} price"
                f"  (min {px[0]:.2f}  max {px[-1]:.2f})")
        if xa:
            xa.sort()
            line += f"   median {xa[len(xa)//2]:.2f}xATR"
        print(line)

    summarise("TP distance", tp_dists)
    summarise("SL distance", sl_dists)
    if tp_dists and sl_dists:
        mt = sorted(r[0] for r in tp_dists)[len(tp_dists)//2]
        ms = sorted(r[0] for r in sl_dists)[len(sl_dists)//2]
        print(f"  implied R:R = {mt/ms:.2f} : 1   (median TP / median SL)")
    va = [a for a in atrs if a]
    if va:
        va.sort()
        print(f"  H1 ATR14 at entry: median {va[len(va)//2]:.2f}")
    print()
    print("  exit mix: " + "  ".join(
        f"{k}={v}" for k, v in Counter(p["close_reason"] for p in manual).most_common()))
    print("  direction: " + "  ".join(
        f"{k} {v[0]} trades, {v[1]} won ({100.0*v[1]/v[0]:.0f}%)"
        for k, v in by_dir.items()))
    print("  entry hour (UTC): " + "  ".join(
        f"{h:02d}h×{c}" for h, c in sorted(hours.items())))
    print("  symbols: " + "  ".join(
        f"{k}×{v}" for k, v in Counter(p["sym"] for p in manual).most_common()))
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
