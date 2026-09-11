"""Which coin should a Binance spot account actually hold?

Runs the FROZEN Combo LongBias engine -- imported from the live bot, not
re-typed -- on every coin we have Binance data for, with Binance SPOT costs
(a fee per side on turnover, no swap, because you own the coin).

Three things decide the answer, and all three are printed:

  1  does it beat BUY-AND-HOLD of that same coin?  A strategy that does not
     is a worse way to own the coin than owning the coin.
  2  does it work in BOTH halves of its own history?  One-half results are
     what this project has thrown away all session.
  3  are the coins independent?  Twelve alts that follow BTC are ONE bet
     with twelve lots of fees, and the correlation matrix says which.

No tuning. The engine is whatever the live bot uses.
"""
from __future__ import annotations
import os, sys, glob
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from daily_sleeves_bot import combo_daily_frame     # the live engine, verbatim

DL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "download")
FEE_BPS = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0   # per side, spot
ANN = 365.0


def load_daily(path):
    """Two formats live in download/: headerless epoch-ms, and a header row
    with ISO timestamps. Reading one as the other silently yields an empty
    frame and the coin vanishes from the table -- so handle both and SAY
    when a file produces nothing."""
    head = open(path, "r").readline().lower()
    has_header = head.startswith(("timestamp", "date", "time"))
    d = pd.read_csv(path, low_memory=False) if has_header else \
        pd.read_csv(path, header=None, low_memory=False,
                    names=["timestamp", "open", "high", "low", "close"])
    d.columns = [str(c).strip().lower() for c in d.columns]
    tcol = d.columns[0]

    # BOTH formats carry the header word "timestamp" -- what differs is the
    # VALUE: epoch milliseconds in some files, an ISO string in others.
    # pd.to_datetime on bare integers assumes NANOSECONDS and silently maps
    # every row to 1970, which collapses the series to one day and makes the
    # coin disappear. Decide on the value, never on the header.
    ts_num = pd.to_numeric(d[tcol], errors="coerce")
    if ts_num.notna().mean() > 0.9:
        d = d[ts_num.notna()].copy()
        d["dt"] = pd.to_datetime(ts_num[ts_num.notna()].astype("int64"),
                                 unit="ms", utc=True)
    else:
        d["dt"] = pd.to_datetime(d[tcol], utc=True, errors="coerce")
    d["close"] = pd.to_numeric(d["close"], errors="coerce")
    d = d.dropna(subset=["dt", "close"]).set_index("dt").sort_index()
    return d["close"].resample("1D").last().dropna()


def run(close, fee_bps):
    """Daily equity curve of the combo, decided on close, held next day."""
    f = combo_daily_frame(close)
    w = pd.Series(f["target_frac"].to_numpy(), index=close.index).shift(1).fillna(0.0)
    r = close.pct_change().fillna(0.0)
    turn = w.diff().abs().fillna(w.abs())
    net = w * r - turn * (fee_bps / 1e4)
    return net, w


def stats(net, years):
    eq = (1.0 + net).cumprod()
    cagr = eq.iloc[-1] ** (1.0 / years) - 1.0 if years > 0 else np.nan
    sd = net.std() * np.sqrt(ANN)
    sharpe = (net.mean() * ANN) / sd if sd > 0 else np.nan
    dd = float((eq / eq.cummax() - 1.0).min())
    return cagr, sharpe, dd


