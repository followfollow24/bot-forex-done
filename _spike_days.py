#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_spike_days.py -- how often does the chart drop like it did today?

On 4 Sep BTCUSDm fell roughly 2.3% right at 19:30 Thai, the same minute
gold did. The operator asks whether other days look like that. Three
things have to be separated to answer it honestly:

  1. HOW BIG was today, ranked against every other day at the same time.
  2. HOW OFTEN a move that size happens at 19:30. A pattern you can trade
     needs to recur; one that shows up twice a year is an anecdote.
  3. WHETHER 19:30 IS SPECIAL AT ALL. BTC runs 24/7, so a violent move at
     19:30 means nothing unless 19:30 is more violent than the other 23
     hours. The same measurement is therefore run on every hour and the
     rank of 19:30 among them is printed. This is the control, and
     without it a big number at 19:30 proves only that BTC moves.

Sizes are in PERCENT, not points: BTC ranged widely over the sample and
a 500-point move is a different event at 30k than at 100k.

Usage:  python _spike_days.py [symbol] [days] [weekends:0|1]
"""
import sys
from datetime import datetime, timedelta, timezone

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)"); sys.exit(1)

import numpy as np

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDm"
DAYS = int(sys.argv[2]) if len(sys.argv) > 2 else 1400
WEEKENDS = bool(int(sys.argv[3])) if len(sys.argv) > 3 else True
THAI, TARGET_H, TARGET_M = 7, 19, 30
HORIZONS = [5, 15, 30, 60]          # minutes after the bell


def load_m5(symbol, days):
    mt5.symbol_select(symbol, True)
    chunks, cursor = [], datetime.now()
    stop = datetime.now() - timedelta(days=days)
    while cursor > stop:
        p = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5,
                                 cursor - timedelta(days=45), cursor)
        if p is not None and len(p):
            chunks.append(p)
        cursor -= timedelta(days=45)
    if not chunks:
        return None
    r = np.concatenate(list(reversed(chunks)))
    _, keep = np.unique(r["time"], return_index=True)
    return r[np.sort(keep)]


def moves_at(r, tod, want_sec, horizon_bars):
    """Signed % move over `horizon_bars` M5 bars starting at the bar whose
    time-of-day is want_sec."""
    idx = np.where(tod == want_sec)[0]
    idx = idx[(idx > 0) & (idx < len(r) - horizon_bars - 1)]
    if len(idx) == 0:
        return np.array([]), np.array([])
    o = r["open"][idx].astype(float)
    c = r["close"][idx + horizon_bars].astype(float)
    return 100.0 * (c - o) / o, r["time"][idx]


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed"); return 2
    info = mt5.symbol_info(SYMBOL)
    if info is None:
        print(f"[ERROR] {SYMBOL} not found"); return 2
    r = load_m5(SYMBOL, DAYS)
    if r is None or len(r) < 5000:
        print(f"[ERROR] not enough M5 ({mt5.last_error()})"); return 2

    tk = mt5.symbol_info_tick(SYMBOL)
    off = int(round((tk.time - datetime.now(timezone.utc).timestamp()) / 3600.0))
    if not WEEKENDS:
        keep = np.array([datetime.fromtimestamp(int(t), timezone.utc).weekday() < 5
                         for t in r["time"]])
        r = r[keep]
    tod = r["time"] % 86400
    srv_h = (TARGET_H - THAI + off) % 24
    want = srv_h * 3600 + TARGET_M * 60

    print("=" * 86)
    print(f" SPIKES AT {TARGET_H}:{TARGET_M:02d} THAI -- {SYMBOL}")
    print(f" {len(r):,} M5 bars  "
          f"{datetime.fromtimestamp(r[0]['time']):%Y-%m-%d} -> "
          f"{datetime.fromtimestamp(r[-1]['time']):%Y-%m-%d}   "
          f"server UTC{off:+d}, so the bell is {srv_h}:{TARGET_M:02d} server")
    print("=" * 86)

    print(f"\n1. HOW BIG ARE THE MOVES AT {TARGET_H}:{TARGET_M:02d}?  "
          f"(absolute %, signed mean shown too)\n")
    print(f"{'window':>8}{'days':>7}{'median':>9}{'mean':>8}{'p90':>8}"
          f"{'max':>9}{'>=1%':>8}{'>=2%':>8}{'>=3%':>8}")
    ref = {}
    for hz in HORIZONS:
        pct, times = moves_at(r, tod, want, hz // 5)
        if len(pct) == 0:
            continue
        a = np.abs(pct)
        ref[hz] = (pct, times)
        print(f"{hz:>6}m{len(a):>8}{np.median(a):>9.2f}{a.mean():>8.2f}"
              f"{np.percentile(a, 90):>8.2f}{a.max():>9.2f}"
              f"{100.0*np.mean(a >= 1):>7.0f}%{100.0*np.mean(a >= 2):>7.0f}%"
              f"{100.0*np.mean(a >= 3):>7.0f}%")

    hz = 30
    if hz in ref:
        pct, times = ref[hz]
        order = np.argsort(-np.abs(pct))[:15]
        print(f"\n2. THE 15 BIGGEST {hz}-MINUTE MOVES FROM "
              f"{TARGET_H}:{TARGET_M:02d} THAI\n")
        print(f"   {'date (Thai)':>16}{'move %':>10}{'rank':>7}")
        for k, i in enumerate(order, 1):
            d = datetime.fromtimestamp(int(times[i]) - off * 3600 + THAI * 3600,
                                       timezone.utc)
            print(f"   {d:%Y-%m-%d %a}{pct[i]:>+10.2f}{k:>7}")
        newest = datetime.fromtimestamp(int(times[-1]) - off * 3600 + THAI * 3600,
                                        timezone.utc)
        rank = int(np.sum(np.abs(pct) >= abs(pct[-1])))
        print(f"\n   most recent session {newest:%Y-%m-%d}: {pct[-1]:+.2f}% "
              f"-- rank {rank} of {len(pct)} "
              f"(top {100.0*rank/len(pct):.0f}%)")

    print(f"\n3. IS {TARGET_H}:{TARGET_M:02d} SPECIAL, OR DOES BTC DO THIS "
          f"ROUND THE CLOCK?")
    print("   Same 30-minute measurement started on every hour.\n")
    print(f"   {'Thai':>7}{'days':>7}{'median':>9}{'mean':>8}{'>=1%':>8}"
          f"{'>=2%':>8}")
    rows = []
    for h in range(24):
        w = h * 3600
        pct, _ = moves_at(r, tod, w, 6)
        if len(pct) < 50:
            continue
        a = np.abs(pct)
        rows.append((h, len(a), float(np.median(a)), float(a.mean()),
                     100.0 * np.mean(a >= 1), 100.0 * np.mean(a >= 2)))
    for h, n, med, mean, p1, p2 in rows:
        th = (h - off + THAI) % 24
        star = "  <-- the bell" if h == srv_h else ""
        print(f"   {th:>5}:00{n:>7}{med:>9.2f}{mean:>8.2f}{p1:>7.0f}%"
              f"{p2:>7.0f}%{star}")
    if rows:
        best = sorted(rows, key=lambda x: -x[3])
        pos = [i for i, x in enumerate(best, 1) if x[0] == srv_h]
        print(f"\n   the bell hour ranks {pos[0] if pos else '?'} of "
              f"{len(rows)} hours by mean move -- "
              + ("it is one of the most violent hours"
                 if pos and pos[0] <= 5 else
                 "it is NOT unusually violent; big moves here are just BTC "
                 "being BTC"))
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
