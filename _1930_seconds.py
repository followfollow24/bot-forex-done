#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_1930_seconds.py -- let it tick for a few SECONDS, only to learn which
way, then get in immediately.

  "let the chart move a little, just to see whether it is a sell or a
   buy. not entering at 19:30:00 -- maybe 19:30:10, something like that."

This is a different rule from everything tested so far, and the
difference is the whole point:

  - entering at 19:30 on the PREVIOUS 5-minute bar   -> lost outright
  - waiting for the 19:30-19:35 bar to CLOSE (5 min) -> beat random in
    all six cells, z +1.77..+2.46, but netted zero after spread
  - waiting for a BIG move to confirm (0.1-0.8 xATR) -> lost again;
    confirmation gave away the informative part of the move

The pattern across those three says the information sits at the very
front of the move and decays fast. If that is right, then reading
direction from ten seconds instead of five minutes should be BETTER than
the best result so far, not worse. This measures exactly that, at the
resolution the operator actually described.

Bars cannot answer it -- the finest is M1, and one M1 bar is already six
times longer than "19:30:10". So this runs on TICK history, sweeping the
observation window from 5 to 60 seconds.

TICK HISTORY IS USUALLY SHORTER THAN BAR HISTORY, so the number of days
that actually came back is printed before any result. A conclusion drawn
from 30 days of ticks is not a conclusion, and this project has already
been burned once by an M1 sample too short to split (36 trades a half,
z +2.89 in one half and -0.33 in the other).

Exits are the ones that worked before -- fixed 15/30/60 minutes, and
"two M5 bars closing against you" -- so only the ENTRY TIMING changes
against the earlier tests.

Usage (VPS):  python _1930_seconds.py [symbol] [days]
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
DAYS = int(sys.argv[2]) if len(sys.argv) > 2 else 500
THAI, TARGET = 7, (19, 30)
SECONDS = [1, 2, 3, 5, 10, 20, 30, 60]
HOLDS = [15, 30, 60]          # minutes
LOT = 0.05
N_CTRL = 20
CAP = 24                      # M5 bars for the "stops running" exit
PATIENCE = 2


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


