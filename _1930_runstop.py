#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_1930_runstop.py -- the rule exactly as stated, exit included:

  "at 19:30 see which way it is running, buy that way at that moment,
   and when it stops running, take profit."

The previous test held for fixed times, which was not the rule. The exit
here is the one they described: stay in while it runs, leave when it
stops. Nothing else is added -- no stop-loss, no profit target, no
filter on whether the move is big enough.

WHAT "IT STOPS RUNNING" MEANS is the one thing needing a definition, so
three are run side by side, from most literal to most forgiving, on
IDENTICAL entries:

  first opposite  -- out at the close of the first M5 bar that closes
                     against the position. The plainest reading.
  two opposite    -- out after two consecutive bars close against it,
                     so one pullback bar does not end the trade.
  momentum flips  -- out when the 3-bar move turns against the position.

Average holding time is reported for each, because "until it stops"
should be checked against what the operator actually experiences -- if
it comes out at four minutes, the definition is wrong whatever the P&L
says.

RISK IS REPORTED EVEN THOUGH THE RULE HAS NO STOP, because a rule with
no stop still has a worst day, and an average carried by one lucky
session is not an edge. So: max drawdown in real money, the worst single
day, and what share of all profit the best 5 days account for.

Entry is tested both ways again -- at 19:30 on the prior bar, and at
19:35 on the 19:30 bar -- since that five-minute difference was what
separated a losing version from a positive one last time.

Usage (VPS):  python _1930_runstop.py [symbol] [years]
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
LOT = 0.05
CAP_BARS = 48          # 4h backstop so a trade cannot run to the weekend
N_CTRL = 20
EXITS = ["first opposite", "two opposite", "momentum flips"]


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


def trade(r, e, s, mode, spread):
    """Enter at bar e's open, leave when the run stops. Returns
    (signed points net of spread, bars held)."""
    o, c = r["open"], r["close"]
    entry = o[e]
    opp = 0
    for j in range(e, min(e + CAP_BARS, len(r))):
        bar = c[j] - o[j]
        if mode == "first opposite":
            stop_now = bar * s < 0
        elif mode == "two opposite":
            opp = opp + 1 if bar * s < 0 else 0
            stop_now = opp >= 2
        else:
            stop_now = (j - 3 >= e) and (c[j] - c[j - 3]) * s < 0
        if stop_now and j > e:
            return (c[j] - entry) * s - spread, j - e + 1
    j = min(e + CAP_BARS, len(r)) - 1
    return (c[j] - entry) * s - spread, j - e + 1


def summarise(res, holds, ctl, per_point):
    n = len(res)
    if n == 0:
        return None
    a = np.array(res, dtype=float)
    eq = np.cumsum(a)
    dd = float(np.max(np.maximum.accumulate(eq) - eq))
    top5 = float(np.sum(np.sort(a)[-5:]))
    gross = float(np.sum(a[a > 0]))
    cmeans = [float(np.mean(x)) for x in ctl]
    mu, sd = float(np.mean(cmeans)), float(np.std(cmeans, ddof=1))
    return dict(n=n, hit=100.0 * float(np.sum(a > 0)) / n, avg=float(a.mean()),
                tot=float(a.sum()), hold=float(np.mean(holds)) * 5.0,
                dd=dd, worst=float(a.min()), top5=100.0 * top5 / gross if gross else 0.0,
                ctl=mu, z=(float(a.mean()) - mu) / sd if sd > 0 else 0.0,
                usd=float(a.sum()) * per_point, dd_usd=dd * per_point,
                worst_usd=float(a.min()) * per_point)


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
    tick = mt5.symbol_info_tick(SYMBOL)
    offh = int(round((tick.time - datetime.now(timezone.utc).timestamp()) / 3600.0)) if tick else 0
    sh, sm = (TARGET[0] - THAI + offh) % 24, TARGET[1]
    per_point = 0.0
    if tick:
        p1 = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, SYMBOL, LOT,
                                   tick.ask, tick.ask + 1.0)
        per_point = float(p1) if p1 else 0.0

    tod = r["time"] % 86400
    slot = [int(i) for i in np.where(tod == sh * 3600 + sm * 60)[0]
            if i > 2 and i < len(r) - CAP_BARS - 2]
    half = len(slot) // 2

    print("=" * 94)
    print(f" 19:30 -> RIDE -> EXIT WHEN IT STOPS -- {SYMBOL}")
    print(f" {datetime.fromtimestamp(r[0]['time']):%Y-%m-%d} -> "
          f"{datetime.fromtimestamp(r[-1]['time']):%Y-%m-%d}   {len(slot)} days   "
          f"spread {spread:.2f}   1.0 pt on {LOT} lot = {per_point:.2f} "
          f"{info.currency_profit}")
    print(f" no stop-loss and no target, as asked; 4h backstop only")
    print("=" * 94)

    for ename, doff, eoff in (("A. enter 19:30 (prior bar's direction)", -1, 0),
                              ("B. enter 19:35 (the 19:30 bar's direction)", 0, 1)):
        print(f"\n{ename}")
        print(f"   {'exit rule':>15}{'half':>7}{'n':>5}{'held':>8}{'hit':>7}"
              f"{'avg pt':>8}{'total$':>9}{'maxDD$':>9}{'worst$':>9}"
              f"{'top5%':>7}{'ctl':>7}{'z':>6}")
        for mode in EXITS:
            for tag, sub in (("train", slot[:half]), ("TEST", slot[half:])):
                res, holds, ctl = [], [], [[] for _ in range(N_CTRL)]
                rngs = [np.random.default_rng(s) for s in range(1, N_CTRL + 1)]
                for i in sub:
                    b = i + doff
                    d = c[b] - o[b]
                    if d == 0:
                        continue
                    s = 1 if d > 0 else -1
                    e = i + eoff
                    pts, hb = trade(r, e, s, mode, spread)
                    res.append(pts); holds.append(hb)
                    for k, g in enumerate(rngs):
                        ctl[k].append(trade(r, e, int(g.choice((1, -1))),
                                            mode, spread)[0])
                st = summarise(res, holds, ctl, per_point)
                if not st:
                    continue
                print(f"   {mode:>15}{tag:>7}{st['n']:>5}{st['hold']:>7.0f}m"
                      f"{st['hit']:>6.1f}%{st['avg']:>+8.2f}{st['usd']:>+9.0f}"
                      f"{st['dd_usd']:>9.0f}{st['worst_usd']:>+9.0f}"
                      f"{st['top5']:>6.0f}%{st['ctl']:>+7.2f}{st['z']:>+6.2f}"
                      f"{'  <<<' if abs(st['z']) >= 2 else ''}")

    print("\n   'held' is what the exit rule actually did -- check it against what")
    print("   you experience; a 10-minute average is not 'riding it'.")
    print("   'top5%' is the share of ALL gross profit made on the best 5 days.")
    print("   Above ~50% means the average is a handful of sessions, not an edge.")
    print("   maxDD$ and worst$ are on {} lot with NO stop-loss.".format(LOT))
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
