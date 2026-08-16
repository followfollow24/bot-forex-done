#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_tp15_base_rate.py -- is btc_h1_manual broken, or just judged too early?

Live: 13 trades, 8 wins, 61.5% -- and -421.62. A win rate that high losing
money is not a paradox, it is a description of the exit geometry:

    SL  = 2.5 x ATR   (broker-side)
    TP  = 15  x ATR   (broker-side)  -> 6R away
    timeout at 64 H1 bars            -> exits at whatever price is there

A target 6R away is almost never reached. So "wins" are mostly timeouts
closed slightly green, while losses are the full 2.5xATR. That shape can
still be profitable -- but only if the rare 6R winner turns up often
enough to pay for all the small ones. Over 13 trades it may simply not
have had the chance.

This measures how often that 6R target actually lands, on real bars, so
the question stops being an argument and becomes arithmetic:

    P(TP)  P(SL)  P(timeout)  mean R of a timeout  EV per trade
    and then: P(zero TP hits in 13 trades)

If P(TP) is small enough that seeing none in 13 is unremarkable, the live
loss is variance and the risk cut was the right response. If instead the
geometry says EV is negative even with the rare winners counted, the
strategy is mis-specified and no amount of waiting fixes it.

SCOPE, stated plainly: this walks EVERY bar, so it measures the EXIT
GEOMETRY, not the bot's entry signal (trend-pullback + ADX + touch
tolerance). The entry filter changes which bars are taken, not how far
away the target sits -- and the far target is what makes a 61.5% win rate
lose money. Read it as "what this exit structure does in general", not as
a backtest of the strategy.

Pessimistic: a bar touching both levels counts as the stop.

Usage (on the VPS):  python _tp15_base_rate.py [symbol] [sl_atr] [tp_atr]
  e.g.               python _tp15_base_rate.py BTCUSDc 2.5 15.0
