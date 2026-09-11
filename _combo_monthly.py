"""What does the combo actually pay PER MONTH?

A CAGR is an average of a thing that never happens. "40%/yr" reads as
"3.3% a month" and that is not what this is: the rule is flat roughly 60%
of the time, so most months are nothing and a few carry everything. If you
are going to run it on real money you have to know the shape, not the mean.

Market: BTCUSDT, Binance SPOT, long-or-flat, fees both ways, no swap.
(The live btc_combo_lb ran on MT5 Exness BTCUSDc instead -- a cent account
whose 0.01-lot step made it refuse to trade at all below ~464 USD.)
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _binance_capital as M
from _which_coin import load_daily
from daily_sleeves_bot import combo_daily_frame

ANN = 365.0


def main():
    close = load_daily(M.BTC)
    f = combo_daily_frame(close)
    w = pd.Series(f["target_frac"].to_numpy(),
                  index=close.index).shift(1).fillna(0.0)
    eq, blocked, done, fees = M.simulate(close, w, 50.0, 10.0)

    m = eq.resample("ME").last()
    mr = m.pct_change().dropna()
    bh = close.resample("ME").last().pct_change().dropna().reindex(mr.index)

    print("=" * 84)
    print(" COMBO (BTC) PER MONTH -- Binance spot, 10 bps/side, start 50 USD")
    print(f" {close.index[0]:%Y-%m} to {close.index[-1]:%Y-%m}"
          f"   {len(mr)} months   {done} trades   fees {fees:.0f} USD")
    print("=" * 84)

    def block(name, x):
        pos = (x > 0).mean()
        flat = (x.abs() < 0.005).mean()
        print(f"\n  {name}")
        print(f"     mean {x.mean()*100:>7.2f}%     median {x.median()*100:>7.2f}%"
              f"     sd {x.std()*100:>6.2f}%")
        print(f"     best {x.max()*100:>7.1f}%      worst {x.min()*100:>7.1f}%")
        print(f"     months positive {pos*100:>5.1f}%"
              f"     months within +/-0.5% (nothing happened) {flat*100:>5.1f}%")

    block("ALL 106 MONTHS", mr)
    h = len(mr) // 2
    block(f"1st half  ({mr.index[0]:%Y-%m} .. {mr.index[h-1]:%Y-%m})", mr.iloc[:h])
    block(f"2nd half  ({mr.index[h]:%Y-%m} .. {mr.index[-1]:%Y-%m})", mr.iloc[h:])

    print("\n" + "=" * 84)
    print(" THE SHAPE: how concentrated is it?")
    print("=" * 84)
    srt = mr.sort_values(ascending=False)
    tot = float((1 + mr).prod() - 1)
    for k in (1, 3, 6, 12):
        rest = float((1 + mr.drop(srt.index[:k])).prod() - 1)
        print(f"\n  remove the best {k:>2} month(s) of {len(mr)}:"
              f"  total {tot*100:>10,.0f}%  ->  {rest*100:>9,.0f}%")

    print("\n" + "=" * 84)
    print(" LOSING STREAKS (consecutive negative months)")
    print("=" * 84)
    run = best_run = 0
    for v in mr:
        run = run + 1 if v < 0 else 0
        best_run = max(best_run, run)
    under = (eq / eq.cummax() - 1)
    below = under < -0.20
    longest, cur = 0, 0
    for b in below:
        cur = cur + 1 if b else 0
        longest = max(longest, cur)
    print(f"\n  longest run of losing months: {best_run}")
    print(f"  longest stretch more than 20% below the prior peak:"
          f" {longest} days ({longest/30.4:.1f} months)")

    print("\n" + "=" * 84)
    print(" YEAR BY YEAR")
    print("=" * 84)
    y = eq.resample("YE").last().pct_change().dropna()
    yb = close.resample("YE").last().pct_change().dropna().reindex(y.index)
    print(f"\n{'year':>7}{'combo':>10}{'hold BTC':>11}{'better?':>10}")
    for d, v in y.items():
        b = yb.loc[d]
        print(f"{d.year:>7}{v*100:>9.1f}%{b*100:>10.1f}%"
              f"{('YES' if v > b else 'no'):>10}")
    print(f"\n  combo beat holding in {int((y > yb).sum())} of {len(y)} years")

    print("\n" + "=" * 84)
    print(" THE HONEST MONTHLY NUMBER")
    print("=" * 84)
    h2 = mr.iloc[h:]
    print(f"\n  Do NOT quote the mean -- it is dragged by a handful of months.")
    print(f"  The MEDIAN month in the 2nd half is {h2.median()*100:+.2f}%,")
    print(f"  and {(h2.abs() < 0.005).mean()*100:.0f}% of months do essentially"
          f" nothing.")
    print(f"  Compounding the 2nd half gives"
          f" {((1+h2).prod()**(12/len(h2))-1)*100:.1f}%/yr"
          f" = {((1+h2).prod()**(1/len(h2))-1)*100:.2f}%/month geometric.")
    print("=" * 84)
    return 0


if __name__ == "__main__":
    sys.exit(main())
