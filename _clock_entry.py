#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_clock_entry.py -- the operator's rule, stated as simply as it can be:

  "at 19:30 Thai time, the moment it arrives: if the chart is going up,
   buy; if it is going down, sell."

One trade per day at a fixed clock time. That makes this the best-powered
thing tested in this project so far -- roughly 250 trades a year instead
of the 43-to-200 the threshold sweeps kept ending up with.

TWO THINGS THE RULE DOES NOT SPECIFY, so both are swept rather than
guessed (guessing one of these is exactly how the first copy-trade test
went wrong):

  - "the chart is going up" over WHAT LOOKBACK? 15 / 30 / 60 / 120 min.
  - what stop? The run-continuation study found stop width mattered more
    than the entry rule -- SL 1.0 beat SL 0.42 in 10 of 14 matched pairs
    -- so four stop/target pairs run on identical entries.

NO LOOKAHEAD: direction is read from bars that closed BEFORE the target
time, and the fill is that bar's open. The decision uses only what was
on screen at 19:29.

THE BROKER CLOCK IS A REAL HAZARD HERE. Thailand has no daylight saving,
so 19:30 Thai is always 12:30 UTC, but if this broker shifted its server
clock historically then a fixed server hour drifts by an hour against it
and would quietly smear the very thing being measured. The neighbouring
times are therefore scanned too -- an hour-shifted clock shows up as the
result moving to the neighbour instead of vanishing.

Direction balance (how many buys vs sells) is printed because a rule
like this can look profitable purely by being short through a falling
year, which is a bet on the sample, not an edge.

Usage (VPS):  python _clock_entry.py [symbol] [years]
"""
import sys
from datetime import datetime, timedelta, timezone

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)")
    sys.exit(1)

import numpy as np

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "XAUAUDm"
YEARS = float(sys.argv[2]) if len(sys.argv) > 2 else 4.0

THAI = 7
TARGET_THAI = (19, 30)
LOOKBACKS = [15, 30, 60, 120]              # minutes used to judge direction
EXITS = [(0.42, 0.51), (1.00, 1.50), (1.50, 2.00), (2.50, 3.00)]   # (SL, TP) xATR
MAX_HOLD = 48                              # M5 bars = 4 hours
N_CTRL = 20
NEIGHBOURS = [(18, 30), (19, 0), (19, 15), (19, 30), (19, 45), (20, 0), (20, 30)]


def load_m5(symbol, years):
    mt5.symbol_select(symbol, True)
    chunks, cursor = [], datetime.now()
    stop = datetime.now() - timedelta(days=int(years * 365))
    while cursor > stop:
        part = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5,
                                    cursor - timedelta(days=45), cursor)
        if part is not None and len(part):
            chunks.append(part)
        cursor -= timedelta(days=45)
    if not chunks:
        return None
    r = np.concatenate(list(reversed(chunks)))
    _, keep = np.unique(r["time"], return_index=True)
    return r[np.sort(keep)]


def h1_atr_on(r, n=14):
    h1, cur = [], None
    for b in r:
        h = (b["time"] // 3600) * 3600
        if cur is None or cur["t"] != h:
            if cur is not None:
                h1.append(cur)
            cur = {"t": h, "h": b["high"], "l": b["low"], "c": b["close"]}
        else:
            cur["h"] = max(cur["h"], b["high"]); cur["l"] = min(cur["l"], b["low"])
            cur["c"] = b["close"]
    if cur:
        h1.append(cur)
    avail, trs = {}, []
    for i in range(1, len(h1)):
        trs.append(max(h1[i]["h"] - h1[i]["l"], abs(h1[i]["h"] - h1[i - 1]["c"]),
                       abs(h1[i]["l"] - h1[i - 1]["c"])))
        if len(trs) >= n:
            avail[h1[i]["t"] + 3600] = sum(trs[-n:]) / n
    keys = sorted(avail)
    out, ki, cv = np.full(len(r), np.nan), 0, np.nan
    for i, b in enumerate(r):
        while ki < len(keys) and keys[ki] <= b["time"]:
            cv = avail[keys[ki]]; ki += 1
        out[i] = cv
    return out


def slots(r, atr, srv_h, srv_m, back_bars):
    """Bars sitting exactly on the target server clock time, with enough
    history behind them to read a direction."""
    tod = r["time"] % 86400
    want = srv_h * 3600 + srv_m * 60
    return [i for i in np.where(tod == want)[0]
            if i > back_bars and i < len(r) - MAX_HOLD - 1
            and np.isfinite(atr[i]) and atr[i] > 0]


def simulate(r, atr, idx, back_bars, sl_m, tp_m, spread, seed=None):
    """Enter at the target bar's OPEN. Direction from bars already closed."""
    rng = np.random.default_rng(seed)
    c, out, longs = r["close"], [], 0
    for i in idx:
        if seed is None:
            d = c[i - 1] - c[i - 1 - back_bars]
            if d == 0:
                continue
            s = 1 if d > 0 else -1
        else:
            s = int(rng.choice((1, -1)))
        longs += 1 if s > 0 else 0
        a = atr[i]
        entry = r["open"][i]
        tp_d, sl_d = tp_m * a, sl_m * a
        tp, sl = entry + s * tp_d, entry - s * sl_d
        res = None
        for j in range(i, min(i + MAX_HOLD, len(r))):
            hp, lp = r["high"][j], r["low"][j]
            if (lp <= sl) if s > 0 else (hp >= sl):        # stop wins ties
                res = -1.0; break
            if (hp >= tp) if s > 0 else (lp <= tp):
                res = tp_d / sl_d; break
        if res is None:
            j = min(i + MAX_HOLD, len(r) - 1)
            res = ((c[j] - entry) * s) / sl_d
        out.append(res - spread / sl_d)
    return out, longs


