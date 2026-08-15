#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_loser_anatomy.py -- why did the losers lose, and what would have saved them?

Every losing chart_ai trade cost about the same (-41 to -50). That
uniformity is the first clue: they are not assorted mishaps, they are the
stop being hit, exactly as designed. The wins were +67 to +85, so the
1.5:1 payoff worked too. Nothing about the EXITS is malfunctioning.

But "the stop was hit" does not say WHY. Two very different worlds produce
the same stop-out:

  A. price went the wrong way immediately -- the direction call was wrong,
     and no exit rule can rescue it.
  B. price first went 0.8R our way, then reversed into the stop -- the
     direction was right, the TARGET was simply too far away.

These demand opposite fixes, and the live log cannot tell them apart. So
for every real trade this walks the actual M15 bars and records the
MAXIMUM PROFIT THAT WAS EVER AVAILABLE before the 1R stop would have been
touched. That single number separates world A from world B, and it also
prices every possible take-profit at once: a target at X wins if and only
if the best excursion reached X.

From that, the optimal TP per symbol falls out arithmetically instead of
being guessed -- for the original direction AND for the inverted one,
kept separate because the 150-sample replay found gold and BTC behaving
oppositely (gold ~coin-flip, BTC significantly anti-predictive).

Pessimistic throughout: within any bar the adverse extreme is assumed to
happen first, so a bar that spans both levels counts as the stop. That
biases every result DOWN, including the ones this script might otherwise
make look attractive.

