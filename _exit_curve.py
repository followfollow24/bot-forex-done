#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_exit_curve.py -- when is the best moment to get out? Answer the whole
curve, not a handful of rules.

_exit_modes.py compared ten named exits and found a monotone trend --
longer held better, right up to the 40-minute cap it happened to use.
A trend that runs into the edge of the test is not an answer: the peak
could be at 40 minutes, or at two hours, and that run could not tell.

So this drops the named rules and measures the thing underneath. For
every day, the position is priced at EVERY minute from 1 to 120, and the
averages are plotted against holding time. The top of that curve is the
best exit, and no rule can beat it -- a stall or speed rule is only ever
an attempt to approximate this shape without knowing the future.

WHAT MAKES OR BREAKS THE ANSWER: whether the peak sits in the same place
in both halves. A curve that peaks at minute 12 in one half and minute 70
in the other has no optimum, only noise, and the honest output is then
"pick on other grounds", not a number. Both halves are therefore printed
side by side and their peaks named separately.

The stall family is swept again alongside, out to ten minutes, because a
stall adapts to each day while a fixed time cannot -- if adapting is
worth anything, it shows as a stall beating the best fixed time.

Entry is the live bot's: reference tick at 19:30:00, direction at +3s.
Only the exit varies.

Usage:  python _exit_curve.py [symbol] [days]
"""
import sys
from datetime import datetime, timedelta, timezone

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)"); sys.exit(1)

import numpy as np

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "XAUAUDm"
DAYS = int(sys.argv[2]) if len(sys.argv) > 2 else 400
DECIDE, SL_ATR, LOT, THAI, TARGET = 3.0, 3.0, 0.05, 7, (19, 30)
WINDOW_MIN = 125
MINUTES = list(range(1, 31)) + [35, 40, 45, 50, 60, 75, 90, 105, 120]
STALLS = [30, 60, 90, 120, 180, 240, 300, 420, 600]


def h1_atr_at(sym, when_srv, n=14):
    r = mt5.copy_rates_range(sym, mt5.TIMEFRAME_H1,
                             when_srv - timedelta(hours=40), when_srv)
    if r is None or len(r) < n + 1:
        return None
    trs = [max(float(r[i]["high"]) - float(r[i]["low"]),
               abs(float(r[i]["high"]) - float(r[i - 1]["close"])),
               abs(float(r[i]["low"]) - float(r[i - 1]["close"])))
           for i in range(1, len(r))]
    return sum(trs[-n:]) / n


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed"); return 2
    info = mt5.symbol_info(SYMBOL)
    if info is None:
        print(f"[ERROR] {SYMBOL} not found"); return 2
    mt5.symbol_select(SYMBOL, True)
    spread = info.spread * info.point
    tkn = mt5.symbol_info_tick(SYMBOL)
    off = int(round((tkn.time - datetime.now(timezone.utc).timestamp()) / 3600.0))
    per_pt = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, SYMBOL, LOT,
                                   tkn.ask, tkn.ask + 1.0) or 0.0

    curve, stallres = [], []
    today = (datetime.now(timezone.utc) + timedelta(hours=THAI)).date()
    for back in range(DAYS, 0, -1):
        d = today - timedelta(days=back)
        if d.weekday() >= 5:
            continue
        s_utc = datetime(d.year, d.month, d.day, TARGET[0] - THAI, TARGET[1],
                         tzinfo=timezone.utc)
        s_srv = s_utc + timedelta(hours=off)
        tk = mt5.copy_ticks_range(SYMBOL, s_srv,
                                  s_srv + timedelta(minutes=WINDOW_MIN),
                                  mt5.COPY_TICKS_ALL)
        if tk is None or len(tk) < 200:
            continue
        t0 = int(s_srv.timestamp() * 1000)
        sec = (tk["time_msc"].astype(np.int64) - t0) / 1000.0
        bid = tk["bid"].astype(float); ask = tk["ask"].astype(float)
        mid = np.where(ask > 0, (bid + ask) / 2.0, bid)
        m = sec <= DECIDE
        if m.sum() < 2:
            continue
        moved = float(mid[m][-1] - mid[0])
        if moved == 0:
            continue
        atr = h1_atr_at(SYMBOL, s_srv)
        if not atr:
            continue
        s = 1 if moved > 0 else -1
        i_e = int(np.where(m)[0][-1])
        entry = float(ask[i_e]) if s > 0 else float(bid[i_e])
        sl = entry - s * SL_ATR * atr
        fs, fp = sec[i_e:], mid[i_e:]
        if len(fs) < 50 or fs[-1] < 60 * max(MINUTES) * 0.5:
            pass                                  # short day: still usable

        # stop first, if it was ever touched
        hit = np.where((fp <= sl) if s > 0 else (fp >= sl))[0]
        t_sl = float(fs[hit[0]]) if len(hit) else float("inf")

        row = []
        for mnt in MINUTES:
            t = mnt * 60.0
            if t > fs[-1]:
                row.append(np.nan); continue
            if t_sl <= t:
                row.append((sl - entry) * s - spread); continue
            k = int(np.searchsorted(fs, t)) - 1
            row.append((float(fp[max(k, 0)]) - entry) * s - spread)
        curve.append(row)

        srow, best, best_t = [], entry, fs[0]
        for st in STALLS:
            out = None
            b, bt = entry, fs[0]
            for k in range(len(fs)):
                p, t = float(fp[k]), float(fs[k])
                if ((p <= sl) if s > 0 else (p >= sl)):
                    out = (sl - entry) * s - spread; break
                if (p > b) if s > 0 else (p < b):
                    b, bt = p, t
                elif t - bt >= st:
                    out = (p - entry) * s - spread; break
            srow.append(out if out is not None
                        else (float(fp[-1]) - entry) * s - spread)
        stallres.append(srow)

    n = len(curve)
    print("=" * 88)
    print(f" EXIT-TIME CURVE -- {SYMBOL}   entry +{DECIDE}s, SL {SL_ATR}xATR, "
          f"lot {LOT}")
    print(f" {n} days   spread {spread:.2f}   1.0 pt on {LOT} lot = "
          f"{per_pt:.2f} {info.currency_profit}")
    print("=" * 88)
    if n < 30:
        print(" not enough days"); mt5.shutdown(); return 0

    A = np.array(curve, dtype=float)
    S = np.array(stallres, dtype=float)
    half = n // 2

    def col(arr, j, lo, hi):
        v = arr[lo:hi, j]
        v = v[~np.isnan(v)]
        return v

    print(f"\nAVERAGE RESULT BY HOLDING TIME  (per trade, in points)\n")
    print(f"{'hold':>7}{'n tr':>6}{'train':>9}{'n te':>6}{'TEST':>9}"
          f"{'both':>9}{'win both':>10}{'total$ both':>13}")
    print("-" * 70)
    best = {"train": (None, -9e9), "TEST": (None, -9e9), "both": (None, -9e9)}
    for j, mnt in enumerate(MINUTES):
        tr, te = col(A, j, 0, half), col(A, j, half, n)
        bo = col(A, j, 0, n)
        if len(bo) < 20:
            continue
        for k, v, arr in (("train", tr.mean(), tr), ("TEST", te.mean(), te),
                          ("both", bo.mean(), bo)):
            if len(arr) >= 20 and v > best[k][1]:
                best[k] = (mnt, v)
        mark = ""
        print(f"{mnt:>6}m{len(tr):>6}{tr.mean():>+9.2f}{len(te):>6}"
              f"{te.mean():>+9.2f}{bo.mean():>+9.2f}"
              f"{100.0*np.mean(bo > 0):>9.0f}%{bo.sum()*per_pt:>+13.0f}{mark}")
    print("-" * 70)
    print(f"  peak train : {best['train'][0]} min ({best['train'][1]:+.2f} pts)")
    print(f"  peak TEST  : {best['TEST'][0]} min ({best['TEST'][1]:+.2f} pts)")
    print(f"  peak both  : {best['both'][0]} min ({best['both'][1]:+.2f} pts)")
    gap = abs((best['train'][0] or 0) - (best['TEST'][0] or 0))
    print(f"  the two halves peak {gap} minutes apart -- "
          + ("close enough to call an optimum" if gap <= 10 else
             "NO STABLE OPTIMUM, the peak is noise"))

    print(f"\nSTALL RULES (adapt to each day) vs the best fixed time\n")
    print(f"{'stall':>8}{'train':>9}{'TEST':>9}{'both':>9}{'win':>7}"
          f"{'total$':>10}")
    print("-" * 52)
    for j, st in enumerate(STALLS):
        tr, te, bo = S[:half, j], S[half:, j], S[:, j]
        print(f"{st:>7}s{tr.mean():>+9.2f}{te.mean():>+9.2f}{bo.mean():>+9.2f}"
              f"{100.0*np.mean(bo > 0):>6.0f}%{bo.sum()*per_pt:>+10.0f}")
    print("-" * 52)
    print("  A stall only earns its complexity if it beats the best fixed")
    print("  time above. If it does not, hold the clock and stop watching.")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
