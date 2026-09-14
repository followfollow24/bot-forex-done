"""Can the up-down waves at 19:30 be caught?

The operator watches gold at 19:30 Thai and sees it swing up and down all
the time. Correct -- it is the busiest hour of the day. The question is not
whether it moves; it is whether a move COMES BACK by more than the spread.

Measured directly on 13 years of M5 gold, 19:30-21:30 Thai (12:30-14:30 UTC):

  after a 5-minute candle moves m points, trade AGAINST it at that candle's
  close ("catch the wave back") and measure the next 5 / 10 / 15 minutes
  in points. Compare that to the 0.26 spread paid on XAUUSDm.

THE NULL: the same evening's 5-minute moves, shuffled. Same sizes, same
volatility, same total -- only the order is destroyed. A finite shuffled
window reverts a little by construction (its moves must still add up to the
same total), so "it came back" only counts beyond what the shuffle gives.
Each evening's first price is held in place.

Statistics are clustered by day: 20 wiggles in one evening are one evening.
M5 bars cannot resolve a +1-point exit -- that needs ticks -- but they can
say how big the catchable wave is.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
M5 = os.path.join(HERE, "download", "xauusd-m5-bid-2013-01-01-2026-06-01.csv")
SPREAD = 0.26
HORIZ = (1, 2, 3)                          # bars = 5 / 10 / 15 minutes
BUCKETS = [(1, 3), (3, 6), (6, 10), (10, 1e9)]
PERIODS = [("2013-2019", "2013-01-01", "2019-12-31"),
           ("2020-2023", "2020-01-01", "2023-12-31"),
           ("2024-2026", "2024-01-01", "2026-12-31")]
NSHUF = 200
RNG = np.random.default_rng(1930)


def clustered(prof, day):
    """mean and day-clustered standard error"""
    if len(prof) < 2:
        return np.nan, np.nan
    m = prof.mean()
    e = prof - m
    s = pd.Series(e).groupby(day).sum().to_numpy()
    return m, np.sqrt((s ** 2).sum()) / len(prof)


def fade_events(C):
    """C: days x 28 closes (12:25 .. 14:40 UTC). Returns per-event arrays."""
    R = np.diff(C, axis=1)                       # 27 moves; R[:, j] ends at col j+1
    out = {h: {"m": [], "prof": [], "day": []} for h in HORIZ}
    for j in range(24):                          # signal candles 12:30 .. 14:25
        m = R[:, j]
        for h in HORIZ:
            fwd = C[:, j + 1 + h] - C[:, j + 1]
            out[h]["m"].append(m)
            out[h]["prof"].append(-np.sign(m) * fwd)
            out[h]["day"].append(np.arange(len(C)))
    for h in HORIZ:
        for k in out[h]:
            out[h][k] = np.concatenate(out[h][k])
    return out


def table(ev_real, ev_null_list, label):
    print(f"\n  {label}")
    print(f"{'move':>10}{'hold':>6}{'events':>8}{'comes back':>12}{'shuffle':>9}"
          f"{'beyond':>8}{'t':>6}{'after spread':>14}")
    best = None
    for lo, hi in BUCKETS:
        for h in HORIZ:
            e = ev_real[h]
            sel = (np.abs(e["m"]) >= lo) & (np.abs(e["m"]) < hi)
            if sel.sum() < 30:
                continue
            m, se = clustered(e["prof"][sel], e["day"][sel])
            nulls = []
            for en in ev_null_list:
                s2 = (np.abs(en[h]["m"]) >= lo) & (np.abs(en[h]["m"]) < hi)
                if s2.sum():
                    nulls.append(en[h]["prof"][s2].mean())
            nm = float(np.mean(nulls)) if nulls else np.nan
            beyond = m - nm
            t = beyond / se if se and se > 0 else np.nan
            net = m - SPREAD
            tag = f"{lo:g}-{hi:g}" if hi < 1e8 else f"{lo:g}+"
            print(f"{tag+' pts':>10}{h*5:>4}m{sel.sum():>8,}{m:>+11.2f}p{nm:>+8.2f}p"
                  f"{beyond:>+7.2f}p{t:>6.1f}{net:>+13.2f}p")
            if best is None or net > best[0]:
                best = (net, tag, h * 5, m, t)
    return best


def main():
    print("=" * 84)
    print(" CAN THE 19:30 WAVES BE CAUGHT?  gold M5, 19:30-21:30 Thai, fade the last candle")
    print("=" * 84, flush=True)
    d = pd.read_csv(M5)
    d["timestamp"] = pd.to_datetime(d["timestamp"], utc=True)
    d = d.set_index("timestamp")

    # sanity: is this file really UTC?  In US summer the 8:30 ET releases land
    # at 12:30 UTC = 19:30 Thai, so that slot should be at or near the top.
    s = d.loc["2024-06-01":"2025-08-31"]
    s = s[(s.index.month >= 4) & (s.index.month <= 9)]
    absr = s["close"].diff().abs()
    prof = absr.groupby(s.index.strftime("%H:%M")).mean().sort_values(ascending=False)
    print(f"\n  timezone check -- busiest 5-min slots (Apr-Sep 2024-25, UTC):"
          f" {', '.join(f'{k} {v:.2f}p' for k, v in prof.head(4).items())}")
    print(f"  12:30 UTC (= 19:30 Thai) ranks #{list(prof.index).index('12:30')+1}"
          f" of {len(prof)}")

    mins = d.index.hour * 60 + d.index.minute
    w = d[(mins >= 12 * 60 + 25) & (mins <= 14 * 60 + 40)].copy()
    w["date"] = w.index.date
    w["slot"] = w.index.strftime("%H:%M")
    piv = w.pivot_table(index="date", columns="slot", values="close")
    piv = piv.dropna()
    piv = piv[(piv.max(axis=1) - piv.min(axis=1)) > 0]       # drop dead holidays
    piv.index = pd.to_datetime(piv.index)
    print(f"\n  complete evenings: {len(piv):,}   slots per evening: {piv.shape[1]}")

    overall = []
    for name, a, b in PERIODS:
        P = piv.loc[a:b]
        if len(P) < 50:
            continue
        C = P.to_numpy(float)
        ev = fade_events(C)
        R = np.diff(C, axis=1)
        nulls = []
        for _ in range(NSHUF):
            Rs = np.apply_along_axis(RNG.permutation, 1, R)
            Cs = np.concatenate([C[:, :1], C[:, :1] + np.cumsum(Rs, axis=1)], axis=1)
            nulls.append(fade_events(Cs))
        med5 = np.median(np.abs(R))
        print("\n" + "-" * 84)
        print(f" {name}   {len(P)} evenings   typical 5-min candle {med5:.2f} pts"
              f"   gold ~{C[:, 0].mean():,.0f}")
        print("-" * 84)
        overall.append((name, table(ev, nulls, "fade the last 5-min candle")))

    print("\n" + "=" * 84)
    print(" BEST CELL IN EACH PERIOD, after the 0.26 spread")
    print("=" * 84)
    for name, best in overall:
        if best:
            net, tag, mins_, m, t = best
            print(f"  {name}:  move {tag} pts, hold {mins_}m  ->  comes back {m:+.2f}p,"
                  f"  net {net:+.2f}p per trade  (t beyond shuffle {t:.1f})")
    print("\n  'comes back' = points recovered after trading against the candle.")
    print("  'beyond' = what is left after subtracting what shuffled evenings")
    print("  give. 'after spread' is what a trader would actually keep.")
    print("=" * 84)
    return 0


if __name__ == "__main__":
    sys.exit(main())
