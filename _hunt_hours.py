#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_hunt_hours.py -- put the three candidate hours through what 19:30 failed.

An earlier study ranked all 22 tradeable hours and found 07:00, 08:00 and
13:00 Thai beating their own controls at z +2.72 / +3.06 / +3.22. That was
never split into train and test, the three were SELECTED out of 22, and it
concluded the spread ate the edge -- with a spread constant this project
has separately found to be wrong by ~12x elsewhere. So the numbers are a
lead, not a result, and this asks the same questions of them that emptied
the 19:30 window of content:

  * search on one half of the history, score on the other, BOTH ways
  * the rank correlation across every variant, which says whether tuning
    transfers at all or is sorting noise
  * how many variants clear both halves, against what chance would give
  * every result recomputed at three slippage levels, because "the spread
    ate it" is the specific claim that closed this line of enquiry and it
    deserves to be tested rather than inherited

Each hour is reported on its own AND the three pooled, because trading all
three is the operator's actual question. Pooling is not free: 07:00 and
08:00 are adjacent and will often be the same move, so three sessions a
day is nearer two independent bets than three, and the pooled row should
be read with that in mind rather than as a tripling of anything.

Usage:  python _hunt_hours.py [symbol] [days] [thai_hours]
        python _hunt_hours.py XAUAUDm 365 7,8,13
