#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Systematically search all market pairs instead of the 12 hand-guessed ones.

The current book leans hard on BRENT/WTI (Sharpe 2.04 of a 3-of-12 hit rate).
More working pairs is the fix -- but searching every combination over the full
history and keeping the best would be pure overfitting: with 34 markets there
are 561 pairs, and roughly 5% will look great on noise alone.

So the search is honest by construction:
  - candidates are SELECTED on the first half of history only
  - selection needs BOTH a cointegration-style statistical test AND positive
    train Sharpe, so it is not just a return screen
  - everything is then scored ONLY on the second half, which the selection
    never saw. That out-of-sample number is the result.
  - a random-pair control is run the same way, so we can see how much of the
    OOS performance is just what any pair would give under this procedure
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
from itertools import combinations

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _new_signal_classes import load_all_daily, build_panel, TRADING_DAYS
from _pairs_validate import pair_returns, perf

ENTRY_Z, EXIT_Z, WIN = 1.5, 0.5, 60      # best-of-family from the sensitivity grid
COST_BPS = 5.0
MIN_TRAIN_SHARPE = 0.5
MAX_ADF_P = 0.10                          # spread must look mean-reverting in train
MIN_OVERLAP = 1500

# The first run of this search selected almost nothing but XXX/VIX pairs, all
# with drawdowns of 200-2500% (i.e. the equity went negative). Two reasons, both
# disqualifying:
#   - VIX is strongly mean-reverting BY ITSELF, so ADF on log(A)-log(VIX) passes
#     for any A. The test was measuring VIX, not a relationship.
#   - VIX is an index, not a tradeable spot instrument, and its daily moves
#     (+-30%) make the log-spread explode.
# So: drop non-tradeable legs, and require the pair to survive as an actual
# equity curve rather than just score a Sharpe.
EXCLUDE = {"VIX"}
MAX_ACCEPTABLE_DD = 60.0                  # a pair whose equity halves is not a pair trade


def adf_pvalue(x: pd.Series) -> float:
    """Augmented Dickey-Fuller p-value; falls back to a variance-ratio proxy."""
    try:
        from statsmodels.tsa.stattools import adfuller
        return float(adfuller(x.dropna(), maxlag=5, autolag=None)[1])
    except Exception:
        # proxy: lag-1 AR coefficient well below 1 implies mean reversion
        s = x.dropna()
        if len(s) < 200:
            return 1.0
        s1, s0 = s.iloc[1:].values, s.iloc[:-1].values
        beta = np.polyfit(s0, s1, 1)[0]
        return 0.01 if beta < 0.97 else 0.99


