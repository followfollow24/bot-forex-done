#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_stress_test_er.py -- three attacks on the strong-trend follow edge before
                      it is allowed anywhere near live money.

The claim under test: when the 40-bar efficiency ratio is very high, stop
inverting and follow the move. In-sample EV +0.131R at ER >= 0.465;
out-of-sample +0.050R at the train-chosen cutoff 0.436, n=152.

+0.050R is a thin margin, so it gets attacked three ways.

  1. COSTS. Spread is ALREADY charged in that number (cost = spread/R per
     trade, ~0.05-0.07R on BTC). What is missing is commission and swap.
     Commission is measured from the real fills log rather than assumed,
     and swap is computed from each trade's own stop distance and price
     instead of a flat guess. Then the break-even extra cost is reported:
     how much more friction the edge can absorb before it dies.

  2. THRESHOLD ROBUSTNESS. A real effect is a plateau; curve-fitting is a
     spike. The cutoff is walked in 0.005 steps and EV printed in-sample
     and out-of-sample, so a single lucky cell is visible as a single
     lucky cell. The neighbouring bands already look wrong -- EV is about
     -0.263 between ER 0.395 and 0.465 -- which is why this matters.

  3. CLUSTERING. Adjacent 15-minute bars inside one trend are the same
     event counted many times. The 152 out-of-sample samples are resolved
     into contiguous episodes, dated, and the share of total profit coming
     from the single largest episode is reported. An earlier finding in
     this project survived every average and died here: 84% of its trades
     sat in one 78-hour window.

Passing means: still positive after real costs, positive across a band of
neighbouring thresholds, and not dependent on one episode.

