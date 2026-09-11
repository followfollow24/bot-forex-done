"""How do you actually catch a 1000% move?

The operator wants alts with a shot at +1000% (11x). Nobody can name which
coin will do it, and this script does not pretend to. What it CAN answer,
from the Binance data we hold:

  1  BASE RATE -- over every rolling 365-day window, how often does a coin
     gain >= 1000%?  That is the real prior, before anyone's opinion.
  2  THE PROFILE -- when it did happen, where did the run start, how long
     did it take, and what drawdown did you have to sit through first?
  3  CAN A RULE CATCH IT -- was the frozen combo engine actually long
     through those runs, and how much of each one did it collect?  If a
     trend follower captures most of it, you do not need to pick.
  4  THE SIZING -- what a 1-in-N lottery is worth at 50 and 200 USD.

THE BIAS THAT CANNOT BE FIXED HERE: every coin in download/ survived to
today. LUNA, FTT and the rest are absent. So every base rate below is an
OVERSTATEMENT -- the true denominator includes coins that went to zero.
"""
from __future__ import annotations
import os, sys, glob
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _which_coin import load_daily, run          # same loader, same engine
from daily_sleeves_bot import combo_daily_frame

DL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "download")
MULT = 11.0            # +1000%
WIN = 365
FEE_BPS = 10.0


def main():
    files = sorted(glob.glob(os.path.join(DL, "*-1h-binance.csv")))
    btc = os.path.join(DL, "btcusdt-15m-binance-2017-08-17-2026-06-30.csv")
    if os.path.exists(btc):
        files = [btc] + files

    print("=" * 96)
    print(f" CHASING +1000%  ({MULT:.0f}x within {WIN} days)"
          f"   -- survivors-only universe, so every rate here is too HIGH")
    print("=" * 96)

    tot_win = hit_win = 0
    best = []
    for path in files:
        name = os.path.basename(path).split("-")[0].replace("usdt", "").upper()
        try:
            c = load_daily(path)
        except Exception:
            continue
        if len(c) < WIN + 60:
            continue
        v = c.to_numpy()
        fwd_max = pd.Series(v).rolling(WIN).max().shift(-(WIN - 1)).to_numpy()
        ratio = fwd_max / v
        ok = ~np.isnan(ratio)
        tot_win += int(ok.sum())
        hit_win += int((ratio[ok] >= MULT).sum())

        i = int(np.nanargmax(ratio)) if ok.any() else 0
        peak_rel = float(ratio[i])
        j = i + int(np.nanargmax(v[i:i + WIN]))
        # worst drawdown in the 180 days BEFORE the run started
        pre = v[max(0, i - 180):i + 1]
        pre_dd = float(pre.min() / pre.max() - 1.0) if len(pre) > 5 else np.nan

        # what the frozen engine held through that run
        net, w = run(c, FEE_BPS)
        wseg = w.to_numpy()[i:j + 1]
        held = float(np.mean(wseg)) if len(wseg) else 0.0
        capt = float((1 + net.iloc[i:j + 1]).prod() - 1.0) if j > i else 0.0
        best.append((name, peak_rel, c.index[i], c.index[j], (j - i),
                     pre_dd, held, capt))

    print(f"\n  rolling {WIN}-day windows examined: {tot_win:,}")
    print(f"  windows that produced {MULT:.0f}x or more: {hit_win:,}"
          f"   = {hit_win/max(tot_win,1)*100:.2f}%")
    print(f"  -> on this survivors-only set, a randomly chosen day on a")
    print(f"     randomly chosen coin precedes a {MULT:.0f}x year"
          f" {hit_win/max(tot_win,1)*100:.1f}% of the time.")

    print("\n" + "=" * 96)
    print(" EVERY COIN'S SINGLE BEST RUN -- and what the trend rule collected")
    print("=" * 96)
    print(f"\n{'coin':>7}{'best run':>10}{'started':>12}{'peaked':>12}"
          f"{'days':>6}{'drawdown before':>17}{'rule long':>11}{'rule got':>11}")
    for name, pk, d0, d1, nd, pdd, held, capt in sorted(
            best, key=lambda r: -r[1]):
        print(f"{name:>7}{pk:>9.1f}x{d0:%Y-%m-%d}".rjust(29)
              + f"{d1:%Y-%m-%d}".rjust(12)
              + f"{nd:>6}"
              + (f"{pdd*100:>16.0f}%" if not np.isnan(pdd) else f"{'--':>17}")
              + f"{held*100:>10.0f}%"
              + f"{capt*100:>10.0f}%")

    tenx = [r for r in best if r[1] >= MULT]
    print(f"\n  {len(tenx)} of {len(best)} coins ever had a {MULT:.0f}x year:"
          f"  {', '.join(r[0] for r in tenx) or '(none)'}")
    if tenx:
        mean_held = np.mean([r[6] for r in tenx])
        mean_capt = np.mean([r[7] for r in tenx])
        print(f"  through those runs the frozen rule was long"
              f" {mean_held*100:.0f}% of the time on average")
        print(f"  and collected {mean_capt*100:.0f}% on average"
              f" -- WITHOUT anyone picking the coin in advance.")

    print("\n" + "=" * 96)
    print(" WHAT A LOTTERY TICKET IS WORTH")
    print("=" * 96)
    p = hit_win / max(tot_win, 1)
    print(f"\n  Suppose you buy N equal tickets and each independently has"
          f" the measured")
    print(f"  {p*100:.1f}% chance of an {MULT:.0f}x year, and is worth ~0"
          f" otherwise (the honest")
    print(f"  assumption once dead coins are counted).")
    print(f"\n{'capital':>9}{'tickets':>9}{'per ticket':>12}"
          f"{'P(>=1 hit)':>12}{'EV':>10}{'P(total loss)':>15}")
    for cap in (50.0, 200.0, 1000.0):
        for n in (5, 20):
            per = cap / n
            if per < 5.0:            # Binance MIN_NOTIONAL
                continue
            p_any = 1 - (1 - p) ** n
            ev = n * per * (p * MULT)
            print(f"{cap:>8.0f}${n:>9}{per:>11.2f}$"
                  f"{p_any*100:>11.1f}%{ev:>9.0f}${(1-p)**n*100:>14.1f}%")
    print(f"\n  EV looks positive only because the losers are assumed to lose")
    print(f"  100%, not more, and because the {p*100:.1f}% comes from coins"
          f" that SURVIVED.")
    print(f"  Count the dead and the same arithmetic turns negative -- which")
    print(f"  is exactly why the measured answer is a RULE, not a pick.")
    print("=" * 96)
    return 0


if __name__ == "__main__":
    sys.exit(main())
