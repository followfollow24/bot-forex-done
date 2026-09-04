#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_entry_fingerprint.py -- what is different about the bars the operator
actually entered on?

The gap this exists to close: the operator's own manual trades run
WR 64% / PF 2.01 over 33 trades, but the rule they described -- "if the
chart is moving up I go long" -- measured EXACTLY equal to a coin flip
when coded literally (8 definitions, 101,399 M5 bars, EV identical to a
random-direction control). Both cannot be the whole truth. Either the 33
trades are luck, or the eye is using something the code is not.

This measures the entry bars against the bars that were passed over, on
features available BEFORE the entry, and reports effect sizes:

  momentum agreement  did the trade direction actually match the recent
                      move? this is the described rule -- if entries do
                      not align with it, the description is not what is
                      being done, which alone would explain the failure
  vol regime          ATR(M5) vs its own recent median at entry
  extension           |close - EMA20| in ATR -- entering into stretch or
                      into quiet?
  range position      where in the last 20 bars' range the entry sat
  hour                already known to cluster 11-15 UTC

Two comparisons, because they answer different questions:
  ENTRY vs PASSED-OVER  -- what makes a bar worth trading at all
  WIN vs LOSS           -- what separates the good calls from the bad,
                           i.e. a filter that could be added

HONESTY ABOUT POWER: n=33, split 21/12 for the win/loss cut. Differences
below roughly 0.7 standard deviations are indistinguishable from noise at
this size. Every comparison therefore reports Cohen's d and a permutation
p-value, and anything that does not clear both is reported as "not
separable" rather than described as a finding.

