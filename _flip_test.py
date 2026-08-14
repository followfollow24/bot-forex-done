#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_flip_test.py -- would REVERSING chart_ai_trader's signals have made money?

The bot went 0-for-14. The obvious idea is "so just trade the opposite".
This tests that against real bars rather than reasoning about it, because
the intuition has a specific hole worth measuring:

  Losing by stop does NOT imply winning by target when flipped.
  At R:R 1.5 the stop sits 1R away and the target 1.5R away. When the
  original trade's stop is hit, the flipped trade is only +1R -- it still
  needs price to travel another 0.5R in the same direction to reach its
  target, and if price reverts first (which is exactly what a stop-hunt
  wick does) the flipped trade dies at ITS stop. So a 100% loss rate can
  invert to well under a 100% win rate.

Also: costs do not invert. Spread is paid entering and exiting in either
direction, so it is a drag on both the original and the mirror.

Method: parse every [OPEN] line the live bot logged (symbol, side, fill,
sl, tp -- the real numbers it actually sent to the broker), then walk
forward through real M15 bars from the entry bar and see which level the
market touched first, for the original AND for the mirror (same distances,
opposite side). Intrabar order is unknowable from OHLC, so when a single
bar spans both levels the run is scored as the LOSS for whichever side is
being evaluated -- pessimistic for both, so neither is flattered.

Usage (on the VPS, MT5 running):  python _flip_test.py
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

# round-trip cost in PRICE units, per the repo's verified specs:
# XAUUSDc spread $0.24 (measured live 2026-08-05), BTCUSDc $10 (2026-07-07).
SPREAD = {"XAUUSDc": 0.24, "BTCUSDc": 10.0, "ETHUSDc": 0.6}
MAX_HOLD_BARS = 96          # 96 M15 bars = 24h; beyond that call it a timeout

OPEN_RE = re.compile(
    r"^(?P<ts>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ \[INFO\] \[OPEN\] "
    r"(?P<side>LONG|SHORT) (?P<sym>\S+) lot=(?P<lot>[\d.]+) "
    r"fill=(?P<fill>[\d.]+) sl=(?P<sl>[\d.]+) tp=(?P<tp>[\d.]+)")


def parse_opens():
    if not os.path.exists(LOG):
        print(f"[ERROR] {LOG} not found")
        sys.exit(1)
    out = []
    with open(LOG, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = OPEN_RE.match(line)
            if m:
                out.append({
                    "ts": datetime.strptime(m["ts"], "%Y-%m-%d %H:%M:%S"),
                    "side": m["side"].lower(), "sym": m["sym"],
                    "fill": float(m["fill"]), "sl": float(m["sl"]),
                    "tp": float(m["tp"]), "lot": float(m["lot"]),
                })
    return out


def bars_after(sym, ts, n=MAX_HOLD_BARS):
    rates = mt5.copy_rates_from(sym, mt5.TIMEFRAME_M15, ts, n + 2)
    if rates is None or len(rates) == 0:
        return []
    return [(r["time"], float(r["high"]), float(r["low"])) for r in rates]


def resolve(bars, entry, sl, tp, long_):
    """Which level does price touch first? Returns 'TP', 'SL' or 'TIMEOUT'.
    A bar spanning both is scored SL (pessimistic)."""
    for _, hi, lo in bars:
        hit_sl = (lo <= sl) if long_ else (hi >= sl)
        hit_tp = (hi >= tp) if long_ else (lo <= tp)
        if hit_sl:
            return "SL"          # checked first == pessimistic
        if hit_tp:
            return "TP"
    return "TIMEOUT"


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed")
        sys.exit(1)

    trades = parse_opens()
    print("=" * 92)
    print(f" FLIP TEST -- {len(trades)} live [OPEN] events from chart_ai_trader")
    print("=" * 92)
    if not trades:
        print("no [OPEN] lines found in the log")
        mt5.shutdown()
        return

    print(f"{'when':<17}{'sym':<9}{'side':<6}{'fill':>10}{'slD':>8}{'tpD':>8}"
          f"  {'ORIG':<8}{'FLIP':<8}")
    print("-" * 92)

    tally = {"orig": {"TP": 0, "SL": 0, "TIMEOUT": 0},
             "flip": {"TP": 0, "SL": 0, "TIMEOUT": 0}}
    r_orig = r_flip = 0.0

    for t in trades:
        long_ = t["side"] == "long"
        sl_d = abs(t["fill"] - t["sl"])
        tp_d = abs(t["tp"] - t["fill"])
        bars = bars_after(t["sym"], t["ts"])
        if not bars:
            print(f"{t['ts']:%m-%d %H:%M}    {t['sym']:<9}{t['side']:<6}"
                  f"{t['fill']:>10.2f}{sl_d:>8.2f}{tp_d:>8.2f}   (no bars)")
            continue

        o = resolve(bars, t["fill"], t["sl"], t["tp"], long_)
        # mirror: opposite side, SAME distances
        f_sl = t["fill"] + sl_d if long_ else t["fill"] - sl_d
        f_tp = t["fill"] - tp_d if long_ else t["fill"] + tp_d
        f = resolve(bars, t["fill"], f_sl, f_tp, not long_)

        tally["orig"][o] += 1
        tally["flip"][f] += 1
        sp = SPREAD.get(t["sym"], 0.0)
        # express result in R (multiples of the stop distance), net of spread
        for key, res in (("o", o), ("f", f)):
            r = (tp_d / sl_d) if res == "TP" else (-1.0 if res == "SL" else 0.0)
            r -= sp / sl_d                     # spread drag, paid either way
            if key == "o":
                r_orig += r
            else:
                r_flip += r

        print(f"{t['ts']:%m-%d %H:%M}    {t['sym']:<9}{t['side']:<6}"
              f"{t['fill']:>10.2f}{sl_d:>8.2f}{tp_d:>8.2f}  {o:<8}{f:<8}")

    n = sum(tally["orig"].values())
    print("-" * 92)
    for k, lbl in (("orig", "ORIGINAL (what ran)"), ("flip", "MIRROR (reversed)")):
        d = tally[k]
        wins = d["TP"]
        wr = 100.0 * wins / n if n else 0.0
        tot = r_orig if k == "orig" else r_flip
        print(f"  {lbl:<22} TP={d['TP']:<3} SL={d['SL']:<3} "
              f"TIMEOUT={d['TIMEOUT']:<3}  WR={wr:5.1f}%   "
              f"net={tot:+.2f}R (after spread)")
    print()
    print("  R = multiples of each trade's own stop distance, so the two rows")
    print("  are comparable even though the trades are different sizes.")
    print("  A bar touching both levels is scored SL for whichever side is")
    print("  being judged, so neither row is flattered by intrabar guessing.")
    mt5.shutdown()


if __name__ == "__main__":
    main()
