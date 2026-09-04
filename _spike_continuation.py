#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_spike_continuation.py -- the operator circled a near-vertical drop on
XAUAUDm M15 and described the rule as:

  "when it starts running down like that, wait 0.2-0.5 seconds to see
   it is really running, then get in."

This is NOT the rule already tested and rejected. The calibrated retest
swept momentum only up to 2.0 xATR and ran out of samples there (43
trades). What is circled is far outside that range -- a single-bar move
several times ATR. The tail was never measured. This measures it.

WHAT IS TESTABLE AND WHAT IS NOT
----------------------------------------------------------------------
0.2-0.5 SECONDS IS NOT TESTABLE HERE and would not be actionable if it
were: the finest history MT5 serves is M1, and a bot that reads closed
bars can react no faster than the next bar's open. So this runs on M1
and enters at the NEXT M1 OPEN -- the fastest any bar-driven system can
move, and already optimistic compared to a human tapping a phone.

The test is also optimistic in a second way that has to be said out
loud: it charges the CURRENT quoted spread on every fill. During a
vertical move on a gold cross the spread widens and fills slip, often
by multiples. So a losing result here is conclusive, while a winning
result would still need live proof before it meant anything.

WHAT IT MEASURES
  1. The actual event in the screenshot -- the largest M1 moves of the
     last few days, with server AND Thai timestamps, so we are looking
     at the same candle the operator is.
  2. Whether those moves are even ENTERABLE: a spike that arrives as a
     price gap cannot be joined at any speed, because no trade prints
     between the two prices. This counts them.
  3. Continuation vs reversion after a spike, swept by spike size, at
     +5 / +15 / +30 / +60 minutes, in ATR units.
  4. The tradeable version: enter next M1 open with the operator's own
     0.42/0.51 brackets, against 20 random-direction draws on the same
     spike bars.

