#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_named_days.py -- were the operator's "strong days" recognisable BEFORE
19:30, or only afterwards?

They point at specific dates -- 2, 3, 4 and 10 September -- and say: skip
the quiet days, trade these. That is a reasonable instinct and it is
testable, but only one way round. The question is not whether those days
moved. They did. It is whether anything visible AT 19:29 separated them
from every other day.

So for each named day this ranks it, as a percentile among all sessions in
the archive, on measures that use only closed bars:

    move 1h / 3h / 6h before the bell, in ATR
    ATR itself against its own recent median   (is today volatile at all)
    yesterday's range against the same median

and beside them the OUTCOME -- the size of the 19:30 move -- which is what
the eye actually selected on.

Then the number that settles it: the correlation across ALL sessions
between each pre-bell measure and the outcome. If a measure ranks the
named days high AND correlates with the outcome generally, it is a filter
worth having. If the named days rank high only on the outcome, the eye is
reading the answer off the back page.

Usage:  python _named_days.py [symbol] [days_back] [YYYY-MM-DD ...]
"""
import sys
from datetime import datetime, timedelta, timezone

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)"); sys.exit(1)

import numpy as np

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "XAUUSDm"
BACK = int(sys.argv[2]) if len(sys.argv) > 2 else 200
NAMED = sys.argv[3:] or ["2026-09-02", "2026-09-03", "2026-09-04", "2026-09-10"]
MAXW, THAI = 900, 7


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed"); return 2
    if mt5.symbol_info(SYMBOL) is None:
        print(f"[ERROR] {SYMBOL} not found"); mt5.shutdown(); return 2
    mt5.symbol_select(SYMBOL, True)

    today = (datetime.now(timezone.utc) + timedelta(hours=THAI)).date()
    rows = []
    for b in range(BACK, -1, -1):
        d = today - timedelta(days=b)
        if d.weekday() >= 5:
            continue
        bell = datetime(d.year, d.month, d.day, 12, 30, tzinfo=timezone.utc)
        h1 = mt5.copy_rates_range(SYMBOL, mt5.TIMEFRAME_H1,
                                  bell - timedelta(days=6), bell)
        if h1 is None or len(h1) < 40:
            continue
        c = h1["close"].astype(float)
        hi, lo = h1["high"].astype(float), h1["low"].astype(float)
        pc = np.concatenate(([c[0]], c[:-1]))
        tr = np.maximum(hi - lo, np.maximum(np.abs(hi - pc), np.abs(lo - pc)))
        atr = float(tr[-14:].mean())
        if atr <= 0:
            continue
        # everything below uses bars that CLOSED before the bell
        m1 = abs(c[-1] - c[-2]) / atr if len(c) > 2 else np.nan
        m3 = abs(c[-1] - c[-4]) / atr if len(c) > 4 else np.nan
        m6 = abs(c[-1] - c[-7]) / atr if len(c) > 7 else np.nan
        med = float(np.median(tr[-120:])) if len(tr) >= 40 else np.nan
        vol = atr / med if med and med > 0 else np.nan
        yr = (hi[-25:-1].max() - lo[-25:-1].min()) / atr if len(hi) > 26 else np.nan
        t = mt5.copy_ticks_range(SYMBOL, bell,
                                 bell + timedelta(seconds=MAXW),
                                 mt5.COPY_TICKS_ALL)
        if t is None or len(t) < 20:
            continue
        bid, ask = t["bid"].astype(float), t["ask"].astype(float)
        mid = np.where(ask > 0, (bid + ask) / 2.0, bid)
        ref = float(mid[0])
        outcome = float(mid.max() - mid.min())        # the 19:30 range
        rows.append(dict(d=d, m1=m1, m3=m3, m6=m6, vol=vol, yr=yr,
                         out=outcome, out_atr=outcome / atr))
    if len(rows) < 40:
        print(f"[ERROR] only {len(rows)} sessions with data"); mt5.shutdown(); return 2

    keys = [("m1", "move 1h before"), ("m3", "move 3h before"),
            ("m6", "move 6h before"), ("vol", "ATR vs median"),
            ("yr", "yesterday range"), ("out_atr", "THE 19:30 MOVE")]
    print("=" * 78)
    print(f" WERE THEY RECOGNISABLE BEFORE THE BELL? -- {SYMBOL},"
          f" {len(rows)} sessions")
    print(f" {rows[0]['d']} to {rows[-1]['d']}   percentile among all of them")
    print("=" * 78)
    print(f"\n  {'day':>12}" + "".join(f"{lab:>17}" for _, lab in keys))
    named = set(NAMED)
    for r in rows:
        if str(r["d"]) not in named:
            continue
        line = f"  {str(r['d']):>12}"
        for k, _ in keys:
            v = [x[k] for x in rows if np.isfinite(x[k])]
            pr = 100.0 * np.mean(np.array(v) <= r[k]) if np.isfinite(r[k]) else np.nan
            line += f"{pr:>13.0f}th  " if np.isfinite(pr) else f"{'--':>17}"
        print(line)
    print(f"\n  100th = the largest of all sessions, 50th = perfectly ordinary")

    print(f"\n  DOES ANY PRE-BELL MEASURE PREDICT THE 19:30 MOVE?")
    print(f"    {'measure':>18}{'correlation':>14}   reading")
    out = np.array([r["out_atr"] for r in rows])
    for k, lab in keys[:-1]:
        v = np.array([r[k] for r in rows])
        ok = np.isfinite(v) & np.isfinite(out)
        if ok.sum() < 30:
            continue
        c = float(np.corrcoef(v[ok], out[ok])[0, 1])
        print(f"    {lab:>18}{c:>+14.2f}   "
              + ("useful" if abs(c) > 0.3 else
                 "weak" if abs(c) > 0.15 else "no relationship"))
    print(f"\n  A filter needs to rank the named days high AND correlate with")
    print(f"  the outcome. High only on THE 19:30 MOVE column means the days")
    print(f"  were chosen by what they did, which cannot be known at 19:29.")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
