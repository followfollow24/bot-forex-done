#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_atr_regime_geometry.py -- is the LOW-volatility regime actually worse?

Raising MAX_SPREAD_R from 0.08 to 0.12 lets the bot trade again, but it
does something subtler than "pay a bit more". Every trade behind the
+0.353R walk-forward figure was taken at roughly 0.068R cost, when BTC's
stop distances ran ~200 points. Volatility then halved, the same $10
spread became 0.098-0.123R, and the gate skipped four consensus decisions
in one morning. Widening it admits setups from a volatility regime with
no measurements behind it at all.

This asks whether that regime is different, using historical bars only --
no API calls, no money, no AI. Pure geometry:

    starting at each bar, does price touch +1.25R before it touches -1R?

R is the stop the bot would have used (SL_ATR x ATR at that bar), so a
low-ATR bar automatically gets the tighter stop the live bot would have
placed. Bars are then split by ATR into quintiles and the same race is
scored inside each one. If the lowest quintile wins that race as often as
the highest, tight stops are not being clipped more easily and 0.12 is
safe. If it is materially worse, put the ceiling back to 0.08.

Spread is charged per bucket in the units that matter -- as a fraction of
THAT bucket's stop -- which is the whole point: a fixed $10 spread is a
small tax on a 200-point stop and a large one on an 80-point stop.

Pessimistic: a bar spanning both levels scores as the stop, so no bucket
is flattered.

This measures the UNCONDITIONAL race, not the AI-conditioned one. It can
show that the low-ATR regime is structurally worse; it cannot prove the
AI's edge survives there. Treat a pass as "no red flag", not as validation.

Usage (on the VPS):  python _atr_regime_geometry.py [symbol] [sl_atr] [tp_r]
  e.g.               python _atr_regime_geometry.py BTCUSDc 1.8 1.25
"""
import sys

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)")
    sys.exit(1)

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDc"
SL_ATR = float(sys.argv[2]) if len(sys.argv) > 2 else 1.8
TP_R = float(sys.argv[3]) if len(sys.argv) > 3 else 1.25
BARS = 6000                 # ~62 days of M15
HOLD = 96                   # 24h, as the live bot's practical horizon
SPREAD = {"XAUUSDc": 0.24, "BTCUSDc": 10.0, "ETHUSDc": 0.6}
N_BUCKETS = 5


def atr14(rates, i):
    if i < 15:
        return None
    trs = []
    for j in range(i - 13, i + 1):
        h, l = float(rates[j]["high"]), float(rates[j]["low"])
        pc = float(rates[j - 1]["close"])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs)


def race(rates, i, entry, R, long_):
    """Does +TP_R arrive before -1R? Pessimistic on a bar spanning both."""
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
    return False            # unresolved counts as a miss, not a win


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed")
        sys.exit(1)
    rates = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M15, 0, BARS)
    if rates is None or len(rates) < 500:
        print(f"[ERROR] not enough bars for {SYMBOL}")
        mt5.shutdown()
        return

    sp = SPREAD.get(SYMBOL, 0.0)
    rows = []
    for i in range(20, len(rates) - HOLD):
        a = atr14(rates, i)
        if not a or a <= 0:
            continue
        R = SL_ATR * a
        if R <= 0:
            continue
        entry = float(rates[i]["close"])
        rows.append((R, race(rates, i, entry, R, True),
                     race(rates, i, entry, R, False)))
    if not rows:
        print("[ERROR] no samples")
        mt5.shutdown()
        return

    rows.sort(key=lambda r: r[0])
    n = len(rows)
    size = n // N_BUCKETS

    print("=" * 86)
    print(f" ATR REGIME GEOMETRY -- {SYMBOL} M15, stop {SL_ATR}xATR, "
          f"target {TP_R}R, {HOLD}-bar hold")
    print(f" {n} sample bars, split into {N_BUCKETS} buckets by stop size")
    print(f" question: do TIGHT stops reach {TP_R}R as often as wide ones?")
    print("=" * 86)
    print(f"{'bucket':<9}{'stop range':>20}{'n':>7}{'long win%':>11}"
          f"{'short win%':>12}{'cost R':>9}{'EV/trade':>10}")
    print("-" * 86)

    out = []
    for b in range(N_BUCKETS):
        lo_i = b * size
        hi_i = (b + 1) * size if b < N_BUCKETS - 1 else n
        chunk = rows[lo_i:hi_i]
        if not chunk:
            continue
        stops = [c[0] for c in chunk]
        lw = sum(1 for c in chunk if c[1]) / len(chunk)
        sw = sum(1 for c in chunk if c[2]) / len(chunk)
        # spread as a share of THIS bucket's stop -- the whole point
        cost = sum(sp / c[0] for c in chunk) / len(chunk)
        wr = max(lw, sw)
        ev = wr * TP_R - (1 - wr) * 1.0 - cost
        out.append((b, wr, cost, ev))
        print(f"{'Q'+str(b+1):<9}{f'{min(stops):.0f}-{max(stops):.0f}':>20}"
              f"{len(chunk):>7}{100*lw:>10.1f}%{100*sw:>11.1f}%"
              f"{cost:>9.3f}{ev:>+10.3f}")

    print("-" * 86)
    if len(out) >= 2:
        lo_wr, hi_wr = out[0][1], out[-1][1]
        lo_ev, hi_ev = out[0][3], out[-1][3]
        print(f"  tightest stops (Q1): win {100*lo_wr:.1f}%  "
              f"cost {out[0][2]:.3f}R  EV {lo_ev:+.3f}R")
        print(f"  widest stops  (Q{len(out)}): win {100*hi_wr:.1f}%  "
              f"cost {out[-1][2]:.3f}R  EV {hi_ev:+.3f}R")
        gap = 100 * (hi_wr - lo_wr)
        print()
        print(f"  win-rate gap (wide minus tight): {gap:+.1f} points")
        print(f"  EV gap                         : {hi_ev - lo_ev:+.3f}R")
        print()
        if gap > 5.0:
            print("  -> TIGHT STOPS ARE WORSE by more than 5 points. The low-ATR")
            print("     regime is structurally different; put MAX_SPREAD_R back")
            print("     to 0.08 rather than trading it on faith.")
        elif lo_ev < 0 <= hi_ev:
            print("  -> geometry is similar but COST alone flips tight stops")
            print("     negative. The ceiling, not the market, is the problem.")
        else:
            print("  -> no red flag: tight stops reach the target about as often")
            print("     as wide ones. 0.12 is defensible. This is an absence of")
            print("     evidence against, NOT evidence the AI edge survives here.")
    mt5.shutdown()


if __name__ == "__main__":
    main()