def main():
    px = build_panel(load_all_daily())
    px = px.drop(columns=[c for c in px.columns if c in EXCLUDE], errors="ignore")
    cols = list(px.columns)
    print(f"[panel] {len(cols)} markets (excluded: {', '.join(sorted(EXCLUDE))}), {px.shape[0]} days")
    split = px.index[len(px) // 2]
    print(f"[split] train ..{split.date()}   test {split.date()}..\n")

    tr_px, te_px = px.loc[:split], px.loc[split:]

    print("=" * 96)
    print(" SELECTION on TRAIN half only (stat test + train Sharpe)")
    print("=" * 96)
    selected = []
    tested = 0
    for a, b in combinations(cols, 2):
        sub = tr_px[[a, b]].dropna()
        if len(sub) < MIN_OVERLAP:
            continue
        tested += 1
        spread = np.log(sub[a]) - np.log(sub[b])
        if adf_pvalue(spread) > MAX_ADF_P:
            continue
        r = pair_returns(tr_px, a, b, ENTRY_Z, EXIT_Z, WIN, COST_BPS)
        if r is None:
            continue
        p = perf(r)
        if p and p["sharpe"] >= MIN_TRAIN_SHARPE and p["dd"] <= MAX_ACCEPTABLE_DD:
            selected.append((a, b, p["sharpe"]))
    selected.sort(key=lambda x: -x[2])
    print(f"  pairs tested        : {tested}")
    print(f"  passed selection    : {len(selected)}")
    for a, b, sh in selected[:25]:
        print(f"    {a}/{b:<12} train Sharpe={sh:.2f}")
    if len(selected) > 25:
        print(f"    ... and {len(selected)-25} more")

    if not selected:
        print("\n  nothing passed selection.")
        return

    print("\n" + "=" * 96)
    print(" OUT-OF-SAMPLE: those same pairs, scored on the TEST half only")
    print("=" * 96)
    oos = {}
    for a, b, _ in selected:
        r = pair_returns(te_px, a, b, ENTRY_Z, EXIT_Z, WIN, COST_BPS)
        if r is not None and len(r) > 300:
            oos[f"{a}/{b}"] = r
    if not oos:
        print("  no OOS returns."); return
    df = pd.concat(oos, axis=1)
    bookr = df.mean(axis=1)
    p_book = perf(bookr, "SELECTED BOOK (equal weight)")

    n_pos = sum(1 for c in df.columns if (perf(df[c]) or {}).get("sharpe", -9) > 0)
    print(f"  pairs positive OOS: {n_pos}/{df.shape[1]}")
    print("\n  top OOS performers:")
    rows = []
    for c in df.columns:
        p = perf(df[c])
        if p: rows.append((p["sharpe"], c, p))
    rows.sort(reverse=True)
    for sh, c, p in rows[:12]:
        print(f"    {c:<22} Sharpe={sh:>6.2f}  CAGR={p['cagr']:>+7.2f}%  DD={p['dd']:>5.1f}%")

    print("\n" + "=" * 96)
    print(" CONTROL: random pairs put through the identical procedure")
    print("=" * 96)
    rng = np.random.default_rng(7)
    ctrl_sharpes = []
    for _ in range(min(len(selected), 30)):
        a, b = rng.choice(cols, 2, replace=False)
        r = pair_returns(te_px, a, b, ENTRY_Z, EXIT_Z, WIN, COST_BPS)
        if r is not None and len(r) > 300:
            p = perf(r)
            if p: ctrl_sharpes.append(p["sharpe"])
    if ctrl_sharpes:
        print(f"  random pairs OOS Sharpe: mean={np.mean(ctrl_sharpes):+.2f}  "
              f"median={np.median(ctrl_sharpes):+.2f}  n={len(ctrl_sharpes)}")
        if p_book:
            edge = p_book["sharpe"] - np.mean(ctrl_sharpes)
            print(f"  selected book OOS Sharpe: {p_book['sharpe']:+.2f}")
            print(f"  -> genuine selection edge: {edge:+.2f} Sharpe over random")

    print("\n" + "=" * 96)
    print(" WITH vs WITHOUT the old BRENT/WTI dependence")
    print("=" * 96)
    has_bw = [c for c in df.columns if set(c.split("/")) == {"BRENT", "WTI"}]
    if has_bw:
        perf(df.drop(columns=has_bw).mean(axis=1), "book excluding BRENT/WTI")
    else:
        print("  BRENT/WTI not selected by the statistical filter")
        perf(bookr, "book (no BRENT/WTI in it anyway)")

    if p_book:
        print("\n" + "=" * 96)
        print(" WHAT THE OOS BOOK SUPPORTS")
        print("=" * 96)
        print(f"  {'target DD':<12}{'scale':>8}{'CAGR%/yr':>12}{'%/day':>10}")
        for target in [10.0, 15.0, 20.0, 25.0]:
            k = target / p_book["dd"]
            p2 = perf(bookr * k)
            if p2:
                dpd = (1 + p2["cagr"] / 100) ** (1 / TRADING_DAYS) * 100 - 100
                print(f"  {target:<12.0f}{k:>7.1f}x{p2['cagr']:>12.2f}{dpd:>10.3f}")


if __name__ == "__main__":
    main()
