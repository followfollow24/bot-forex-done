#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_session_trend.py -- test the operator's own observation:

  "from about 19:30 Thai time onward the chart runs a long way in one
   direction. Enter with it repeatedly until it stops, then take profit."

That is three separate claims, and they are not equally likely to be
true. This measures each one on its own rather than testing the bundle.

  1. IS IT TRUE that price trends more persistently in that window?
     Measured as directional efficiency: |net move| / |path travelled|
     over the following hour. A pure trend scores 1.0, pure chop scores
     near 0. Reported per SERVER hour, with the Thai-time mapping
     printed so the window is unambiguous.

  2. IS IT TRADEABLE? Same momentum entry as the calibrated retest,
     split by hour, each hour controlled against 20 random-direction
     draws on that hour's own bars. An hour cannot look good merely
     because it has more or bigger moves -- the control has those too.

  3. DOES "RIDE IT UNTIL IT STOPS" BEAT A FIXED TP? The operator's
     filled brackets were TP 0.51 xATR, but what they describe is a
     momentum exit, which is a different strategy. Three exits are run
     on the SAME entries so the exit is the only thing that varies.

WHAT THIS DELIBERATELY DOES NOT TEST: firing several entries into one
move. That needs no simulation -- N entries on one move is one larger
position with an averaged entry, so it multiplies the per-trade EV and
multiplies the drawdown with it. If (2) comes back negative, stacking
makes it worse, not better. The margin arithmetic for stacking on the
live account is printed at the end so the size limit is a number rather
than a feeling.

Usage (VPS):  python _session_trend.py [symbol] [years]
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
YEARS = float(sys.argv[2]) if len(sys.argv) > 2 else 1.4

TP_ATR, SL_ATR = 0.51, 0.42
LOOKBACK, MAX_HOLD = 3, 24
HORIZON = 12          # bars ahead for the persistence measure (1 hour)
MOM_HOURLY = 0.50     # loose enough to give each hour a readable n
N_CTRL = 20
THAI = 7              # Thailand is UTC+7


def load(symbol, years):
    mt5.symbol_select(symbol, True)
    chunks, cursor, stop = [], datetime.now(), datetime.now() - timedelta(days=int(years * 365))
    while cursor > stop:
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


def trade(r, atr, c, i, s, spread, exit_mode):
    """One trade opened at bar i+1 in direction s. Returns result in R."""
    a = atr[i]
    entry = r["open"][i + 1]
    tp_d, sl_d = TP_ATR * a, SL_ATR * a
    tp, sl = entry + s * tp_d, entry - s * sl_d
    peak = entry
    for j in range(i + 1, min(i + 1 + MAX_HOLD, len(r))):
        hp, lp = r["high"][j], r["low"][j]
        if (lp <= sl) if s > 0 else (hp >= sl):
            return -1.0 - spread / sl_d, j
        if exit_mode == "fixed":
            if (hp >= tp) if s > 0 else (lp <= tp):
                return tp_d / sl_d - spread / sl_d, j
        elif exit_mode == "trail":
            peak = max(peak, hp) if s > 0 else min(peak, lp)
            stop = peak - s * sl_d
            if (r["low"][j] <= stop) if s > 0 else (r["high"][j] >= stop):
                return ((stop - entry) * s) / sl_d - spread / sl_d, j
        elif exit_mode == "momo":
            # ride while the move continues; leave when it stops
            if j - LOOKBACK >= 0 and (c[j] - c[j - LOOKBACK]) * s < 0:
                return ((c[j] - entry) * s) / sl_d - spread / sl_d, j
    j = min(i + MAX_HOLD, len(r) - 1)
    return ((c[j] - entry) * s) / sl_d - spread / sl_d, j


