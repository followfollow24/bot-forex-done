#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_proxy_validity.py -- [A] is chart_ai's inversion actually "fade the move"?

The 6.64-year refutation that killed the ER idea tested a PROXY I invented:
"the AI calls LONG at breakouts, so inverting = shorting after a rise =
fading the last 20 bars". I inferred that from a handful of logged reasons
and then let four agents spend an hour testing it. If the proxy is wrong,
every conclusion drawn from it is about a strategy this bot does not run.

That assumption was never checked, and checking it is two minutes of
arithmetic on data already on disk.

METHOD. Every [OPEN] the bot logged carries a timestamp and a side, and
the log also records what the models said before inversion. For each one,
compare the AI's ORIGINAL direction (before the bot flipped it) against
the direction of the preceding N-bar move:

    AI said LONG  after price rose   -> agrees with momentum
    AI said SHORT after price fell   -> agrees with momentum

If the AI simply extrapolates the recent move, agreement runs high (~80%+)
and the fade proxy is sound, so the 6.64-year result applies directly. If
agreement is near 50%, the AI is doing something else, the proxy is a
fiction, and the whole refutation says nothing about this bot.

Reported across several lookbacks, because "the recent move" is not one
number and the answer should not hinge on picking 20.

Usage (on the VPS):  python _proxy_validity.py
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

LOOKBACKS = [5, 10, 20, 40, 80]
SINCE = "2026-08-15 04:33"          # the invert deploy
LOG = os.path.join(os.path.expanduser("~"), "Desktop",
                   "forex_bot_chart_ai_trader.log")

# "[BTCUSDC] INVERTED -> LONG sl=... (was SHORT)"  -> AI original = SHORT
INV_RE = re.compile(
    r"^(?P<ts>2026-\d\d-\d\d \d\d:\d\d:\d\d),\d+ \[INFO\]\s+\[(?P<sym>\w+)\] "
    r"INVERTED -> (?P<newside>LONG|SHORT).*\(was (?P<orig>LONG|SHORT)\)")


def main():
    if not os.path.exists(LOG):
        print(f"[ERROR] log not found: {LOG}")
        sys.exit(1)
    rows = []
    with open(LOG, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = INV_RE.match(line.strip())
            if m and m.group("ts") >= SINCE:
                rows.append((datetime.strptime(m.group("ts"),
                                               "%Y-%m-%d %H:%M:%S"),
                             m.group("orig")))
    print("=" * 78)
    print(" [A] PROXY VALIDITY -- does the AI just extrapolate the recent move?")
    print("=" * 78)
    print(f"  parsed {len(rows)} inverted decisions since {SINCE}")
    if not rows:
        print("  nothing to test")
        return
    if not mt5.initialize():
        print("[ERROR] MT5 init failed")
        sys.exit(1)
    r = mt5.copy_rates_from_pos("BTCUSDc", mt5.TIMEFRAME_M15, 0, 4000)
    mt5.shutdown()
    if r is None:
        print("[ERROR] no bars")
        return
    r = list(r)
    idx = {int(x["time"]): k for k, x in enumerate(r)}

    print(f"\n  {'lookback':<12}{'matched':>9}{'AI agrees with move':>22}")
    print("  " + "-" * 74)
    detail = {}
    for N in LOOKBACKS:
        agree = tot = 0
        marks = []
        for ts, orig in rows:
            k = idx.get(int(ts.timestamp()) // 900 * 900)
            if k is None or k < N:
                continue
            up = float(r[k]["close"]) > float(r[k - N]["close"])
            ai_up = orig == "LONG"
            tot += 1
            hit = (up == ai_up)
            agree += 1 if hit else 0
            marks.append("Y" if hit else ".")
        if tot:
            detail[N] = (tot, agree)
            print(f"  {N:<12}{tot:>9}{100*agree/tot:>21.1f}%   "
                  + "".join(marks))
    print("  " + "-" * 74)
    print("\n  READING IT")
    best = max(detail.items(), key=lambda kv: kv[1][1] / kv[1][0]) if detail else None
    if not best:
        print("  no decisions could be matched to bars")
        return
    N, (tot, agree) = best
    pct = 100 * agree / tot
    print(f"  strongest agreement: {pct:.1f}% at lookback {N} (n={tot})")
    if pct >= 75:
        print("  -> the AI DOES extrapolate the recent move. Inverting really is")
        print("     fade-the-move, so the 6.64-year refutation applies directly")
        print("     and the case against this strategy stands.")
    elif pct <= 60:
        print("  -> the AI does NOT simply extrapolate. The fade proxy is a")
        print("     fiction, the 6.64-year result says nothing about this bot,")
        print("     and every conclusion drawn from it must be withdrawn.")
    else:
        print("  -> partial. The proxy is a rough but imperfect stand-in; treat")
        print("     the 6.64-year result as suggestive, not decisive, here.")
    print(f"\n  n={tot} is small. This distinguishes 'always' from 'coin flip',")
    print("  which is all it is being asked to do.")


if __name__ == "__main__":
    main()
