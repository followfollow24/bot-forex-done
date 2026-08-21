#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_er_threshold_sweep.py -- is the strong-trend edge a slope or a cliff?

_strong_trend_follow.py found follow-EV negative in every efficiency-ratio
decile, then positive in the top 2% (ER >= 0.465, EV +0.131, holding at
+0.050 out of sample). Encouraging -- until the neighbouring numbers are
subtracted:

    top 5%  (ER >= 0.395)  n=392  EV -0.106
    top 2%  (ER >= 0.465)  n=156  EV +0.131
    => the 236 samples BETWEEN them carry EV about -0.263

So EV does not rise smoothly into the extreme. It is deeply negative just
below the cutoff and strongly positive just above it. A real market
mechanism does not switch sign across a 0.07 change in a smoothed ratio;
an overfitted threshold does exactly that.

This walks the cutoff in 0.01 steps and prints EV either side, in-sample
and out-of-sample, so the shape is visible instead of inferred. What
matters is not whether some cutoff is positive -- with 30 candidates one
usually is -- but whether POSITIVE EV forms a contiguous plateau that
survives the split. A single spiking cell surrounded by negative ones is
noise wearing a threshold.

Also reports, for each cutoff, the count of contiguous EPISODES rather
than bars. Adjacent 15-minute bars in one strong trend are the same event
seen repeatedly, so 156 "samples" may be a handful of independent trends --
the same overlap that turned a p=0.001 long/short finding into an artifact
earlier in this project.

Usage (on the VPS):  python _er_threshold_sweep.py [symbol] [fade_bars]
"""
import sys

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)")
    sys.exit(1)

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDc"
FADE_N = int(sys.argv[2]) if len(sys.argv) > 2 else 20
ER_WIN, SL_ATR, TP_R, HOLD, BARS = 40, 1.8, 1.25, 96, 8000
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


def ev(ch):
    if not ch:
        return 0, 0.0, 0.0
    w = sum(1 for x in ch if x[2]) / len(ch)
    c = sum(x[4] for x in ch) / len(ch)
    return len(ch), w, w * TP_R - (1 - w) * 1.0 - c


def episodes(idxs, gap=HOLD):
    """Count contiguous runs: bars closer together than one hold window
    belong to the same trend, not to independent bets."""
    if not idxs:
        return 0
    n, prev = 1, idxs[0]
    for i in idxs[1:]:
        if i - prev > gap:
            n += 1
        prev = i
    return n


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed")
        sys.exit(1)
    r = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M15, 0, BARS)
    if r is None or len(r) < 1000:
        print(f"[ERROR] not enough bars")
        mt5.shutdown()
        return
    r = list(r)
    sp = SPREAD.get(SYMBOL, 0.0)

    rows = []
    for i in range(max(ER_WIN, FADE_N) + 20, len(r) - HOLD):
        a = atr14(r, i)
        e = eff_ratio(r, i)
        if not a or a <= 0 or e is None:
            continue
        entry = float(r[i]["close"])
        R = SL_ATR * a
        up = entry > float(r[i - FADE_N]["close"])
        rows.append((i, e, race(r, i, entry, R, up), None,
                     sp / R if R > 0 else 0.0))
    n = len(rows)
    mid = n // 2
    tr, te = rows[:mid], rows[mid:]

    print("=" * 88)
    print(f" ER THRESHOLD SWEEP (follow only) -- {SYMBOL} M15   n={n}")
    print(" a real edge is a PLATEAU; a single positive cell is noise")
    print("=" * 88)
    print(f"  {'ER >=':>7}{'n':>7}{'episodes':>10}{'win%':>8}{'EV':>9}"
          f"{'  |':>4}{'n_test':>8}{'win% te':>9}{'EV test':>10}")
    print("  " + "-" * 84)
    for k in range(28, 61):
        cut = k / 100
        ch = [x for x in rows if x[1] >= cut]
        cn, cw, ce = ev(ch)
        if cn < 20:
            continue
        eps = episodes([x[0] for x in ch])
        cht = [x for x in te if x[1] >= cut]
        tn, tw, tev = ev(cht)
        mark = ""
        if ce > 0 and tev > 0:
            mark = "  <<< both +"
        elif ce > 0:
            mark = "  (in-sample + only)"
        print(f"  {cut:>7.2f}{cn:>7}{eps:>10}{100*cw:>7.1f}%{ce:>+9.3f}"
              f"{'  |':>4}{tn:>8}{100*tw:>8.1f}%{tev:>+10.3f}{mark}")

    print("\n" + "=" * 88)
    print("  READING IT")
    print("=" * 88)
    print("  'episodes' counts contiguous runs, not bars: adjacent 15-minute")
    print("  bars inside one strong trend are the SAME event measured again.")
    print("  If 150 samples are 4 episodes, the honest sample size is 4 -- the")
    print("  overlap that turned an earlier p=0.001 finding into an artifact.")
    print()
    print("  Look for a run of consecutive cutoffs positive in BOTH columns.")
    print("  One isolated '<<< both +' row with few episodes is not an edge.")
    mt5.shutdown()


if __name__ == "__main__":
    main()
