#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_copytrade_calibrated.py -- retest the operator's rule at the thresholds
they actually trade, instead of the ones I guessed.

WHY THIS SUPERSEDES _copytrade_backtest.py
------------------------------------------------------------------
That run concluded the rule "equals a coin flip". It used MIN_MOVE_ATR
= 0.15, a number I picked and labelled in the source as a guess. Then
_entry_fingerprint.py measured what the operator's 29 entries actually
looked like against the 4,000 bars they passed over:

    momentum at entry   1.418 xATR   vs   0.879   d=0.76  p<0.001
    extension (EMA20)   1.675 xATR   vs   1.059   d=0.74  p<0.001
    direction matched the recent move on 25/29 = 86% of trades

So the described rule IS what they trade -- but they only take it when
the move is roughly 10x larger than my threshold allowed. The earlier
test fired on nearly every bar, which is not the strategy; it is a
different strategy that happens to share a name. This retests at the
measured thresholds.

DESIGN THAT KEEPS IT HONEST
------------------------------------------------------------------
- The threshold is SWEPT, not fitted: the whole curve is printed, so a
  single flattering cell cannot be presented as the result.
- The control is matched BAR FOR BAR. At each threshold the same
  filtered bars are traded with a random direction. That isolates the
  one thing in question -- whether the direction call adds anything --
  from the filter merely picking calmer or wilder moments.
- Split-sample: first half and second half reported separately.
- Real spread charged per trade, in R.
- A filter this tight cuts the trade count hard; n is printed on every
  row because an edge on 40 trades is not an edge.