def main():
    files = sorted(glob.glob(os.path.join(DL, "*-1h-binance.csv")))
    btc = os.path.join(DL, "btcusdt-15m-binance-2017-08-17-2026-06-30.csv")
    if os.path.exists(btc):
        files = [btc] + files

    print("=" * 100)
    print(f" WHICH COIN?  Combo LongBias (frozen live engine)"
          f"   Binance spot fee {FEE_BPS:.1f} bps/side, no swap")
    print("=" * 100)
    # BTC buy-and-hold is the benchmark that matters: if a strategy on an
    # alt cannot beat simply owning BTC over the SAME window, holding the
    # alt is a worse way to own crypto beta.
    btc_close = load_daily(btc) if os.path.exists(btc) else None

    print(f"\n{'coin':>7}{'days':>7}{'from':>9}{'CAGR':>9}{'own B&H':>9}"
          f"{'BTC B&H':>9}{'beats BTC':>11}{'Sharpe':>8}{'maxDD':>7}"
          f"{'1st half':>10}{'2nd half':>10}{'both+':>7}")

    rows, curves = [], {}
    for path in files:
        name = os.path.basename(path).split("-")[0].replace("usdt", "").upper()
        try:
            close = load_daily(path)
        except Exception as exc:
            print(f"{name:>9}  unreadable: {exc!r}")
            continue
        if len(close) < 400:
            print(f"{name:>7}  only {len(close)} daily bars -- SKIPPED"
                  f" (check the file format, not the coin)")
            continue
        net, w = run(close, FEE_BPS)
        years = len(close) / ANN
        cagr, sh, dd = stats(net, years)
        bh = close.iloc[-1] / close.iloc[0]
        bh_cagr = bh ** (1.0 / years) - 1.0

        # BTC buy-and-hold over exactly this coin's window
        btc_bh = np.nan
        if btc_close is not None:
            seg = btc_close.reindex(close.index).ffill().dropna()
            if len(seg) > 100:
                yrs = len(seg) / ANN
                btc_bh = (seg.iloc[-1] / seg.iloc[0]) ** (1.0 / yrs) - 1.0

        mid = len(net) // 2
        h1 = (1 + net.iloc[:mid]).prod() ** (ANN / mid) - 1.0
        h2 = (1 + net.iloc[mid:]).prod() ** (ANN / (len(net) - mid)) - 1.0
        both = h1 > 0 and h2 > 0

        beats_btc = bool(cagr > btc_bh) if not np.isnan(btc_bh) else False
        curves[name] = net
        rows.append((name, cagr, bh_cagr, beats_btc, sh, dd, h1, h2, both,
                     btc_bh))
        print(f"{name:>7}{len(close):>7}{close.index[0]:%Y-%m}".rjust(23)
              + f"{cagr*100:>8.1f}%{bh_cagr*100:>8.1f}%"
              + f"{btc_bh*100:>8.1f}%"
              + f"{('YES' if beats_btc else 'no'):>11}"
              + f"{sh:>8.2f}{dd*100:>6.0f}%"
              + f"{h1*100:>9.1f}%{h2*100:>9.1f}%"
              + f"{('YES' if both else 'no'):>7}")

    if not rows:
        print("\n  no usable data"); return 0

    beat = [r for r in rows if r[3]]
    both = [r for r in rows if r[8]]
    survive = [r for r in rows if r[3] and r[8]]
    print("\n" + "=" * 100)
    print(" THE THREE FILTERS")
    print("=" * 100)
    print(f"\n  {len(rows)} coins tested")
    print(f"  beat BTC buy-and-hold ............. {len(beat):>2}"
          f"   {', '.join(r[0] for r in beat) or '(none)'}")
    print(f"  positive in BOTH halves ............ {len(both):>2}"
          f"   {', '.join(r[0] for r in both) or '(none)'}")
    print(f"  BOTH of the above ................. {len(survive):>2}"
          f"   {', '.join(r[0] for r in survive) or '(none)'}")

    print("\n" + "=" * 100)
    print(" ARE THESE INDEPENDENT BETS, OR ONE BET REPEATED?")
    print("=" * 100)
    allm = pd.DataFrame(curves).dropna(how="all")
    corr = allm.corr()
    v = corr.values[np.triu_indices_from(corr.values, k=1)]
    v = v[~np.isnan(v)]
    if len(v):
        mean_c = float(v.mean())
        n = len(corr)
        n_eff = n / (1.0 + (n - 1) * max(mean_c, 0.0))
        print(f"\n  mean pairwise correlation of the STRATEGY returns"
              f" = {mean_c:+.3f}   max = {v.max():+.2f}")
        print(f"  nominal coins = {n}"
              f"   EFFECTIVE independent bets = {n_eff:.1f}")
        if "BTC" in corr.columns:
            vs = corr["BTC"].drop("BTC").sort_values(ascending=False)
            print(f"\n  correlation to BTC:  highest"
                  f" {vs.index[0]} {vs.iloc[0]:+.2f}"
                  f"   lowest {vs.index[-1]} {vs.iloc[-1]:+.2f}"
                  f"   mean {vs.mean():+.2f}")
        if mean_c > 0.6:
            print("\n  -> this is close to ONE crypto bet paying"
                  " N sets of fees, not N bets.")

    print("\n" + "=" * 100)
    print(" Costs are fee-only (spot: you own the coin, no funding, no swap).")
    print(" Alt spreads/slippage are NOT modelled and are worse than BTC's,")
    print(" so any alt that merely ties BTC here loses in reality.")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    sys.exit(main())
