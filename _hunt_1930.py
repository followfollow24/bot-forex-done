#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_hunt_1930.py -- is there ANY usable rule at 19:30, or is the window dead?

The operator asked whether nothing at 19:30 works. Answering that by
sweeping variants and reporting the winner is exactly the mistake that
produced the last set of numbers, so this is built the other way round.

METHOD. 90 variants (9 gates x follow/fade x 5 hold times) are scored on
each half of the history separately: January-May and June-September.
Then, twice and in both directions, the best variant on one half is taken
-- untouched -- to the other. A rule that only wins where it was chosen
is noise no matter how large its number.

The decisive figure is neither of those winners. It is the RANK
CORRELATION between the two halves across all 90 variants. If tuning at
this bell carried any information, variants that did well in one half
would tend to do well in the other. A correlation near zero says the
ranking is noise, and that no amount of searching will find a rule here
-- which is a real answer, and a cheaper one than discovering it with
money.

The count of variants profitable in BOTH halves is reported against what
chance alone would produce, for the same reason.

"fade" trades the opposite way to the move: worth including because a
19:30 spike reversing is at least as plausible a story as it continuing,
and a search that only looks in the direction you already believe in
cannot tell you that you were wrong.

Usage:  python _hunt_1930.py [symbol] [days]
"""
import sys
from datetime import date, datetime, timedelta, timezone

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)"); sys.exit(1)

import numpy as np

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "XAUAUDm"
DAYS = int(sys.argv[2]) if len(sys.argv) > 2 else 365
GATES = [5, 8, 11, 14, 17, 20, 25, 30, 40]
HOLDS = [10, 20, 30, 45, 60]
SIDES = [("follow", +1), ("fade", -1)]
SPLIT = date(2026, 6, 1)
MIN_TRADES = 10          # a two-trade fluke must not win a sweep
SL_ATR, MIN_WAIT, MAXW, THAI = 3.0, 1.0, 900, 7


def atr_at(sym, when, n=14):
    r = mt5.copy_rates_range(sym, mt5.TIMEFRAME_H1,
                             when - timedelta(hours=40), when)
    if r is None or len(r) < n + 1:
        return None
    trs = [max(float(r[i]["high"]) - float(r[i]["low"]),
               abs(float(r[i]["high"]) - float(r[i - 1]["close"])),
               abs(float(r[i]["low"]) - float(r[i - 1]["close"])))
           for i in range(1, len(r))]
    return sum(trs[-n:]) / n


def ride(bars, srv, entry, side, sl, hold_min, spread):
    end = srv + hold_min * 60
    exit_px = None
    for b in bars:
        bt = int(b["time"])
        if bt + 300 <= srv:
            continue
        h, l, c = float(b["high"]), float(b["low"]), float(b["close"])
        if ((l <= sl) if side > 0 else (h >= sl)):
            exit_px = sl; break
        if bt + 300 >= end:
            exit_px = c; break
    if exit_px is None:
        exit_px = float(bars[-1]["close"])
    return (exit_px - entry) * side - spread


def load(sym):
    """Per session: for every gate, the crossing instant; then every
    (direction, hold) outcome from that instant."""
    out = []
    today = (datetime.now(timezone.utc) + timedelta(hours=THAI)).date()
    for back in range(DAYS, 0, -1):
        d = today - timedelta(days=back)
        if d.weekday() >= 5:
            continue
        s = datetime(d.year, d.month, d.day, 12, 30, tzinfo=timezone.utc)
        t = mt5.copy_ticks_range(sym, s, s + timedelta(seconds=MAXW + 60),
                                 mt5.COPY_TICKS_ALL)
        if t is None or len(t) < 20:
            continue
        bars = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M5, s,
                                    s + timedelta(minutes=150))
        atr = atr_at(sym, s)
        if bars is None or len(bars) < 6 or not atr:
            continue
        si = mt5.symbol_info(sym)
        spread = si.spread * si.point
        t0 = int(s.timestamp())
        sec = (t["time_msc"].astype(np.int64) - t0 * 1000) / 1000.0
        bid, ask = t["bid"].astype(float), t["ask"].astype(float)
        mid = np.where(ask > 0, (bid + ask) / 2.0, bid)
        ref = float(mid[0])
        res = {}
        for g in GATES:
            w = np.where((sec >= MIN_WAIT) & (np.abs(mid - ref) >= g))[0]
            if len(w) == 0:
                continue
            i = int(w[0])
            moved = 1 if mid[i] > ref else -1
            srv = t0 + float(sec[i])
            for name, mult in SIDES:
                side = moved * mult
                entry = float(ask[i]) if side > 0 else float(bid[i])
                sl = entry - side * SL_ATR * atr
                for h in HOLDS:
                    res[(g, name, h)] = ride(bars, srv, entry, side, sl,
                                             h, spread)
        out.append({"date": d, "res": res})
    return out


def score(rows, key):
    v = [r["res"][key] for r in rows if key in r["res"]]
    if not v:
        return 0, 0.0, 0.0
    return len(v), float(np.mean(v)), float(np.sum(v))


def rank(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    for pos, i in enumerate(order):
        r[i] = pos
    return r


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed"); return 2
    if mt5.symbol_info(SYMBOL) is None:
        print(f"[ERROR] {SYMBOL} not found"); mt5.shutdown(); return 2
    mt5.symbol_select(SYMBOL, True)
    print(f"loading up to {DAYS} days of ticks ...", flush=True)
    rows = load(SYMBOL)
    if len(rows) < 40:
        print(f"[ERROR] only {len(rows)} sessions"); mt5.shutdown(); return 2
    early = [r for r in rows if r["date"] < SPLIT]
    late = [r for r in rows if r["date"] >= SPLIT]

    keys = [(g, n, h) for g in GATES for n, _ in SIDES for h in HOLDS]
    print("=" * 76)
    print(f" HUNTING 19:30 -- {SYMBOL}   {len(keys)} variants, "
          f"{len(rows)} sessions")
    print(f" EARLY {early[0]['date']} to {early[-1]['date']}  ({len(early)})"
          f"   |   LATE {late[0]['date']} to {late[-1]['date']}  "
          f"({len(late)})")
    print("=" * 76)

    stats = {}
    for k in keys:
        ne, me, se = score(early, k)
        nl, ml, sl_ = score(late, k)
        stats[k] = (ne, me, se, nl, ml, sl_)

    def best_on(which):
        cand = [k for k in keys
                if (stats[k][0] if which == "early" else stats[k][3])
                >= MIN_TRADES]
        if not cand:
            return None
        return max(cand, key=lambda k: stats[k][1] if which == "early"
                   else stats[k][4])

    print("\nCHOOSE ON ONE HALF, SCORE ON THE OTHER -- both directions\n")
    print(f"{'chosen on':>12}{'variant':>26}{'its own half':>16}"
          f"{'the other half':>18}")
    print("-" * 74)
    for which, other in (("early", "late"), ("late", "early")):
        k = best_on(which)
        if k is None:
            print(f"{which:>12}   no variant reached {MIN_TRADES} trades")
            continue
        ne, me, se, nl, ml, sl_ = stats[k]
        own, oth = ((ne, me), (nl, ml)) if which == "early" else ((nl, ml), (ne, me))
        label = f"gate {k[0]} {k[1]} hold {k[2]}m"
        print(f"{which:>12}{label:>26}"
              f"{own[1]:>+11.2f}/trade{oth[1]:>+13.2f}/trade"
              + ("   HELD UP" if oth[1] > 0 else "   FELL APART"))
    print("-" * 74)
    print("  A variant that only wins where it was chosen is noise, however")
    print("  large its number. Both rows have to hold for the search to mean")
    print("  anything.")

    # ---- does the ranking transfer at all? -----------------------------
    usable = [k for k in keys
              if stats[k][0] >= MIN_TRADES and stats[k][3] >= MIN_TRADES]
    if len(usable) >= 8:
        a = [stats[k][1] for k in usable]
        b = [stats[k][4] for k in usable]
        ra, rb = rank(a), rank(b)
        rho = float(np.corrcoef(ra, rb)[0, 1])
        both = sum(1 for k in usable if stats[k][1] > 0 and stats[k][4] > 0)
        pe = sum(1 for k in usable if stats[k][1] > 0) / len(usable)
        pl = sum(1 for k in usable if stats[k][4] > 0) / len(usable)
        print(f"\nDOES TUNING TRANSFER? ({len(usable)} variants traded "
              f"enough in both halves)\n")
        print(f"  rank correlation between the halves:  rho = {rho:+.2f}")
        print(f"  profitable in EARLY: {100*pe:.0f}%   "
              f"in LATE: {100*pl:.0f}%")
        print(f"  profitable in BOTH:  {both} of {len(usable)}"
              f"   (chance alone would give ~{pe*pl*len(usable):.0f})")
        print()
        if rho < 0.2:
            print("  Near zero. Doing well in one half does not predict doing")
            print("  well in the other, so searching this bell harder cannot")
            print("  find a rule -- the ranking it would sort by is noise.")
        else:
            print("  Some signal in the ranking. Worth a closer look, but a")
            print("  single positive correlation is not an edge either.")

    # ---- anything positive in both, listed honestly ---------------------
    survivors = [k for k in usable if stats[k][1] > 0 and stats[k][4] > 0]
    print(f"\nVARIANTS PROFITABLE IN BOTH HALVES\n")
    if not survivors:
        print("  none.")
    else:
        print(f"{'variant':>26}{'EARLY /trade':>16}{'n':>5}"
              f"{'LATE /trade':>15}{'n':>5}")
        print("-" * 67)
        for k in sorted(survivors,
                        key=lambda k: -min(stats[k][1], stats[k][4])):
            ne, me, se, nl, ml, sl_ = stats[k]
            print(f"{f'gate {k[0]} {k[1]} hold {k[2]}m':>26}"
                  f"{me:>+16.2f}{ne:>5}{ml:>+15.2f}{nl:>5}")
        print("-" * 67)
        print("  Survivors are candidates, NOT edges. With 90 variants tried,")
        print("  a handful clearing both halves is what chance produces --")
        print("  compare the count above against the chance figure before")
        print("  believing any single row.")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
