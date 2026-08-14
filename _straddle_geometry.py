#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_straddle_geometry.py -- would opening BOTH directions at once ever pay?

The proposal: run the normal bot and the inverted bot together, so one
side always wins. The arithmetic says that is not what happens.

With a stop at 1R and a target at 1.5R on BOTH sides, from one entry:

    price runs 1.5R one way, cleanly   ->  +1.5R and -1R  =  +0.5R
    price wanders, both stops touched  ->  -1R and -1R    =  -2.0R
    one stops out, other times out     ->  -1R and  0R    =  -1.0R

So the pair is a bet that price TRENDS 1.5R before it retraces 1R -- a
long-volatility position with negative carry, since both legs pay spread.
Whether it wins is not a matter of opinion: it is the frequency of that
one geometric event, which can be counted directly off historical bars
with no money at risk.

That is all this does. For every bar in the sample it asks: starting
here, does price touch +1.5R before it touches -1R (up-run), or -1.5R
before +1R (down-run), or neither within the hold window? Then it prices
the resulting pair, including double spread.

The breakeven is easy to state: a pair needs P(clean run either way) high
enough that 0.5R x P(run) beats 2R x P(both stopped) plus costs.

Usage (on the VPS):  python _straddle_geometry.py [symbol] [sl_atr]
  e.g.               python _straddle_geometry.py BTCUSDc 1.8
"""
import sys

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)")
    sys.exit(1)

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDc"
SL_ATR = float(sys.argv[2]) if len(sys.argv) > 2 else 1.8   # bot's typical stop
TF = sys.argv[3] if len(sys.argv) > 3 else "15m"
RR = 1.5                    # bot's minimum reward:risk
BARS = 4000
# hold = 96 bars on M15 (24h). Scaled per timeframe so every run asks the
# same question -- "does it resolve within a comparable horizon?" -- rather
# than giving slower timeframes an unfairly short window.
_TF = {"15m": (mt5.TIMEFRAME_M15, 96), "1h": (mt5.TIMEFRAME_H1, 48),
       "4h": (mt5.TIMEFRAME_H4, 30), "1d": (mt5.TIMEFRAME_D1, 20)}
if TF not in _TF:
    print(f"[ERROR] timeframe must be one of {list(_TF)}")
    sys.exit(1)
MT5_TF, HOLD = _TF[TF]
SPREAD = {"XAUUSDc": 0.24, "BTCUSDc": 10.0, "ETHUSDc": 0.6}


def atr14(rates, i):
    if i < 15:
        return None
    trs = []
    for j in range(i - 13, i + 1):
        h, l = float(rates[j]["high"]), float(rates[j]["low"])
        pc = float(rates[j - 1]["close"])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs)


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed")
        sys.exit(1)
    rates = mt5.copy_rates_from_pos(SYMBOL, MT5_TF, 0, BARS)
    if rates is None or len(rates) < 200:
        print(f"[ERROR] no bars for {SYMBOL}")
        mt5.shutdown()
        return

    sp = SPREAD.get(SYMBOL, 0.0)
    up = down = both = neither = 0
    n = 0

    for i in range(20, len(rates) - HOLD):
        atr = atr14(rates, i)
        if not atr or atr <= 0:
            continue
        entry = float(rates[i]["close"])
        R = SL_ATR * atr
        tp_up, sl_up = entry + RR * R, entry - R      # the LONG leg
        tp_dn, sl_dn = entry - RR * R, entry + R      # the SHORT leg

        long_res = short_res = None
        for j in range(i + 1, i + 1 + HOLD):
            hi, lo = float(rates[j]["high"]), float(rates[j]["low"])
            # pessimistic: a bar spanning both levels resolves as the STOP
            if long_res is None:
                if lo <= sl_up:
                    long_res = "SL"
                elif hi >= tp_up:
                    long_res = "TP"
            if short_res is None:
                if hi >= sl_dn:
                    short_res = "SL"
                elif lo <= tp_dn:
                    short_res = "TP"
            if long_res and short_res:
                break

        n += 1
        if long_res == "TP":
            up += 1
        elif short_res == "TP":
            down += 1
        elif long_res == "SL" and short_res == "SL":
            both += 1
        else:
            neither += 1

    runs = up + down
    p_run = runs / n
    p_both = both / n
    p_nei = neither / n
    # pair P&L in R: a clean run pays +RR on one leg, -1 on the other.
    # both-stopped pays -2. neither pays about -1 (one stop, one timeout).
    spread_R = 2 * sp / (SL_ATR * atr14(rates, len(rates) - HOLD - 1) or 1)
    ev = p_run * (RR - 1.0) + p_both * (-2.0) + p_nei * (-1.0) - spread_R

    print("=" * 78)
    print(f" STRADDLE GEOMETRY -- {SYMBOL} {TF}, stop {SL_ATR}xATR, target {RR}R")
    print(f" {n} sample entries, {HOLD}-bar hold, {BARS} bars of history")
    print("=" * 78)
    print(f"  clean run UP    (long TP first) : {up:>5}  {100*up/n:>5.1f}%")
    print(f"  clean run DOWN  (short TP first): {down:>5}  {100*down/n:>5.1f}%")
    print(f"  BOTH stops hit  (whipsaw)       : {both:>5}  {100*p_both:>5.1f}%   -> -2R each")
    print(f"  neither resolves                : {neither:>5}  {100*p_nei:>5.1f}%")
    print("-" * 78)
    print(f"  P(clean run either way)         : {100*p_run:.1f}%")
    print(f"  double-spread drag              : {spread_R:.3f}R per pair")
    print(f"  EXPECTED VALUE PER PAIR         : {ev:+.3f}R")
    print()
    need = (2.0 * p_both + 1.0 * p_nei + spread_R) / (RR - 1.0)
    print(f"  breakeven needs P(clean run) >= {100*min(need,1.0):.1f}%  "
          f"(actual {100*p_run:.1f}%)")
    print()
    # ---- what a CONSTANT direction would earn: the bar any predictor
    # must clear. If this is already near breakeven, there is very little
    # room for skill to add value on this timeframe.
    c1 = spread_R / 2.0                       # one-sided spread cost
    ev_long = (up / n) * RR - ((down + both) / n) - c1
    ev_short = (down / n) * RR - ((up + both) / n) - c1
    be_wr = (1.0 + c1) / (1.0 + RR)
    print(f"  ALWAYS-LONG  : hit {100*up/n:.1f}%  EV {ev_long:+.3f}R")
    print(f"  ALWAYS-SHORT : hit {100*down/n:.1f}%  EV {ev_short:+.3f}R")
    print(f"  breakeven win rate on this timeframe : {100*be_wr:.1f}%")
    print(f"  headroom over a coin flip            : "
          f"{100*(max(up,down)/n - be_wr):+.1f} points")
    print()
    if ev > 0:
        print("  -> POSITIVE. Worth a closer look (then walk-forward it).")
    else:
        print("  -> NEGATIVE. Opening both directions loses on this data:")
        print("     whipsaws cost 2R and outnumber the clean runs that pay 0.5R.")
    mt5.shutdown()


if __name__ == "__main__":
    main()