Runs on the VPS (needs MT5).
Usage:  python _entry_fingerprint.py [days]
"""
import sys
import random
from datetime import datetime, timedelta

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)")
    sys.exit(1)

import numpy as np

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 60
REASON = {0: "CLIENT", 1: "MOBILE", 2: "WEB", 3: "EXPERT", 4: "SL", 5: "TP", 6: "STOPOUT"}
HUMAN = {"CLIENT", "MOBILE", "WEB"}
LOOKBACK = 3
random.seed(7)


def ema(a, n):
    k = 2.0 / (n + 1)
    out = np.empty(len(a))
    out[0] = a[0]
    for i in range(1, len(a)):
        out[i] = a[i] * k + out[i - 1] * (1 - k)
    return out


def atr_series(h, l, c, n=14):
    tr = np.maximum(h[1:] - l[1:],
                    np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
    out = np.full(len(c), np.nan)
    for i in range(n, len(tr) + 1):
        out[i] = tr[i - n:i].mean()
    return out


def features(bars, i, side=None):
    """State at bar i, using bars 0..i only (i is the last CLOSED bar)."""
    c = bars["close"]
    if i < 60 or np.isnan(bars["atr"][i]) or bars["atr"][i] <= 0:
        return None
    a = bars["atr"][i]
    lo20 = bars["low"][i - 19:i + 1].min()
    hi20 = bars["high"][i - 19:i + 1].max()
    rng = hi20 - lo20
    mom = (c[i] - c[i - LOOKBACK]) / a
    f = {
        "vol_ratio":  a / np.nanmedian(bars["atr"][max(0, i - 200):i + 1]),
        "extension":  abs(c[i] - bars["ema20"][i]) / a,
        "mom_abs":    abs(mom),
        "range_pos":  ((c[i] - lo20) / rng) if rng > 0 else 0.5,
        "hour":       float(datetime.fromtimestamp(bars["time"][i]).hour),
    }
    if side is not None:
        # +1 when the trade direction matches the recent move, -1 when against
        f["mom_agree"] = float(np.sign(mom) * (1 if side == "long" else -1))
        f["mom_signed"] = mom * (1 if side == "long" else -1)
    return f


def cohens_d(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 2 or len(b) < 2:
        return float("nan")
    s = np.sqrt(((len(a) - 1) * a.var(ddof=1) + (len(b) - 1) * b.var(ddof=1))
                / (len(a) + len(b) - 2))
    return (a.mean() - b.mean()) / s if s > 0 else float("nan")


def perm_p(a, b, reps=20000):
    a, b = np.asarray(a, float), np.asarray(b, float)
    obs = abs(a.mean() - b.mean())
    pool = np.concatenate([a, b])
    n = len(a)
    hits = 0
    for _ in range(reps):
        np.random.shuffle(pool)
        if abs(pool[:n].mean() - pool[n:].mean()) >= obs:
            hits += 1
    return (hits + 1) / (reps + 1)


def compare(title, groups, keys):
    (na, ga), (nb, gb) = groups
    print(f"\n  {title}")
    print(f"  {'feature':<14}{na+' n='+str(len(ga)):>16}{nb+' n='+str(len(gb)):>16}"
          f"{'d':>8}{'p':>8}   verdict")
    print("  " + "-" * 76)
    for k in keys:
        A = [g[k] for g in ga if k in g and not np.isnan(g[k])]
        B = [g[k] for g in gb if k in g and not np.isnan(g[k])]
        if len(A) < 3 or len(B) < 3:
            continue
        d = cohens_d(A, B)
        p = perm_p(A, B)
        sep = "SEPARATES" if (abs(d) >= 0.7 and p < 0.05) else "not separable"
        print(f"  {k:<14}{np.mean(A):>16.3f}{np.mean(B):>16.3f}"
              f"{d:>8.2f}{p:>8.3f}   {sep}")


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed")
        return 2
    acct = mt5.account_info()
    frm = datetime.now() - timedelta(days=DAYS)
    deals = mt5.history_deals_get(frm, datetime.now() + timedelta(days=1)) or []

    pos = {}
    for d in deals:
        if d.position_id == 0:
            continue
        p = pos.setdefault(d.position_id, {"magic": 0, "net": 0.0, "sym": d.symbol,
                                           "open": None, "side": None, "oreason": None})
        p["net"] += d.profit + d.swap + d.commission
        if d.magic and not p["magic"]:
            p["magic"] = d.magic
        if d.entry == mt5.DEAL_ENTRY_IN:
            p["open"] = datetime.fromtimestamp(d.time)
            p["side"] = "long" if d.type == mt5.DEAL_TYPE_BUY else "short"
            p["oreason"] = REASON.get(d.reason, str(d.reason))
        elif d.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY):
            p["closed"] = True

    manual = [p for p in pos.values()
              if p["magic"] == 0 and p.get("closed") and p["oreason"] in HUMAN
              and p["open"]]
    if not manual:
        print("  no manual trades found")
        mt5.shutdown()
        return 0

    sym = max({p["sym"] for p in manual},
              key=lambda s: sum(1 for p in manual if p["sym"] == s))
    manual = [p for p in manual if p["sym"] == sym]
    print("=" * 80)
    print(f" ENTRY FINGERPRINT -- {sym}   account {acct.login}")
    print(f" {len(manual)} manual trades, last {DAYS} days")
    print("=" * 80)

    # one M5 series covering the whole window, fetched in slices
    chunks, cursor = [], datetime.now()
    while cursor > frm - timedelta(days=5):
        part = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M5,
                                    cursor - timedelta(days=45), cursor)
        if part is not None and len(part):
            chunks.append(part)
        cursor -= timedelta(days=45)
    if not chunks:
        print(f"  no M5 data ({mt5.last_error()})")
        mt5.shutdown()
        return 2
    r = np.concatenate(list(reversed(chunks)))
    _, keep = np.unique(r["time"], return_index=True)
    r = r[np.sort(keep)]
    bars = {"time": r["time"], "close": r["close"], "high": r["high"],
            "low": r["low"]}
    bars["atr"] = atr_series(r["high"], r["low"], r["close"])
    bars["ema20"] = ema(r["close"], 20)
    print(f" M5 bars: {len(r):,}  "
          f"{datetime.fromtimestamp(r[0]['time']):%Y-%m-%d} -> "
          f"{datetime.fromtimestamp(r[-1]['time']):%Y-%m-%d}")

    times = bars["time"]
    entries, wins, losses = [], [], []
    entry_idx = set()
    for p in manual:
        ts = int(p["open"].timestamp())
        i = int(np.searchsorted(times, ts) - 1)     # last bar CLOSED before entry
        if i < 60 or i >= len(times):
            continue
        f = features(bars, i, p["side"])
        if f is None:
            continue
        entry_idx.add(i)
        entries.append(f)
        (wins if p["net"] > 0 else losses).append(f)

    hours_traded = {int(f["hour"]) for f in entries}
    control = []
    for i in range(60, len(times) - 1):
        if i in entry_idx:
            continue
        if datetime.fromtimestamp(times[i]).hour not in hours_traded:
            continue                                 # match the trading window
        f = features(bars, i)
        if f:
            control.append(f)
    control = random.sample(control, min(4000, len(control)))

    print(f" entries analysed: {len(entries)}   control bars: {len(control)}"
          f"   (same hours of day)")

    print("\n" + "=" * 80)
    print(" DID THE ENTRIES ACTUALLY FOLLOW THE RECENT MOVE?")
    print("=" * 80)
    agree = [f["mom_agree"] for f in entries if "mom_agree" in f]
    with_move = sum(1 for a in agree if a > 0)
    print(f"  trade direction matched the last {LOOKBACK} bars' move: "
          f"{with_move}/{len(agree)} ({100.0*with_move/len(agree):.0f}%)")
    print(f"  a coin flip would give 50%.  signed momentum at entry: "
          f"{np.mean([f['mom_signed'] for f in entries]):+.3f} xATR")
    print("  If this is near 50%, the described rule is not what is being")
    print("  traded -- which by itself explains why coding it literally failed.")

    compare("ENTRY BARS vs BARS PASSED OVER (what makes a bar worth taking)",
            [("entry", entries), ("control", control)],
            ["vol_ratio", "extension", "mom_abs", "range_pos", "hour"])

    if len(wins) >= 3 and len(losses) >= 3:
        compare("WINNING vs LOSING ENTRIES (a filter that could be added)",
                [("win", wins), ("loss", losses)],
                ["vol_ratio", "extension", "mom_abs", "range_pos", "hour",
                 "mom_signed"])

    print("\n" + "=" * 80)
    print("  d = Cohen's d (standardised difference). p = permutation test.")
    print("  At n=33 anything under |d|=0.7 is inside the noise, so only")
    print("  |d|>=0.7 AND p<0.05 is called a separation. 'not separable'")
    print("  means the feature does not distinguish the groups at this size --")
    print("  NOT that it is irrelevant, only that 33 trades cannot show it.")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