def stats(x):
    if not x:
        return 0, float("nan"), float("nan"), float("nan")
    eq = np.cumsum(x)
    dd = float(np.max(np.maximum.accumulate(eq) - eq)) if len(eq) else 0.0
    return (len(x), 100.0 * sum(1 for v in x if v > 0) / len(x),
            float(np.mean(x)), dd)


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed"); return 2
    info = mt5.symbol_info(SYMBOL)
    if info is None:
        print(f"[ERROR] {SYMBOL} not found"); return 2
    r = load_m5(SYMBOL, YEARS)
    if r is None or len(r) < 20000:
        print(f"[ERROR] not enough M5 data ({mt5.last_error()})"); return 2

    atr = h1_atr_on(r)
    spread = info.spread * info.point
    tick = mt5.symbol_info_tick(SYMBOL)
    off = int(round((tick.time - datetime.now(timezone.utc).timestamp()) / 3600.0)) if tick else 0
    mid = len(r) // 2

    def to_server(th, tm):
        return ((th - THAI + off) % 24, tm)

    sh, sm = to_server(*TARGET_THAI)
    print("=" * 90)
    print(f" FIXED-CLOCK ENTRY -- {SYMBOL}   {len(r):,} M5 bars   spread {spread:.2f}")
    print(f" {datetime.fromtimestamp(r[0]['time']):%Y-%m-%d} -> "
          f"{datetime.fromtimestamp(r[-1]['time']):%Y-%m-%d}")
    print(f" target {TARGET_THAI[0]}:{TARGET_THAI[1]:02d} Thai = "
          f"{sh}:{sm:02d} on this server (server = UTC{off:+d})")
    print(f" hold at most {MAX_HOLD*5//60}h; stop wins ties inside a bar")
    print("=" * 90)

    print(f"\n1. AT {TARGET_THAI[0]}:{TARGET_THAI[1]:02d} THAI, EVERY DAY -- "
          f"buy if it was rising, sell if falling\n")
    print(f"   {'look':>6}{'SL/TP':>12}{'half':>7}{'n':>6}{'%long':>7}"
          f"{'WR':>7}{'EV(R)':>9}{'total R':>10}{'maxDD':>8}{'ctl':>8}{'z':>7}")
    print("   " + "-" * 85)
    for lb in LOOKBACKS:
        bb = lb // 5
        for sl_m, tp_m in EXITS:
            for tag, lo, hi in (("train", 0, mid), ("TEST", mid, len(r))):
                idx = [i for i in slots(r, atr, sh, sm, bb) if lo <= i < hi]
                res, longs = simulate(r, atr, idx, bb, sl_m, tp_m, spread)
                n, wr, e, dd = stats(res)
                if n < 40:
                    print(f"   {lb:>6}{f'{sl_m}/{tp_m}':>12}{tag:>7}{n:>6}"
                          f"   -- too few --")
                    continue
                cs = [stats(simulate(r, atr, idx, bb, sl_m, tp_m, spread, s)[0])[2]
                      for s in range(1, N_CTRL + 1)]
                cm, csd = float(np.mean(cs)), float(np.std(cs, ddof=1))
                z = (e - cm) / csd if csd > 0 else 0.0
                print(f"   {lb:>6}{f'{sl_m}/{tp_m}':>12}{tag:>7}{n:>6}"
                      f"{100.0*longs/n:>6.0f}%{wr:>6.1f}%{e:>+9.3f}"
                      f"{sum(res):>+10.1f}{dd:>8.1f}{cm:>+8.3f}{z:>+7.2f}"
                      f"{'  <<<' if abs(z) >= 2 else ''}")
        print("   " + "-" * 85)

    # ---- is 19:30 special, or is the whole evening the same? -------------
    lb, bb = 60, 12
    sl_m, tp_m = 1.00, 1.50
    print(f"\n2. IS {TARGET_THAI[0]}:{TARGET_THAI[1]:02d} SPECIAL?  same rule at neighbouring")
    print(f"   times (lookback {lb}min, SL {sl_m}/TP {tp_m}), FULL sample.")
    print("   A broker clock that shifted by an hour in the past would show up")
    print("   here as the result sitting on a neighbour rather than disappearing.\n")
    print(f"   {'Thai':>8}{'server':>9}{'n':>6}{'WR':>7}{'EV(R)':>9}"
          f"{'total R':>10}{'ctl':>8}{'z':>7}")
    for th, tm in NEIGHBOURS:
        h2, m2 = to_server(th, tm)
        idx = slots(r, atr, h2, m2, bb)
        res, longs = simulate(r, atr, idx, bb, sl_m, tp_m, spread)
        n, wr, e, dd = stats(res)
        if n < 40:
            print(f"   {f'{th}:{tm:02d}':>8}{f'{h2}:{m2:02d}':>9}{n:>6}  -- too few --")
            continue
        cs = [stats(simulate(r, atr, idx, bb, sl_m, tp_m, spread, s)[0])[2]
              for s in range(1, N_CTRL + 1)]
        cm, csd = float(np.mean(cs)), float(np.std(cs, ddof=1))
        z = (e - cm) / csd if csd > 0 else 0.0
        star = "  <-- the rule as asked" if (th, tm) == TARGET_THAI else ""
        print(f"   {f'{th}:{tm:02d}':>8}{f'{h2}:{m2:02d}':>9}{n:>6}{wr:>6.1f}%"
              f"{e:>+9.3f}{sum(res):>+10.1f}{cm:>+8.3f}{z:>+7.2f}{star}")

    print("\n   32 cells above and 7 times here. Something will clear 2 sigma in")
    print("   one half by luck; only agreement across BOTH halves counts, and")
    print("   'total R' is what an account would actually have felt.")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
