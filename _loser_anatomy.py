#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_loser_anatomy.py -- why did the losers lose, and what would have saved them?

Every losing chart_ai trade cost about the same (-41 to -50). That
uniformity is the first clue: they are not assorted mishaps, they are the
stop being hit, exactly as designed. The wins were +67 to +85, so the
1.5:1 payoff worked too. Nothing about the EXITS is malfunctioning.

But "the stop was hit" does not say WHY. Two very different worlds produce
the same stop-out:

  A. price went the wrong way immediately -- the direction call was wrong,
     and no exit rule can rescue it.
  B. price first went 0.8R our way, then reversed into the stop -- the
     direction was right, the TARGET was simply too far away.

These demand opposite fixes, and the live log cannot tell them apart. So
for every real trade this walks the actual M15 bars and records the
MAXIMUM PROFIT THAT WAS EVER AVAILABLE before the 1R stop would have been
touched. That single number separates world A from world B, and it also
prices every possible take-profit at once: a target at X wins if and only
if the best excursion reached X.

From that, the optimal TP per symbol falls out arithmetically instead of
being guessed -- for the original direction AND for the inverted one,
kept separate because the 150-sample replay found gold and BTC behaving
oppositely (gold ~coin-flip, BTC significantly anti-predictive).

Pessimistic throughout: within any bar the adverse extreme is assumed to
happen first, so a bar that spans both levels counts as the stop. That
biases every result DOWN, including the ones this script might otherwise
make look attractive.

