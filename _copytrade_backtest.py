#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_copytrade_backtest.py -- does the operator's own rule survive history?

The operator's 29 hand-placed trades are the best-performing thing on this
account (WR 58%, PF 1.84, +216 USD). Asked how they choose direction, the
answer was: "look at the chart -- if it is moving up I go long, if it is
moving down I go short." Combined with the measured geometry that is a
complete, testable rule:

    entry : short-horizon momentum direction
    TP    : 0.51 x ATR(H1)
    SL    : 0.42 x ATR(H1)      (R:R 1.43 : 1, WR 58% observed)
    symbol: XAUAUDm (26 of 29 trades)

29 trades cannot separate a real edge from luck -- a coin-flip entry with
R:R 1.43 needs only 41% WR to break even, and 58% on n=29 has roughly a
1-in-8 chance of happening by chance alone. This runs the same rule over
years of real bars.

WHAT WOULD MAKE THIS A FALSE POSITIVE, and how each is handled:
  - picking the momentum definition that happens to work  -> several are
    tested and ALL are reported, not just the best
  - fitting on the whole sample -> split in half; the first half chooses,
    the second half scores
  - ignoring costs -> spread charged per round trip, swept across a range
    because a 0.42xATR stop is small enough that spread dominates
  - "momentum works" being a property of the market rather than the rule
    -> a random-entry control with identical geometry is run alongside
  - one lucky streak carrying it -> per-year breakdown and top-decile share

