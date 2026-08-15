#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_entry_conditions.py -- what did the market look like when each real trade
was opened, and does that separate the winners from the losers?

_loser_anatomy.py established WHAT happened: 16 of 29 trades never showed
even +0.1R, the median loser died in 2 M15 bars, entries landed on the
turn. It could not say WHY those particular moments were chosen.

This reconstructs the market state at every real [OPEN] -- the same
stretch figures chart_ai_trader now computes, rebuilt from historical
bars as they stood at that instant -- and pairs each one with what the
trade went on to do. If the losers cluster at stretched extremes and the
winners do not, the exhaustion diagnosis is confirmed on the trades that
actually cost money, rather than on replayed charts.

That distinction matters for the fix. The exhaustion logic currently
lives in the PROMPT, which is advisory: the first version made OpenAI
refuse to trade at all, and a model can ignore or over-apply a written
rule in ways nobody can test. A threshold that separates winners from
losers here can instead become a CODE gate -- deterministic, unit-
testable, and impossible for a model to talk itself around.

NO LOOKAHEAD: every figure is built from bars strictly before the entry
timestamp. The outcome is measured only from bars after it.

Usage (on the VPS):  python _entry_conditions.py [log_path]
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chart_ai_trader as cat

BASE = os.path.dirname(os.path.abspath(__file__))
LOOKBACK = 160          # bars of context, as the live bot uses
MAX_HOLD = 96           # 24h resolution window
SPREAD = {"XAUUSDc": 0.24, "BTCUSDc": 10.0, "ETHUSDc": 0.6}

OPEN_RE = re.compile(
    r"^(?P<ts>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ \[INFO\] \[OPEN\] "
    r"(?P<side>LONG|SHORT) (?P<sym>\S+) lot=(?P<lot>[\d.]+) "
    r"fill=(?P<fill>[\d.]+) sl=(?P<sl>[\d.]+) tp=(?P<tp>[\d.]+)")


def find_log():
    if len(sys.argv) > 1:
        return sys.argv[1]
    import glob
    hits = []
    for r in (BASE, os.path.join(os.path.expanduser("~"), "Desktop")):
        hits += glob.glob(os.path.join(r, "*chart_ai*.log"))
    if not hits:
        print("[ERROR] no *chart_ai*.log found")
        sys.exit(1)
    hits.sort(key=lambda p: os.path.getsize(p), reverse=True)
    return hits[0]


def to_ohlcv(rates):
    return [[int(r["time"]) * 1000, float(r["open"]), float(r["high"]),
             float(r["low"]), float(r["close"]), float(r["tick_volume"])]
            for r in rates]


def atr14_from(rates):
    if len(rates) < 15:
        return None
    trs = []
    for j in range(len(rates) - 14, len(rates)):
        h, l = float(rates[j]["high"]), float(rates[j]["low"])
        pc = float(rates[j - 1]["close"])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs)


def outcome(bars, entry, sl, tp, long_):
    """TP or SL first, pessimistic on a bar spanning both."""
    for b in bars:
        hi, lo = float(b["high"]), float(b["low"])
        if long_:
            if lo <= sl:
                return "LOSS"
            if hi >= tp:
                return "WIN"
        else:
            if hi >= sl:
                return "LOSS"
            if lo <= tp:
                return "WIN"
    return "OPEN"


def split_report(name, rows, key, edges):
    """Win rate inside each band of one condition. The question is never
    'is this number big' but 'does it sort winners from losers'."""
    print(f"\n  {name}")
    print(f"    {'band':<18}{'n':>5}{'wins':>6}{'win%':>8}")
    labels = []
    for i, e in enumerate(edges):
        lo = edges[i - 1] if i else float("-inf")
        labels.append((lo, e, f"{lo:g} .. {e:g}" if i else f"< {e:g}"))
    labels.append((edges[-1], float("inf"), f">= {edges[-1]:g}"))
    for lo, hi, lbl in labels:
        band = [r for r in rows if lo <= key(r) < hi]
        if not band:
            continue
        w = sum(1 for r in band if r["res"] == "WIN")
        print(f"    {lbl:<18}{len(band):>5}{w:>6}{100*w/len(band):>7.0f}%")


