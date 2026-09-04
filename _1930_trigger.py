#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_1930_trigger.py -- "join it once it is already moving, leave when it stops".

The operator circled a 4 Sep M15 candle: in near the the top of a ~90
point drop, out at the bottom. Not an entry at 19:30 on the clock -- an
entry once the move is visibly underway.

THE HONEST DIFFICULTY, STATED BEFORE THE NUMBERS
----------------------------------------------------------------------
The circled entry sits about 9 points below that candle's open. At the
instant price is there, it is indistinguishable from the many small
dips that go nowhere; "this candle will run 90 points" is information
that exists only afterwards. So the rule cannot be "enter on the big
move" -- it has to be "enter once it has moved X, and accept that the
same trigger also fires on the days it fizzles". Whether the winners
pay for the fizzles is the entire question, and it is answerable.

So: from 19:30 Thai, watch. The first time price is X x ATR(H1) away
from the 19:30 open, join in that direction. Leave when it stops.

X is swept from small to large. A small X catches the real runs but also
every false start; a large X only fires on days already committed, but
gives away the part of the move the operator wants. The trade-off is the
result, so both ends are printed rather than one chosen.

ALSO REPORTED, because it is what the picture is really claiming:
  - how often the trigger fires at all (days per 100)
  - MFE: how far it runs in your favour AFTER the trigger, on average
    and at the median. If the median is small while the mean is large,
    the strategy is a lottery on rare days like the circled one, and the
    equity curve will not feel like that picture looks.
  - the last few days individually, including 4 Sep, so the example can
    be checked against the rule rather than remembered.

Usage (VPS):  python _1930_trigger.py [symbol] [months] [M5|M1]
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
MONTHS = float(sys.argv[2]) if len(sys.argv) > 2 else 14.0
TF = (sys.argv[3] if len(sys.argv) > 3 else "M5").upper()
STEP = 1 if TF == "M1" else 5          # minutes per bar
THAI, TARGET = 7, (19, 30)
WATCH = 30            # minutes after 19:30 in which the trigger may fire
PATIENCE = 2          # bars closing against you before you call it stopped
TRIGGERS = [0.10, 0.20, 0.35, 0.50, 0.80]      # xATR(H1) from the 19:30 open
CAP = 120             # minutes max in a trade
LOT = 0.05
N_CTRL = 20


def load_bars(symbol, months):
    """M1 history on this terminal is short (~72 trading days), which is
    not enough to split a daily rule in half. M5 reaches 364 days and,
    on a 5-minute clock, 'the first bar that closes against you' stops
    firing three minutes into every trade."""
    mt5.symbol_select(symbol, True)
    chunks, cursor = [], datetime.now()
    stop = datetime.now() - timedelta(days=int(months * 30.5))
    while cursor > stop:
        tfc = mt5.TIMEFRAME_M1 if TF == "M1" else mt5.TIMEFRAME_M5
        p = mt5.copy_rates_range(symbol, tfc,
                                 cursor - timedelta(days=30), cursor)
        if p is not None and len(p):
            chunks.append(p)
        cursor -= timedelta(days=30)
    if not chunks:
        return None
    r = np.concatenate(list(reversed(chunks)))
    _, keep = np.unique(r["time"], return_index=True)
    return r[np.sort(keep)]


def h1_atr_on(r, n=14):
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


