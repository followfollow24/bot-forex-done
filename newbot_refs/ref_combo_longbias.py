#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ref_combo_longbias.py -- FROZEN reference implementation of the BTC "Combo
LongBias" daily strategy, for the live-bot build. DO NOT RE-TUNE.

Provenance:
  * Original finder: "bot forex"/backtest_btc.py (s_trend_longflat + s_tsmom +
    s_donchian averaged, net-short clipped to 0), day-params on 1H bars.
  * Independent adversarial re-implementation from spec text alone
    (scratchpad/verify_btc_combo_daily.py) matched: full monthly Sharpe
    1.06-1.09, OOS-half 0.68-0.75, WF-favourite config EMA25/120 TS40 DC20/10.
  * THIS file is the daily-bar frozen form the live bot must reproduce
    bar-for-bar: signal once per day on the UTC-midnight daily close.

FROZEN RULE (daily bars, UTC midnight boundaries, close = daily close):
  Sleeve A  trend long/flat : 1.0 if EMA(close,span=25,adjust=False)
                              > EMA(close,span=120,adjust=False) else 0.0.
                              Forced 0.0 for the first 120 daily bars (warm-up).
  Sleeve B  TSMOM 40d       : sign(close[t]/close[t-40] - 1); 0 when the
                              lookback is unavailable or the ratio is exactly 1.
  Sleeve C  Donchian 20/10  : channels on CLOSE, prior-day (shift(1), today's
                              close excluded). entry_hi/lo = rolling(20) max/min,
                              exit_hi/lo = rolling(10) max/min. Stop-and-reverse
                              state machine, STRICT inequalities:
                                p<=0 and close>entry_hi  -> p=+1
                                elif p>=0 and close<entry_lo -> p=-1
                                elif p==+1 and close<exit_lo -> p=0
                                elif p==-1 and close>exit_hi -> p=0
                              p=0 while entry channel is NaN (first 20 bars).
  Combo     target_frac = clip((A+B+C)/3, 0, None)  in {0, 1/3, 2/3, 1}.
  Execution: target from day-i close is adopted at day-(i+1) open (for 24/7
             crypto that open IS the same midnight tick). No intraday stops,
             no TP -- position is only changed at the daily open.

COST MODEL used for the sanity equity curve (matches the verified numbers):
  spread : 0.016% of price per ROUND TRIP ($10 on ~$63k BTC); each position
           change of |d| units pays |d| * 0.008% (half-spread per side).
  swap   : -6.9%/365 charged on every day the held position is long.
           PRIMARY (verified/conservative): full charge whenever target>0,
           regardless of fraction. Also reported: proportional (x target).
Outputs:
  ref_combo_signals.csv  (date, close, ema25, ema120, tsmom40_sign,
    donch20_hi, donch10_lo, sleeve_trend, sleeve_tsmom, sleeve_donch,
    target_frac, action) -- action = order to execute at the NEXT daily open.