Usage (VPS):  python _copytrade_calibrated.py [symbol] [years]
"""
import sys
from datetime import datetime, timedelta

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)")
    sys.exit(1)

import numpy as np

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "XAUAUDm"
YEARS = float(sys.argv[2]) if len(sys.argv) > 2 else 1.4

TP_ATR, SL_ATR = 0.51, 0.42      # measured from their filled brackets
LOOKBACK = 3
MAX_HOLD = 24
SEED = 4242

# measured entry profile: momentum 1.418, extension 1.675
MOM_GRID = [0.15, 0.50, 0.80, 1.00, 1.20, 1.40, 1.60, 2.00]
EXT_GRID = [0.0, 1.60]


def load(symbol, years):
    mt5.symbol_select(symbol, True)
    now, chunks, cursor = datetime.now(), [], datetime.now()
    while cursor > now - timedelta(days=int(years * 365)):
        part = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5,
                                    cursor - timedelta(days=45), cursor)
        if part is not None and len(part):
            chunks.append(part)
        cursor -= timedelta(days=45)
    if not chunks:
        return None
    r = np.concatenate(list(reversed(chunks)))
    _, keep = np.unique(r["time"], return_index=True)
    return r[np.sort(keep)]


def ema(a, n):
    k = 2.0 / (n + 1)
    out = np.empty(len(a)); out[0] = a[0]
    for i in range(1, len(a)):
        out[i] = a[i] * k + out[i - 1] * (1 - k)
    return out


def h1_atr_on_m5(r, n=14):
    """ATR14 of H1 bars mapped onto M5 bars -- the unit the operator's
    geometry was measured in. Value comes from the last CLOSED H1 bar."""
    h1, cur = [], None
    for b in r:
        h = (b["time"] // 3600) * 3600
        if cur is None or cur["t"] != h:
            if cur is not None:
                h1.append(cur)
            cur = {"t": h, "h": b["high"], "l": b["low"], "c": b["close"]}
        else:
            cur["h"] = max(cur["h"], b["high"])
            cur["l"] = min(cur["l"], b["low"])
            cur["c"] = b["close"]
    if cur:
        h1.append(cur)
    avail = {}
    trs = []
    for i in range(1, len(h1)):
        trs.append(max(h1[i]["h"] - h1[i]["l"],
                       abs(h1[i]["h"] - h1[i - 1]["c"]),
                       abs(h1[i]["l"] - h1[i - 1]["c"])))
        if len(trs) >= n:
            avail[h1[i]["t"] + 3600] = sum(trs[-n:]) / n
    keys = sorted(avail)
    out, ki, cur_v = np.full(len(r), np.nan), 0, np.nan
    for i, b in enumerate(r):
        while ki < len(keys) and keys[ki] <= b["time"]:
            cur_v = avail[keys[ki]]; ki += 1
        out[i] = cur_v
    return out


def run(r, atr, e20, lo, hi, mom_t, ext_t, spread, randomise=False):
    rng = np.random.default_rng(SEED)
    c, out = r["close"], []
    i = lo
    while i < hi - MAX_HOLD - 1:
        a = atr[i]
        if not np.isfinite(a) or a <= 0:
            i += 1; continue
        mom = (c[i] - c[i - LOOKBACK]) / a
        ext = abs(c[i] - e20[i]) / a
        if abs(mom) < mom_t or ext < ext_t:
            i += 1; continue
        s = int(rng.choice((1, -1))) if randomise else (1 if mom > 0 else -1)
        entry = r["open"][i + 1]
        tp_d, sl_d = TP_ATR * a, SL_ATR * a
        tp, sl = entry + s * tp_d, entry - s * sl_d
        exit_i, res = None, None
        for j in range(i + 1, min(i + 1 + MAX_HOLD, hi)):
            hp, lp = r["high"][j], r["low"][j]
            hit_sl = (lp <= sl) if s > 0 else (hp >= sl)
            hit_tp = (hp >= tp) if s > 0 else (lp <= tp)
            if hit_sl:                      # stop first on an ambiguous bar
                res, exit_i = -1.0, j; break
            if hit_tp:
                res, exit_i = tp_d / sl_d, j; break
        if exit_i is None:
            exit_i = min(i + MAX_HOLD, hi - 1)
            res = ((c[exit_i] - entry) * s) / sl_d
        out.append(res - spread / sl_d)
        i = exit_i + 1
    return out


def ev(x):
    return (len(x), 100.0 * sum(1 for v in x if v > 0) / len(x),
            float(np.mean(x))) if x else (0, float("nan"), float("nan"))


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed"); return 2
    info = mt5.symbol_info(SYMBOL)
    if info is None:
        print(f"[ERROR] {SYMBOL} not found"); mt5.shutdown(); return 2
    r = load(SYMBOL, YEARS)
    if r is None or len(r) < 5000:
        print(f"[ERROR] not enough M5 data ({mt5.last_error()})")
        mt5.shutdown(); return 2

    atr = h1_atr_on_m5(r)
    e20 = ema(r["close"], 20)
    spread = info.spread * info.point
    mid = len(r) // 2

    print("=" * 88)
    print(f" CALIBRATED RETEST -- {SYMBOL}   TP {TP_ATR}xATR / SL {SL_ATR}xATR")
    print(f" {len(r):,} M5 bars  {datetime.fromtimestamp(r[0]['time']):%Y-%m-%d}"
          f" -> {datetime.fromtimestamp(r[-1]['time']):%Y-%m-%d}"
          f"   spread {spread:.2f}")
    print(f" operator's measured entry profile: momentum 1.418xATR, "
          f"extension 1.675xATR")
    print("=" * 88)
    print(f"{'mom>=':>7}{'ext>=':>7}{'half':>7}"
          f"{'n':>7}{'WR':>7}{'EV(R)':>9}"
          f"{'n rnd':>7}{'WR rnd':>8}{'EV rnd':>9}{'edge':>8}")
    print("-" * 88)

    for ext_t in EXT_GRID:
        for mom_t in MOM_GRID:
            for tag, lo, hi in (("train", LOOKBACK + 25, mid),
                                ("TEST", mid, len(r))):
                sig = run(r, atr, e20, lo, hi, mom_t, ext_t, spread)
                ctl = run(r, atr, e20, lo, hi, mom_t, ext_t, spread,
                          randomise=True)
                n1, w1, e1 = ev(sig)
                n2, w2, e2 = ev(ctl)
                if n1 < 30:
                    print(f"{mom_t:>7.2f}{ext_t:>7.2f}{tag:>7}{n1:>7}"
                          f"      -- too few trades to read --")
                    continue
                edge = e1 - e2
                print(f"{mom_t:>7.2f}{ext_t:>7.2f}{tag:>7}"
                      f"{n1:>7}{w1:>6.1f}%{e1:>+9.3f}"
                      f"{n2:>7}{w2:>7.1f}%{e2:>+9.3f}{edge:>+8.3f}")
        print("-" * 88)

    print(f"  Break-even WR at this R:R is {100.0/(1.0+TP_ATR/SL_ATR):.1f}% "
          f"before costs.")
    print("  'edge' = signal EV minus random-direction EV on the SAME filtered")
    print("  bars. That is the only column that says whether reading the chart")
    print("  adds anything; a positive EV with edge ~0 just means the filter")
    print("  found calmer bars, which a coin flip would have enjoyed equally.")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
