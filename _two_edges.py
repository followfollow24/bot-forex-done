"""The smoother month you are asking for, from what already passed.

You do not need a new edge to change the SHAPE of the returns. You need a
second one that does not move with the first. This project already has two
that cleared the honest bar:

  gold   H1 trend-pullback (gold_time_BLK20_01 monthly series)
  btc    Combo LongBias

They trade different assets on different timescales, so the question is
simply whether their monthly returns move together. If they do not,
combining them cuts the drawdown and the losing streaks WITHOUT anyone
finding anything new.

Everything below is measured on the saved monthly series in
research_2026_08_07/, over the window where both exist.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

R = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "research_2026_08_07")


def load(name):
    d = pd.read_csv(os.path.join(R, name))
    d["month"] = pd.PeriodIndex(d["month"], freq="M")
    return d.set_index("month")["ret_pct"] / 100.0


def profile(name, r):
    eq = (1 + r).cumprod()
    yrs = len(r) / 12.0
    cagr = eq.iloc[-1] ** (1 / yrs) - 1
    dd = float((eq / eq.cummax() - 1).min())
    sh = (r.mean() * 12) / (r.std() * np.sqrt(12)) if r.std() > 0 else np.nan
    run = worst = 0
    for v in r:
        run = run + 1 if v < 0 else 0
        worst = max(worst, run)
    return dict(name=name, cagr=cagr, dd=dd, sharpe=sh,
                med=r.median(), pos=(r > 0).mean(), streak=worst,
                best=r.max(), wor=r.min())


def show(rows):
    print(f"\n{'':>22}{'CAGR':>9}{'maxDD':>8}{'Sharpe':>8}"
          f"{'median mo':>11}{'mo +':>7}{'worst streak':>14}"
          f"{'best mo':>9}{'worst mo':>10}")
    for d in rows:
        print(f"{d['name']:>22}{d['cagr']*100:>8.1f}%{d['dd']*100:>7.0f}%"
              f"{d['sharpe']:>8.2f}{d['med']*100:>10.2f}%{d['pos']*100:>6.0f}%"
              f"{str(d['streak'])+' months':>14}"
              f"{d['best']*100:>8.0f}%{d['wor']*100:>9.0f}%")


def main():
    gold = load("gold_time_BLK20_01_monthly.csv")
    btc = load("btc_combo_longbias_monthly.csv")
    idx = gold.index.intersection(btc.index)
    g, b = gold.reindex(idx), btc.reindex(idx)

    print("=" * 104)
    print(f" TWO EDGES THAT ALREADY PASSED, PUT TOGETHER"
          f"   {idx[0]} .. {idx[-1]}   ({len(idx)} months)")
    print("=" * 104)

    c = float(np.corrcoef(g, b)[0, 1])
    print(f"\n  correlation of the two monthly series = {c:+.3f}")
    print(f"  -> they are {'essentially UNRELATED' if abs(c) < 0.25 else 'related'}."
          f" That is the whole point: gold's bad months are not BTC's.")
    both_down = ((g < 0) & (b < 0)).mean()
    print(f"  both negative in the same month: {both_down*100:.0f}%"
          f"   (if they were identical it would be"
          f" {max((g<0).mean(), (b<0).mean())*100:.0f}%)")

    # inverse-vol weights, computed ONCE on the whole window (a live system
    # would re-estimate on a trailing window; this is the idealised version
    # and is therefore slightly flattering)
    wg = (1 / g.std()) / ((1 / g.std()) + (1 / b.std()))
    wb = 1 - wg
    mix = wg * g + wb * b
    half = wg * g * 0.5 + wb * b * 0.5      # same mix at half the risk

    print(f"\n  inverse-vol weights: gold {wg*100:.0f}%, btc {wb*100:.0f}%")

    rows = [profile("gold alone", g), profile("btc combo alone", b),
            profile("MIX (inverse-vol)", mix),
            profile("MIX at half risk", half)]
    show(rows)

    print("\n" + "=" * 104)
    print(" WHAT THE MIX ACTUALLY BUYS YOU")
    print("=" * 104)
    m, gp, bp = rows[2], rows[0], rows[1]
    print(f"\n  worst losing streak  gold {gp['streak']} months,"
          f" btc {bp['streak']} months  ->  MIX {m['streak']} months")
    print(f"  max drawdown         gold {gp['dd']*100:.0f}%,"
          f" btc {bp['dd']*100:.0f}%  ->  MIX {m['dd']*100:.0f}%")
    print(f"  positive months      gold {gp['pos']*100:.0f}%,"
          f" btc {bp['pos']*100:.0f}%  ->  MIX {m['pos']*100:.0f}%")
    print(f"  Sharpe               gold {gp['sharpe']:.2f},"
          f" btc {bp['sharpe']:.2f}  ->  MIX {m['sharpe']:.2f}")

    print("\n" + "=" * 104)
    print(" YEAR BY YEAR -- where the two rescue each other")
    print("=" * 104)
    yr = pd.DataFrame({"gold": g, "btc": b, "mix": mix})
    yr.index = yr.index.to_timestamp()
    ann = yr.resample("YE").apply(lambda x: (1 + x).prod() - 1)
    print(f"\n{'year':>7}{'gold':>10}{'btc':>10}{'mix':>10}   rescued by")
    for d, row in ann.iterrows():
        who = ""
        if row["gold"] < 0 and row["btc"] > 0:
            who = "btc carried gold"
        elif row["btc"] < 0 and row["gold"] > 0:
            who = "gold carried btc"
        elif row["gold"] < 0 and row["btc"] < 0:
            who = "both down"
        print(f"{d.year:>7}{row['gold']*100:>9.1f}%{row['btc']*100:>9.1f}%"
              f"{row['mix']*100:>9.1f}%   {who}")

    print("\n" + "=" * 104)
    print(" Weights are fitted on the whole window, so the mix is flattered")
    print(" a little. Both series are backtests. Nothing here is new research")
    print(" -- it is the two things that already passed, held at once.")
    print("=" * 104)
    return 0


if __name__ == "__main__":
    sys.exit(main())
