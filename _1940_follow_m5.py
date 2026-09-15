"""Is 'follow the 5-minute move at 19:40-20:00' real, or just 2026?

The tick replay (86 evenings, May-Sep 2026) found following a >= 6-point
5-minute candle in 19:40-20:00 Thai positive in every setting and both
halves. That sample is one regime, and the window came from the operator
eyeballing charts inside it. Two checks on 13 years of M5 gold:

  1  EVERY YEAR, 2013-2026. Thresholds are scaled to price (6 pts at
     $4,000 = 0.15%; the 5-pt gate = 0.125%), so a 2013 candle is judged
     like a 2026 one. Exit is a fixed 10 minutes later on bar closes --
     M5 bars cannot say whether a +1 TP or a -5 SL came first, but they
     can say whether the move CONTINUED, which is the whole claim.

  2  THE NEWS CLOCK. 19:30 Thai is the US 8:30 data release only while
     the US is on summer time. In winter the release is at 20:30 Thai.
     If the effect is post-news momentum it must follow the release:
       summer, gate 19:30 / trade 19:40-20:00       (the claim)
       winter, gate 20:30 / trade 20:40-21:00       (same news, moved)
       winter, gate 19:30 / trade 19:40-20:00       (no news -- control)

Null: each evening's 5-minute moves shuffled (first price held), same
rule, 200 times. Spread 0.26 pts charged per trade.
"""
from __future__ import annotations
import os, sys
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd

M5 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "download",
                  "xauusd-m5-bid-2013-01-01-2026-06-01.csv")
SPREAD = 0.26
MIN_PCT, GATE_PCT = 0.0015, 0.00125
HOLD = 2                                     # bars = 10 minutes
NY = ZoneInfo("America/New_York")
RNG = np.random.default_rng(1940)


def slots(h, m, n):
    out = []
    for k in range(n):
        mm = h * 60 + m + 5 * k
        out.append(f"{mm // 60:02d}:{mm % 60:02d}")
    return out


def trades_for(O, C, gate_col, sig_cols, gate_on):
    """O, C: evenings x slots (open, close). Returns net points per trade and
    evening index. Entry at the signal candle's close, exit HOLD bars later."""
    px = C[:, gate_col]
    ok_gate = np.abs(C[:, gate_col] - O[:, gate_col]) >= GATE_PCT * px if gate_on \
        else np.ones(len(C), bool)
    nets, days = [], []
    for e in range(len(C)):
        if not ok_gate[e]:
            continue
        free_from = -1
        for s in sig_cols:
            if s < free_from or s + HOLD >= C.shape[1]:
                continue
            move = C[e, s] - O[e, s]
            if abs(move) < MIN_PCT * C[e, s] or move == 0:
                continue
            d = 1.0 if move > 0 else -1.0
            nets.append(d * (C[e, s + HOLD] - C[e, s]) - SPREAD)
            days.append(e)
            free_from = s + HOLD
    return np.array(nets), np.array(days, int)


def shuffle_closes(O, C):
    """keep each evening's first open, shuffle its bar-to-bar moves; rebuild
    opens as the previous close (M5 gold bars are contiguous)."""
    R = np.diff(np.concatenate([O[:, :1], C], axis=1), axis=1)
    Rs = np.apply_along_axis(RNG.permutation, 1, R)
    Cs = O[:, :1] + np.cumsum(Rs, axis=1)
    Os = np.concatenate([O[:, :1], Cs[:, :-1]], axis=1)
    return Os, Cs


def main():
    d = pd.read_csv(M5)
    d["timestamp"] = pd.to_datetime(d["timestamp"], utc=True)
    d = d.set_index("timestamp")
    hm = d.index.strftime("%H:%M")
    cols = slots(12, 25, 22)                   # 12:25 .. 14:10 UTC
    w = d[np.isin(hm, cols)].copy()
    w["date"] = w.index.date
    w["slot"] = w.index.strftime("%H:%M")
    Op = w.pivot_table(index="date", columns="slot", values="open").reindex(columns=cols)
    Cp = w.pivot_table(index="date", columns="slot", values="close").reindex(columns=cols)
    good = Op.notna().all(axis=1) & Cp.notna().all(axis=1) & \
        ((Cp.max(axis=1) - Cp.min(axis=1)) > 0)
    Op, Cp = Op[good], Cp[good]
    dates = pd.to_datetime(Op.index)
    dst = np.array([bool(pd.Timestamp(dt).tz_localize("UTC").tz_convert(NY).dst())
                    for dt in dates + pd.Timedelta(hours=12, minutes=30)])
    years = dates.year.to_numpy()
    O, C = Op.to_numpy(float), Cp.to_numpy(float)
    ix = {c: i for i, c in enumerate(cols)}

    variants = [
        ("SUMMER news  gate 19:30 trade 19:40-20:00", dst, ix["12:30"], [ix[s] for s in slots(12, 40, 4)]),
        ("WINTER news  gate 20:30 trade 20:40-21:00", ~dst, ix["13:30"], [ix[s] for s in slots(13, 40, 4)]),
        ("WINTER no-news gate 19:30 trade 19:40-20:00", ~dst, ix["12:30"], [ix[s] for s in slots(12, 40, 4)]),
    ]

    print("=" * 96)
    print(" FOLLOW THE 5-MIN MOVE AFTER THE US RELEASE -- gold M5 2013-2026,"
          " exit 10 min later, spread 0.26")
    print(f" candle >= {MIN_PCT*100:.3f}% of price (6 pts @ $4,000), gate >= {GATE_PCT*100:.4f}% (5 pts)")
    print("=" * 96)

    for gate_on in (True, False):
        print(f"\n{'#'*96}\n  DAY GATE {'ON (19:30-equivalent candle must move)' if gate_on else 'OFF (every day)'}\n{'#'*96}")
        for name, mask, gcol, scols in variants:
            Om, Cm, ym = O[mask], C[mask], years[mask]
            nets, days = trades_for(Om, Cm, gcol, scols, gate_on)
            null = []
            for _ in range(200):
                Os, Cs = shuffle_closes(Om, Cm)
                nn, _ = trades_for(Os, Cs, gcol, scols, gate_on)
                null.append(nn.mean() if len(nn) else np.nan)
            null = np.array(null)
            real = nets.mean() if len(nets) else np.nan
            p_beat = np.nanmean(null < real)
            print(f"\n  {name}")
            print(f"  all years: {len(nets)} trades on {len(set(days))} evenings,"
                  f" net {real:+.3f} pts/trade, {np.mean(nets > 0)*100 if len(nets) else 0:.0f}% positive,"
                  f" sum {nets.sum():+.1f} pts")
            print(f"  shuffle null: mean {np.nanmean(null):+.3f}, 95th {np.nanpercentile(null, 95):+.3f}"
                  f"  -> real beats {p_beat*100:.0f}%")
            yr_line = "   "
            for y in range(2013, 2027):
                sel = ym[days] == y if len(days) else np.array([], bool)
                if sel.sum() == 0:
                    yr_line += f" {y%100:02d}:  --  "
                    continue
                yr_line += f" {y%100:02d}:{nets[sel].mean():+5.2f}({sel.sum():>2})"
            print(yr_line)
    print("\n  per year: net pts/trade (trades). Gold was ~$1,200-1,800 before 2020, so")
    print("  pre-2020 points are small by construction; the SIGN is what to read.")
    print("=" * 96)


if __name__ == "__main__":
    main()