def find_trigger(r, i0, ref, thresh):
    """First minute within WATCH where price is `thresh` away from ref.
    Entry is the NEXT bar's open -- you cannot fill on the bar that is
    still forming when the level is touched."""
    for j in range(i0, min(i0 + WATCH // STEP, len(r) - 2)):
        if r["high"][j] - ref >= thresh:
            return j + 1, 1
        if ref - r["low"][j] >= thresh:
            return j + 1, -1
    return None, 0


def ride(r, e, s, spread):
    """Hold while it runs; out once PATIENCE bars have closed against it,
    so one pullback bar does not end the trade. Returns (net points,
    minutes held, best excursion seen)."""
    o, c = r["open"], r["close"]
    entry, mfe, opp = o[e], 0.0, 0
    for j in range(e, min(e + CAP // STEP, len(r))):
        best = (r["high"][j] if s > 0 else r["low"][j])
        mfe = max(mfe, (best - entry) * s)
        if j > e:
            opp = opp + 1 if (c[j] - o[j]) * s < 0 else 0
            if opp >= PATIENCE:
                return (c[j] - entry) * s - spread, (j - e + 1) * STEP, mfe
    j = min(e + CAP // STEP, len(r)) - 1
    return (c[j] - entry) * s - spread, (j - e + 1) * STEP, mfe


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed"); return 2
    info = mt5.symbol_info(SYMBOL)
    if info is None:
        print(f"[ERROR] {SYMBOL} not found"); return 2
    r = load_bars(SYMBOL, MONTHS)
    if r is None or len(r) < 15000:
        print(f"[ERROR] not enough M1 data ({mt5.last_error()})"); return 2

    atr, spread = h1_atr_on(r), info.spread * info.point
    _acct = mt5.account_info()
    acct_ccy = _acct.currency if _acct else "?"
    tick = mt5.symbol_info_tick(SYMBOL)
    offh = int(round((tick.time - datetime.now(timezone.utc).timestamp()) / 3600.0)) if tick else 0
    sh, sm = (TARGET[0] - THAI + offh) % 24, TARGET[1]
    per_pt = 0.0
    if tick:
        p = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, SYMBOL, LOT,
                                  tick.ask, tick.ask + 1.0)
        per_pt = float(p) if p else 0.0

    tod = r["time"] % 86400
    days = [int(i) for i in np.where(tod == sh * 3600 + sm * 60)[0]
            if i > 2 and i < len(r) - (CAP + WATCH) // STEP - 2 and np.isfinite(atr[i]) and atr[i] > 0]
    half = len(days) // 2

    print("=" * 96)
    print(f" 19:30 -> WAIT FOR IT TO MOVE -> JOIN -> EXIT WHEN IT STOPS -- {SYMBOL}")
    print(f" {datetime.fromtimestamp(r[0]['time']):%Y-%m-%d} -> "
          f"{datetime.fromtimestamp(r[-1]['time']):%Y-%m-%d}   {len(days)} days   "
          f"spread {spread:.2f}   1.0 pt on {LOT} lot = {per_pt:.2f} {acct_ccy}")
    print(f" {TF} bars; trigger must fire within {WATCH} min of 19:30; "
          f"max {CAP} min in trade; exit after {PATIENCE} bars against")
    print("=" * 96)
    print(f"\n{'trig':>6}{'half':>7}{'fired':>8}{'n':>5}{'held':>7}{'hit':>7}"
          f"{'avg pt':>8}{'total$':>9}{'maxDD$':>9}{'MFE med':>9}{'MFE avg':>9}"
          f"{'ctl':>7}{'z':>6}")
    print("-" * 96)
    for th in TRIGGERS:
        for tag, sub in (("train", days[:half]), ("TEST", days[half:])):
            res, holds, mfes, ctl = [], [], [], [[] for _ in range(N_CTRL)]
            rngs = [np.random.default_rng(s) for s in range(1, N_CTRL + 1)]
            for i in sub:
                e, s = find_trigger(r, i, r["open"][i], th * atr[i])
                if e is None:
                    continue
                pts, hb, mfe = ride(r, e, s, spread)
                res.append(pts); holds.append(hb); mfes.append(mfe)
                for k, g in enumerate(rngs):
                    ctl[k].append(ride(r, e, int(g.choice((1, -1))), spread)[0])
            if len(res) < 30:
                print(f"{th:>6.2f}{tag:>7}{100.0*len(res)/len(sub):>7.0f}%"
                      f"{len(res):>5}   -- too few --")
                continue
            a = np.array(res)
            eq = np.cumsum(a)
            dd = float(np.max(np.maximum.accumulate(eq) - eq))
            cms = [float(np.mean(x)) for x in ctl]
            mu, sd = float(np.mean(cms)), float(np.std(cms, ddof=1))
            z = (a.mean() - mu) / sd if sd > 0 else 0.0
            print(f"{th:>6.2f}{tag:>7}{100.0*len(res)/len(sub):>7.0f}%{len(res):>5}"
                  f"{np.mean(holds):>6.0f}m{100.0*np.mean(a>0):>6.1f}%"
                  f"{a.mean():>+8.2f}{a.sum()*per_pt:>+9.0f}{dd*per_pt:>9.0f}"
                  f"{np.median(mfes):>9.1f}{np.mean(mfes):>9.1f}"
                  f"{mu:>+7.2f}{z:>+6.2f}{'  <<<' if abs(z) >= 2 else ''}")
        print("-" * 96)

    # the operator's own example, and its neighbours
    th = 0.35
    print(f"\nTHE LAST 8 DAYS INDIVIDUALLY  (trigger {th} xATR) -- check 4 Sep here")
    print(f"   {'date (Thai)':>14}{'dir':>5}{'entry':>10}{'exit':>10}"
          f"{'held':>7}{'points':>9}{'at 0.05 lot':>13}{'MFE':>8}")
    for i in days[-8:]:
        e, s = find_trigger(r, i, r["open"][i], th * atr[i])
        d = datetime.fromtimestamp(int(r["time"][i]) - offh * 3600 + THAI * 3600,
                                   timezone.utc)
        if e is None:
            print(f"   {d:%Y-%m-%d %a}{'--':>5}   trigger never fired")
            continue
        pts, hb, mfe = ride(r, e, s, spread)
        print(f"   {d:%Y-%m-%d %a}{'BUY' if s > 0 else 'SELL':>5}"
              f"{r['open'][e]:>10.2f}{r['open'][e]+s*(pts+spread):>10.2f}"
              f"{hb:>6}m{pts:>+9.2f}{pts*per_pt:>+12.0f}${mfe:>8.1f}")

    print("\n  'fired' is the share of days the trigger happened at all.")
    print("  'MFE med' vs 'MFE avg': if the average is far above the median,")
    print("  the profit lives on a few days like the one circled, and most")
    print("  sessions look nothing like that picture.")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
