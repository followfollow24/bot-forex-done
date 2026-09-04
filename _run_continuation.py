#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_run_continuation.py -- the operator's event, on their terms this time.

WHY THIS EXISTS
----------------------------------------------------------------------
_spike_continuation.py measured a SINGLE M1 bar moving >= 0.5 xATR and
found 161 events in six months -- under one a day. The operator's reply
was that it happens almost every day, and they are right that the two
things are not the same: they are watching M15, where a run unfolds over
many minutes. A one-minute body is a much rarer object than what they
see. That was my definition being too narrow, not their observation
being wrong.

So the event is redefined the way they actually describe it: price
covering ground in ONE DIRECTION over a window of minutes.

    net move over the last N minutes  >=  K x ATR(H1)

and, because "it is really running" is the operative phrase, a
straightness filter:

    efficiency = |net move| / |path travelled|  >=  E

E separates a clean slide from a move that wandered to the same place.
Both are >= K; only one looks like the thing they circled. Their own
window, Thai 19:00-21:00, has the largest moves of the day but perfectly
ordinary efficiency, so this filter is what decides whether the
distinction is worth money or is just something the eye likes.

The frequency question is answered directly and first: events per
trading day at every setting, plus a day-by-day log of the last three
weeks, so the claim "almost every day" is checked against a number they
can compare with their own chart rather than against my assertion.

Two exits are carried side by side on identical entries, because the
right exit for a runner is not obviously the one from their filled
brackets:
    tight = their measured TP 0.51 / SL 0.42 xATR
    wide  = TP 1.50 / SL 1.00 xATR, room to actually ride it

Everything is split train/TEST. This is a sweep -- 4 windows x 4 sizes
x 2 filters -- so some cell will look good in one half by luck alone,
and only a cell that holds in BOTH halves means anything.

