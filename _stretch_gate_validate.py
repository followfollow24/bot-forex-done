#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_stretch_gate_validate.py -- does the entry gate survive outside the 29
trades it was fitted on?

entry_conditions_allow() blocks three market states that lost every time
in the live sample: stretched more than 2 ATR from EMA20, travelled more
than 2 ATR in ten bars, or a run of fewer than 2 / at least 4 same-way
closes. Applied to those 29 trades it keeps all four winners and drops 19
of 25 losers -- 14% becomes 44%.

That number is worthless as evidence. The thresholds were read off the
same trades they are scored on and only nine survive the filter. A rule
fitted that tightly nearly always evaporates, which is precisely how this
bot went live unvalidated in the first place.

So: score the identical rule on thousands of historical bars it has never
seen. At every bar, compute the same stretch figures, ask the gate, and
race +1.25R against -1R exactly as the live bot would. If ALLOWED bars win
that race materially more often than BLOCKED ones, the rule describes
something real about the market. If the two are the same, the split was
noise and the gate should stay off.

Split by symbol, and reported separately for long and short, because a
one-sided sample can manufacture a gap out of pure drift.

Pessimistic: a bar spanning both levels counts as the stop.

Usage (on the VPS):  python _stretch_gate_validate.py [symbol] [sl_atr]
"""
import os
import sys

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)")
    sys.exit(1)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chart_ai_trader as cat

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDc"
SL_ATR = float(sys.argv[2]) if len(sys.argv) > 2 else 1.8
TP_R = 1.25
BARS = 6000
HOLD = 96
CTX = 60          # bars of context needed for the stretch figures


def atr14(rates, i):
    trs = []
    for j in range(i - 13, i + 1):
        h, l = float(rates[j]["high"]), float(rates[j]["low"])
        pc = float(rates[j - 1]["close"])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs)


def to_ohlcv(rates):
    return [[int(r["time"]) * 1000, float(r["open"]), float(r["high"]),
             float(r["low"]), float(r["close"]), float(r["tick_volume"])]
            for r in rates]


def race(rates, i, entry, R, long_):
    tp = entry + TP_R * R if long_ else entry - TP_R * R
    sl = entry - R if long_ else entry + R
    for j in range(i + 1, min(i + 1 + HOLD, len(rates))):
        hi, lo = float(rates[j]["high"]), float(rates[j]["low"])
        if long_:
            if lo <= sl:
                return False
            if hi >= tp:
                return True
        else:
            if hi >= sl:
                return False
            if lo <= tp:
                return True
    return False


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed")
        sys.exit(1)
    rates = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M15, 0, BARS)
    if rates is None or len(rates) < 500:
        print(f"[ERROR] not enough bars for {SYMBOL}")
        mt5.shutdown()
        return
    rates = list(rates)

    buckets = {("allow", True): [], ("allow", False): [],
               ("block", True): [], ("block", False): []}
    reasons = {}
    for i in range(CTX, len(rates) - HOLD):
        a = atr14(rates, i)
        if not a or a <= 0:
            continue
        ex = cat.build_exhaustion_context(to_ohlcv(rates[i - CTX + 1:i + 1]), a)
        ok, why = cat.entry_conditions_allow(ex)
        if not ok:
            reasons[why.split("(")[0].strip()] = \
                reasons.get(why.split("(")[0].strip(), 0) + 1
        key = "allow" if ok else "block"
        entry, R = float(rates[i]["close"]), SL_ATR * a
        buckets[(key, True)].append(race(rates, i, entry, R, True))
        buckets[(key, False)].append(race(rates, i, entry, R, False))

    print("=" * 84)
    print(f" STRETCH GATE VALIDATION -- {SYMBOL} M15, stop {SL_ATR}xATR, "
          f"target {TP_R}R")
    print(f" the SAME rule fitted on 29 live trades, scored on "
          f"{len(buckets[('allow', True)]) + len(buckets[('block', True)])} "
          f"unseen bars")
    print("=" * 84)
    print(f"{'gate':<10}{'side':<8}{'n':>8}{'win%':>9}{'EV/trade':>11}")
    print("-" * 84)
    res = {}
    for key in ("allow", "block"):
        for long_, lbl in ((True, "long"), (False, "short")):
            v = buckets[(key, long_)]
            if not v:
                continue
            wr = sum(1 for x in v if x) / len(v)
            ev = wr * TP_R - (1 - wr) * 1.0
            res[(key, lbl)] = wr
            print(f"{key:<10}{lbl:<8}{len(v):>8}{100*wr:>8.1f}%{ev:>+11.3f}")
    print("-" * 84)

    if reasons:
        print("  blocked by:")
        for k, v in sorted(reasons.items(), key=lambda kv: -kv[1]):
            print(f"    {v:>7}  {k}")

    print()
    gaps = []
    for lbl in ("long", "short"):
        if ("allow", lbl) in res and ("block", lbl) in res:
            g = 100 * (res[("allow", lbl)] - res[("block", lbl)])
            gaps.append(g)
            print(f"  {lbl:<6} allow minus block: {g:+.1f} points")
    print()
    if not gaps:
        print("  -> not enough data on one side to judge")
    elif min(gaps) >= 2.0:
        print("  -> HOLDS. Allowed bars beat blocked ones on BOTH sides, on")
        print("     data the thresholds never saw. Worth wiring live behind")
        print("     STRETCH_GATE, then watching the first trades.")
    elif max(gaps) <= 0.0:
        print("  -> FAILS. Blocked bars do at least as well. The 44% was the")
        print("     thresholds memorising 29 trades. Leave STRETCH_GATE off.")
    else:
        print("  -> MIXED: one side gains, the other does not. Not enough to")
        print("     justify a live gate; the effect is not symmetric and may")
        print("     just be this sample's drift.")
    mt5.shutdown()


if __name__ == "__main__":
    main()