def run(r, atr, e20, c, spread, mom_t, hours=None, seed=None, exit_mode="fixed"):
    rng = np.random.default_rng(seed)
    out, i = [], LOOKBACK + 25
    while i < len(r) - MAX_HOLD - 1:
        a = atr[i]
        if not np.isfinite(a) or a <= 0:
            i += 1; continue
        if hours is not None and ((r["time"][i] // 3600) % 24) not in hours:
            i += 1; continue
        mom = (c[i] - c[i - LOOKBACK]) / a
        if abs(mom) < mom_t:
            i += 1; continue
        s = int(rng.choice((1, -1))) if seed is not None else (1 if mom > 0 else -1)
        res, j = trade(r, atr, c, i, s, spread, exit_mode)
        out.append(res); i = j + 1
    return out


def ev(x):
    return ((len(x), 100.0 * sum(1 for v in x if v > 0) / len(x), float(np.mean(x)))
            if x else (0, float("nan"), float("nan")))


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed"); return 2
    info = mt5.symbol_info(SYMBOL)
    if info is None:
        print(f"[ERROR] {SYMBOL} not found"); return 2
    r = load(SYMBOL, YEARS)
    if r is None or len(r) < 5000:
        print(f"[ERROR] not enough M5 data ({mt5.last_error()})"); return 2

    atr, c = h1_atr_on_m5(r), r["close"]
    e20 = ema(c, 20)
    spread = info.spread * info.point

    # server clock vs UTC -- assuming this is guesswork is how a session
    # study ends up studying the wrong session
    tick = mt5.symbol_info_tick(SYMBOL)
    utc_now = datetime.now(timezone.utc).timestamp()
    offset = int(round((tick.time - utc_now) / 3600.0)) if tick else 0

    print("=" * 84)
    print(f" SESSION TREND STUDY -- {SYMBOL}   {len(r):,} M5 bars   spread {spread:.2f}")
    print(f" {datetime.fromtimestamp(r[0]['time']):%Y-%m-%d} -> "
          f"{datetime.fromtimestamp(r[-1]['time']):%Y-%m-%d}")
    print(f" broker server clock = UTC{offset:+d}   Thailand = UTC+{THAI}")
    print(f" 19:30 Thai = {(19 - THAI + offset) % 24:02d}:30 server "
          f"= {(19 - THAI) % 24:02d}:30 UTC")
    print("=" * 84)

    # ---- 1. is the operator's observation true? -------------------------
    print("\n1. DIRECTIONAL PERSISTENCE BY HOUR  (next 1h: |net move| / |path|)")
    print("   1.00 = a clean one-way run, 0.20 = chop. This is the operator's")
    print("   claim measured directly, with no strategy attached.\n")
    print(f"   {'server':>7}{'Thai':>7}{'n':>8}{'efficiency':>12}{'move xATR':>11}")
    rows = []
    for h in range(24):
        idx = [i for i in range(30, len(r) - HORIZON)
               if (r["time"][i] // 3600) % 24 == h and np.isfinite(atr[i]) and atr[i] > 0]
        if len(idx) < 200:
            continue
        ers, mv = [], []
        for i in idx:
            seg = c[i:i + HORIZON + 1]
            path = float(np.sum(np.abs(np.diff(seg))))
            if path > 0:
                ers.append(abs(float(seg[-1] - seg[0])) / path)
                mv.append(abs(float(seg[-1] - seg[0])) / atr[i])
        rows.append((h, len(ers), float(np.mean(ers)), float(np.mean(mv))))
    best = max(rows, key=lambda t: t[2])[0] if rows else None
    for h, n, e, m in rows:
        thai = (h - offset + THAI) % 24
        star = "  <-- most trending" if h == best else ""
        print(f"   {h:>5}:00{thai:>5}:00{n:>8}{e:>12.3f}{m:>11.2f}{star}")

    # ---- 2. is that window tradeable? -----------------------------------
    print(f"\n2. ENTRY EV BY HOUR  (momentum >= {MOM_HOURLY} xATR, fixed TP/SL,")
    print(f"   each hour vs {N_CTRL} random-direction runs on THAT HOUR's bars)\n")
    print(f"   {'server':>7}{'Thai':>7}{'n':>7}{'WR':>7}{'EV(R)':>9}"
          f"{'ctl EV':>9}{'z':>7}")
    for h, _, _, _ in rows:
        sig = run(r, atr, e20, c, spread, MOM_HOURLY, hours={h})
        n1, w1, e1 = ev(sig)
        if n1 < 40:
            continue
        cs = [ev(run(r, atr, e20, c, spread, MOM_HOURLY, hours={h}, seed=s))[2]
              for s in range(1, N_CTRL + 1)]
        cm, csd = float(np.mean(cs)), float(np.std(cs, ddof=1))
        z = (e1 - cm) / csd if csd > 0 else 0.0
        flag = "  <<<" if abs(z) >= 2 else ""
        print(f"   {h:>5}:00{(h - offset + THAI) % 24:>5}:00{n1:>7}{w1:>6.1f}%"
              f"{e1:>+9.3f}{cm:>+9.3f}{z:>+7.2f}{flag}")

    # ---- 3. does riding it beat taking a fixed profit? -------------------
    win = {h for h in range(24) if 12 <= ((h - offset) % 24) <= 20}
    print(f"\n3. EXIT COMPARISON on the SAME entries, restricted to the operator's")
    print(f"   window (19:00-03:00 Thai = {sorted(x for x in win)} server hours)\n")
    print(f"   {'exit':>10}{'n':>7}{'WR':>7}{'EV(R)':>9}{'ctl EV':>9}{'z':>7}")
    for mode, label in (("fixed", "TP 0.51"), ("momo", "ride"), ("trail", "trail")):
        sig = run(r, atr, e20, c, spread, MOM_HOURLY, hours=win, exit_mode=mode)
        n1, w1, e1 = ev(sig)
        if n1 < 40:
            print(f"   {label:>10}{n1:>7}   -- too few --"); continue
        cs = [ev(run(r, atr, e20, c, spread, MOM_HOURLY, hours=win, seed=s,
                     exit_mode=mode))[2] for s in range(1, N_CTRL + 1)]
        cm, csd = float(np.mean(cs)), float(np.std(cs, ddof=1))
        z = (e1 - cm) / csd if csd > 0 else 0.0
        print(f"   {label:>10}{n1:>7}{w1:>6.1f}%{e1:>+9.3f}{cm:>+9.3f}{z:>+7.2f}")

    # ---- 4. what stacking actually costs on THIS account -----------------
    acct = mt5.account_info()
    print("\n4. HOW MANY ENTRIES THIS ACCOUNT CAN ACTUALLY STACK")
    if acct and tick:
        eq = float(acct.equity)
        m1 = mt5.order_calc_margin(mt5.ORDER_TYPE_BUY, SYMBOL, 0.05, tick.ask)
        print(f"   equity {eq:,.2f} {acct.currency}   margin for 0.05 lot: "
              f"{m1:,.2f}" if m1 else "   margin calc unavailable")
        if m1 and m1 > 0:
            print(f"   -> {int(eq // m1)} entries of 0.05 lot uses the WHOLE account "
                  f"as margin, with no room left for the position to move against you.")
            a = float(np.nanmedian(atr))
            per = SL_ATR * a / (info.point or 1e-9)
            print(f"   -> all of them stop out together: they are one position with")
            print(f"      an averaged entry, not {int(eq // m1)} independent bets.")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