Usage (VPS):  python _spike_continuation.py [symbol] [months]
"""
import sys
from datetime import datetime, timedelta, timezone

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)")
    sys.exit(1)

import numpy as np

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "XAUAUDm"
MONTHS = float(sys.argv[2]) if len(sys.argv) > 2 else 6.0

TP_ATR, SL_ATR = 0.51, 0.42
MAX_HOLD = 60                       # M1 bars = 1 hour
SPIKE_GRID = [0.5, 1.0, 1.5, 2.0, 3.0]     # single-bar move, in ATR(H1)
HORIZONS = [5, 15, 30, 60]
GAP_ATR = 0.20                      # open-vs-prev-close treated as a gap
N_CTRL = 20
THAI = 7


def load_m1(symbol, months):
    mt5.symbol_select(symbol, True)
    chunks, cursor = [], datetime.now()
    stop = datetime.now() - timedelta(days=int(months * 30.5))
    while cursor > stop:
        part = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M1,
                                    cursor - timedelta(days=30), cursor)
        if part is not None and len(part):
            chunks.append(part)
        cursor -= timedelta(days=30)
    if not chunks:
        return None
    r = np.concatenate(list(reversed(chunks)))
    _, keep = np.unique(r["time"], return_index=True)
    return r[np.sort(keep)]


def h1_atr_on(r, n=14):
    """ATR14 of H1 bars carried onto every bar of r, from the last CLOSED
    H1 bar -- no lookahead."""
    h1, cur = [], None
    for b in r:
        h = (b["time"] // 3600) * 3600
        if cur is None or cur["t"] != h:
            if cur is not None:
                h1.append(cur)
            cur = {"t": h, "h": b["high"], "l": b["low"], "c": b["close"]}
        else:
            cur["h"] = max(cur["h"], b["high"]); cur["l"] = min(cur["l"], b["low"])
            cur["c"] = b["close"]
    if cur:
        h1.append(cur)
    avail, trs = {}, []
    for i in range(1, len(h1)):
        trs.append(max(h1[i]["h"] - h1[i]["l"], abs(h1[i]["h"] - h1[i - 1]["c"]),
                       abs(h1[i]["l"] - h1[i - 1]["c"])))
        if len(trs) >= n:
            avail[h1[i]["t"] + 3600] = sum(trs[-n:]) / n
    keys = sorted(avail)
    out, ki, cv = np.full(len(r), np.nan), 0, np.nan
    for i, b in enumerate(r):
        while ki < len(keys) and keys[ki] <= b["time"]:
            cv = avail[keys[ki]]; ki += 1
        out[i] = cv
    return out


def spikes(r, atr, k):
    """Indices of bars whose own body moved >= k * ATR(H1)."""
    o, c = r["open"], r["close"]
    out = []
    for i in range(1, len(r) - MAX_HOLD - 1):
        a = atr[i]
        if not np.isfinite(a) or a <= 0:
            continue
        if abs(c[i] - o[i]) >= k * a:
            out.append(i)
    return out


def bracket(r, atr, i, s, spread):
    a = atr[i]
    entry = r["open"][i + 1]
    tp_d, sl_d = TP_ATR * a, SL_ATR * a
    tp, sl = entry + s * tp_d, entry - s * sl_d
    for j in range(i + 1, min(i + 1 + MAX_HOLD, len(r))):
        hp, lp = r["high"][j], r["low"][j]
        if (lp <= sl) if s > 0 else (hp >= sl):
            return -1.0 - spread / sl_d
        if (hp >= tp) if s > 0 else (lp <= tp):
            return tp_d / sl_d - spread / sl_d
    j = min(i + MAX_HOLD, len(r) - 1)
    return ((r["close"][j] - entry) * s) / sl_d - spread / sl_d


def ev(x):
    return ((len(x), 100.0 * sum(1 for v in x if v > 0) / len(x), float(np.mean(x)))
            if x else (0, float("nan"), float("nan")))


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed"); return 2
    info = mt5.symbol_info(SYMBOL)
    if info is None:
        print(f"[ERROR] {SYMBOL} not found"); return 2
    r = load_m1(SYMBOL, MONTHS)
    if r is None or len(r) < 20000:
        print(f"[ERROR] not enough M1 data ({mt5.last_error()})"); return 2

    atr = h1_atr_on(r)
    o, c, spread = r["open"], r["close"], info.spread * info.point
    tick = mt5.symbol_info_tick(SYMBOL)
    off = int(round((tick.time - datetime.now(timezone.utc).timestamp()) / 3600.0)) if tick else 0

    def thai(ts):
        return datetime.fromtimestamp(int(ts) - off * 3600 + THAI * 3600,
                                      timezone.utc)

    print("=" * 86)
    print(f" SPIKE CONTINUATION -- {SYMBOL}   {len(r):,} M1 bars   "
          f"quoted spread {spread:.2f}")
    print(f" {datetime.fromtimestamp(r[0]['time']):%Y-%m-%d} -> "
          f"{datetime.fromtimestamp(r[-1]['time']):%Y-%m-%d}   server = UTC{off:+d}")
    print("=" * 86)

    # ---- 1. the candle in the screenshot ----------------------------------
    print("\n1. THE 12 LARGEST SINGLE-MINUTE MOVES IN THE LAST 5 DAYS")
    print("   (so we are certainly looking at the same candle)\n")
    print(f"   {'Thai time':>17}{'move':>9}{'xATR':>8}{'gap in':>9}{'range':>9}")
    recent = [i for i in range(1, len(r)) if r["time"][i] > r["time"][-1] - 5 * 86400
              and np.isfinite(atr[i]) and atr[i] > 0]
    top = sorted(recent, key=lambda i: -abs(c[i] - o[i]))[:12]
    for i in sorted(top):
        gap = o[i] - c[i - 1]
        print(f"   {thai(r['time'][i]):%Y-%m-%d %H:%M}{c[i]-o[i]:>9.2f}"
              f"{(c[i]-o[i])/atr[i]:>8.2f}{gap:>9.2f}"
              f"{r['high'][i]-r['low'][i]:>9.2f}")

    # ---- 2/3/4 by spike size ---------------------------------------------
    for k in SPIKE_GRID:
        idx = spikes(r, atr, k)
        if len(idx) < 30:
            print(f"\n>= {k} xATR in one minute: only {len(idx)} events -- too few")
            continue
        gapped = sum(1 for i in idx if abs(o[i] - c[i - 1]) >= GAP_ATR * atr[i])
        print(f"\n{'-'*86}\nSPIKE >= {k:.1f} xATR IN ONE MINUTE   "
              f"n={len(idx)}   arriving as a price gap: {gapped} "
              f"({100.0*gapped/len(idx):.0f}%)")
        if gapped > len(idx) * 0.3:
            print("   NOTE: a gap cannot be joined at any reaction speed --")
            print("   no trade prints between the two prices.")

        # forward drift, signed by the spike direction
        line = "   drift after the spike (xATR, + = kept going):  "
        for hz in HORIZONS:
            d = [((c[min(i + hz, len(r) - 1)] - c[i]) * np.sign(c[i] - o[i])) / atr[i]
                 for i in idx]
            line += f"+{hz}m {np.mean(d):+.3f}   "
        print(line)

        sig = [bracket(r, atr, i, int(np.sign(c[i] - o[i])), spread) for i in idx]
        n1, w1, e1 = ev(sig)
        cs = []
        for s in range(1, N_CTRL + 1):
            rng = np.random.default_rng(s)
            cs.append(ev([bracket(r, atr, i, int(rng.choice((1, -1))), spread)
                          for i in idx])[2])
        cm, csd = float(np.mean(cs)), float(np.std(cs, ddof=1))
        z = (e1 - cm) / csd if csd > 0 else 0.0
        print(f"   enter next minute, TP {TP_ATR}/SL {SL_ATR} xATR:  "
              f"n={n1}  WR {w1:.1f}%  EV {e1:+.3f} R   "
              f"vs control {cm:+.3f}   z {z:+.2f}"
              f"{'   <<<' if abs(z) >= 2 else ''}")

    print(f"\n{'-'*86}")
    print("  Every fill above is charged the CALM quoted spread and filled at the")
    print("  next minute's open with no slippage. Real fills into a vertical move")
    print("  are worse than this on both counts, so these numbers are a CEILING.")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
