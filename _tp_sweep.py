#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_tp_sweep.py -- is 15xATR really the right target, or just the inherited one?

_tp15_base_rate.py measured the target that is actually configured: at
15xATR against a 2.5xATR stop the take-profit sits 6R away and lands 2.8%
(long) / 5.4% (short) of the time, for an EV of -0.03R / +0.24R. Nothing
about those numbers says 6R is the best place to put it -- only that it is
where it currently is.

This sweeps the target itself, from 0.5R to 8R, on real H1 bars with the
live stop and hold window, and prices each one.

NOT a re-run of the 2026-08-07 exit-management sweep. That compared exit
SCHEMES -- trailing, move-to-break-even, partial take-profit -- against a
flat SL2.5/TP15 and found all 20 worse. It held the target FIXED at 15 and
varied the machinery around it. This holds the machinery fixed and varies
the target, which that sweep never asked.

Two guards against reading a fitted number as a real one:

  - the history is split in half; the target is chosen on the FIRST half
    and scored on the SECOND. An optimum that moves between halves is
    noise, and the flat curve near the top usually matters more than the
    peak.
  - spread is charged per trade, so a target close enough to be hit often
    still has to pay for being close.

SCOPE: this walks every bar, so it optimises the EXIT GEOMETRY against the
market, not against the bot's filtered entries. The entry signal changes
which bars are taken. Treat the answer as "where the target belongs given
how this market moves", and confirm on the strategy's own trades before
trusting it with money.

Usage (on the VPS):  python _tp_sweep.py [symbol] [sl_atr]
  e.g.               python _tp_sweep.py BTCUSDc 2.5
"""
import sys

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)")
    sys.exit(1)

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDc"
SL_ATR = float(sys.argv[2]) if len(sys.argv) > 2 else 2.5
HOLD = 64
BARS = 8000
SPREAD = {"XAUUSDc": 0.24, "BTCUSDc": 10.0}
# target as a multiple of the STOP distance (R), which is the unit that
# matters; 15xATR / 2.5xATR = the 6.0R currently configured
TP_GRID = [0.5, 0.75, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0]


def atr14(rates, i):
    trs = []
    for j in range(i - 13, i + 1):
        h, l = float(rates[j]["high"]), float(rates[j]["low"])
        pc = float(rates[j - 1]["close"])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs)


def outcomes(rates, lo, hi):
    """Per entry, the best-R reachable info needed to price ANY target:
    (max favourable R before the stop, stopped?, R at timeout)."""
    out = []
    for i in range(max(20, lo), min(hi, len(rates) - HOLD)):
        a = atr14(rates, i)
        if not a or a <= 0:
            continue
        entry = float(rates[i]["close"])
        R = SL_ATR * a
        for long_ in (True, False):
            best, stopped, tor = 0.0, False, None
            for j in range(i + 1, i + 1 + HOLD):
                b = rates[j]
                h2, l2 = float(b["high"]), float(b["low"])
                adverse = l2 if long_ else h2
                if (((entry - adverse) if long_ else (adverse - entry)) / R) >= 1.0:
                    stopped = True
                    break
                fav = h2 if long_ else l2
                f = ((fav - entry) if long_ else (entry - fav)) / R
                if f > best:
                    best = f
            if not stopped:
                px = float(rates[min(i + HOLD, len(rates) - 1)]["close"])
                tor = ((px - entry) if long_ else (entry - px)) / R
            out.append((best, stopped, tor, R, long_))
    return out


def price(rows, tp_r, sp):
    """EV in R for one target, given the pre-computed excursions."""
    if not rows:
        return None
    tot, hits = 0.0, 0
    for best, stopped, tor, R, _ in rows:
        cost = sp / R if R > 0 else 0.0
        if best >= tp_r:
            tot += tp_r - cost
            hits += 1
        elif stopped:
            tot += -1.0 - cost
        else:
            tot += (tor or 0.0) - cost
    return tot / len(rows), hits / len(rows)


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed")
        sys.exit(1)
    rates = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_H1, 0, BARS)
    if rates is None or len(rates) < 1000:
        print(f"[ERROR] not enough H1 bars for {SYMBOL}")
        mt5.shutdown()
        return
    rates = list(rates)
    mt5.shutdown()
    sp = SPREAD.get(SYMBOL, 0.0)

    mid = len(rates) // 2
    train = outcomes(rates, 20, mid)
    test = outcomes(rates, mid, len(rates))

    print("=" * 80)
    print(f" TP SWEEP -- {SYMBOL} H1, stop {SL_ATR:g}xATR, hold {HOLD} bars")
    print(f" target chosen on the FIRST half, scored on the SECOND")
    print(f" train {len(train)} entries / test {len(test)} entries")
    print("=" * 80)
    print(f"{'target':>8}{'= xATR':>9}{'hit% tr':>10}{'EV train':>11}"
          f"{'hit% te':>10}{'EV test':>10}")
    print("-" * 80)

    best_train, best_tp = None, None
    for tp in TP_GRID:
        a = price(train, tp, sp)
        b = price(test, tp, sp)
        if not a or not b:
            continue
        mark = "  <- configured" if abs(tp - 6.0) < 1e-9 else ""
        if best_train is None or a[0] > best_train:
            best_train, best_tp = a[0], tp
        print(f"{tp:>7.2f}R{tp*SL_ATR:>9.1f}{100*a[1]:>9.1f}%{a[0]:>+11.3f}"
              f"{100*b[1]:>9.1f}%{b[0]:>+10.3f}{mark}")

    print("-" * 80)
    cur = price(test, 6.0, sp)
    chosen = price(test, best_tp, sp)
    print(f"  best on TRAIN            : {best_tp:.2f}R "
          f"({best_tp*SL_ATR:.1f}xATR)   EV train {best_train:+.3f}R")
    print(f"  that target, on TEST     : EV {chosen[0]:+.3f}R  "
          f"hit {100*chosen[1]:.1f}%")
    print(f"  currently configured 6.0R: EV {cur[0]:+.3f}R  "
          f"hit {100*cur[1]:.1f}%   (= 15xATR)")
    print()
    d = chosen[0] - cur[0]
    if best_tp == 6.0:
        print("  -> the configured target already wins the sweep. 15xATR is not")
        print("     an arbitrary inheritance; leave it alone.")
    elif d > 0.05:
        print(f"  -> {best_tp:.2f}R beats the configured 6.0R by {d:+.3f}R per")
        print("     trade OUT OF SAMPLE. Worth changing -- but confirm on the")
        print("     strategy's own entries first, since this sweep used every bar.")
    else:
        print(f"  -> {best_tp:.2f}R wins on train but only {d:+.3f}R on test.")
        print("     That is inside the noise: the curve is flat near the top and")
        print("     the 'optimum' is a fitted artefact. Do NOT move the target on")
        print("     this evidence.")


if __name__ == "__main__":
    main()