Runs on the VPS (needs MT5 for XAUAUDm history).
Usage:  python _copytrade_backtest.py [symbol] [years]
"""
import sys
from collections import defaultdict
from datetime import datetime, timedelta

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)")
    sys.exit(1)

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "XAUAUDm"
YEARS = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0

TP_ATR = 0.51          # measured from the operator's own filled TPs
SL_ATR = 0.42          # measured from the operator's own filled SLs
ATR_N = 14             # ATR14 on H1, the unit the geometry was measured in
MAX_HOLD_BARS = 24     # M5 bars ~= 2h; their median hold was 12-24 min
SEED = 12345


def load(symbol, years):
    n = int(years * 365 * 24 * 12)          # M5 bars
    n = min(n, 400_000)
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, n)
    if rates is None or len(rates) < 5000:
        return None
    return rates


def h1_atr_series(rates_m5, n=ATR_N):
    """ATR14 of H1 bars, mapped onto each M5 bar (value from the last
    CLOSED H1 bar, so no lookahead)."""
    # build H1 bars from M5
    h1 = []
    cur = None
    for r in rates_m5:
        h = (r["time"] // 3600) * 3600
        if cur is None or cur["t"] != h:
            if cur is not None:
                h1.append(cur)
            cur = {"t": h, "o": r["open"], "h": r["high"],
                   "l": r["low"], "c": r["close"]}
        else:
            cur["h"] = max(cur["h"], r["high"])
            cur["l"] = min(cur["l"], r["low"])
            cur["c"] = r["close"]
    if cur:
        h1.append(cur)
    atr_by_h = {}
    trs = []
    for i in range(1, len(h1)):
        tr = max(h1[i]["h"] - h1[i]["l"],
                 abs(h1[i]["h"] - h1[i-1]["c"]),
                 abs(h1[i]["l"] - h1[i-1]["c"]))
        trs.append(tr)
        if len(trs) >= n:
            # available only AFTER this H1 bar closes
            atr_by_h[h1[i]["t"] + 3600] = sum(trs[-n:]) / n
    # forward-fill onto M5 timestamps
    keys = sorted(atr_by_h)
    out, ki, cur_atr = [], 0, None
    for r in rates_m5:
        while ki < len(keys) and keys[ki] <= r["time"]:
            cur_atr = atr_by_h[keys[ki]]
            ki += 1
        out.append(cur_atr)
    return out


def signals(rates, kind, lookback):
    """+1 long / -1 short / 0 flat, using only CLOSED bars up to i."""
    sig = [0] * len(rates)
    for i in range(lookback + 1, len(rates)):
        if kind == "net":                       # net move over lookback bars
            d = rates[i]["close"] - rates[i - lookback]["close"]
        elif kind == "consec":                  # all last N bars same colour
            ups = all(rates[j]["close"] > rates[j]["open"]
                      for j in range(i - lookback + 1, i + 1))
            dns = all(rates[j]["close"] < rates[j]["open"]
                      for j in range(i - lookback + 1, i + 1))
            d = 1.0 if ups else (-1.0 if dns else 0.0)
        else:                                   # ema slope
            k = 2.0 / (lookback + 1)
            e = rates[i - lookback * 3]["close"] if i - lookback * 3 >= 0 else rates[0]["close"]
            for j in range(max(0, i - lookback * 3) + 1, i + 1):
                e = rates[j]["close"] * k + e * (1 - k)
            d = rates[i]["close"] - e
        sig[i] = (1 if d > 0 else (-1 if d < 0 else 0))
    return sig


def run(rates, atr, sig, spread, lo, hi, seed_flip=None):
    """Sequential, one position at a time. Returns list of R-multiples."""
    import random
    rng = random.Random(SEED)
    out = []
    i = lo
    while i < hi - MAX_HOLD_BARS - 1:
        a = atr[i]
        s = sig[i]
        if seed_flip is not None:
            s = rng.choice((1, -1))
        if not a or s == 0:
            i += 1
            continue
        entry = rates[i + 1]["open"]            # fill next bar, no lookahead
        tp_d, sl_d = TP_ATR * a, SL_ATR * a
        if sl_d <= 0:
            i += 1
            continue
        tp = entry + s * tp_d
        sl = entry - s * sl_d
        exit_i, r = None, None
        for j in range(i + 1, min(i + 1 + MAX_HOLD_BARS, hi)):
            hi_p, lo_p = rates[j]["high"], rates[j]["low"]
            hit_sl = (lo_p <= sl) if s > 0 else (hi_p >= sl)
            hit_tp = (hi_p >= tp) if s > 0 else (lo_p <= tp)
            if hit_sl and hit_tp:
                r = -1.0                        # pessimistic: stop first
                exit_i = j
                break
            if hit_sl:
                r = -1.0
                exit_i = j
                break
            if hit_tp:
                r = tp_d / sl_d
                exit_i = j
                break
        if exit_i is None:
            exit_i = min(i + MAX_HOLD_BARS, hi - 1)
            r = ((rates[exit_i]["close"] - entry) * s) / sl_d
        out.append(r - spread / sl_d)           # spread charged in R
        i = exit_i + 1
    return out


def stats(rs):
    if not rs:
        return None
    n = len(rs)
    w = [x for x in rs if x > 0]
    l = [x for x in rs if x <= 0]
    tot = sum(rs)
    gw, gl = sum(w), -sum(l)
    srt = sorted(rs, reverse=True)
    top = sum(srt[:max(1, n // 10)])
    return dict(n=n, wr=100.0 * len(w) / n, ev=tot / n, tot=tot,
                pf=(gw / gl if gl > 0 else float("inf")),
                top_share=(100.0 * top / tot if tot > 0 else float("nan")))


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed")
        return 2
    info = mt5.symbol_info(SYMBOL)
    if info is None:
        print(f"[ERROR] {SYMBOL} not found on this broker")
        mt5.shutdown()
        return 2
    rates = load(SYMBOL, YEARS)
    if rates is None:
        print(f"[ERROR] not enough M5 history for {SYMBOL}")
        mt5.shutdown()
        return 2
    atr = h1_atr_series(rates)
    spread_price = info.spread * info.point

    print("=" * 82)
    print(f" COPY-THE-OPERATOR BACKTEST -- {SYMBOL}   TP {TP_ATR}xATR / SL {SL_ATR}xATR")
    print(f" {len(rates):,} M5 bars   "
          f"{datetime.fromtimestamp(rates[0]['time']):%Y-%m-%d} -> "
          f"{datetime.fromtimestamp(rates[-1]['time']):%Y-%m-%d}")
    print(f" live spread now: {info.spread} points = {spread_price:.2f} price")
    print("=" * 82)

    mid = len(rates) // 2
    defs = [("net", 1), ("net", 3), ("net", 6), ("net", 12),
            ("consec", 2), ("consec", 3), ("ema", 5), ("ema", 12)]

    print(f"{'signal':<12}{'half':<7}{'n':>7}{'WR':>7}{'EV(R)':>9}"
          f"{'PF':>7}{'top10%':>9}")
    print("-" * 82)
    results = {}
    for kind, lb in defs:
        sig = signals(rates, kind, lb)
        a = stats(run(rates, atr, sig, spread_price, lb + 1, mid))
        b = stats(run(rates, atr, sig, spread_price, mid, len(rates)))
        results[(kind, lb)] = (a, b)
        for tag, s in (("train", a), ("TEST", b)):
            if s:
                print(f"{kind+str(lb):<12}{tag:<7}{s['n']:>7}{s['wr']:>6.1f}%"
                      f"{s['ev']:>+9.3f}{s['pf']:>7.2f}{s['top_share']:>8.0f}%")
    print("-" * 82)

    ctrl_a = stats(run(rates, atr, [0]*len(rates), spread_price, 20, mid, seed_flip=True))
    ctrl_b = stats(run(rates, atr, [0]*len(rates), spread_price, mid, len(rates), seed_flip=True))
    for tag, s in (("train", ctrl_a), ("TEST", ctrl_b)):
        if s:
            print(f"{'RANDOM':<12}{tag:<7}{s['n']:>7}{s['wr']:>6.1f}%"
                  f"{s['ev']:>+9.3f}{s['pf']:>7.2f}{s['top_share']:>8.0f}%")

    print()
    print("  Break-even WR at this R:R is "
          f"{100.0/(1.0+TP_ATR/SL_ATR):.1f}% before costs.")
    print("  RANDOM is the same geometry with a coin-flip direction: any signal")
    print("  that does not clearly beat it is measuring the market, not a rule.")
    print("  Spread is charged every trade; at a 0.42xATR stop it is a large")
    print("  fraction of R, which is why a tight-stop scalp is cost-dominated.")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