"""
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)"); sys.exit(1)

import numpy as np

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "XAUAUDm"
DAYS = int(sys.argv[2]) if len(sys.argv) > 2 else 365
HOURS = [int(x) for x in (sys.argv[3] if len(sys.argv) > 3
                          else "7,8,13").split(",")]
GATES = [5, 8, 11, 14, 17, 20, 25]
HOLDS = [10, 20, 30, 45, 60]
SIDES = [("follow", +1), ("fade", -1)]
SLIPS = [0.0, 0.25, 0.5]          # points, charged once per trade
SPLIT = date(2026, 6, 1)
MIN_TRADES = 10
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


def ride(bars, srv, entry, side, sl, hold_min):
    end = srv + hold_min * 60
    for b in bars:
        bt = int(b["time"])
        if bt + 300 <= srv:
            continue
        h, l, c = float(b["high"]), float(b["low"]), float(b["close"])
        if ((l <= sl) if side > 0 else (h >= sl)):
            return sl
        if bt + 300 >= end:
            return c
    return float(bars[-1]["close"])


def load(sym, hour_utc):
    """One session per weekday at this UTC hour, with every variant's
    gross points (spread and slippage are charged later, so the same load
    can answer the cost question at several levels)."""
    out = []
    today = (datetime.now(timezone.utc) + timedelta(hours=THAI)).date()
    for back in range(DAYS, 0, -1):
        d = today - timedelta(days=back)
        if d.weekday() >= 5:
            continue
        s = datetime(d.year, d.month, d.day, hour_utc, 0, tzinfo=timezone.utc)
        t = mt5.copy_ticks_range(sym, s, s + timedelta(seconds=MAXW + 60),
                                 mt5.COPY_TICKS_ALL)
        if t is None or len(t) < 20:
            continue
        bars = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M5, s,
                                    s + timedelta(minutes=150))
        atr = atr_at(sym, s)
        if bars is None or len(bars) < 6 or not atr:
            continue
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
                    px = ride(bars, srv, entry, side, sl, h)
                    res[(g, name, h)] = (px - entry) * side
        out.append({"date": d, "res": res})
    return out


def rank(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    for pos, i in enumerate(order):
        r[i] = pos
    return r


def analyse(label, rows, cost, keys):
    """cost = spread + slippage, charged once per trade."""
    early = [r for r in rows if r["date"] < SPLIT]
    late = [r for r in rows if r["date"] >= SPLIT]
    if not early or not late:
        print(f"  {label}: not enough history either side of the split")
        return None

    def sc(part, k):
        v = [r["res"][k] - cost for r in part if k in r["res"]]
        return (len(v), float(np.mean(v))) if v else (0, 0.0)

    st = {k: sc(early, k) + sc(late, k) for k in keys}
    usable = [k for k in keys if st[k][0] >= MIN_TRADES and st[k][2] >= MIN_TRADES]
    if len(usable) < 8:
        print(f"  {label}: only {len(usable)} variants traded enough")
        return None
    a = [st[k][1] for k in usable]
    b = [st[k][3] for k in usable]
    rho = float(np.corrcoef(rank(a), rank(b))[0, 1])
    pe = sum(1 for k in usable if st[k][1] > 0) / len(usable)
    pl = sum(1 for k in usable if st[k][3] > 0) / len(usable)
    both = sum(1 for k in usable if st[k][1] > 0 and st[k][3] > 0)

    be = max(usable, key=lambda k: st[k][1])
    bl = max(usable, key=lambda k: st[k][3])
    held = (st[be][3] > 0) + (st[bl][1] > 0)

    print(f"  {label:>22}  rho {rho:+.2f}   both {both:>2}/{len(usable)}"
          f" (chance ~{pe*pl*len(usable):.0f})   "
          f"OOS tests held {held}/2   "
          f"best-early {st[be][1]:+.2f}->{st[be][3]:+.2f}")
    return rho, both, len(usable), held


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed"); return 2
    si = mt5.symbol_info(SYMBOL)
    if si is None:
        print(f"[ERROR] {SYMBOL} not found"); mt5.shutdown(); return 2
    mt5.symbol_select(SYMBOL, True)
    spread = si.spread * si.point
    keys = [(g, n, h) for g in GATES for n, _ in SIDES for h in HOLDS]

    print("=" * 78)
    print(f" THREE CANDIDATE HOURS -- {SYMBOL}   {len(keys)} variants each")
    print(f" LIVE spread read from the broker: {spread:.3f} pts"
          f"   (not a constant -- this project has shipped a wrong one)")
    print(f" Thai hours {HOURS} = UTC {[(h - THAI) % 24 for h in HOURS]}")
    print("=" * 78)

    per_hour, pooled = {}, defaultdict(list)
    for th in HOURS:
        uh = (th - THAI) % 24
        print(f"\nloading {th:02d}:00 Thai ({uh:02d}:00 UTC) ...", flush=True)
        rows = load(SYMBOL, uh)
        print(f"  {len(rows)} sessions with tick data")
        per_hour[th] = rows
        for r in rows:
            pooled[r["date"]].append(r)

    pooled_rows = []
    for d, lst in sorted(pooled.items()):
        merged = {}
        for j, r in enumerate(lst):
            for k, v in r["res"].items():
                merged[(k, j)] = v
        pooled_rows.append({"date": d, "res": merged})
    pooled_keys = sorted({k for r in pooled_rows for k in r["res"]})

    for slip in SLIPS:
        cost = spread + slip
        print(f"\n{'='*78}")
        print(f" COST {cost:.3f} pts per trade "
              f"(spread {spread:.3f} + slippage {slip:.2f})")
        print(f"{'='*78}")
        for th in HOURS:
            analyse(f"{th:02d}:00 Thai", per_hour[th], cost, keys)
        analyse("all three pooled", pooled_rows, cost, pooled_keys)

    print("\nHOW TO READ THIS")
    print("  rho     rank correlation of every variant between the two")
    print("          halves. Near zero means a search here is sorting")
    print("          noise, and no amount of further tuning finds a rule.")
    print("          19:30 scored +0.07.")
    print("  both    variants profitable in BOTH halves, against what")
    print("          chance alone gives. 19:30 scored 5 of 70 vs ~5.")
    print("  held    of the two out-of-sample tests -- best-of-early")
    print("          carried to late, and the reverse -- how many stayed")
    print("          positive. 19:30 scored 1 of 2, a coin flip.")
    print("\n  An hour is only worth more work if rho is clearly above")
    print("  zero, both exceeds chance, AND held is 2 of 2 -- at a")
    print("  slippage of 0.25 or more, not just at zero.")
    print("\n  07:00 and 08:00 Thai are adjacent and will often be the same")
    print("  move, so the pooled row is nearer two independent bets than")
    print("  three. Do not read it as a tripling of anything.")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
