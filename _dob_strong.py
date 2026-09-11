"""Double-or-bust at 19:30, restricted to STRONG days.

The operator asked for the bet measured only on the days the chart really
moves -- the ones they named (2, 3, 4 Sep, 7 Aug, 12 Aug, and this week).

Two separate things are printed, because they answer different questions:

  PART 1  the named days, replayed exactly. This is a record, not evidence:
          the days were chosen BECAUSE they moved, so a good result here
          proves only that they moved.

  PART 2  the same bet over 365 days, restricted by a filter that is known
          BEFORE the bell -- how far price travelled in the hour leading
          into 19:30, measured in ATR. That is the operator's own
          correction (distance, not EMA slope) and it is causal, so it
          can be counted.

Target = the whole balance, so target distance and ruin distance are the
same number at every lot. Only P(touch +d before -d) can move the result.
"""
import sys, math
from datetime import datetime, timedelta, timezone

try:
    import MetaTrader5 as mt5
except Exception:
    print("[ERROR] needs MetaTrader5 (run on the VPS)"); sys.exit(1)

import numpy as np

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "XAUUSDm"
DAYS = int(sys.argv[2]) if len(sys.argv) > 2 else 365
EQUITY = float(sys.argv[3]) if len(sys.argv) > 3 else 50.0
GATES = [11.0, 14.0]
LOTS = [0.01, 0.02, 0.03, 0.05, 0.08, 0.10]
MIN_WAIT, MAXW, THAI = 1.0, 900, 7
WATCH_H = 12.0
PRE_H = 1.0                       # the hour before the bell
KS = [0.0, 0.5, 0.75, 1.0]        # pre-bell distance in ATR

NAMED = ["2026-08-07", "2026-08-12", "2026-09-02", "2026-09-03",
         "2026-09-04", "2026-09-10", "2026-09-11"]


P1, G11LOW, SINK = [], [], []


def out(line="", keep=None):
    """print, and optionally keep a copy for the tail re-print"""
    print(line)
    SINK.append(line)
    if keep is not None:
        keep.append(line)


def binom_p_one_sided(w, n):
    """P(X >= w) under a fair coin -- exact."""
    if n == 0:
        return 1.0
    return sum(math.comb(n, k) for k in range(w, n + 1)) / 2.0 ** n


