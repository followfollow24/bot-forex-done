#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_hybrid_trend_test.py -- follow the move when the trend is strong, fade it
                         otherwise. Does the switch beat always-fading?

_trend_filter_test.py asked whether BLOCKING trades in a trend helps and
found it does not: fading loses about the same in every regime (EV -0.037
to -0.071 across efficiency-ratio quintiles, all negative, spread only
1.5 points of win rate). That answered a different question from this one.

The proposal here is not to skip strong trends but to REVERSE the
handling: when the market is clearly going somewhere, stop inverting and
ride it; when it is not, keep inverting as now. Blocking and flipping are
different rules and the first result does not settle the second -- if
fading loses in trends, following might win there, or might lose too once
spread and the 1.25R target are paid. That is not deducible; it has to be
counted.

MAPPING TO THE LIVE BOT. chart_ai calls LONG at breakouts, so inverting
means "short after a rise" = fade. Not inverting means "long after a rise"
= follow. The rule therefore implements as: SKIP THE INVERSION when the
trend is strong. The proxy traded here is the same one used before --
fade or follow the last N bars, stop 1.8xATR, target 1.25R, 96-bar hold --
so the two tests are directly comparable.

Thresholds are chosen on the FIRST half of history and scored on the
SECOND. A rule that only works at a hand-picked cutoff on the data that
picked it is not a rule.

Three baselines are printed alongside, because "better than always-fading"
is a low bar when always-fading loses: pure fade, pure follow, and the
hybrid. The hybrid has to beat both AND clear zero to be worth building.

Usage (on the VPS):  python _hybrid_trend_test.py [symbol] [fade_bars]
"""
import os
import re
import sys
from datetime import datetime

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


def ev_of(rows, follow_if_er_at_least=None):
    """rows: (er, up, hit_follow, hit_fade, cost). None threshold = pure fade."""
    if not rows:
        return None, None, 0
    wins = 0
    cost = 0.0
    for er, up, hf, hd, c in rows:
        if follow_if_er_at_least is not None and er >= follow_if_er_at_least:
            hit = hf
        else:
            hit = hd
        wins += 1 if hit else 0
        cost += c
    n = len(rows)
    w = wins / n
    return w, w * TP_R - (1 - w) * 1.0 - cost / n, n


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
        # follow = trade WITH the move; fade = trade against it
        hit_follow = race(r, i, entry, R, up)
        hit_fade = race(r, i, entry, R, not up)
        rows.append((er, up, hit_follow, hit_fade, sp / R if R > 0 else 0.0))

    n = len(rows)
    print("=" * 84)
    print(f" HYBRID TREND TEST -- {SYMBOL} M15   n={n}")
    print(f" follow the last {FADE_N} bars when ER >= cutoff, else fade")
    print(f" stop {SL_ATR}xATR  target {TP_R}R  hold {HOLD}  spread charged")
    print("=" * 84)

    fw, fe, _ = ev_of(rows, None)
    lw, le, _ = ev_of(rows, -1.0)     # threshold below all -> always follow
    print(f"  ALWAYS FADE   (current strategy proxy): win {100*fw:.1f}%  EV {fe:+.3f}R")
    print(f"  ALWAYS FOLLOW                          : win {100*lw:.1f}%  EV {le:+.3f}R")

    print(f"\n  {'cutoff':>8}{'% follow':>11}{'win%':>9}{'EV/trade':>11}")
    print("  " + "-" * 80)
    grid = [x / 100 for x in range(10, 76, 5)]
    for cut in grid:
        share = sum(1 for er, *_ in rows if er >= cut) / n
        w, e, _ = ev_of(rows, cut)
        print(f"  {cut:>8.2f}{100*share:>10.1f}%{100*w:>8.1f}%{e:>+11.3f}")

    # ---- train/test: choose the cutoff on the first half only ----
    mid = n // 2
    tr, te = rows[:mid], rows[mid:]
    best_cut, best_e = None, None
    for cut in grid:
        if not (0.05 <= sum(1 for er, *_ in tr if er >= cut) / len(tr) <= 0.6):
            continue
        _, e, _ = ev_of(tr, cut)
        if best_e is None or e > best_e:
            best_cut, best_e = cut, e
    print("\n" + "=" * 84)
    print("  OUT-OF-SAMPLE (cutoff picked on first half, scored on second)")
    print("=" * 84)
    te_fade_w, te_fade_e, _ = ev_of(te, None)
    te_foll_w, te_foll_e, _ = ev_of(te, -1.0)
    if best_cut is None:
        print("  no cutoff met the coverage constraint on the training half")
        mt5.shutdown()
        return
    hw, he, _ = ev_of(te, best_cut)
    print(f"  cutoff chosen: ER >= {best_cut:.2f}   (train EV {best_e:+.3f}R)")
    print(f"    HYBRID        win {100*hw:.1f}%   EV {he:+.3f}R")
    print(f"    always fade   win {100*te_fade_w:.1f}%   EV {te_fade_e:+.3f}R")
    print(f"    always follow win {100*te_foll_w:.1f}%   EV {te_foll_e:+.3f}R")
    gain = he - te_fade_e
    print(f"\n  hybrid minus always-fade: {gain:+.3f}R per trade")
    if he > 0 and gain >= 0.03 and he > te_foll_e:
        print("  -> WORTH BUILDING: positive EV, beats both baselines out of sample")
    elif gain >= 0.03:
        print("  -> improves on fading but EV is still <= 0 or worse than simply")
        print("     always following. It reduces the loss; it does not make money.")
    else:
        print("  -> the switch does NOT help out of sample. Trend strength does")
        print("     not tell you when to flip direction; do not build it.")

    # ---- where the 17 real trades would have landed ----
    log = os.path.join(os.path.expanduser("~"), "Desktop",
                       "forex_bot_chart_ai_trader.log")
    if os.path.exists(log) and best_cut is not None:
        pat = re.compile(r"^(?P<ts>2026-\d\d-\d\d \d\d:\d\d:\d\d),\d+ \[INFO\]"
                         r"\s+\[OPEN\] (LONG|SHORT) BTCUSDc")
        ts = []
        with open(log, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = pat.match(line.strip())
                if m and m.group("ts") >= "2026-08-15 04:33":
                    ts.append(datetime.strptime(m.group("ts"), "%Y-%m-%d %H:%M:%S"))
        byts = {int(x["time"]): k for k, x in enumerate(r)}
        flip = keep = 0
        for t in ts:
            k = byts.get(int(t.timestamp()) // 900 * 900)
            if k is None or k < ER_WIN:
                continue
            e = eff_ratio(r, k)
            if e is None:
                continue
            if e >= best_cut:
                flip += 1
            else:
                keep += 1
        if flip + keep:
            print(f"\n  the {flip+keep} real trades under this rule: "
                  f"{flip} would FLIP to follow, {keep} stay inverted")
    mt5.shutdown()


if __name__ == "__main__":
    main()