Usage (on the VPS, MT5 running):  python _loser_anatomy.py
"""
import os
import re
import sys
from datetime import datetime, timedelta

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)")
    sys.exit(1)

BASE = os.path.dirname(os.path.abspath(__file__))


def find_log():
    """The bots are launched from the Desktop, so the log is NOT beside this
    script in bot_repo. Search both, and glob rather than hard-code the
    filename so a renamed log surfaces as a clear list instead of a bare
    'not found'. An explicit path as argv[1] always wins.
    """
    if len(sys.argv) > 1:
        return sys.argv[1]
    import glob
    roots = [BASE, os.path.join(os.path.expanduser("~"), "Desktop")]
    hits = []
    for r in roots:
        hits += glob.glob(os.path.join(r, "*chart_ai*.log"))
    if not hits:
        print("[ERROR] no *chart_ai*.log found in:")
        for r in roots:
            print(f"    {r}")
        print("  pass the path explicitly:  python _loser_anatomy.py <path>")
        sys.exit(1)
    hits.sort(key=lambda p: os.path.getsize(p), reverse=True)
    if len(hits) > 1:
        print(f"[note] {len(hits)} candidates; using the largest:")
        for h in hits:
            print(f"    {os.path.getsize(h):>10,}  {h}")
    return hits[0]


LOG = None

# round-trip cost in PRICE units, per the repo's verified specs
SPREAD = {"XAUUSDc": 0.24, "BTCUSDc": 10.0, "ETHUSDc": 0.6}
MAX_HOLD_BARS = 96                      # 24h on M15
TP_GRID = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5]

OPEN_RE = re.compile(
    r"^(?P<ts>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ \[INFO\] \[OPEN\] "
    r"(?P<side>LONG|SHORT) (?P<sym>\S+) lot=(?P<lot>[\d.]+) "
    r"fill=(?P<fill>[\d.]+) sl=(?P<sl>[\d.]+) tp=(?P<tp>[\d.]+)")


def parse_opens():
    print(f"  log: {LOG}")
    out = []
    with open(LOG, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            m = OPEN_RE.match(line.strip())
            if not m:
                continue
            d = m.groupdict()
            out.append({
                "ts": datetime.strptime(d["ts"], "%Y-%m-%d %H:%M:%S"),
                "side": d["side"], "sym": d["sym"],
                "fill": float(d["fill"]), "sl": float(d["sl"]),
                "tp": float(d["tp"]),
            })
    return out


def bars_after(sym, ts, n=MAX_HOLD_BARS):
    rates = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M15,
                                 ts, ts + timedelta(minutes=15 * (n + 4)))
    if rates is None or len(rates) < 2:
        return []
    return list(rates)[1:n + 1]          # skip the entry bar itself


def best_excursion(bars, entry, long_, R):
    """Maximum favourable move (in R) reached BEFORE the 1R stop is touched.

    Returns (best_R, stopped, bars_used). Within each bar the adverse extreme
    is taken first, so a bar containing both the target and the stop resolves
    as the stop -- the pessimistic reading, applied to original and mirror
    alike so neither side is flattered. bars_used is how many M15 bars had
    elapsed when the stop hit, i.e. how fast the trade died.
    """
    best = 0.0
    for i, b in enumerate(bars):
        hi, lo = float(b["high"]), float(b["low"])
        adverse = lo if long_ else hi
        adv_R = ((entry - adverse) if long_ else (adverse - entry)) / R
        if adv_R >= 1.0:
            return best, True, i + 1
        fav = hi if long_ else lo
        fav_R = ((fav - entry) if long_ else (entry - fav)) / R
        if fav_R > best:
            best = fav_R
    return best, False, len(bars)


def verdict(o_best, own_rr):
    """Why this trade lost, in one label, from how far it ever got.

    The distinction that matters is whether the trade was EVER in profit.
    'wrong way' means the direction call failed outright and no exit rule
    could have helped. 'gave it back' means the direction was right for a
    while and only the target placement failed -- a fixable problem.
    """
    if o_best >= own_rr:
        return "WON (reached its target)"
    if o_best < 0.10:
        return "wrong way from bar 1"
    if o_best < 0.50:
        return f"barely moved (max +{o_best:.2f}R)"
    return f"gave it back (was +{o_best:.2f}R)"


def report(title, rows, sym):
    """rows: list of (best_R, stopped). Prices every TP on the same trades."""
    n = len(rows)
    if n == 0:
        return
    sp = SPREAD.get(sym, 0.0)
    cost_R = rows[0][2] if len(rows[0]) > 2 else 0.0
    print(f"\n  {title}   ({n} trades)")
    print(f"  {'TP':>6}{'win%':>8}{'EV/trade':>11}{'total R':>10}")
    best_ev, best_tp = None, None
    for tp in TP_GRID:
        wins = sum(1 for r in rows if r[0] >= tp)
        wr = wins / n
        ev = wr * tp - (1 - wr) * 1.0 - cost_R
        if best_ev is None or ev > best_ev:
            best_ev, best_tp = ev, tp
        flag = ""
        print(f"  {tp:>6.2f}{100*wr:>7.1f}%{ev:>+11.3f}{ev*n:>+10.1f}{flag}")
    print(f"  -> best target {best_tp}R, EV {best_ev:+.3f}R/trade "
          f"({best_ev*n:+.1f}R over {n})")
    return best_ev, best_tp


WF = []
PER_TRADE = []


def per_trade_table():
    """One line per real trade: why it lost, and what it would have done
    under the inverted rule. The aggregates hide the thing worth seeing --
    that the failures are not assorted, they are one failure repeated.
    """
    rows = sorted(PER_TRADE, key=lambda r: r["ts"])
    print("\n" + "=" * 100)
    print("  EVERY TRADE, ONE LINE EACH")
    print("  maxfav = most profit ever available before the stop, in R")
    print("  bars   = M15 bars until the stop hit (4 = one hour)")
    print("=" * 100)
    print(f"  {'when':<13}{'symbol':<10}{'side':<7}{'own':>5}{'maxfav':>8}"
          f"{'bars':>6}  {'why it lost':<26}{'inv@1.25R':>10}")
    print("  " + "-" * 96)
    inv_win = inv_loss = 0
    tally = {}
    for r in rows:
        v = verdict(r["o_best"], r["own_rr"])
        tally[v.split(" (")[0]] = tally.get(v.split(" (")[0], 0) + 1
        iw = r["i_best"] >= 1.25
        if iw:
            inv_win += 1
        else:
            inv_loss += 1
        print(f"  {r['ts']:%m-%d %H:%M}  {r['sym']:<10}{r['side']:<7}"
              f"{r['own_rr']:>5.2f}{r['o_best']:>8.2f}{r['bars']:>6}  "
              f"{v:<26}{'WIN' if iw else 'loss':>10}")
    print("  " + "-" * 96)
    n = len(rows)
    for k, c in sorted(tally.items(), key=lambda kv: -kv[1]):
        print(f"    {c:>3}/{n}  {k}")
    print(f"\n    inverted with a 1.25R target: {inv_win} win / {inv_loss} loss"
          f"  ({100*inv_win/max(n,1):.0f}% win rate on these same entries)")


def ev_at(rows, tp):
    """EV per trade in R for a fixed target, net of spread."""
    if not rows:
        return None
    wins = sum(1 for r in rows if r["inv"] >= tp)
    wr = wins / len(rows)
    cost = sum(r["cost"] for r in rows) / len(rows)
    return wr * tp - (1 - wr) * 1.0 - cost, wr


def walk_forward():
    """The TP table above is fitted on the SAME trades it is scored on, so
    its winner is guaranteed to look good. This is the check that isn't
    rigged: split chronologically, choose the target using only the EARLY
    half, then score it on the LATE half it never saw.

    Two rules are tested on the late half:
      - the target fitted on the early half (an honest walk-forward)
      - a flat 1.25R chosen a priori (no fitting anywhere)
    If the fitted target collapses out of sample while the flat rule holds,
    the fitting was noise and the flat rule is what should ship.

    n is small enough that this cannot CONFIRM an edge -- it can only fail
    to refute one. A collapse here is still decisive in the other
    direction: it would kill the idea outright.
    """
    print("\n" + "=" * 74)
    print("  WALK-FORWARD -- fit on early trades, score on later ones")
    print("=" * 74)
    rows = sorted(WF, key=lambda r: r["ts"])
    groups = {"ALL": rows}
    for r in rows:
        groups.setdefault(r["sym"], []).append(r)

    print(f"  {'group':<10}{'n':>4}{'split':>7}{'fit TP':>8}"
          f"{'EV in':>8}{'EV out':>8}{'WR out':>8}{'flat 1.25R out':>16}")
    for name in ("ALL", "BTCUSDc", "XAUUSDc", "ETHUSDc"):
        g = groups.get(name) or []
        if len(g) < 6:
            print(f"  {name:<10}{len(g):>4}   too few trades to split")
            continue
        cut = len(g) // 2
        early, late = g[:cut], g[cut:]
        best = max(TP_GRID, key=lambda tp: ev_at(early, tp)[0])
        ev_in, _ = ev_at(early, best)
        ev_out, wr_out = ev_at(late, best)
        ev_flat, _ = ev_at(late, 1.25)
        print(f"  {name:<10}{len(g):>4}{cut:>4}/{len(late):<3}{best:>7.2f}"
              f"{ev_in:>+8.3f}{ev_out:>+8.3f}{100*wr_out:>7.1f}%"
              f"{ev_flat:>+16.3f}")
    print()
    print("  'EV out' near or above 'EV in' -> the target was not just fitted")
    print("     to noise. Far below -> it was, and the in-sample table lied.")
    print("  'flat 1.25R out' is the number to trust most: nothing about it")
    print("     was chosen by looking at these trades.")


def main():
    global LOG
    LOG = find_log()
    if not mt5.initialize():
        print("[ERROR] MT5 init failed")
        sys.exit(1)

    trades = parse_opens()
    print(f"  parsed {len(trades)} [OPEN] lines")
    if not trades:
        print("[ERROR] no [OPEN] lines parsed")
        mt5.shutdown()
        return

    by_sym = {}
    never = {}
    for t in trades:
        bars = bars_after(t["sym"], t["ts"])
        if len(bars) < 4:
            continue
        R = abs(t["fill"] - t["sl"])
        if R <= 0:
            continue
        sp = SPREAD.get(t["sym"], 0.0)
        cost_R = sp / R
        long_ = t["side"] == "LONG"

        o_best, o_stopped, o_bars = best_excursion(bars, t["fill"], long_, R)
        i_best, _, _ = best_excursion(bars, t["fill"], not long_, R)
        own_rr = abs(t["tp"] - t["fill"]) / R
        PER_TRADE.append({
            "ts": t["ts"], "sym": t["sym"], "side": t["side"],
            "own_rr": own_rr, "o_best": o_best, "i_best": i_best,
            "bars": o_bars, "stopped": o_stopped, "cost": cost_R,
        })
        WF.append({"ts": t["ts"], "sym": t["sym"],
                   "inv": i_best, "cost": cost_R})
        d = by_sym.setdefault(t["sym"], {"orig": [], "inv": []})
        d["orig"].append((o_best, True, cost_R))
        d["inv"].append((i_best, True, cost_R))

    per_trade_table()

    print("\n" + "=" * 74)
    print(" LOSER ANATOMY -- chart_ai_trader, real trades vs real M15 bars")
    print(" 'best excursion' = most profit ever available before the 1R stop")
    print(" pessimistic: a bar spanning both levels counts as the stop")
    print("=" * 74)

    grand = {}
    for sym in sorted(by_sym):
        d = by_sym[sym]
        n = len(d["orig"])
        print("\n" + "-" * 74)
        print(f"  {sym}   {n} trades")
        print("-" * 74)

        # --- world A vs world B: did the trade EVER show a profit? ---
        for lbl, key in (("as traded", "orig"), ("inverted", "inv")):
            rows = d[key]
            dead = sum(1 for r in rows if r[0] < 0.10)
            half = sum(1 for r in rows if r[0] >= 0.50)
            full = sum(1 for r in rows if r[0] >= 1.50)
            print(f"  {lbl:<10} never +0.1R: {dead:>3}/{n}  "
                  f"reached +0.5R: {half:>3}/{n}  reached +1.5R: {full:>3}/{n}")

        for lbl, key in (("AS TRADED", "orig"), ("INVERTED", "inv")):
            r = report(lbl, d[key], sym)
            if r:
                grand.setdefault(key, []).append((sym, r[0], r[1], len(d[key])))

    walk_forward()

    print("\n" + "=" * 74)
    print("  READING IT")
    print("  'never +0.1R' high  -> direction was simply wrong; no exit rule")
    print("     saves these, and a closer target does not help either.")
    print("  'reached +0.5R' high but '+1.5R' low -> direction was fine, the")
    print("     1.5R target was too far. A closer target converts them.")
    print("  EV is in R and already net of the measured spread. Positive EV")
    print("  here is necessary, NOT sufficient: it is in-sample on the same")
    print("  trades that motivated the idea, so it must clear walk-forward")
    print("  before any of it goes near real money.")
    mt5.shutdown()


if __name__ == "__main__":
    main()