def main():
    log = find_log()
    if not mt5.initialize():
        print("[ERROR] MT5 init failed")
        sys.exit(1)

    trades = []
    with open(log, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            m = OPEN_RE.match(line.strip())
            if m:
                d = m.groupdict()
                trades.append({
                    "ts": datetime.strptime(d["ts"], "%Y-%m-%d %H:%M:%S"),
                    "side": d["side"], "sym": d["sym"],
                    "fill": float(d["fill"]), "sl": float(d["sl"]),
                    "tp": float(d["tp"])})

    print("=" * 96)
    print(" ENTRY CONDITIONS -- market state at every real chart_ai [OPEN]")
    print(f" log: {log}   parsed: {len(trades)} trades")
    print(" figures rebuilt from bars BEFORE the entry; outcome from bars after")
    print("=" * 96)
    print(f"  {'when':<13}{'symbol':<10}{'side':<7}{'stretch':>9}{'rangep':>8}"
          f"{'run':>5}{'trav10':>8}{'htf':>9}{'res':>6}")
    print("  " + "-" * 92)

    rows = []
    for t in trades:
        pre = mt5.copy_rates_range(
            t["sym"], mt5.TIMEFRAME_M15,
            t["ts"] - timedelta(minutes=15 * (LOOKBACK + 5)), t["ts"])
        post = mt5.copy_rates_range(
            t["sym"], mt5.TIMEFRAME_M15,
            t["ts"], t["ts"] + timedelta(minutes=15 * (MAX_HOLD + 2)))
        if pre is None or len(pre) < 60 or post is None or len(post) < 4:
            continue
        pre = list(pre)
        atr = atr14_from(pre)
        if not atr or atr <= 0:
            continue
        cand = to_ohlcv(pre)
        ex = cat.build_exhaustion_context(cand, atr)

        h4 = mt5.copy_rates_range(
            t["sym"], mt5.TIMEFRAME_H4,
            t["ts"] - timedelta(days=40), t["ts"])
        h1 = mt5.copy_rates_range(
            t["sym"], mt5.TIMEFRAME_H1,
            t["ts"] - timedelta(days=12), t["ts"])
        htf = cat.build_htf_context(
            to_ohlcv(list(h4)[:-1]) if h4 is not None and len(h4) > 1 else [],
            to_ohlcv(list(h1)[:-1]) if h1 is not None and len(h1) > 1 else [])
        htf_lbl = (htf.get("htf") or {}).get("trend", "-")

        long_ = t["side"] == "LONG"
        res = outcome(list(post)[1:], t["fill"], t["sl"], t["tp"], long_)
        # was the trade taken WITH the current run, or against it?
        with_run = (long_ and ex["run_dir"] == "up") or \
                   ((not long_) and ex["run_dir"] == "down")

        r = {"t": t, "ex": ex, "htf": htf_lbl, "res": res,
             "with_run": with_run,
             "abs_stretch": abs(ex["stretch_atr"]),
             "edge_pos": max(ex["range_pos"], 1.0 - ex["range_pos"]),
             "run": ex["run_bars"], "trav": abs(ex["travel_atr"])}
        rows.append(r)
        print(f"  {t['ts']:%m-%d %H:%M}  {t['sym']:<10}{t['side']:<7}"
              f"{ex['stretch_atr']:>+9.2f}{ex['range_pos']:>8.2f}"
              f"{ex['run_bars']:>5}{ex['travel_atr']:>+8.2f}"
              f"{htf_lbl:>9}{res:>6}")

    mt5.shutdown()
    if not rows:
        print("  no rows resolved")
        return

    n = len(rows)
    wins = sum(1 for r in rows if r["res"] == "WIN")
    print("  " + "-" * 92)
    print(f"  overall: {wins}/{n} = {100*wins/n:.0f}% win rate as traded")

    print("\n" + "=" * 96)
    print("  DOES ANY CONDITION SORT WINNERS FROM LOSERS?")
    print("=" * 96)
    split_report("|distance from EMA20|, in ATR", rows,
                 lambda r: r["abs_stretch"], [0.5, 1.0, 1.5, 2.0])
    split_report("distance from the MIDDLE of the range (0.5 = mid, 1.0 = edge)",
                 rows, lambda r: r["edge_pos"], [0.6, 0.75, 0.9])
    split_report("consecutive same-direction closes", rows,
                 lambda r: r["run"], [2, 4, 6])
    split_report("|net travel over 10 bars|, in ATR", rows,
                 lambda r: r["trav"], [1.0, 2.0, 3.0])

    print("\n  traded WITH the current run vs AGAINST it")
    for lbl, want in (("WITH the run", True), ("AGAINST the run", False)):
        band = [r for r in rows if r["with_run"] is want]
        if band:
            w = sum(1 for r in band if r["res"] == "WIN")
            print(f"    {lbl:<18}{len(band):>5}{w:>6}{100*w/len(band):>7.0f}%")

    print("\n  H4 trend at entry")
    for lbl in ("BULLISH", "BEARISH", "NEUTRAL", "-"):
        band = [r for r in rows if r["htf"] == lbl]
        if band:
            w = sum(1 for r in band if r["res"] == "WIN")
            print(f"    {lbl:<18}{len(band):>5}{w:>6}{100*w/len(band):>7.0f}%")

    print("\n" + "=" * 96)
    print("  A band that is much worse than the overall rate is a candidate")
    print("  for a CODE gate -- deterministic and testable, unlike a prompt")
    print("  instruction a model can ignore. At n=29 a single band holds only")
    print("  a handful of trades, so treat any split as a lead to confirm on")
    print("  history, not as a threshold to ship.")


if __name__ == "__main__":
    main()
