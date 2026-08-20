#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_trend_filter_test.py -- would refusing to trade in a trending market have
                         saved the invert strategy?

chart_ai_trader is 6 wins / 10 losses since the invert deploy, and 8 of
those losses came in one run while BTC went 64,300 -> 72,500 (+12.8% in
two days). The story that fits is: inverting means fading whatever the
models see, the models see breakouts, and fading a breakout works in a
range and dies in a trend.

That story is plausible and cheap to believe, which is exactly why it
needs measuring before anything is built on it.

WHAT IS ACTUALLY TESTED. The AI's historical direction cannot be
reconstructed without spending API calls, but its behaviour is known from
the live reasons it logged: it calls LONG when price sits above both EMAs
near recent highs. Inverted, that is "SHORT after a rise" -- i.e. FADE THE
RECENT MOVE. So the proxy traded here is: if the last N bars are net up,
go short; if net down, go long. Stop 1.8xATR, target 1.25R, exactly the
live geometry.

The regime metric is the efficiency ratio -- net displacement divided by
the total path travelled to get there. Near 1.0 the market went straight
(trend); near 0 it wandered (chop). It is chosen over ADX because it is a
direct measure of the thing the story is about, not a smoothed proxy for
it.

If the story is right, fading should win clearly in low-ER buckets and
lose in high-ER ones, and the split should survive out of sample. If the
win rate is flat across buckets, the trend explanation is wrong and the
losses are something else -- which would matter more than the filter.

Pessimistic resolution throughout: a bar spanning both levels counts as
the stop.