def ride(r, e, s, spread):
    o, c, opp = r["open"], r["close"], 0
    entry = o[e]
    for j in range(e, min(e + CAP, len(r))):
        if j > e:
            opp = opp + 1 if (c[j] - o[j]) * s < 0 else 0
            if opp >= PATIENCE:
                return (c[j] - entry) * s - spread
    j = min(e + CAP, len(r)) - 1
    return (c[j] - entry) * s - spread


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed"); return 2
    info = mt5.symbol_info(SYMBOL)
    if info is None:
        print(f"[ERROR] {SYMBOL} not found"); return 2
    r = load_m5(SYMBOL, DAYS)
    if r is None:
        print("[ERROR] no M5 bars"); return 2

    spread = info.spread * info.point
    tick = mt5.symbol_info_tick(SYMBOL)
    offh = int(round((tick.time - datetime.now(timezone.utc).timestamp()) / 3600.0)) if tick else 0
    sh, sm = (TARGET[0] - THAI + offh) % 24, TARGET[1]
    per_pt = 0.0
    if tick:
        p = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, SYMBOL, LOT,
                                  tick.ask, tick.ask + 1.0)
        per_pt = float(p) if p else 0.0

    tod = r["time"] % 86400
    slots = [int(i) for i in np.where(tod == sh * 3600 + sm * 60)[0]
             if i > 2 and i < len(r) - CAP - 2]

    # ---- pull one minute of ticks per day -------------------------------
    print("=" * 94)
    print(f" 19:30 + A FEW SECONDS -- {SYMBOL}   spread {spread:.2f}   "
          f"1.0 pt on {LOT} lot = {per_pt:.2f} {info.currency_profit}")
    print(" fetching tick history (this is the part that may be short)...")
    days = []
    for i in slots:
        t0 = int(r["time"][i])
        tk = mt5.copy_ticks_range(SYMBOL,
                                  datetime.fromtimestamp(t0, timezone.utc),
                                  datetime.fromtimestamp(t0 + 61, timezone.utc),
                                  mt5.COPY_TICKS_ALL)
        if tk is None or len(tk) < 5:
            continue
        days.append((i, t0, tk))
    if not days:
        print(f"[ERROR] no tick history returned ({mt5.last_error()})")
        mt5.shutdown(); return 2

    first = datetime.fromtimestamp(days[0][1], timezone.utc)
    last = datetime.fromtimestamp(days[-1][1], timezone.utc)
    print(f" {len(slots)} days have M5 bars at {sh}:{sm:02d} server; "
          f"TICKS came back for {len(days)} of them")
    print(f" tick coverage {first:%Y-%m-%d} -> {last:%Y-%m-%d}")
    if len(days) < 120:
        print(" *** WARNING: fewer than 120 days of ticks. Split-half results")
        print(" *** below are indicative only -- see the M1 lesson in the docstring.")
    print("=" * 94)

    half = len(days) // 2

    print("\nIS THERE ANYTHING TO SEE IN THAT WINDOW?")
    print("  If the price barely moves in the time you are watching, the")
    print("  direction you read is quote flicker, not the market picking a")
    print("  side -- and no exit rule can rescue a coin flip. The spread is")
    print(f"  {spread:.2f}; a window whose move is under that is not a signal.\n")
    print(f"  {'watch':>7}{'days':>7}{'ticks':>8}{'median move':>14}"
          f"{'vs spread':>12}")
    for sec in SECONDS:
        mv, nt, ok = [], [], 0
        for i, t0, tk in days:
            tt = tk["time_msc"].astype(np.int64)
            px = np.where(tk["bid"] > 0, tk["bid"], tk["last"]).astype(float)
            m = tt <= (t0 + sec) * 1000
            if m.sum() < 2:
                continue
            ok += 1
            nt.append(int(m.sum()))
            mv.append(abs(float(px[m][-1] - px[m][0])))
        if not mv:
            print(f"  {sec:>6}s{0:>7}   -- no ticks in this window --")
            continue
        med = float(np.median(mv))
        print(f"  {sec:>6}s{ok:>7}{float(np.median(nt)):>8.0f}{med:>14.2f}"
              f"{med/spread:>11.2f}x{'   <-- below the spread' if med < spread else ''}")

    print(f"\n{'watch':>7}{'exit':>10}{'half':>7}{'n':>5}{'hit':>7}{'avg pt':>9}"
          f"{'total$':>9}{'maxDD$':>9}{'ctl':>8}{'z':>7}")
    print("-" * 94)
    for sec in SECONDS:
        for exit_name in [f"{h}min" for h in HOLDS] + ["stops"]:
            for tag, sub in (("train", days[:half]), ("TEST", days[half:])):
                res, ctl = [], [[] for _ in range(N_CTRL)]
                rngs = [np.random.default_rng(s) for s in range(1, N_CTRL + 1)]
                for i, t0, tk in sub:
                    tt = tk["time_msc"].astype(np.int64)
                    px = np.where(tk["bid"] > 0, tk["bid"], tk["last"]).astype(float)
                    m = tt <= (t0 + sec) * 1000
                    if m.sum() < 2 or (~m).sum() < 1:
                        continue
                    d = px[m][-1] - px[m][0]
                    if d == 0:
                        continue
                    s = 1 if d > 0 else -1
                    entry = float(px[m][-1])

                    def result(sign):
                        if exit_name == "stops":
                            e = i + 1
                            if e >= len(r):
                                return None
                            base = ride(r, e, sign, spread)
                            return base + (r["open"][e] - entry) * sign
                        h = int(exit_name.replace("min", ""))
                        j = i + h // 5
                        if j >= len(r):
                            return None
                        return (r["close"][j] - entry) * sign - spread

                    v = result(s)
                    if v is None:
                        continue
                    res.append(v)
                    for k, g in enumerate(rngs):
                        ctl[k].append(result(int(g.choice((1, -1)))))
                if len(res) < 30:
                    print(f"{sec:>6}s{exit_name:>10}{tag:>7}{len(res):>5}"
                          f"   -- too few --")
                    continue
                a = np.array(res, dtype=float)
                eq = np.cumsum(a)
                dd = float(np.max(np.maximum.accumulate(eq) - eq))
                cms = [float(np.mean(x)) for x in ctl]
                mu, sd = float(np.mean(cms)), float(np.std(cms, ddof=1))
                z = (a.mean() - mu) / sd if sd > 0 else 0.0
                print(f"{sec:>6}s{exit_name:>10}{tag:>7}{len(a):>5}"
                      f"{100.0*np.mean(a>0):>6.1f}%{a.mean():>+9.2f}"
                      f"{a.sum()*per_pt:>+9.0f}{dd*per_pt:>9.0f}"
                      f"{mu:>+8.2f}{z:>+7.2f}{'  <<<' if abs(z) >= 2 else ''}")
        print("-" * 94)
    print("\n  'watch' is how many seconds of ticks decided buy vs sell.")
    print("  The claim being tested is that SHORTER is better -- if the")
    print("  information really sits at the front of the move, 5-10s should")
    print("  beat the 5-minute version, and beat it in both halves.")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