"""
import os
import numpy as np
import pandas as pd

DATA = ("/Users/follow/Desktop/outputs/bot forex/download/"
        "btcusdt-15m-binance-2017-08-17-2026-06-30.csv")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "ref_combo_signals.csv")

# ---- frozen parameters ----
EMA_FAST, EMA_SLOW = 25, 120
TSMOM_LOOK = 40
DONCH_ENTRY, DONCH_EXIT = 20, 10
SPREAD_RT_PCT = 0.00016          # $10 / ~$63k round trip
HALF_SPREAD = SPREAD_RT_PCT / 2  # per side of each position change
SWAP_LONG_DAY = 0.069 / 365.0    # long financing; short would be 0 (unused)


def load_daily():
    """Binance 15m -> UTC-midnight daily OHLC. This is the reference bar
    construction the live bot must match (aggregate H1/M15 in UTC, do NOT
    trust broker D1 bars whose day may roll at server time, not UTC)."""
    df = pd.read_csv(DATA, parse_dates=["timestamp"]).set_index("timestamp")
    d = pd.DataFrame({
        "open": df["open"].resample("1D").first(),
        "high": df["high"].resample("1D").max(),
        "low": df["low"].resample("1D").min(),
        "close": df["close"].resample("1D").last(),
    }).dropna()
    return d


def sleeve_trend(close):
    f = close.ewm(span=EMA_FAST, adjust=False).mean()
    s = close.ewm(span=EMA_SLOW, adjust=False).mean()
    a = (f > s).astype(float)
    a.iloc[:EMA_SLOW] = 0.0          # frozen warm-up: no trade first 120 bars
    return a, f, s


def sleeve_tsmom(close):
    r = close / close.shift(TSMOM_LOOK) - 1.0
    return pd.Series(np.sign(r), index=close.index).fillna(0.0)


def sleeve_donch(close):
    ehi = close.rolling(DONCH_ENTRY).max().shift(1)
    elo = close.rolling(DONCH_ENTRY).min().shift(1)
    xhi = close.rolling(DONCH_EXIT).max().shift(1)
    xlo = close.rolling(DONCH_EXIT).min().shift(1)
    c = close.values
    eh, el, xh, xl = ehi.values, elo.values, xhi.values, xlo.values
    pos = np.zeros(len(c))
    p = 0.0
    for i in range(len(c)):
        if np.isnan(eh[i]):
            pos[i] = 0.0
            continue
        if p <= 0 and c[i] > eh[i]:
            p = 1.0
        elif p >= 0 and c[i] < el[i]:
            p = -1.0
        elif p == 1.0 and c[i] < xl[i]:
            p = 0.0
        elif p == -1.0 and c[i] > xh[i]:
            p = 0.0
        pos[i] = p
    return pd.Series(pos, index=close.index), ehi, xlo


def build_signals(d):
    a, ema_f, ema_s = sleeve_trend(d["close"])
    b = sleeve_tsmom(d["close"])
    cpos, donch_ehi, donch_xlo = sleeve_donch(d["close"])
    target = ((a + b + cpos) / 3.0).clip(lower=0.0)
    # snap to exact thirds so the CSV is unambiguous
    target = (np.round(target * 3.0) / 3.0).astype(float)

    prev = target.shift(1).fillna(0.0)
    delta = target - prev
    action = np.where(np.abs(delta) < 1e-9, "HOLD",
                      np.where(delta > 0,
                               "BUY " + pd.Series(delta).round(4).astype(str),
                               "SELL " + pd.Series(-delta).round(4).astype(str)))
    out = pd.DataFrame({
        "date": d.index.strftime("%Y-%m-%d"),
        "close": d["close"].values,
        "ema25": ema_f.round(2).values,
        "ema120": ema_s.round(2).values,
        "tsmom40_sign": b.astype(int).values,
        "donch20_hi": donch_ehi.round(2).values,
        "donch10_lo": donch_xlo.round(2).values,
        "sleeve_trend": a.astype(int).values,
        "sleeve_tsmom": b.astype(int).values,
        "sleeve_donch": cpos.astype(int).values,
        "target_frac": np.round(target.values, 6),
        "action": action,
    })
    return out, target


def sanity_backtest(d, target, swap_mode="full", spread_mode="pct"):
    """Next-open execution on daily bars.
    Day i: overnight close[i-1]->open[i] held at yesterday's executed position;
    intraday open[i]->close[i] held at today's newly-executed position
    (= target decided at close of day i-1). For 24/7 BTC open[i] ~ close[i-1]
    so the overnight leg is ~0; kept for exactness."""
    o, c = d["open"].values, d["close"].values
    tgt = target.values
    n = len(c)
    pos_exec = np.zeros(n)               # position held during day i (open->close)
    pos_exec[1:] = tgt[:-1]              # decided at close i-1, filled at open i
    pos_prev = np.zeros(n)               # position held overnight into day i
    pos_prev[1:] = pos_exec[:-1]

    r_ovn = np.zeros(n)
    r_ovn[1:] = o[1:] / c[:-1] - 1.0
    r_day = c / o - 1.0

    turn = np.abs(pos_exec - pos_prev)
    if spread_mode == "pct":
        tc = turn * HALF_SPREAD
    else:                                # fixed $10 RT -> $5/side, price-dep %
        tc = turn * (5.0 / o)
    if swap_mode == "full":
        swap = np.where(pos_exec > 0, SWAP_LONG_DAY, 0.0)
    else:                                # proportional to the fraction held
        swap = pos_exec * SWAP_LONG_DAY

    net = pos_prev * r_ovn + pos_exec * r_day - tc - swap
    return pd.Series(net, index=d.index)


def stats(net, label):
    eq = (1 + net).cumprod()
    yrs = len(net) / 365.25
    cagr = eq.iloc[-1] ** (1 / yrs) - 1
    dd = -(eq / eq.cummax() - 1).min()
    mret = (1 + net).resample("ME").prod() - 1
    msh = mret.mean() / mret.std() * np.sqrt(12) if mret.std() > 0 else np.nan
    mar = cagr / dd if dd > 0 else np.nan
    print(f"{label:<44} CAGR {cagr*100:6.1f}%  DD {dd*100:5.1f}%  "
          f"MAR {mar:5.2f}  mSharpe {msh:5.2f}  Tot {(eq.iloc[-1]-1)*100:8.0f}%")
    return dict(cagr=cagr, dd=dd, msharpe=msh, mar=mar)


def main():
    d = load_daily()
    print(f"daily bars: {len(d)}  {d.index[0].date()} .. {d.index[-1].date()}")

    csv, target = build_signals(d)
    csv.to_csv(OUT, index=False)
    print(f"signals CSV: {OUT}  ({len(csv)} rows)")
    vc = csv["target_frac"].value_counts().sort_index()
    print("target_frac distribution:", dict(vc))
    nch = int((csv["action"] != "HOLD").sum())
    print(f"position-change events: {nch} total = {nch/(len(d)/365.25):.1f}/yr")

    print("\n== SANITY: next-open execution, BTCUSDc costs ==")
    net = sanity_backtest(d, target, "full", "pct")
    m = stats(net, "PRIMARY 0.016%RT spread + FULL swap on longs")
    stats(sanity_backtest(d, target, "prop", "pct"),
          "variant  0.016%RT spread + proportional swap")
    stats(sanity_backtest(d, target, "full", "usd"),
          "variant  fixed $10 spread (price-dep) + full swap")

    mid = d.index[len(d) // 2]
    print(f"\nhalf-split at {mid.date()}")
    stats(net[net.index < mid], "  first half")
    stats(net[net.index >= mid], "  second half (OOS-half)")

    print("\nyearly returns (PRIMARY costs) vs B&H:")
    bh = d["close"].pct_change().fillna(0.0)
    for y in sorted(set(d.index.year)):
        msk = net.index.year == y
        if msk.sum() < 30:
            continue
        ry = (1 + net[msk]).prod() - 1
        rb = (1 + bh[msk]).prod() - 1
        print(f"  {y}: {ry*100:+8.1f}%   B&H {rb*100:+8.1f}%")

    print(f"\nverified target: full mSharpe ~1.0-1.1, OOS-half ~0.68-0.75, "
          f"CAGR ~50%/yr at full notional -> got mSharpe {m['msharpe']:.2f}, "
          f"CAGR {m['cagr']*100:.0f}%")


if __name__ == "__main__":
    main()