Usage (on the VPS):  python _trend_filter_test.py [symbol] [fade_bars]
"""
import os
import re
import sys
from datetime import datetime, timedelta

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)")
    sys.exit(1)

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDc"
FADE_N = int(sys.argv[2]) if len(sys.argv) > 2 else 20   # bars of move to fade
ER_WIN = 40          # bars used for the efficiency ratio
SL_ATR = 1.8
TP_R = 1.25
HOLD = 96
BARS = 8000
SPREAD = {"XAUUSDc": 0.24, "BTCUSDc": 10.0}
BASE = os.path.dirname(os.path.abspath(__file__))


def atr14(r, i):
    t = []
    for j in range(i - 13, i + 1):
        h, l = float(r[j]["high"]), float(r[j]["low"])
        pc = float(r[j - 1]["close"])
        t.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(t) / len(t)


def efficiency_ratio(r, i, win=ER_WIN):
    """|net move| / total path. 1.0 = straight line, 0 = pure chop.
    Uses only bars up to i, so it is knowable at decision time."""
    if i < win:
        return None
    net = abs(float(r[i]["close"]) - float(r[i - win]["close"]))
    path = sum(abs(float(r[j]["close"]) - float(r[j - 1]["close"]))
               for j in range(i - win + 1, i + 1))
    return (net / path) if path > 0 else 0.0


def race(r, i, entry, R, long_):
    """Reaches +TP_R before -1R? Pessimistic on a bar holding both."""
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
        er = efficiency_ratio(r, i)
        if not a or a <= 0 or er is None:
            continue
        entry = float(r[i]["close"])
        R = SL_ATR * a
        move_up = entry > float(r[i - FADE_N]["close"])
        long_ = not move_up          # FADE the move -- the invert proxy
        rows.append((er, race(r, i, entry, R, long_), sp / R if R > 0 else 0.0))

    n = len(rows)
    print("=" * 82)
    print(f" TREND FILTER TEST -- {SYMBOL} M15")
    print(f" trade = fade the last {FADE_N} bars   stop {SL_ATR}xATR   "
          f"target {TP_R}R   hold {HOLD}")
    print(f" regime = {ER_WIN}-bar efficiency ratio (1.0 straight / 0 chop)")
    print(f" {n} sample entries")
    print("=" * 82)

    def ev(chunk):
        if not chunk:
            return None, None
        w = sum(1 for _, hit, _ in chunk if hit) / len(chunk)
        c = sum(c for _, _, c in chunk) / len(chunk)
        return w, w * TP_R - (1 - w) * 1.0 - c

    allw, allev = ev(rows)
    print(f"  ALL regimes: win {100*allw:.1f}%   EV {allev:+.3f}R")
    print(f"\n  {'bucket':<9}{'ER range':>16}{'n':>7}{'win%':>9}{'EV/trade':>11}")
    print("  " + "-" * 78)
    order = sorted(rows, key=lambda x: x[0])
    q = n // 5
    buckets = []
    for k in range(5):
        lo, hi = k * q, (k + 1) * q if k < 4 else n
        ch = order[lo:hi]
        w, e = ev(ch)
        buckets.append((ch[0][0], ch[-1][0], len(ch), w, e))
        tag = "  <- chop" if k == 0 else ("  <- trend" if k == 4 else "")
        print(f"  Q{k+1:<8}{ch[0][0]:>7.3f}..{ch[-1][0]:<8.3f}{len(ch):>7}"
              f"{100*w:>8.1f}%{e:>+11.3f}{tag}")

    print("  " + "-" * 78)
    chop_w, chop_ev = buckets[0][3], buckets[0][4]
    tr_w, tr_ev = buckets[4][3], buckets[4][4]
    print(f"  chop (Q1) minus trend (Q5):  win {100*(chop_w-tr_w):+.1f} points"
          f"   EV {chop_ev-tr_ev:+.3f}R")

    # out-of-sample: pick the ER cutoff on the first half, score on the second
    mid = n // 2
    tr_rows, te_rows = rows[:mid], rows[mid:]
    best_cut, best_ev = None, None
    for cut in [x / 100 for x in range(10, 71, 5)]:
        ch = [x for x in tr_rows if x[0] <= cut]
        if len(ch) < 200:
            continue
        _, e = ev(ch)
        if best_ev is None or e > best_ev:
            best_cut, best_ev = cut, e
    print(f"\n  OUT-OF-SAMPLE (cutoff chosen on first half, scored on second)")
    if best_cut is None:
        print("    no usable cutoff found on the training half")
    else:
        kept = [x for x in te_rows if x[0] <= best_cut]
        skip = [x for x in te_rows if x[0] > best_cut]
        kw, ke = ev(kept)
        sw, se = ev(skip)
        _, base_e = ev(te_rows)
        print(f"    cutoff ER <= {best_cut:.2f}   (EV {best_ev:+.3f}R on train)")
        print(f"    TRADED  {len(kept):>5} entries   win {100*kw:.1f}%   EV {ke:+.3f}R")
        print(f"    BLOCKED {len(skip):>5} entries   win {100*sw:.1f}%   EV {se:+.3f}R")
        print(f"    no filter at all       EV {base_e:+.3f}R")
        gain = ke - base_e
        print(f"\n    filter gain out of sample: {gain:+.3f}R per trade")
        if gain >= 0.05 and ke > 0:
            print("    -> the filter HELPS and the filtered strategy is positive")
        elif gain >= 0.05:
            print("    -> the filter helps but the result is still NEGATIVE;")
            print("       it reduces the bleeding, it does not create an edge")
        else:
            print("    -> the filter does NOT help out of sample. The trend")
            print("       story does not explain the losses; do not build it.")

    # ---- the 16 real trades: where did they sit on this scale? ----
    log = os.path.join(os.path.expanduser("~"), "Desktop",
                       "forex_bot_chart_ai_trader.log")
    if os.path.exists(log):
        pat = re.compile(r"^(?P<ts>2026-\d\d-\d\d \d\d:\d\d:\d\d),\d+ \[INFO\]"
                         r"\s+\[OPEN\] (?P<side>LONG|SHORT) BTCUSDc")
        opens = []
        with open(log, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = pat.match(line.strip())
                if m and m.group("ts") >= "2026-08-15 04:33":
                    opens.append(datetime.strptime(m.group("ts"),
                                                   "%Y-%m-%d %H:%M:%S"))
        if opens:
            print("\n" + "=" * 82)
            print("  WHERE THE REAL TRADES SAT ON THIS SCALE")
            print("=" * 82)
            byts = {int(x["time"]): k for k, x in enumerate(r)}
            vals = []
            for t in opens:
                key = int(t.timestamp()) // 900 * 900
                k = byts.get(key)
                if k is None or k < ER_WIN:
                    continue
                e = efficiency_ratio(r, k)
                if e is not None:
                    vals.append((t, e))
            if vals:
                med = sorted(v for _, v in vals)[len(vals) // 2]
                print(f"  {len(vals)} of {len(opens)} entries matched to bars")
                print(f"  median ER at entry: {med:.3f}")
                q1hi = buckets[0][1]
                inchop = sum(1 for _, v in vals if v <= q1hi)
                print(f"  entries inside the CHOP bucket (ER <= {q1hi:.3f}): "
                      f"{inchop}/{len(vals)}")
                for t, v in vals[-6:]:
                    print(f"    {t:%m-%d %H:%M}  ER {v:.3f}")
            else:
                print("  could not align any entry to a bar")
    mt5.shutdown()


if __name__ == "__main__":
    main()