"""
import sys

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)")
    sys.exit(1)

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDc"
SL_ATR = float(sys.argv[2]) if len(sys.argv) > 2 else 2.5
TP_ATR = float(sys.argv[3]) if len(sys.argv) > 3 else 15.0
HOLD = 64                    # max_hold_bars, mirrored from the live config
BARS = 8000                  # ~11 months of H1
SPREAD = {"XAUUSDc": 0.24, "BTCUSDc": 10.0}
LIVE_N, LIVE_WINS = 13, 8    # what the account actually shows


def atr14(rates, i):
    trs = []
    for j in range(i - 13, i + 1):
        h, l = float(rates[j]["high"]), float(rates[j]["low"])
        pc = float(rates[j - 1]["close"])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs)


def simulate(rates, i, long_):
    """Returns (outcome, R) with R in units of the STOP distance."""
    a = atr14(rates, i)
    if not a or a <= 0:
        return None
    entry = float(rates[i]["close"])
    R = SL_ATR * a
    tp = entry + TP_ATR * a if long_ else entry - TP_ATR * a
    sl = entry - R if long_ else entry + R
    for j in range(i + 1, min(i + 1 + HOLD, len(rates))):
        hi, lo = float(rates[j]["high"]), float(rates[j]["low"])
        if long_:
            if lo <= sl:
                return ("SL", -1.0)
            if hi >= tp:
                return ("TP", TP_ATR / SL_ATR)
        else:
            if hi >= sl:
                return ("SL", -1.0)
            if lo <= tp:
                return ("TP", TP_ATR / SL_ATR)
    # timeout: exit at the close of the last held bar
    k = min(i + HOLD, len(rates) - 1)
    px = float(rates[k]["close"])
    move = (px - entry) if long_ else (entry - px)
    return ("TIMEOUT", move / R)


def binom_zero(p, n):
    return (1.0 - p) ** n


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed")
        sys.exit(1)
    rates = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_H1, 0, BARS)
    if rates is None or len(rates) < 500:
        print(f"[ERROR] not enough H1 bars for {SYMBOL}")
        mt5.shutdown()
        return
    rates = list(rates)

    res = {}
    for long_ in (True, False):
        side = "long" if long_ else "short"
        out = {"TP": [], "SL": [], "TIMEOUT": []}
        for i in range(20, len(rates) - HOLD):
            r = simulate(rates, i, long_)
            if r:
                out[r[0]].append(r[1])
        res[side] = out

    print("=" * 84)
    print(f" TP-{TP_ATR:g}xATR BASE RATE -- {SYMBOL} H1, SL {SL_ATR:g}xATR, "
          f"hold {HOLD} bars")
    print(f" target sits {TP_ATR/SL_ATR:.1f}R away; measures the EXIT GEOMETRY, "
          f"not the entry signal")
    print("=" * 84)
    print(f"{'side':<8}{'n':>7}{'TP%':>8}{'SL%':>8}{'timeout%':>10}"
          f"{'mean R (timeout)':>18}{'EV/trade':>10}")
    print("-" * 84)

    sp = SPREAD.get(SYMBOL, 0.0)
    summary = {}
    for side, out in res.items():
        n = sum(len(v) for v in out.values())
        if not n:
            continue
        p_tp, p_sl = len(out["TP"]) / n, len(out["SL"]) / n
        p_to = len(out["TIMEOUT"]) / n
        mean_to = (sum(out["TIMEOUT"]) / len(out["TIMEOUT"])
                   if out["TIMEOUT"] else 0.0)
        ev = (p_tp * (TP_ATR / SL_ATR)) + (p_sl * -1.0) + (p_to * mean_to)
        summary[side] = (p_tp, p_sl, p_to, mean_to, ev, n)
        print(f"{side:<8}{n:>7}{100*p_tp:>7.2f}%{100*p_sl:>7.1f}%"
              f"{100*p_to:>9.1f}%{mean_to:>17.3f}{ev:>+10.3f}")

    print("-" * 84)
    # the win rate this geometry implies: a TP, or a timeout closed green
    for side, out in res.items():
        n = sum(len(v) for v in out.values())
        if not n:
            continue
        wins = len(out["TP"]) + sum(1 for r in out["TIMEOUT"] if r > 0)
        print(f"  {side:<6} implied win rate (TP or green timeout): "
              f"{100*wins/n:.1f}%   -- live shows "
              f"{100*LIVE_WINS/LIVE_N:.1f}% over {LIVE_N} trades")

    print("\n" + "=" * 84)
    print("  IS 0 TAKE-PROFITS IN 13 TRADES SURPRISING?")
    print("=" * 84)
    for side, (p_tp, _, _, _, ev, _) in summary.items():
        p0 = binom_zero(p_tp, LIVE_N)
        print(f"  {side:<6} P(TP) = {100*p_tp:.2f}%  ->  "
              f"P(zero in {LIVE_N} trades) = {100*p0:.0f}%")
    print()
    avg_ev = sum(v[4] for v in summary.values()) / max(len(summary), 1)
    if avg_ev > 0:
        print(f"  EV is POSITIVE ({avg_ev:+.3f}R avg) and driven by the rare far")
        print("  target. If P(zero) above is large, then 13 losing-ish trades is")
        print("  ordinary variance, not a defect -- the strategy needs far more")
        print("  trades before its result means anything, and cutting risk while")
        print("  waiting was the right call.")
    else:
        print(f"  EV is NEGATIVE ({avg_ev:+.3f}R avg) even counting the rare far")
        print("  target. Then waiting does NOT fix this: the exit structure loses")
        print("  on its own geometry and the target/stop pair needs rethinking.")
    print()
    print("  Caveat: entry filters change WHICH bars are taken, not how far the")
    print("  target sits. Treat these as the geometry's own numbers.")
    mt5.shutdown()


if __name__ == "__main__":
    main()
