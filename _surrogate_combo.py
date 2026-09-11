"""The surrogate test the combo has not faced yet.

Both halves are positive and it beats buy-and-hold. This session has shown
twice that this is not enough: exit geometry passed a surrogate and failed
a two-way split, trending-day 19:30 passed a two-way split and failed a
surrogate. So run the surrogate.

THE CLAIM BEING TESTED is narrow and must be matched exactly: "WHEN this
rule is in the market carries information." Not "BTC went up" -- a
long-biased rule on an asset that rose 30x makes money with no skill at
all. So the statistic is never the raw return; it is always the EXCESS
over buy-and-hold ON THE SAME PRICE PATH, plus the drawdown it saved.

TWO NULLS, because each destroys something different:

  A  ROTATE THE WEIGHTS.  Keep the real price. Take the rule's own weight
     series and rotate it circularly by a random offset. Exposure, the
     number of switches, and the length of every in/out run are preserved
     EXACTLY -- only the alignment with price is destroyed. This is the
     tightest possible null for a timing claim.

  B  BOOTSTRAP THE PRICE.  Stationary block-bootstrap the daily returns
     into new paths that keep short-run structure and the drift, then run
     the REAL engine on them. This asks whether the rule needs genuine
     trends or merely a drifting series.

A real timing edge has to beat both.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _which_coin import load_daily
from daily_sleeves_bot import combo_daily_frame

DL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "download")
BTC = os.path.join(DL, "btcusdt-15m-binance-2017-08-17-2026-06-30.csv")
FEE = 10.0 / 1e4
ANN = 365.0
NDRAW = 1000
NBOOT = 500
MEAN_BLOCK = 20
RNG = np.random.default_rng(20260911)


def metrics(w, r, px):
    """Net-of-fee strategy vs buy-and-hold on the SAME path."""
    turn = np.abs(np.diff(w, prepend=0.0))
    net = w * r - turn * FEE
    eq = np.cumprod(1.0 + net)
    yrs = len(r) / ANN
    cagr = eq[-1] ** (1 / yrs) - 1
    bh = np.cumprod(1.0 + r)
    bh_cagr = bh[-1] ** (1 / yrs) - 1
    dd = float((eq / np.maximum.accumulate(eq) - 1).min())
    bh_dd = float((bh / np.maximum.accumulate(bh) - 1).min())
    sd = net.std() * np.sqrt(ANN)
    return (cagr - bh_cagr,           # excess CAGR
            dd - bh_dd,               # drawdown saved (positive = better)
            (net.mean() * ANN) / sd if sd > 0 else np.nan)


def pct_rank(real, draws, higher_is_better=True):
    d = np.asarray(draws)
    d = d[~np.isnan(d)]
    if len(d) == 0:
        return np.nan, np.nan
    beat = (d < real).mean() if higher_is_better else (d > real).mean()
    p = 1.0 - beat
    return beat, p


def main():
    close = load_daily(BTC)
    f = combo_daily_frame(close)
    w_real = pd.Series(f["target_frac"].to_numpy(),
                       index=close.index).shift(1).fillna(0.0).to_numpy()
    px = close.to_numpy()
    r = np.diff(px) / px[:-1]
    r = np.concatenate([[0.0], r])

    real = metrics(w_real, r, px)
    print("=" * 92)
    print(f" SURROGATE TEST -- Combo LongBias on BTCUSDT"
          f"  {close.index[0]:%Y-%m} to {close.index[-1]:%Y-%m}"
          f"  ({len(px)/ANN:.1f} yr)")
    print("=" * 92)
    print(f"\n  average exposure {w_real.mean()*100:.0f}% of equity,"
          f" {int((np.diff(w_real) != 0).sum())} weight changes")
    print(f"\n  REAL:  excess CAGR {real[0]*100:>+7.1f} pts"
          f"   drawdown saved {real[1]*100:>6.1f} pts"
          f"   Sharpe {real[2]:.2f}")

    # ---- NULL A: rotate the weight series --------------------------------
    n = len(w_real)
    A = []
    for _ in range(NDRAW):
        k = int(RNG.integers(1, n))
        A.append(metrics(np.roll(w_real, k), r, px))
    A = np.array(A)

    print("\n" + "=" * 92)
    print(" NULL A -- same weights, same price, alignment destroyed"
          f"   ({NDRAW} rotations)")
    print("=" * 92)
    print(f"\n{'statistic':>20}{'real':>12}{'null mean':>12}{'null sd':>10}"
          f"{'null 95th':>12}{'beats':>9}{'p':>8}")
    for i, (name, hib) in enumerate([("excess CAGR (pts)", True),
                                     ("drawdown saved (pts)", True),
                                     ("Sharpe", True)]):
        col = A[:, i]
        rv = real[i]
        sc = 100 if i < 2 else 1
        beat, p = pct_rank(rv, col, hib)
        q = np.nanpercentile(col, 95)
        print(f"{name:>20}{rv*sc:>12.2f}{np.nanmean(col)*sc:>12.2f}"
              f"{np.nanstd(col)*sc:>10.2f}{q*sc:>12.2f}"
              f"{beat*100:>8.1f}%{p:>8.3f}")

    # ---- NULL B: bootstrap the price -------------------------------------
    B = []
    p_geo = 1.0 / MEAN_BLOCK
    for _ in range(NBOOT):
        idx = np.empty(n, dtype=int)
        i = 0
        while i < n:
            start = int(RNG.integers(0, n))
            L = min(int(RNG.geometric(p_geo)), n - i)
            for j in range(L):
                idx[i + j] = (start + j) % n
            i += L
        rb = r[idx]
        pb = np.cumprod(1.0 + rb) * px[0]
        fb = combo_daily_frame(pd.Series(pb, index=close.index))
        wb = pd.Series(fb["target_frac"].to_numpy(),
                       index=close.index).shift(1).fillna(0.0).to_numpy()
        B.append(metrics(wb, rb, pb))
    B = np.array(B)

    print("\n" + "=" * 92)
    print(f" NULL B -- real engine on bootstrapped price paths"
          f"   ({NBOOT} draws, mean block {MEAN_BLOCK}d)")
    print("=" * 92)
    print(f"\n{'statistic':>20}{'real':>12}{'null mean':>12}{'null sd':>10}"
          f"{'null 95th':>12}{'beats':>9}{'p':>8}")
    for i, (name, hib) in enumerate([("excess CAGR (pts)", True),
                                     ("drawdown saved (pts)", True),
                                     ("Sharpe", True)]):
        col = B[:, i]
        rv = real[i]
        sc = 100 if i < 2 else 1
        beat, p = pct_rank(rv, col, hib)
        q = np.nanpercentile(col, 95)
        print(f"{name:>20}{rv*sc:>12.2f}{np.nanmean(col)*sc:>12.2f}"
              f"{np.nanstd(col)*sc:>10.2f}{q*sc:>12.2f}"
              f"{beat*100:>8.1f}%{p:>8.3f}")

    print("\n" + "=" * 92)
    print(" NULL B, BLOCK LENGTH SWEEP -- does the block preserve the very")
    print(" structure the rule trades?")
    print("=" * 92)
    print(f"\n{'block':>8}{'null mean':>12}{'null sd':>10}{'real':>10}"
          f"{'beats':>9}{'p':>8}")
    for mb in (2, 5, 10, 20, 60):
        pg = 1.0 / mb
        vals = []
        for _ in range(200):
            idx = np.empty(n, dtype=int); i = 0
            while i < n:
                st = int(RNG.integers(0, n))
                L = min(int(RNG.geometric(pg)), n - i)
                for j in range(L):
                    idx[i + j] = (st + j) % n
                i += L
            rb = r[idx]
            pb = np.cumprod(1.0 + rb) * px[0]
            fb = combo_daily_frame(pd.Series(pb, index=close.index))
            wb = pd.Series(fb["target_frac"].to_numpy(),
                           index=close.index).shift(1).fillna(0.0).to_numpy()
            vals.append(metrics(wb, rb, pb)[0])
        beat, pv = pct_rank(real[0], vals, True)
        print(f"{mb:>7}d{np.nanmean(vals)*100:>12.2f}"
              f"{np.nanstd(vals)*100:>10.2f}{real[0]*100:>10.2f}"
              f"{beat*100:>8.1f}%{pv:>8.3f}")

    print("\n" + "=" * 92)
    print(" A surrogate mean near the real value means the result is the")
    print(" EXPOSURE, not the timing. p above 0.05 on either null is a fail.")
    print("=" * 92)
    return 0


if __name__ == "__main__":
    sys.exit(main())
