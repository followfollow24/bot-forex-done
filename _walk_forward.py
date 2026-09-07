#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_walk_forward.py -- does the 19:30 gate survive being chosen honestly?

Every number reported for this strategy so far was produced the same way:
sweep the gate over one stretch of history, keep the best, then quote that
best on the same stretch. That is where overfitting lives, so this script
never lets a parameter be chosen on the data it is scored against.

METHOD. Sessions are ordered by date and cut into K contiguous blocks. For
each block after the first, the gate is chosen on everything BEFORE it and
then applied, untouched, to that block alone. The pooled out-of-sample
result is the sum of those blocks. A parameter picked in fold 3 never sees
fold 3, and no fold ever sees a later one -- there is no way for the
future to leak backwards.

Three comparators run beside it, because an out-of-sample number means
nothing on its own:

  fixed 14      the gate currently configured, scored on the SAME
                out-of-sample windows. If picking the gate per fold cannot
                beat leaving it alone, the sweep is decoration.
  every gate    each candidate scored on those windows. If most of them
                make money the period was simply favourable and the gate
                is not what is working.
  coin flip     the same entries at the same instants with the direction
                drawn at random, 40 times. This separates "we enter on
                days that move" from "we enter on the right side". A
                single draw is not enough -- one draw once swung an edge
                column in this project by 0.26R -- so it is a
                distribution, and the strategy has to clear it.

The fast-session premise is tested the same way: only in out-of-sample
windows, comparing what fast entries returned against what slow ones did.

Ticks are fetched once per session and every fold is computed from that
table, so the cost is one pass over history however many folds are run.

