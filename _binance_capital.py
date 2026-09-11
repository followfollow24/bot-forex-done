"""Does the combo still work on a REAL small Binance spot account?

The engine passed walk-forward on price. That is not the same as working
on 50 dollars, because a live spot account has mechanics a return series
does not:

  MIN_NOTIONAL   Binance refuses any order under ~5 USDT. When a rebalance
                 asks for less than that, the trade does not happen -- the
                 weight stays wrong until the next one is big enough.
  LOT_SIZE       quantity rounds down to 0.00001 BTC, so the fill is
                 slightly smaller than asked.
  FEES           charged on every execution, both directions.
  NO SHORTING    spot: weights are 0, 1/3, 2/3, 1 of equity in BTC and the
                 rest sits in USDT earning nothing.

So this simulates the order loop, not the return series, and reports how
often the account was simply too small to do what the rule asked.
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
MIN_NOTIONAL = 5.0
LOT_STEP = 1e-5
ANN = 365.0
CAPITALS = [50.0, 100.0, 200.0, 1000.0]
FEES = [(10.0, "plain"), (7.5, "BNB discount")]


def simulate(close, weights, cap0, fee_bps):
    """Spot order loop.

    The rule only ever asks for a DIFFERENT position when its target weight
    changes -- {0, 1/3, 2/3, 1}. Re-targeting every single day instead would
    chase price drift, invent thousands of tiny orders, and charge fees for
    them; that is a different (worse) strategy, not this one. So act on
    signal changes, and count a block only when a real signal change could
    not be executed. Drift between signals is left alone.
    """
    usdt, btc = cap0, 0.0
    eq, done, fees = [], 0, 0.0
    blocked_signal = 0          # a signal change the account could not place
    px_arr = close.to_numpy()
    w_arr = weights.to_numpy()
    prev_w = None
    for i in range(len(px_arr)):
        px = px_arr[i]
        equity = usdt + btc * px
        changed = (prev_w is None) or (w_arr[i] != prev_w)
        prev_w = w_arr[i]
        if not changed:
            eq.append(usdt + btc * px)
            continue
        want_notional = w_arr[i] * equity
        delta = want_notional - btc * px
        if abs(delta) >= MIN_NOTIONAL:
            qty = np.floor(abs(delta) / px / LOT_STEP) * LOT_STEP
            if qty > 0:
                traded = qty * px
                fee = traded * fee_bps / 1e4
                if delta > 0:
                    if traded + fee <= usdt:
                        usdt -= traded + fee
                        btc += qty
                        done += 1; fees += fee
                    else:                       # not enough cash, buy less
                        aff = usdt / (1 + fee_bps / 1e4)
                        qty = np.floor(aff / px / LOT_STEP) * LOT_STEP
                        if qty * px >= MIN_NOTIONAL:
                            traded = qty * px
                            fee = traded * fee_bps / 1e4
                            usdt -= traded + fee; btc += qty
                            done += 1; fees += fee
                        else:
                            blocked_signal += 1
                else:
                    qty = min(qty, btc)
                    traded = qty * px
                    fee = traded * fee_bps / 1e4
                    usdt += traded - fee; btc -= qty
                    done += 1; fees += fee
            else:
                blocked_signal += 1
        elif abs(delta) > 0.01:
            blocked_signal += 1     # signal changed, order under MIN_NOTIONAL
        eq.append(usdt + btc * px)
    return pd.Series(eq, index=close.index), blocked_signal, done, fees


def stats(eq):
    r = eq.pct_change().fillna(0.0)
    years = len(eq) / ANN
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1
    dd = float((eq / eq.cummax() - 1).min())
    sd = r.std() * np.sqrt(ANN)
    sh = (r.mean() * ANN) / sd if sd > 0 else np.nan
    return cagr, sh, dd


def main():
    close = load_daily(BTC)
    f = combo_daily_frame(close)
    w = pd.Series(f["target_frac"].to_numpy(), index=close.index) \
          .shift(1).fillna(0.0)          # decide on close, hold from next day

    print("=" * 94)
    print(f" COMBO ON A REAL BINANCE SPOT ACCOUNT -- BTCUSDT"
          f"  {close.index[0]:%Y-%m} to {close.index[-1]:%Y-%m}"
          f"  ({len(close)/ANN:.1f} yr)")
    print(f" MIN_NOTIONAL {MIN_NOTIONAL:.0f} USDT, lot step {LOT_STEP},"
          f" no swap, no shorting")
    print("=" * 94)

    bh = close.iloc[-1] / close.iloc[0]
    bh_cagr = bh ** (ANN / len(close)) - 1
    bh_dd = float((close / close.cummax() - 1).min())
    print(f"\n  benchmark, just holding BTC:"
          f"  CAGR {bh_cagr*100:.1f}%   maxDD {bh_dd*100:.0f}%")

    for fee_bps, tag in FEES:
        print(f"\n{'='*94}\n FEE {fee_bps:.1f} bps/side ({tag})\n{'='*94}")
        print(f"\n{'start':>8}{'final':>12}{'CAGR':>9}{'Sharpe':>8}{'maxDD':>8}"
              f"{'beats hold':>12}{'trades':>8}{'BLOCKED':>9}{'fees paid':>11}")
        for cap in CAPITALS:
            eq, skipped, done, fees = simulate(close, w, cap, fee_bps)
            cagr, sh, dd = stats(eq)
            print(f"{cap:>7.0f}${eq.iloc[-1]:>11,.0f}{cagr*100:>8.1f}%"
                  f"{sh:>8.2f}{dd*100:>7.0f}%"
                  f"{('YES' if cagr > bh_cagr else 'no'):>12}"
                  f"{done:>8}{skipped:>9}{fees:>10,.0f}$")

    print("\n" + "=" * 94)
    print(" THE TWO HALVES, AND THE 2022 BEAR")
    print("=" * 94)
    mid = len(close) // 2
    for cap in CAPITALS:
        eq, sk, dn, fe = simulate(close, w, cap, 10.0)
        h1 = (eq.iloc[mid] / eq.iloc[0]) ** (ANN / mid) - 1
        h2 = (eq.iloc[-1] / eq.iloc[mid]) ** (ANN / (len(eq) - mid)) - 1
        bear = eq.loc["2022-01-01":"2022-12-31"]
        bhb = close.loc["2022-01-01":"2022-12-31"]
        b_s = bear.iloc[-1] / bear.iloc[0] - 1 if len(bear) > 2 else np.nan
        b_h = bhb.iloc[-1] / bhb.iloc[0] - 1 if len(bhb) > 2 else np.nan
        print(f"\n  start {cap:>6.0f}$   1st half {h1*100:>+7.1f}%/yr"
              f"   2nd half {h2*100:>+7.1f}%/yr"
              f"   both positive: {'YES' if h1>0 and h2>0 else 'NO'}")
        print(f"{'':>16}2022 bear: strategy {b_s*100:>+6.1f}%"
              f"   vs holding BTC {b_h*100:>+6.1f}%")

    print("\n" + "=" * 94)
    print(" 'BLOCKED' counts days the rule wanted a different position and the")
    print(" account could not place the order. A high count means the strategy")
    print(" running at that size is NOT the strategy that was validated.")
    print("=" * 94)
    return 0


if __name__ == "__main__":
    sys.exit(main())
