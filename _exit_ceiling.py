#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_exit_ceiling.py -- before asking which exit is best, measure the best any
exit could possibly be.

The operator asks what happens if the exit is not a clock. Fair question:
on 10 Sep the trade was +24.9 points in profit and left at -1.14. But
sweeping exit rules over 62 trades would find a winner by chance and be
unable to confirm it, so the useful thing first is a CEILING.

For every trade the gate actually took, this measures:

    MFE   the furthest it ever went in favour, before any exit
    MAE   the furthest it ever went against
    30m   where the current rule leaves

MFE is the ceiling. No exit rule, however clever, can capture more than
the move offered -- and no exit knows in advance where that point was, so
the realistic take is some fraction of it. If the average MFE barely
clears the spread, no exit design rescues this entry and the question is
settled without a search. If it is large, the gap between MFE and what the
30-minute rule takes is what an exit could compete for.

Both halves are reported separately, because a ceiling that only exists in
one half is the same regime problem as everything else here.

Then, and only then, a SMALL pre-specified set of exits -- not a sweep:
take-profit at 0.5, 1, 1.5 and 2 times the gate, each with the 3xATR stop.
Four numbers chosen before looking, so there is nothing to overfit.

Usage:  python _exit_ceiling.py [symbol] [days] [gate_pts]
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
GATE = float(sys.argv[3]) if len(sys.argv) > 3 else 14.0
MIN_WAIT, MAXW, HOLD_MIN, SL_ATR, THAI = 1.0, 900, 30.0, 3.0, 7
SPLIT = date(2026, 6, 1)
TP_MULTS = [0.5, 1.0, 1.5, 2.0]      # of the gate -- fixed before looking


