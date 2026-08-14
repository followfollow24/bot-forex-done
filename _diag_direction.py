#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_diag_direction.py -- WHY was chart_ai_trader 0-for-17, all one way?

Execution has already been ruled out by inspection and by _flip_test:
signal->side->MT5 order type is correct, and the logged SHORTs really did
carry a stop ABOVE entry, so they really were shorts that really did get
run over. So the direction call itself is what needs explaining.

Two hypotheses this separates:

  H1 "BUG"     -- something systematically points the wrong way (e.g. the
                  HTF gate reading a trend it should not, or the models
                  being fed an inverted picture).
  H2 "REGIME"  -- nothing is inverted; the models plus the HTF gate are
                  all keying off the SAME lagging EMA structure, which
                  stayed bearish while price turned up. Then 17 trades are
                  not 17 bets, they are ONE bet on a stale trend, and the
                  mirror's +20.67R is just the other side of that one bet.

They predict different things, which is what makes this worth running:
  H1 -> the HTF label should look WRONG at entry (e.g. tagged BEARISH
        while H4 price is clearly above both EMAs).
  H2 -> the HTF label should look RIGHT by its own definition and still
        be followed by a rise, i.e. the EMAs are simply late, and the
        entries cluster in time rather than spreading across regimes.

For each live [OPEN] it prints the H4 structure the gate actually saw,
what price did afterwards, and how concentrated the entries were.

Usage (on the VPS, MT5 running):  python _diag_direction.py
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

BASE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(BASE, "forex_bot_chart_ai_trader.log")

OPEN_RE = re.compile(
    r"^(?P<ts>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ \[INFO\] \[OPEN\] "
    r"(?P<side>LONG|SHORT) (?P<sym>\S+) lot=[\d.]+ "
    r"fill=(?P<fill>[\d.]+) sl=(?P<sl>[\d.]+) tp=(?P<tp>[\d.]+)")


def ema(vals, span):
    k = 2.0 / (span + 1.0)
    prev = vals[0]
    for v in vals:
        prev = v * k + prev * (1 - k)
    return prev


def htf_at(sym, ts, bars=120):
    """Reproduce exactly what build_htf_context() would have labelled,
    using only bars that CLOSED at or before the entry -- no lookahead."""
    rates = mt5.copy_rates_from(sym, mt5.TIMEFRAME_H4, ts, bars)
    if rates is None or len(rates) < 50:
        return None
    closes = [float(r["close"]) for r in rates]
    e20, e50, px = ema(closes, 20), ema(closes, 50), closes[-1]
    if px > e20 and e20 > e50:
        lbl = "BULLISH"
    elif px < e20 and e20 < e50:
        lbl = "BEARISH"
    else:
        lbl = "NEUTRAL"
    return {"trend": lbl, "px": px, "e20": e20, "e50": e50}


def move_after(sym, ts, hours=24):
    rates = mt5.copy_rates_from(sym, mt5.TIMEFRAME_M15, ts, hours * 4 + 2)
    if rates is None or len(rates) < 3:
        return None
    p0 = float(rates[0]["close"])
    hi = max(float(r["high"]) for r in rates)
    lo = min(float(r["low"]) for r in rates)
    return {"p0": p0, "hi": hi, "lo": lo, "end": float(rates[-1]["close"])}


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed")
        sys.exit(1)

    trades = []
    with open(LOG, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = OPEN_RE.match(line)
            if m:
                trades.append({"ts": datetime.strptime(m["ts"], "%Y-%m-%d %H:%M:%S"),
                               "side": m["side"], "sym": m["sym"],
                               "fill": float(m["fill"])})
    if not trades:
        print("no [OPEN] lines found")
        mt5.shutdown()
        return

    print("=" * 100)
    print(" DIRECTION DIAGNOSIS -- H4 structure the gate saw, vs what price then did")
    print("=" * 100)
    print(f"{'when':<14}{'sym':<9}{'side':<6}{'H4 label':<9}"
          f"{'H4 px vs e20/e50':<22}{'gate ok?':<9}{'move +24h':<12}")
    print("-" * 100)

    agree = disagree = 0
    for t in trades:
        h = htf_at(t["sym"], t["ts"])
        mv = move_after(t["sym"], t["ts"])
        if not h or not mv:
            continue
        # was the label self-consistent with its own definition?
        rel = (f"{h['px']:.0f} / {h['e20']:.0f} / {h['e50']:.0f}")
        want = "BEARISH" if t["side"] == "SHORT" else "BULLISH"
        gate = "PASS" if h["trend"] == want else "should-BLOCK"
        if gate == "PASS":
            agree += 1
        else:
            disagree += 1
        pct = (mv["end"] - mv["p0"]) / mv["p0"] * 100.0
        print(f"{t['ts']:%m-%d %H:%M}  {t['sym']:<9}{t['side']:<6}{h['trend']:<9}"
              f"{rel:<22}{gate:<9}{pct:>+8.2f}%")

    print("-" * 100)
    print(f"  HTF gate consistent with its own rule : {agree} / {agree + disagree}")
    if disagree:
        print(f"  *** {disagree} entries the gate should have BLOCKED -> points to H1 (bug)")
    else:
        print("  No gate violations -> the gate did what it was told; the RULE is")
        print("  what was wrong, not the code. Points to H2 (lagging-trend regime).")

    # concentration: are these independent bets or one bet repeated?
    span_h = (trades[-1]["ts"] - trades[0]["ts"]).total_seconds() / 3600.0
    shorts = sum(1 for t in trades if t["side"] == "SHORT")
    print()
    print(f"  {len(trades)} entries spanning {span_h:.1f}h "
          f"({shorts} SHORT / {len(trades) - shorts} LONG)")
    syms = {}
    for t in trades:
        syms[t["sym"]] = syms.get(t["sym"], 0) + 1
    print(f"  by symbol: {syms}")
    print("  If these cluster in time and direction, they are close to ONE bet,")
    print("  and both the 0-for-17 and the mirror's +20.67R are that single bet's")
    print("  two faces -- not 17 independent samples of an edge.")
    mt5.shutdown()


if __name__ == "__main__":
    main()
