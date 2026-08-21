#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_strong_trend_follow.py -- is FOLLOWING profitable specifically in the
                           strongest trends, not just on average?

_hybrid_trend_test.py swept a switch cutoff from ER 0.10 to 0.75 and found
the hybrid always worse than plain fading. But that sweep answered a
blunter question than the one asked. Above ER ~0.55 fewer than 1% of bars
qualify, so the "hybrid" there is really just always-fade with a rounding
error, and its EV converges to the fade baseline for arithmetic reasons
rather than because following was tested and failed. The genuinely strong
trends were never measured on their own terms.

This measures them directly: FOLLOW performance inside narrow high-ER
bands, each reported with its sample count so a band too small to trust
is visible as such rather than hidden inside an average.

Two things must both be true before the idea is worth building:
  1. following must be POSITIVE in the strong band, not merely less
     negative than fading -- a smaller loss is still a loss
  2. the band must hold enough bars to be more than noise, and the result
     must survive a train/test split

Reported alongside is how often the band actually occurs, because a rule
that fires on 0.5% of bars would change almost nothing about the live bot
even if its EV were good.

Same geometry as the live bot throughout: fade or follow the last N bars,
stop 1.8xATR, target 1.25R, 96-bar hold, spread charged per trade,
pessimistic resolution when a bar spans both levels.

Usage (on the VPS):  python _strong_trend_follow.py [symbol] [fade_bars]
"""
import sys

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)")
    sys.exit(1)

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDc"
FADE_N = int(sys.argv[2]) if len(sys.argv) > 2 else 20
ER_WIN = 40
SL_ATR = 1.8
TP_R = 1.25
HOLD = 96
BARS = 8000
SPREAD = {"XAUUSDc": 0.24, "BTCUSDc": 10.0}


def atr14(r, i):
    t = []
    for j in range(i - 13, i + 1):
        h, l = float(r[j]["high"]), float(r[j]["low"])
        pc = float(r[j - 1]["close"])
        t.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(t) / len(t)


def eff_ratio(r, i, win=ER_WIN):
    if i < win:
        return None
    net = abs(float(r[i]["close"]) - float(r[i - win]["close"]))
    path = sum(abs(float(r[j]["close"]) - float(r[j - 1]["close"]))
               for j in range(i - win + 1, i + 1))
    return (net / path) if path > 0 else 0.0


def race(r, i, entry, R, long_):
    tp = entry + TP_R * R if long_ else entry - TP_R * R
    sl = entry - R if long_ else entry + R
    for j in range(i + 1, min(i + 1 + HOLD, len(r))):
        hi, lo = float(r[j]["high"]), float(r[j]["low"])
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


def stat(chunk, which):
    """which: 2 = follow, 3 = fade. Returns (n, win, ev)."""
    if not chunk:
        return 0, 0.0, 0.0
    w = sum(1 for x in chunk if x[which]) / len(chunk)
    c = sum(x[4] for x in chunk) / len(chunk)
    return len(chunk), w, w * TP_R - (1 - w) * 1.0 - c


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed")
        sys.exit(1)
    r = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M15, 0, BARS)
    if r is None or len(r) < 1000:
        print(f"[ERROR] not enough bars for {SYMBOL}")
        mt5.shutdown()
        return
    r = list(r)
    sp = SPREAD.get(SYMBOL, 0.0)

    rows = []
    for i in range(max(ER_WIN, FADE_N) + 20, len(r) - HOLD):
        a = atr14(r, i)
        er = eff_ratio(r, i)
        if not a or a <= 0 or er is None:
            continue
        entry = float(r[i]["close"])
        R = SL_ATR * a
        up = entry > float(r[i - FADE_N]["close"])
        rows.append((er, up, race(r, i, entry, R, up),
                     race(r, i, entry, R, not up), sp / R if R > 0 else 0.0))
    n = len(rows)
    order = sorted(rows, key=lambda x: x[0])

    print("=" * 86)
    print(f" STRONG-TREND FOLLOW TEST -- {SYMBOL} M15   n={n}")
    print(f" does FOLLOWING pay inside the strongest trends specifically?")
    print("=" * 86)
    print(f"  {'decile':<9}{'ER range':>18}{'n':>7}"
          f"{'FOLLOW win':>12}{'FOLLOW EV':>12}{'fade EV':>10}")
    print("  " + "-" * 82)
    d = n // 10
    for k in range(10):
        lo, hi = k * d, (k + 1) * d if k < 9 else n
        ch = order[lo:hi]
        cn, fw, fe = stat(ch, 2)
        _, _, de = stat(ch, 3)
        tag = "  <- strongest" if k == 9 else ""
        print(f"  D{k+1:<8}{ch[0][0]:>8.3f}..{ch[-1][0]:<8.3f}{cn:>7}"
              f"{100*fw:>11.1f}%{fe:>+12.3f}{de:>+10.3f}{tag}")

    print("\n  " + "=" * 82)
    print("  EXTREME BANDS -- follow only, with sample counts")
    print("  " + "=" * 82)
    print(f"  {'band':<16}{'ER >=':>9}{'n':>7}{'% of bars':>12}"
          f"{'win%':>9}{'EV':>10}")
    print("  " + "-" * 82)
    best = None
    for pct in (20, 10, 5, 2, 1):
        cutidx = n - max(1, n * pct // 100)
        cut = order[cutidx][0]
        ch = order[cutidx:]
        cn, fw, fe = stat(ch, 2)
        print(f"  top {pct:<12}{cut:>9.3f}{cn:>7}{pct:>11}%"
              f"{100*fw:>8.1f}%{fe:>+10.3f}")
        if fe > 0 and cn >= 100 and (best is None or fe > best[2]):
            best = (pct, cut, fe, cn)

    # train/test on whichever extreme band looked positive
    print("\n  " + "=" * 82)
    print("  OUT-OF-SAMPLE CHECK")
    print("  " + "=" * 82)
    if best is None:
        print("  No extreme band has POSITIVE follow-EV with n >= 100.")
        print("  Following does not pay even in the strongest trends, so the")
        print("  switch has nothing to switch to. Nothing to test further.")
        mt5.shutdown()
        return
    pct, cut, fe, cn = best
    mid = n // 2
    tr, te = rows[:mid], rows[mid:]
    tr_cut = sorted(x[0] for x in tr)[len(tr) - max(1, len(tr) * pct // 100)]
    te_band = [x for x in te if x[0] >= tr_cut]
    bn, bw, be = stat(te_band, 2)
    print(f"  band chosen on train: top {pct}%  ->  ER >= {tr_cut:.3f}")
    print(f"  scored on test half : n={bn}  win {100*bw:.1f}%  EV {be:+.3f}R")
    if be > 0 and bn >= 50:
        print("  -> HOLDS out of sample. Worth building the switch for this band.")
    elif bn < 50:
        print(f"  -> only {bn} test samples. Too few to act on.")
    else:
        print("  -> collapses out of sample. The in-sample positive was noise.")
    mt5.shutdown()


if __name__ == "__main__":
    main()