Usage (on the VPS, MT5 running):  python _loser_anatomy.py
"""
import os
import re
import sys
from datetime import datetime, timedelta

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)")
    sys.exit(1)

BASE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(BASE, "forex_bot_chart_ai_trader.log")

# round-trip cost in PRICE units, per the repo's verified specs
SPREAD = {"XAUUSDc": 0.24, "BTCUSDc": 10.0, "ETHUSDc": 0.6}
MAX_HOLD_BARS = 96                      # 24h on M15
TP_GRID = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5]

OPEN_RE = re.compile(
    r"^(?P<ts>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ \[INFO\] \[OPEN\] "
    r"(?P<side>LONG|SHORT) (?P<sym>\S+) lot=(?P<lot>[\d.]+) "
    r"fill=(?P<fill>[\d.]+) sl=(?P<sl>[\d.]+) tp=(?P<tp>[\d.]+)")


def parse_opens():
    if not os.path.exists(LOG):
        print(f"[ERROR] {LOG} not found")
        sys.exit(1)
    out = []
    with open(LOG, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            m = OPEN_RE.match(line.strip())
            if not m:
                continue
            d = m.groupdict()
            out.append({
                "ts": datetime.strptime(d["ts"], "%Y-%m-%d %H:%M:%S"),
                "side": d["side"], "sym": d["sym"],
                "fill": float(d["fill"]), "sl": float(d["sl"]),
                "tp": float(d["tp"]),
            })
    return out


def bars_after(sym, ts, n=MAX_HOLD_BARS):
    rates = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M15,
                                 ts, ts + timedelta(minutes=15 * (n + 4)))
    if rates is None or len(rates) < 2:
        return []
    return list(rates)[1:n + 1]          # skip the entry bar itself


def best_excursion(bars, entry, long_, R):
    """Maximum favourable move (in R) reached BEFORE the 1R stop is touched.

    Returns (best_R, stopped). Within each bar the adverse extreme is taken
    first, so a bar containing both the target and the stop resolves as the
    stop -- the pessimistic reading, applied to original and mirror alike so
    neither side is flattered.
    """
    best = 0.0
    for b in bars:
        hi, lo = float(b["high"]), float(b["low"])
        adverse = lo if long_ else hi
        adv_R = ((entry - adverse) if long_ else (adverse - entry)) / R
        if adv_R >= 1.0:
            return best, True
        fav = hi if long_ else lo
        fav_R = ((fav - entry) if long_ else (entry - fav)) / R
        if fav_R > best:
            best = fav_R
    return best, False


def report(title, rows, sym):
    """rows: list of (best_R, stopped). Prices every TP on the same trades."""
    n = len(rows)
    if n == 0:
        return
    sp = SPREAD.get(sym, 0.0)
    cost_R = rows[0][2] if len(rows[0]) > 2 else 0.0
    print(f"\n  {title}   ({n} trades)")
    print(f"  {'TP':>6}{'win%':>8}{'EV/trade':>11}{'total R':>10}")
    best_ev, best_tp = None, None
    for tp in TP_GRID:
        wins = sum(1 for r in rows if r[0] >= tp)
        wr = wins / n
        ev = wr * tp - (1 - wr) * 1.0 - cost_R
        if best_ev is None or ev > best_ev:
            best_ev, best_tp = ev, tp
        flag = ""
        print(f"  {tp:>6.2f}{100*wr:>7.1f}%{ev:>+11.3f}{ev*n:>+10.1f}{flag}")
    print(f"  -> best target {best_tp}R, EV {best_ev:+.3f}R/trade "
          f"({best_ev*n:+.1f}R over {n})")
    return best_ev, best_tp


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed")
        sys.exit(1)

    trades = parse_opens()
    if not trades:
        print("[ERROR] no [OPEN] lines parsed")
        mt5.shutdown()
        return

    by_sym = {}
    never = {}
    for t in trades:
        bars = bars_after(t["sym"], t["ts"])
        if len(bars) < 4:
            continue
        R = abs(t["fill"] - t["sl"])
        if R <= 0:
            continue
        sp = SPREAD.get(t["sym"], 0.0)
        cost_R = sp / R
        long_ = t["side"] == "LONG"

        o_best, _ = best_excursion(bars, t["fill"], long_, R)
        i_best, _ = best_excursion(bars, t["fill"], not long_, R)
        d = by_sym.setdefault(t["sym"], {"orig": [], "inv": []})
        d["orig"].append((o_best, True, cost_R))
        d["inv"].append((i_best, True, cost_R))

    print("=" * 74)
    print(" LOSER ANATOMY -- chart_ai_trader, real trades vs real M15 bars")
    print(" 'best excursion' = most profit ever available before the 1R stop")
    print(" pessimistic: a bar spanning both levels counts as the stop")
    print("=" * 74)

    grand = {}
    for sym in sorted(by_sym):
        d = by_sym[sym]
        n = len(d["orig"])
        print("\n" + "-" * 74)
        print(f"  {sym}   {n} trades")
        print("-" * 74)

        # --- world A vs world B: did the trade EVER show a profit? ---
        for lbl, key in (("as traded", "orig"), ("inverted", "inv")):
            rows = d[key]
            dead = sum(1 for r in rows if r[0] < 0.10)
            half = sum(1 for r in rows if r[0] >= 0.50)
            full = sum(1 for r in rows if r[0] >= 1.50)
            print(f"  {lbl:<10} never +0.1R: {dead:>3}/{n}  "
                  f"reached +0.5R: {half:>3}/{n}  reached +1.5R: {full:>3}/{n}")

        for lbl, key in (("AS TRADED", "orig"), ("INVERTED", "inv")):
            r = report(lbl, d[key], sym)
            if r:
                grand.setdefault(key, []).append((sym, r[0], r[1], len(d[key])))

    print("\n" + "=" * 74)
    print("  READING IT")
    print("  'never +0.1R' high  -> direction was simply wrong; no exit rule")
    print("     saves these, and a closer target does not help either.")
    print("  'reached +0.5R' high but '+1.5R' low -> direction was fine, the")
    print("     1.5R target was too far. A closer target converts them.")
    print("  EV is in R and already net of the measured spread. Positive EV")
    print("  here is necessary, NOT sufficient: it is in-sample on the same")
    print("  trades that motivated the idea, so it must clear walk-forward")
    print("  before any of it goes near real money.")
    mt5.shutdown()


if __name__ == "__main__":
    main()