def atr_h1(sym, when, n=14):
    r = mt5.copy_rates_range(sym, mt5.TIMEFRAME_H1,
                             when - timedelta(hours=40), when)
    if r is None or len(r) < n + 1:
        return None
    trs = [max(float(r[i]["high"]) - float(r[i]["low"]),
               abs(float(r[i]["high"]) - float(r[i - 1]["close"])),
               abs(float(r[i]["low"]) - float(r[i - 1]["close"])))
           for i in range(1, len(r))]
    return sum(trs[-n:]) / n


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed"); return 2
    si = mt5.symbol_info(SYMBOL)
    if si is None:
        print(f"[ERROR] {SYMBOL} not found"); mt5.shutdown(); return 2
    mt5.symbol_select(SYMBOL, True)
    spread = si.spread * si.point

    rows = []
    today = (datetime.now(timezone.utc) + timedelta(hours=THAI)).date()
    for back in range(DAYS, -1, -1):
        d = today - timedelta(days=back)
        if d.weekday() >= 5:
            continue
        bell = datetime(d.year, d.month, d.day, 12, 30, tzinfo=timezone.utc)
        t = mt5.copy_ticks_range(SYMBOL, bell,
                                 bell + timedelta(seconds=MAXW + HOLD_MIN*60 + 120),
                                 mt5.COPY_TICKS_ALL)
        atr = atr_h1(SYMBOL, bell)
        if t is None or len(t) < 40 or not atr:
            continue
        t0 = int(bell.timestamp())
        sec = (t["time_msc"].astype(np.int64) - t0 * 1000) / 1000.0
        bid, ask = t["bid"].astype(float), t["ask"].astype(float)
        mid = np.where(ask > 0, (bid + ask) / 2.0, bid)
        ref = float(mid[0])
        w = np.where((sec >= MIN_WAIT) & (sec <= MAXW)
                     & (np.abs(mid - ref) >= GATE))[0]
        if len(w) == 0:
            continue
        i = int(w[0])
        side = 1 if mid[i] > ref else -1
        entry = float(ask[i]) if side > 0 else float(bid[i])
        t_in = float(sec[i])
        after = (sec >= t_in) & (sec <= t_in + HOLD_MIN * 60)
        if not after.any():
            continue
        path = (mid[after] - entry) * side
        rows.append(dict(d=d, side=side, atr=atr,
                         mfe=float(path.max()), mae=float(path.min()),
                         end=float(path[-1]), path=path))
    if len(rows) < 20:
        print(f"[ERROR] only {len(rows)} trades -- not enough to say anything")
        mt5.shutdown(); return 2

    early = [r for r in rows if r["d"] < SPLIT]
    late = [r for r in rows if r["d"] >= SPLIT]
    print("=" * 74)
    print(f" WHAT ANY EXIT COULD POSSIBLY GET -- {SYMBOL} gate {GATE:.0f} pts")
    print(f" {len(rows)} trades   EARLY {len(early)} | LATE {len(late)}"
          f"   spread {spread:.3f} pts")
    print("=" * 74)

    print(f"\n{'':>12}{'MFE avg':>10}{'MFE med':>10}{'MAE avg':>10}"
          f"{'30-min avg':>12}{'n':>5}")
    for name, part in (("EARLY", early), ("LATE", late), ("ALL", rows)):
        if not part:
            continue
        mfe = np.array([r["mfe"] for r in part])
        mae = np.array([r["mae"] for r in part])
        end = np.array([r["end"] for r in part])
        print(f"{name:>12}{mfe.mean():>+10.2f}{np.median(mfe):>+10.2f}"
              f"{mae.mean():>+10.2f}{end.mean():>+12.2f}{len(part):>5}")

    print(f"\n  MFE is the CEILING -- the most a perfect exit could have taken,")
    print(f"  knowing in advance where each move topped out. Nothing real can")
    print(f"  reach it. The 30-min column is what the current rule takes.")

    print(f"\n TAKE-PROFIT AT A FIXED MULTIPLE OF THE GATE"
          f"   (stop {SL_ATR}xATR, cost {spread:.2f})\n")
    print(f"    {'target':>16}{'EARLY /trade':>14}{'hit%':>7}{'n':>5}"
          f"{'LATE /trade':>14}{'hit%':>7}{'n':>5}   sign")
    for m in TP_MULTS:
        tp = GATE * m
        out = {}
        for name, part in (("E", early), ("L", late)):
            pnl, hits = [], 0
            for r in part:
                p = r["path"]
                sl = -SL_ATR * r["atr"]
                i_tp = np.argmax(p >= tp) if (p >= tp).any() else None
                i_sl = np.argmax(p <= sl) if (p <= sl).any() else None
                if i_tp is not None and (i_sl is None or i_tp < i_sl):
                    pnl.append(tp - spread); hits += 1
                elif i_sl is not None:
                    pnl.append(sl - spread)
                else:
                    pnl.append(r["end"] - spread)
            out[name] = (float(np.mean(pnl)) if pnl else 0.0,
                         100.0 * hits / len(part) if part else 0.0, len(pnl))
        (ea, eh, en), (la, lh, ln) = out["E"], out["L"]
        same = (ea > 0) == (la > 0)
        print(f"    {f'{m:g}x gate = {tp:.0f}p':>16}{ea:>+14.2f}{eh:>6.0f}%"
              f"{en:>5}{la:>+14.2f}{lh:>6.0f}%{ln:>5}   "
              + ("BOTH +" if same and ea > 0 else
                 "BOTH -" if same else "split"))
    print(f"\n    {'30 min, no TP':>16}"
          f"{np.mean([r['end'] for r in early]) - spread:>+14.2f}{'':>6} "
          f"{len(early):>5}"
          f"{np.mean([r['end'] for r in late]) - spread:>+14.2f}{'':>6} "
          f"{len(late):>5}   (the current rule)")
    print("\n  Four targets fixed before looking, so there is nothing here to")
    print("  overfit -- but with this many trades a two-way agreement is")
    print("  still weak evidence, not a decision.")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
