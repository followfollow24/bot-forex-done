#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_1930_plain.py -- the rule with nothing added.

  "wait for 19:30. if the chart is running up, buy. if it is running
   down, sell. no strategy, nothing else."

Every previous test of this bolted something on: stop distances, target
distances, four different lookbacks. Those were my additions, not the
operator's rule, and each one is a knob that can be blamed for the
result. This version has NO stop, NO target and NO threshold. It asks
the only question the rule actually poses:

    at 19:30, follow whatever it is doing. Where is price later?

If following the move pays, the average has to be positive before any
exit rule exists. If it is zero, no stop or target can create it -- an
exit can only redistribute a return, never manufacture one. So this is
the honest place to settle the question.

TWO READINGS OF "IS RUNNING", because the sentence allows both and they
are genuinely different trades:

  A. at 19:30 exactly -- direction is whatever the bar that just closed
     (19:25-19:30) did, and you are in at the 19:30 open.
  B. wait for it to start -- let the 19:30 bar close, take ITS direction,
     and enter at 19:35. This is closer to "wait and see that it is
     really running", at the cost of five minutes of the move.

Reported per holding time rather than at one horizon, so the answer is
a shape and not a single number I chose. Against 20 random-direction
draws on the same days, and split train/TEST, because a rule with no
parameters can still be lucky in a sample.

Usage (VPS):  python _1930_plain.py [symbol] [years]
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
YEARS = float(sys.argv[2]) if len(sys.argv) > 2 else 4.0
THAI, TARGET = 7, (19, 30)
HOLDS = [15, 30, 60, 120, 240]          # minutes
LOT = 0.05
N_CTRL = 20


def load_m5(symbol, years):
    mt5.symbol_select(symbol, True)
    chunks, cursor = [], datetime.now()
    stop = datetime.now() - timedelta(days=int(years * 365))
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


def report(name, moves, ctl_moves, per_point):
    """moves[h] = list of signed price moves at holding time h."""
    print(f"\n   {name}")
    print(f"   {'hold':>7}{'days':>7}{'went further':>15}{'avg move':>11}"
          f"{'total':>10}{'at 0.05 lot':>14}{'random':>10}{'z':>7}")
    for h in HOLDS:
        m, cm = moves[h], ctl_moves[h]
        if not m:
            continue
        hit = 100.0 * sum(1 for v in m if v > 0) / len(m)
        avg = float(np.mean(m))
        tot = float(np.sum(m))
        cmeans = [float(np.mean(c)) for c in cm]
        mu, sd = float(np.mean(cmeans)), float(np.std(cmeans, ddof=1))
        z = (avg - mu) / sd if sd > 0 else 0.0
        print(f"   {h:>5}m{len(m):>7}{hit:>14.1f}%{avg:>11.2f}"
              f"{tot:>10.0f}{tot*per_point:>13.0f}$"
              f"{mu:>10.2f}{z:>7.2f}{'  <<<' if abs(z) >= 2 else ''}")


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed"); return 2
    info = mt5.symbol_info(SYMBOL)
    if info is None:
        print(f"[ERROR] {SYMBOL} not found"); return 2
    r = load_m5(SYMBOL, YEARS)
    if r is None or len(r) < 20000:
        print(f"[ERROR] not enough M5 data ({mt5.last_error()})"); return 2

    o, c, spread = r["open"], r["close"], info.spread * info.point
    _acct = mt5.account_info()
    acct_ccy = _acct.currency if _acct else "?"
    tick = mt5.symbol_info_tick(SYMBOL)
    off = int(round((tick.time - datetime.now(timezone.utc).timestamp()) / 3600.0)) if tick else 0
    sh, sm = (TARGET[0] - THAI + off) % 24, TARGET[1]

    # what one point of price is worth on LOT lots, from the broker itself
    per_point = 0.0
    if tick:
        p1 = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, SYMBOL, LOT,
                                   tick.ask, tick.ask + 1.0)
        per_point = float(p1) if p1 else 0.0

    tod = r["time"] % 86400
    slot = np.where(tod == sh * 3600 + sm * 60)[0]
    slot = [i for i in slot if i > 2 and i < len(r) - max(HOLDS) // 5 - 2]

    print("=" * 88)
    print(f" 19:30 PLAIN -- {SYMBOL}   no stop, no target, no threshold")
    print(f" {len(r):,} M5 bars  {datetime.fromtimestamp(r[0]['time']):%Y-%m-%d}"
          f" -> {datetime.fromtimestamp(r[-1]['time']):%Y-%m-%d}   "
          f"{len(slot)} days at {TARGET[0]}:{TARGET[1]:02d} Thai "
          f"(= {sh}:{sm:02d} server)")
    print(f" spread {spread:.2f} charged once per trade; 1.0 price point on "
          f"{LOT} lot = {per_point:.2f} {acct_ccy}")
    print("=" * 88)
    print("\n 'went further' = the share of days price was further along in the")
    print(" direction you followed. 50% is a coin flip. Break-even needs more")
    print(" than 50% because the spread is paid either way.")

    for name, dir_off, entry_off in (
            ("A. enter AT 19:30, following the 19:25-19:30 bar", -1, 0),
            ("B. wait for the 19:30 bar to close, enter 19:35", 0, 1)):
        for tag, sub in (("train", slot[:len(slot) // 2]),
                         ("TEST", slot[len(slot) // 2:])):
            moves = {h: [] for h in HOLDS}
            ctl = {h: [[] for _ in range(N_CTRL)] for h in HOLDS}
            rngs = [np.random.default_rng(s) for s in range(1, N_CTRL + 1)]
            for i in sub:
                b = i + dir_off
                d = c[b] - o[b]
                if d == 0:
                    continue
                s = 1 if d > 0 else -1
                e = i + entry_off
                entry = o[e]
                signs = [int(g.choice((1, -1))) for g in rngs]
                for h in HOLDS:
                    j = e + h // 5
                    if j >= len(r):
                        continue
                    raw = c[j] - entry
                    moves[h].append(raw * s - spread)
                    for k, sg in enumerate(signs):
                        ctl[h][k].append(raw * sg - spread)
            report(f"{name}   [{tag}]", moves, ctl, per_point)

    print("\n   An exit rule can only redistribute what is in these columns.")
    print("   If 'avg move' is not positive here, no stop or target makes it so.")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