Usage (VPS):  python _run_continuation.py [symbol] [months]
"""
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)")
    sys.exit(1)

import numpy as np

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "XAUAUDm"
MONTHS = float(sys.argv[2]) if len(sys.argv) > 2 else 12.0

WINDOWS = [5, 15, 30, 60]
SIZES = [1.0, 1.5, 2.0, 3.0]
EFFS = [0.0, 0.60]
EXITS = {"tight": (0.51, 0.42), "wide": (1.50, 1.00)}
MAX_HOLD = 120
N_CTRL = 20
THAI = 7


def load_m1(symbol, months):
    mt5.symbol_select(symbol, True)
    chunks, cursor = [], datetime.now()
    stop = datetime.now() - timedelta(days=int(months * 30.5))
    while cursor > stop:
        part = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M1,
                                    cursor - timedelta(days=30), cursor)
        if part is not None and len(part):
            chunks.append(part)
        cursor -= timedelta(days=30)
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


def net_and_eff(c, n):
    """Net move and straightness over a trailing n-bar window, per bar."""
    step = np.abs(np.diff(c, prepend=c[0]))
    cum = np.cumsum(step)
    net = np.full(len(c), np.nan)
    eff = np.full(len(c), np.nan)
    net[n:] = c[n:] - c[:-n]
    path = cum[n:] - cum[:-n]
    with np.errstate(divide="ignore", invalid="ignore"):
        eff[n:] = np.where(path > 0, np.abs(net[n:]) / path, 0.0)
    return net, eff


def events(r, atr, net, eff, k, e, lo, hi):
    """Non-overlapping event bars: once one fires, skip its holding period
    so the same run is not counted as many independent trades."""
    out, i = [], lo
    while i < hi:
        a = atr[i]
        if (np.isfinite(a) and a > 0 and np.isfinite(net[i])
                and abs(net[i]) >= k * a and eff[i] >= e):
            out.append(i)
            i += MAX_HOLD
        else:
            i += 1
    return out


def bracket(r, atr, i, s, spread, tp_m, sl_m):
    a = atr[i]
    entry = r["open"][i + 1]
    tp_d, sl_d = tp_m * a, sl_m * a
    tp, sl = entry + s * tp_d, entry - s * sl_d
    for j in range(i + 1, min(i + 1 + MAX_HOLD, len(r))):
        hp, lp = r["high"][j], r["low"][j]
        if (lp <= sl) if s > 0 else (hp >= sl):
            return -1.0 - spread / sl_d
        if (hp >= tp) if s > 0 else (lp <= tp):
            return tp_d / sl_d - spread / sl_d
    j = min(i + MAX_HOLD, len(r) - 1)
    return ((r["close"][j] - entry) * s) / sl_d - spread / sl_d


def ev(x):
    return ((len(x), 100.0 * sum(1 for v in x if v > 0) / len(x), float(np.mean(x)))
            if x else (0, float("nan"), float("nan")))


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed"); return 2
    info = mt5.symbol_info(SYMBOL)
    if info is None:
        print(f"[ERROR] {SYMBOL} not found"); return 2
    r = load_m1(SYMBOL, MONTHS)
    if r is None or len(r) < 50000:
        print(f"[ERROR] not enough M1 data ({mt5.last_error()})"); return 2

    atr, c = h1_atr_on(r), r["close"]
    spread = info.spread * info.point
    tick = mt5.symbol_info_tick(SYMBOL)
    off = int(round((tick.time - datetime.now(timezone.utc).timestamp()) / 3600.0)) if tick else 0
    days = len({int(t) // 86400 for t in r["time"]})
    mid = len(r) // 2

    print("=" * 92)
    print(f" RUN CONTINUATION -- {SYMBOL}   {len(r):,} M1 bars over {days} trading days")
    print(f" {datetime.fromtimestamp(r[0]['time']):%Y-%m-%d} -> "
          f"{datetime.fromtimestamp(r[-1]['time']):%Y-%m-%d}   "
          f"spread {spread:.2f}   server UTC{off:+d}, Thai = server+{THAI}")
    print("=" * 92)

    cache = {n: net_and_eff(c, n) for n in WINDOWS}

    # ---- the frequency question, answered first --------------------------
    print("\n1. HOW OFTEN DOES THE EVENT ACTUALLY HAPPEN?   (events per trading day)")
    print("   'straight' = the move also had efficiency >= 0.60, i.e. it ran")
    print("   rather than wandered.\n")
    print(f"   {'window':>8}" + "".join(f"{f'>={k}xATR':>13}" for k in SIZES))
    for n in WINDOWS:
        net, eff = cache[n]
        row_a = f"   {n:>5}min "
        for k in SIZES:
            a = len(events(r, atr, net, eff, k, 0.0, n + 1, len(r) - MAX_HOLD - 2))
            b = len(events(r, atr, net, eff, k, 0.60, n + 1, len(r) - MAX_HOLD - 2))
            row_a += f"{a/days:>6.2f}/{b/days:<6.2f}"
        print(row_a)
    print("            (left = any move, right = straight moves only)")

    # ---- day by day, so it can be checked against their own chart --------
    n0, k0 = 15, 1.5
    net0, eff0 = cache[n0]
    ev0 = events(r, atr, net0, eff0, k0, 0.60, n0 + 1, len(r) - MAX_HOLD - 2)
    cut = r["time"][-1] - 21 * 86400
    per_day = Counter()
    for i in ev0:
        if r["time"][i] >= cut:
            t = datetime.fromtimestamp(int(r["time"][i]) - off * 3600 + THAI * 3600,
                                       timezone.utc)
            per_day[t.strftime("%Y-%m-%d %a")] += 1
    print(f"\n2. LAST 3 WEEKS, DAY BY DAY  ({n0}min move >= {k0} xATR, straight)")
    print("   Compare this against what you saw on the chart.\n")
    for d in sorted(per_day):
        print(f"   {d}   {'#' * per_day[d]} {per_day[d]}")
    if not per_day:
        print("   (no qualifying events in the last 3 weeks)")

    # ---- and does trading it pay -----------------------------------------
    print("\n3. DOES ENTERING WITH IT PAY?   entry at the next minute's open,")
    print(f"   direction = the way it is running, vs {N_CTRL} random-direction")
    print("   draws on the SAME event bars.\n")
    print(f"   {'win':>5}{'size':>6}{'eff':>6}{'exit':>7}{'half':>7}"
          f"{'n':>6}{'WR':>7}{'EV(R)':>9}{'ctl':>9}{'z':>7}")
    print("   " + "-" * 76)
    for n in WINDOWS:
        net, eff = cache[n]
        for k in SIZES:
            for e in EFFS:
                for lab, (tp_m, sl_m) in EXITS.items():
                    line = []
                    for tag, lo, hi in (("train", n + 1, mid), ("TEST", mid, len(r) - MAX_HOLD - 2)):
                        idx = events(r, atr, net, eff, k, e, lo, hi)
                        if len(idx) < 30:
                            line.append((tag, len(idx), None, None, None))
                            continue
                        n1, w1, e1 = ev([bracket(r, atr, i, int(np.sign(net[i])),
                                                 spread, tp_m, sl_m) for i in idx])
                        cs = []
                        for s in range(1, N_CTRL + 1):
                            rng = np.random.default_rng(s)
                            cs.append(ev([bracket(r, atr, i, int(rng.choice((1, -1))),
                                                  spread, tp_m, sl_m) for i in idx])[2])
                        cm, csd = float(np.mean(cs)), float(np.std(cs, ddof=1))
                        line.append((tag, n1, w1, e1, (e1 - cm) / csd if csd > 0 else 0.0, cm))
                    if all(x[2] is None for x in line):
                        continue
                    for x in line:
                        if x[2] is None:
                            print(f"   {n:>5}{k:>6.1f}{e:>6.2f}{lab:>7}{x[0]:>7}"
                                  f"{x[1]:>6}   -- too few --")
                        else:
                            tag, n1, w1, e1, z, cm = x
                            print(f"   {n:>5}{k:>6.1f}{e:>6.2f}{lab:>7}{tag:>7}"
                                  f"{n1:>6}{w1:>6.1f}%{e1:>+9.3f}{cm:>+9.3f}{z:>+7.2f}"
                                  f"{'  <<<' if abs(z) >= 2 else ''}")
    print("\n   A cell only counts if it clears 2 sigma in BOTH halves with the")
    print("   same sign. This sweep has 64 cells; one or two will clear it in")
    print("   one half by luck alone.")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