Usage (on the VPS):  python _stress_test_er.py [symbol] [fade_bars]
"""
import glob
import os
import sys
from datetime import datetime

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)")
    sys.exit(1)

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDc"
FADE_N = int(sys.argv[2]) if len(sys.argv) > 2 else 20
ER_WIN, SL_ATR, TP_R, HOLD, BARS = 40, 1.8, 1.25, 96, 8000
SPREAD = {"XAUUSDc": 0.24, "BTCUSDc": 10.0}
SWAP_LONG_PCT_YR = -6.9        # verified in reference_btcusdc_specs
SWAP_SHORT_PCT_YR = 0.0


def atr14(r, i):
    t = []
    for j in range(i - 13, i + 1):
        h, l = float(r[j]["high"]), float(r[j]["low"])
        pc = float(r[j - 1]["close"])
        t.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(t) / len(t)


def eff_ratio(r, i, win=ER_WIN):
    if i < win:
        return None
    net = abs(float(r[i]["close"]) - float(r[i - win]["close"]))
    path = sum(abs(float(r[j]["close"]) - float(r[j - 1]["close"]))
               for j in range(i - win + 1, i + 1))
    return (net / path) if path > 0 else 0.0


def race(r, i, entry, R, long_):
    tp = entry + TP_R * R if long_ else entry - TP_R * R
    sl = entry - R if long_ else entry + R
    for j in range(i + 1, min(i + 1 + HOLD, len(r))):
        hi, lo = float(r[j]["high"]), float(r[j]["low"])
        if long_:
            if lo <= sl:
                return False
            if hi >= tp:
                return True
        else:
            if hi >= sl:
                return False
            if lo <= tp:
                return True
    return False


def ev(ch, extra=0.0):
    """ch rows: (idx, er, hit, long_, cost_R, swap_R). extra in R."""
    if not ch:
        return 0, 0.0, 0.0
    w = sum(1 for x in ch if x[2]) / len(ch)
    c = sum(x[4] + x[5] for x in ch) / len(ch)
    return len(ch), w, w * TP_R - (1 - w) * 1.0 - c - extra


def episodes(idxs, gap=HOLD):
    if not idxs:
        return []
    runs, cur = [], [idxs[0]]
    for i in idxs[1:]:
        if i - cur[-1] > gap:
            runs.append(cur)
            cur = [i]
        else:
            cur.append(i)
    runs.append(cur)
    return runs


def measured_commission_R():
    """Real commission per round trip, from the fills log, expressed in R.
    Measuring beats assuming: the account may or may not charge any."""
    hits = []
    for d in (os.path.dirname(os.path.abspath(__file__)),
              os.path.join(os.path.expanduser("~"), "Desktop")):
        hits += glob.glob(os.path.join(d, "fills_log_*.csv"))
    tot, n = 0.0, 0
    for p in hits:
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                head = f.readline().strip().split(",")
                if "commission" not in head:
                    continue
                ci = head.index("commission")
                for line in f:
                    parts = line.rstrip("\n").split(",")
                    if len(parts) > ci:
                        try:
                            tot += abs(float(parts[ci]))
                            n += 1
                        except ValueError:
                            pass
        except OSError:
            pass
    return (tot / n if n else 0.0), n


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed")
        sys.exit(1)
    r = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M15, 0, BARS)
    if r is None or len(r) < 1000:
        print("[ERROR] not enough bars")
        mt5.shutdown()
        return
    r = list(r)
    sp = SPREAD.get(SYMBOL, 0.0)
    hold_days = HOLD * 15 / 60 / 24

    rows = []
    for i in range(max(ER_WIN, FADE_N) + 20, len(r) - HOLD):
        a = atr14(r, i)
        e = eff_ratio(r, i)
        if not a or a <= 0 or e is None:
            continue
        entry = float(r[i]["close"])
        R = SL_ATR * a
        up = entry > float(r[i - FADE_N]["close"])
        # swap in R: annual % of notional, prorated over the hold, divided
        # by the stop distance -- so a tight stop pays proportionally more
        pct = SWAP_LONG_PCT_YR if up else SWAP_SHORT_PCT_YR
        swap_R = abs(pct) / 100.0 / 365.0 * hold_days * entry / R
        rows.append((i, e, race(r, i, entry, R, up), up,
                     sp / R if R > 0 else 0.0, swap_R))
    n = len(rows)
    mid = n // 2
    tr, te = rows[:mid], rows[mid:]
    tr_cut = sorted(x[1] for x in tr)[len(tr) - max(1, len(tr) * 2 // 100)]
    band = [x for x in te if x[1] >= tr_cut]

    print("=" * 86)
    print(f" STRESS TEST -- {SYMBOL} M15   n={n}   train cutoff ER >= {tr_cut:.3f}")
    print("=" * 86)

    # ---------- 1. COSTS ----------
    comm_cash, comm_n = measured_commission_R()
    bn, bw, be = ev(band)
    _, _, be_nosw = ev([(x[0], x[1], x[2], x[3], x[4], 0.0) for x in band])
    avg_sp = sum(x[4] for x in band) / max(len(band), 1)
    avg_sw = sum(x[5] for x in band) / max(len(band), 1)
    print("\n  [1] TRANSACTION COSTS")
    print("  " + "-" * 82)
    print(f"    out-of-sample band: n={bn}   win {100*bw:.1f}%")
    print(f"    spread already charged : {avg_sp:.3f}R per trade")
    print(f"    swap now added         : {avg_sw:.3f}R per trade "
          f"({hold_days*24:.0f}h hold)")
    print(f"    commission measured    : {comm_cash:.4f} account-ccy per fill "
          f"across {comm_n} real fills")
    print(f"    EV before swap: {be_nosw:+.3f}R   ->  EV after swap: {be:+.3f}R")
    print(f"\n    {'extra friction':>16}{'EV':>10}")
    for x in (0.0, 0.02, 0.05, 0.10, 0.15, 0.20):
        _, _, e2 = ev(band, x)
        flag = "  <- turns negative" if e2 < 0 <= (be if x > 0 else 1) and e2 < 0 else ""
        print(f"    {x:>13.2f}R{e2:>+10.3f}{flag if e2 < 0 else ''}")
    # break-even extra friction
    lo, hi = 0.0, 1.0
    for _ in range(40):
        m = (lo + hi) / 2
        if ev(band, m)[2] > 0:
            lo = m
        else:
            hi = m
    print(f"\n    break-even extra friction: {lo:.3f}R "
          f"(edge dies beyond this)")

    # ---------- 2. THRESHOLD ROBUSTNESS ----------
    print("\n  [2] THRESHOLD ROBUSTNESS (plateau or spike?)")
    print("  " + "-" * 82)
    print(f"    {'ER >=':>7}{'n_all':>7}{'EV all':>9}{'n_test':>8}"
          f"{'EV test':>9}{'episodes':>10}")
    pos_run = 0
    best_run = 0
    for k in range(76, 105):
        cut = k / 200.0     # 0.380 .. 0.520 in 0.005 steps
        a_ch = [x for x in rows if x[1] >= cut]
        t_ch = [x for x in te if x[1] >= cut]
        if len(t_ch) < 15:
            continue
        an, _, ae = ev(a_ch)
        tn, _, tev = ev(t_ch)
        eps = len(episodes([x[0] for x in t_ch]))
        both = ae > 0 and tev > 0
        pos_run = pos_run + 1 if both else 0
        best_run = max(best_run, pos_run)
        print(f"    {cut:>7.3f}{an:>7}{ae:>+9.3f}{tn:>8}{tev:>+9.3f}"
              f"{eps:>10}{'  <<<' if both else ''}")
    print(f"\n    longest run of consecutive cutoffs positive on BOTH: {best_run}")
    print("    (1-2 = a spike, curve-fitted. 5+ = a plateau, plausible.)")

    # ---------- 3. CLUSTERING ----------
    print("\n  [3] CLUSTERING OF THE OUT-OF-SAMPLE BAND")
    print("  " + "-" * 82)
    eps = episodes([x[0] for x in band])
    print(f"    {bn} samples resolve into {len(eps)} contiguous episodes")
    tot_pnl = 0.0
    ep_pnl = []
    for run in eps:
        s = set(run)
        ch = [x for x in band if x[0] in s]
        _, w, e = ev(ch)
        pnl = e * len(ch)
        ep_pnl.append((len(ch), pnl, run[0], run[-1]))
        tot_pnl += pnl
    ep_pnl.sort(key=lambda x: -abs(x[1]))
    print(f"    {'bars':>6}{'R total':>10}{'from':>18}{'to':>18}")
    for cnt, pnl, a_i, b_i in ep_pnl[:8]:
        t0 = datetime.fromtimestamp(int(r[a_i]["time"]))
        t1 = datetime.fromtimestamp(int(r[b_i]["time"]))
        print(f"    {cnt:>6}{pnl:>+10.2f}{t0:%Y-%m-%d %H:%M:>18}"
              f"{t1:%Y-%m-%d %H:%M:>18}")
    if ep_pnl and tot_pnl != 0:
        top = ep_pnl[0][1]
        print(f"\n    largest episode contributes {100*top/tot_pnl:.0f}% "
              f"of total R  (total {tot_pnl:+.2f}R over {len(eps)} episodes)")
        if len(eps) < 5:
            print("    -> fewer than 5 independent episodes. Not enough")
            print("       independent bets to call this an edge.")
        elif abs(top / tot_pnl) > 0.5:
            print("    -> one episode carries most of the result. Fragile.")
        else:
            print("    -> spread across episodes. No single window carries it.")
    mt5.shutdown()


if __name__ == "__main__":
    main()