Usage:  python _walk_forward.py [symbol] [days] [folds]
"""
import sys
from datetime import datetime, timedelta, timezone

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)"); sys.exit(1)

import numpy as np

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "XAUAUDm"
DAYS = int(sys.argv[2]) if len(sys.argv) > 2 else 365
FOLDS = int(sys.argv[3]) if len(sys.argv) > 3 else 6
GATES = [5, 8, 11, 14, 17, 20, 25, 30, 40]
CONFIGURED = 14.0
FAST_SEC = 10.0
HOLD_MIN, SL_ATR, MIN_WAIT, MAXW, THAI = 30.0, 3.0, 1.0, 900, 7
DRAWS = 40
RNG = np.random.default_rng(20260908)


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


def simulate(bars, srv_entry, entry, side, sl, spread):
    """Ride the position for HOLD_MIN from M5 bars. Returns (pts, worst)."""
    end = srv_entry + HOLD_MIN * 60
    exit_px, worst = None, 0.0
    for b in bars:
        bt = int(b["time"])
        if bt + 300 <= srv_entry:
            continue
        h, l, c = float(b["high"]), float(b["low"]), float(b["close"])
        worst = min(worst, ((l if side > 0 else h) - entry) * side)
        if ((l <= sl) if side > 0 else (h >= sl)):
            exit_px = sl; break
        if bt + 300 >= end:
            exit_px = c; break
    if exit_px is None:
        exit_px = float(bars[-1]["close"])
    return (exit_px - entry) * side - spread, worst


def load(sym):
    """One pass over history. Per session, the outcome at every gate."""
    out = []
    today = (datetime.now(timezone.utc) + timedelta(hours=THAI)).date()
    shut = thin = 0
    for back in range(DAYS, 0, -1):
        d = today - timedelta(days=back)
        if d.weekday() >= 5:
            continue
        s_utc = datetime(d.year, d.month, d.day, 12, 30, tzinfo=timezone.utc)
        t = mt5.copy_ticks_range(sym, s_utc,
                                 s_utc + timedelta(seconds=MAXW + 60),
                                 mt5.COPY_TICKS_ALL)
        if t is None or len(t) < 20:
            thin += 1; continue
        bars = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M5, s_utc,
                                    s_utc + timedelta(minutes=120))
        atr = atr_at(sym, s_utc)
        if bars is None or len(bars) < 6 or not atr:
            shut += 1; continue
        si = mt5.symbol_info(sym)
        spread = si.spread * si.point
        t0 = int(s_utc.timestamp())
        sec = (t["time_msc"].astype(np.int64) - t0 * 1000) / 1000.0
        bid, ask = t["bid"].astype(float), t["ask"].astype(float)
        mid = np.where(ask > 0, (bid + ask) / 2.0, bid)
        ref = float(mid[0])
        row = {"date": d, "at": {}}
        for g in GATES:
            w = np.where((sec >= MIN_WAIT) & (np.abs(mid - ref) >= g))[0]
            if len(w) == 0:
                continue
            i = int(w[0])
            s = 1 if mid[i] > ref else -1
            entry = float(ask[i]) if s > 0 else float(bid[i])
            pts, worst = simulate(bars, t0 + float(sec[i]), entry, s,
                                  entry - s * SL_ATR * atr, spread)
            row["at"][g] = {"t": float(sec[i]), "side": s, "pts": pts,
                            "worst": worst, "entry": entry, "atr": atr,
                            "spread": spread, "bars": bars,
                            "srv": t0 + float(sec[i])}
        if row["at"]:
            out.append(row)
    return out, shut, thin


def total(rows, gate):
    return sum(r["at"][gate]["pts"] for r in rows if gate in r["at"])


def stats(rows, gate):
    hits = [r["at"][gate]["pts"] for r in rows if gate in r["at"]]
    if not hits:
        return 0, 0, 0.0, 0.0
    wins = sum(1 for p in hits if p > 0)
    return len(hits), wins, sum(hits), 100.0 * wins / len(hits)


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed"); return 2
    if mt5.symbol_info(SYMBOL) is None:
        print(f"[ERROR] {SYMBOL} not found"); mt5.shutdown(); return 2
    mt5.symbol_select(SYMBOL, True)

    print(f"loading up to {DAYS} days of ticks for {SYMBOL} ...", flush=True)
    rows, shut, thin = load(SYMBOL)
    if len(rows) < FOLDS * 4:
        print(f"[ERROR] only {len(rows)} usable sessions -- not enough to "
              f"split {FOLDS} ways. The broker's tick history is the limit; "
              f"try fewer days or fewer folds.")
        mt5.shutdown(); return 2

    print("=" * 78)
    print(f" WALK-FORWARD -- {SYMBOL}   hold {HOLD_MIN:.0f} min   "
          f"SL {SL_ATR}xATR")
    print(f" {len(rows)} sessions with tick data, "
          f"{rows[0]['date']} to {rows[-1]['date']}")
    if thin or shut:
        print(f" ({thin} days had no tick history, {shut} no bars/ATR -- "
              f"the broker keeps only so much)")
    print("=" * 78)

    # ---- folds ----------------------------------------------------------
    edges = [round(i * len(rows) / FOLDS) for i in range(FOLDS + 1)]
    print(f"\nEACH FOLD: gate chosen on everything before it, then applied "
          f"unchanged to it.\n")
    print(f"{'fold':>5}{'test window':>26}{'n':>5}{'gate':>6}"
          f"{'train pts':>11}{'TEST pts':>10}{'fixed 14':>10}")
    print("-" * 73)
    oos, oos_fixed, picks = [], [], []
    for k in range(1, FOLDS):
        tr, te = rows[:edges[k]], rows[edges[k]:edges[k + 1]]
        best = max(GATES, key=lambda g: total(tr, g))
        n, _, tp, _ = stats(te, best)
        _, _, fp, _ = stats(te, CONFIGURED)
        picks.append(best)
        oos += [r["at"][best]["pts"] for r in te if best in r["at"]]
        oos_fixed += [r["at"][CONFIGURED]["pts"] for r in te
                      if CONFIGURED in r["at"]]
        span = (te[0]["date"].strftime("%d %b") + " - "
                + te[-1]["date"].strftime("%d %b %y"))
        print(f"{k:>5}{span:>26}{n:>5}{best:>6}"
              f"{total(tr, best):>11.1f}{tp:>10.1f}{fp:>10.1f}")
    print("-" * 73)
    w = sum(1 for p in oos if p > 0)
    wf = sum(1 for p in oos_fixed if p > 0)
    print(f"{'POOLED OUT-OF-SAMPLE':>36}{len(oos):>5}{'':>6}{'':>11}"
          f"{sum(oos):>10.1f}{sum(oos_fixed):>10.1f}")
    print(f"{'win rate':>36}{'':>5}{'':>6}{'':>11}"
          f"{100.0*w/len(oos) if oos else 0:>9.0f}%"
          f"{100.0*wf/len(oos_fixed) if oos_fixed else 0:>9.0f}%")
    print(f"\n  gates the training data picked: {picks}")

    # ---- was any gate special, or was the period just kind? -------------
    te_all = rows[edges[1]:]
    print(f"\nEVERY GATE ON THE SAME OUT-OF-SAMPLE STRETCH "
          f"({len(te_all)} sessions)\n")
    print(f"{'gate':>6}{'fires':>8}{'trades':>8}{'wins':>7}{'win %':>8}"
          f"{'total pts':>11}{'per trade':>11}")
    print("-" * 59)
    for g in GATES:
        n, wn, tp, wr = stats(te_all, g)
        if not n:
            continue
        print(f"{g:>5}p{100.0*n/len(te_all):>7.0f}%{n:>8}{wn:>7}{wr:>8.0f}%"
              f"{tp:>11.1f}{tp/n:>11.2f}")
    print("-" * 59)

    # ---- is the direction doing anything? -------------------------------
    print(f"\nCOIN-FLIP CONTROL -- same instants, direction drawn at random")
    real = sum(oos_fixed)
    sims = []
    for _ in range(DRAWS):
        s = 0.0
        for r in te_all:
            a = r["at"].get(CONFIGURED)
            if not a:
                continue
            d = 1 if RNG.random() < 0.5 else -1
            pts, _ = simulate(a["bars"], a["srv"], a["entry"], d,
                              a["entry"] - d * SL_ATR * a["atr"], a["spread"])
            s += pts
        sims.append(s)
    sims = np.array(sims)
    z = (real - sims.mean()) / sims.std() if sims.std() > 0 else 0.0
    beat = int((sims >= real).sum())
    print(f"  strategy at gate {CONFIGURED:g}: {real:+.1f} pts")
    print(f"  {DRAWS} random-direction draws: mean {sims.mean():+.1f}, "
          f"sd {sims.std():.1f}, best {sims.max():+.1f}")
    print(f"  z = {z:+.2f}   -- {beat} of {DRAWS} random draws did as well "
          f"or better")
    print("  A rule that only picks volatile days scores like these draws.")
    print("  Only a z well clear of them says the DIRECTION carries anything.")

    # ---- the fast-session premise, out of sample only -------------------
    print(f"\nFAST SESSIONS ({FAST_SEC:g}s) vs SLOW, OUT OF SAMPLE, "
          f"gate {CONFIGURED:g}\n")
    fast = [r["at"][CONFIGURED]["pts"] for r in te_all
            if CONFIGURED in r["at"] and r["at"][CONFIGURED]["t"] <= FAST_SEC]
    slow = [r["at"][CONFIGURED]["pts"] for r in te_all
            if CONFIGURED in r["at"] and r["at"][CONFIGURED]["t"] > FAST_SEC]
    wors = [r["at"][CONFIGURED]["worst"] for r in te_all
            if CONFIGURED in r["at"] and r["at"][CONFIGURED]["t"] <= FAST_SEC]
    for name, v in (("fast", fast), ("slow", slow)):
        if v:
            wn = sum(1 for p in v if p > 0)
            print(f"  {name:>4}: {len(v):>3} trades, {wn} wins "
                  f"({100.0*wn/len(v):.0f}%), {sum(v):+.1f} pts, "
                  f"{np.mean(v):+.2f} per trade")
        else:
            print(f"  {name:>4}: none")
    if fast and slow:
        d = np.mean(fast) - np.mean(slow)
        se = (np.var(fast, ddof=1) / len(fast)
              + np.var(slow, ddof=1) / len(slow)) ** 0.5 if len(fast) > 1 else 0
        print(f"\n  fast minus slow: {d:+.2f} pts per trade"
              + (f"   t = {d/se:+.2f}" if se > 0 else ""))
        print(f"  worst any fast session went against the entry: "
              f"{min(wors):.1f} pts"
              + (f" = {abs(min(wors))*3.61:.0f} USD at 0.05 lot" if wors
                 else ""))
        if len(fast) < 20:
            print(f"  NOTE: {len(fast)} fast sessions is too few to conclude "
                  f"from, whatever the sign says.")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