def atr_at(sym, when, n=14):
    """ATR(H1) using only bars that CLOSED before `when`."""
    r = mt5.copy_rates_from(sym, mt5.TIMEFRAME_H1, when, n + 2)
    if r is None or len(r) < n + 1:
        return None
    r = r[r["time"] < int(when.timestamp())]
    if len(r) < n + 1:
        return None
    r = r[-(n + 1):]
    h, l, c = r["high"].astype(float), r["low"].astype(float), r["close"].astype(float)
    tr = np.maximum(h[1:] - l[1:],
                    np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
    return float(tr.mean())


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed"); return 2
    si = mt5.symbol_info(SYMBOL)
    if si is None:
        print(f"[ERROR] {SYMBOL} not found"); mt5.shutdown(); return 2
    mt5.symbol_select(SYMBOL, True)
    spread = si.spread * si.point
    tk = mt5.symbol_info_tick(SYMBOL)

    print("=" * 92)
    print(f" DOUBLE OR BUST ON STRONG DAYS -- {SYMBOL}"
          f"   equity {EQUITY:.0f}   target +{EQUITY:.0f}"
          f"   spread {spread:.3f}")
    print("=" * 92)

    usable = []
    for lot in LOTS:
        pv = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, SYMBOL, lot,
                                   tk.ask, tk.ask + 1.0) if tk else None
        mg = mt5.order_calc_margin(mt5.ORDER_TYPE_BUY, SYMBOL, lot,
                                   tk.ask) if tk else None
        if not pv or mg is None:
            continue
        pv, mg = float(pv), float(mg)
        if mg < EQUITY * 0.9:
            usable.append((lot, pv, EQUITY / pv))
    if not usable:
        print("\n  no lot fits the margin"); mt5.shutdown(); return 0

    # ---- collect every session once, with its pre-bell context ---------
    print("\nloading ticks ...", flush=True)
    sess = []                     # (date, pre_move, atr, {gate: path})
    today = (datetime.now(timezone.utc) + timedelta(hours=THAI)).date()
    for b in range(DAYS, -1, -1):
        d = today - timedelta(days=b)
        if d.weekday() >= 5:
            continue
        bell = datetime(d.year, d.month, d.day, 12, 30, tzinfo=timezone.utc)
        t = mt5.copy_ticks_range(SYMBOL, bell - timedelta(hours=PRE_H),
                                 bell + timedelta(hours=WATCH_H),
                                 mt5.COPY_TICKS_ALL)
        if t is None or len(t) < 60:
            continue
        t0 = int(bell.timestamp())
        sec = (t["time_msc"].astype(np.int64) - t0 * 1000) / 1000.0
        bid, ask = t["bid"].astype(float), t["ask"].astype(float)
        mid = np.where(ask > 0, (bid + ask) / 2.0, bid)
        pre = sec < 0.0
        post = ~pre
        if pre.sum() < 20 or post.sum() < 20:
            continue
        pre_move = float(mid[post][0] - mid[pre][0])     # signed, causal
        atr = atr_at(SYMBOL, bell)
        if not atr:
            continue
        s_sec, s_mid = sec[post], mid[post]
        s_bid, s_ask = bid[post], ask[post]
        ref = float(s_mid[0])
        paths = {}
        for g in GATES:
            w = np.where((s_sec >= MIN_WAIT) & (s_sec <= MAXW)
                         & (np.abs(s_mid - ref) >= g))[0]
            if len(w) == 0:
                continue
            i = int(w[0])
            side = 1 if s_mid[i] > ref else -1
            entry = float(s_ask[i]) if side > 0 else float(s_bid[i])
            paths[g] = (side, (s_mid[i:] - entry) * side - spread)
        sess.append((d, pre_move, atr, paths))
    print(f"  {len(sess)} sessions with a full pre-bell hour and ATR\n")

    def settle(path, dist):
        up = np.argmax(path >= dist) if (path >= dist).any() else None
        dn = np.argmax(path <= -dist) if (path <= -dist).any() else None
        if up is not None and (dn is None or up < dn):
            return "double"
        if dn is not None:
            return "bust"
        return "neither"

    # ================= PART 1 -- the named days ======================
    out("=" * 92, P1)
    out(" PART 1 -- THE DAYS YOU NAMED, REPLAYED", P1)
    out("=" * 92, P1)
    out("  chosen because they moved, so this is a record, not a test.", P1)
    by_date = {str(d): (pm, atr, paths) for d, pm, atr, paths in sess}
    for g in GATES:
        out(f"\n  ---- gate {g:g} pts ----", P1)
        out(f"{'date':>12}{'pre-bell':>10}{'xATR':>7}{'dir':>5}"
            + "".join(f"{str(l):>8}" for l, _, _ in usable), P1)
        tally = {lot: [0, 0, 0] for lot, _, _ in usable}
        for ds in NAMED:
            got = by_date.get(ds)
            if got is None:
                out(f"{ds:>12}{'no data (market shut or not yet)':>38}", P1)
                continue
            pm, atr, paths = got
            if g not in paths:
                out(f"{ds:>12}{pm:>+10.2f}{abs(pm)/atr:>7.2f}"
                    f"{'--':>5}{'gate never cleared':>28}", P1)
                continue
            side, path = paths[g]
            cells = ""
            for lot, pv, dist in usable:
                r = settle(path, dist)
                tally[lot][0 if r == "double" else 1 if r == "bust" else 2] += 1
                cells += f"{'WIN' if r=='double' else 'BUST' if r=='bust' else '-':>8}"
            out(f"{ds:>12}{pm:>+10.2f}{abs(pm)/atr:>7.2f}"
                f"{'BUY' if side>0 else 'SELL':>5}{cells}", P1)
        out(f"{'TOTAL':>12}{'':>22}"
            + "".join(f"{str(tally[l][0])+'/'+str(tally[l][0]+tally[l][1]):>8}"
                      for l, _, _ in usable), P1)

    # ========== PART 2 -- causal strong-day filter, 365 days ==========
    print("\n" + "=" * 92)
    print(" PART 2 -- SAME BET, STRONG DAYS PICKED BEFORE THE BELL")
    print("=" * 92)
    print("\n  strong = price travelled >= k x ATR(H1) in the hour BEFORE"
          " 19:30.")
    print("  'agrees' also requires the gate break to point the same way as"
          " that hour.")
    for g in GATES:
        print(f"\n{'='*92}\n GATE {g:g} PTS\n{'='*92}")
        for agree_only in (False, True):
            for k in KS:
                rows = []
                for d, pm, atr, paths in sess:
                    if g not in paths:
                        continue
                    if abs(pm) < k * atr:
                        continue
                    side, path = paths[g]
                    if agree_only and k > 0 and side != (1 if pm > 0 else -1):
                        continue
                    rows.append(path)
                if len(rows) < 8:
                    continue
                tag = (f"k>={k:.2f} ATR" + (" + agrees" if agree_only and k > 0
                                            else "")).ljust(22)
                if agree_only and k == 0:
                    continue
                keep = G11LOW if (g == 11.0 and not agree_only
                                  and k <= 0.5) else None
                if keep is not None:
                    keep.append(f"  gate {g:g}  {tag}")
                out(f"\n  {tag}  {len(rows)} sessions"
                    f"  ({len(rows)/len(sess)*100:.0f}% of days)", keep)
                out(f"{'lot':>8}{'target':>9}{'double':>8}{'bust':>7}"
                    f"{'neither':>9}{'P(double)':>11}{'exact p':>9}"
                    f"{'EV/bet':>9}", keep)
                for lot, pv, dist in usable:
                    w = l = n0 = 0
                    for path in rows:
                        r = settle(path, dist)
                        if r == "double": w += 1
                        elif r == "bust": l += 1
                        else: n0 += 1
                    tot = w + l
                    if tot == 0:
                        continue
                    p = w / tot
                    ev = p * EQUITY - (1 - p) * EQUITY
                    out(f"{lot:>8.2f}{dist:>9.1f}{w:>8}{l:>7}{n0:>9}"
                        f"{p*100:>10.1f}%"
                        f"{binom_p_one_sided(w, tot):>9.3f}{ev:>+9.2f}", keep)
        print("\n  'exact p' = P(this many doubles or more from a fair coin).")
        print("  Below 0.05 in a cell you did not pick afterwards is the only"
              " thing that counts.")

    print("\n\n" + "#" * 92)
    print("#  TAIL RE-PRINT -- the blocks that scroll off the top")
    print("#" * 92)
    for line in G11LOW:
        print(line)
    print()
    for line in P1:
        print(line)
    print("\n" + "=" * 92)
    print(" Slippage is not modelled. Spread is charged once at entry.")
    print(" Strong-day filters cut the sample hard -- read the n column"
          " before the P column.")
    print(" Full output also written to dob.txt")
    print("=" * 92)
    try:
        with open("dob.txt", "w") as fh:
            fh.write("\n".join(SINK) + "\n")
    except Exception as exc:
        print(f" (could not write dob.txt: {exc!r})")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
