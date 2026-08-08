#!/usr/bin/env python3
"""Independent re-implementation of the funding-rate-standalone spec (adversarial verify).

Spec (from finder's text, implemented from scratch — NOT copied from funding_battery.py):
- Binance 15m -> D1 UTC bars. f_mean = mean of that day's 8h funding prints.
- Funding-era daily series, indicators, then 15-bar warm-up trim.
- fEMA3/fEMA30 on f_mean, pEMA200 on close, ATR14 Wilder.
- Signal at day-t close -> fill day-t+1 open.
- bias LONG if fEMA3<fEMA30 else SHORT if >. Gate: long only close>pEMA200, short only close<pEMA200, else flat.
- Exit next open on bias flip or gate cross. Hard stop 2.5xATR14(signal day) from entry,
  checked vs intraday low/high, gap -> exit at open.
- Risk 1% equity per trade on stop distance, 3x leverage cap.
- Costs: full spread once per round trip (BTC $10, ETH $1), swap -6.9%/yr pro-rata on LONG holds only.
- Portfolio: 50/50 BTC+ETH.
"""
import os, sys, itertools, json
import numpy as np
import pandas as pd

REPO = "/Users/follow/Desktop/outputs/bot forex"
SCRATCH = os.path.dirname(os.path.abspath(__file__))

SYMS = {
    "BTC": dict(price="download/btcusdt-15m-binance-2017-08-17-2026-06-30.csv",
                fund="download/btc_funding.csv", spread=10.0),
    "ETH": dict(price="download/ethusdt-15m-binance-2017-08-17-2026-06-30.csv",
                fund="download/eth_funding.csv", spread=1.0),
}

_cache = {}

def load_daily(sym):
    if sym in _cache:
        return _cache[sym]
    sp = SYMS[sym]
    px = pd.read_csv(os.path.join(REPO, sp["price"]), parse_dates=["timestamp"])
    px = px.set_index("timestamp").sort_index()
    d = px.resample("1D").agg(open=("open", "first"), high=("high", "max"),
                              low=("low", "min"), close=("close", "last")).dropna()
    f = pd.read_csv(os.path.join(REPO, sp["fund"]), parse_dates=["timestamp"])
    f["date"] = f["timestamp"].dt.floor("D")
    fm = f.groupby("date")["funding_rate"].mean()
    d = d.join(fm.rename("f_mean"), how="left")
    d = d[d.index >= fm.index.min()].copy()
    n_missing = int(d["f_mean"].isna().sum())
    d["f_mean"] = d["f_mean"].ffill()
    d = d.dropna(subset=["f_mean"])
    _cache[sym] = (d, n_missing)
    return _cache[sym]


def build_ind(d, fs, fl, ps, atr_n, adjust_ema=True, full_hist_pema=False, full_close=None):
    d = d.copy()
    d["fe_f"] = d["f_mean"].ewm(span=fs, adjust=adjust_ema).mean()
    d["fe_s"] = d["f_mean"].ewm(span=fl, adjust=adjust_ema).mean()
    if full_hist_pema and full_close is not None:
        pe = full_close.ewm(span=ps, adjust=adjust_ema).mean()
        d["pema"] = pe.reindex(d.index)
    else:
        d["pema"] = d["close"].ewm(span=ps, adjust=adjust_ema).mean()
    pc = d["close"].shift(1)
    tr = np.maximum(d["high"] - d["low"],
                    np.maximum((d["high"] - pc).abs(), (d["low"] - pc).abs()))
    d["atr"] = tr.ewm(alpha=1.0 / atr_n, adjust=False).mean()
    return d


def run_symbol(sym, fs=3, fl=30, ps=200, atr_n=14, stop_mult=2.5, risk=0.01,
               levcap=3.0, adjust_ema=True, full_hist_pema=False, mode="funding",
               costs=True, warmup=15, invert=False):
    """mode: funding (spec) | trendonly (control: bias = gate direction) | fundonly (no price gate)"""
    d, _ = load_daily(sym)
    full_close = None
    if full_hist_pema:
        sp = SYMS[sym]
        px = pd.read_csv(os.path.join(REPO, sp["price"]), parse_dates=["timestamp"])
        px = px.set_index("timestamp").sort_index()
        full_close = px["close"].resample("1D").last().dropna()
    d = build_ind(d, fs, fl, ps, atr_n, adjust_ema, full_hist_pema, full_close)
    d = d.iloc[warmup:]

    o = d["open"].values; h = d["high"].values; l = d["low"].values; c = d["close"].values
    fe_f = d["fe_f"].values; fe_s = d["fe_s"].values; pema = d["pema"].values
    atr = d["atr"].values
    idx = d.index
    n = len(d)
    spread = SYMS[sym]["spread"] if costs else 0.0
    swap_rate = 0.069 if costs else 0.0

    eq = 1.0
    pos = 0            # -1/0/+1
    size = 0.0         # units of coin
    entry = stop = np.nan
    entry_i = -1
    equity_curve = np.full(n, np.nan)
    trades = []        # (exit_date, pnl, dir, days_held)

    def desired_at(i):
        if mode == "trendonly":
            if c[i] > pema[i]:
                return 1
            if c[i] < pema[i]:
                return -1
            return 0
        # funding bias
        if fe_f[i] < fe_s[i]:
            b = 1
        elif fe_f[i] > fe_s[i]:
            b = -1
        else:
            b = 0
        if invert:
            b = -b
        if mode == "fundonly":
            return b
        if b == 1 and c[i] > pema[i]:
            return 1
        if b == -1 and c[i] < pema[i]:
            return -1
        return 0

    def close_trade(exit_px, i):
        nonlocal eq, pos, size, entry, stop, entry_i
        days = i - entry_i
        pnl = size * (exit_px - entry) * pos
        pnl -= size * spread
        if pos == 1:
            pnl -= size * entry * swap_rate * days / 365.0
        eq += pnl
        trades.append((idx[i], pnl, pos, days))
        pos = 0; size = 0.0; entry = stop = np.nan; entry_i = -1

    for i in range(n):
        # --- execution at day-i open, based on signal from day i-1 close ---
        if i > 0:
            want = desired_at(i - 1)
            if pos != 0 and want != pos:
                close_trade(o[i], i)
            if pos == 0 and want != 0 and not np.isnan(atr[i - 1]) and atr[i - 1] > 0:
                sd = stop_mult * atr[i - 1]
                sz = risk * eq / sd
                sz = min(sz, levcap * eq / o[i])
                pos = want; size = sz; entry = o[i]
                stop = entry - pos * sd
                entry_i = i
        # --- intraday stop check on day i (incl. entry day) ---
        # gap through stop at open (only possible on non-entry days) -> exit at open;
        # otherwise touched intraday -> exit at stop price.
        if pos == 1:
            if entry_i < i and o[i] <= stop:
                close_trade(o[i], i)
            elif l[i] <= stop:
                close_trade(stop, i)
        elif pos == -1:
            if entry_i < i and o[i] >= stop:
                close_trade(o[i], i)
            elif h[i] >= stop:
                close_trade(stop, i)
        # mark to market
        m = eq
        if pos != 0:
            m += size * (c[i] - entry) * pos
            if pos == 1:
                m -= size * entry * swap_rate * (i - entry_i) / 365.0
        equity_curve[i] = m

    # close any open position at final close (flagged: end-of-data mark)
    if pos != 0:
        close_trade(c[n - 1], n - 1)
        equity_curve[n - 1] = eq

    ec = pd.Series(equity_curve, index=idx)
    tr = pd.DataFrame(trades, columns=["exit", "pnl", "dir", "days"])
    return ec, tr


def monthly_sharpe(ec):
    m = ec.resample("ME").last().pct_change().dropna()
    if len(m) < 6 or m.std() == 0:
        return np.nan, m
    return m.mean() / m.std() * np.sqrt(12), m


def yearly_stats(ec, tr):
    out = {}
    yr_eq = ec.resample("YE").last()
    prev = ec.iloc[0]
    for ts, v in yr_eq.items():
        y = ts.year
        ret = (v / prev - 1) * 100
        prev = v
        t = tr[tr["exit"].dt.year == y]
        gw = t.loc[t.pnl > 0, "pnl"].sum()
        gl = -t.loc[t.pnl < 0, "pnl"].sum()
        pf = gw / gl if gl > 0 else np.inf
        out[y] = (pf, ret, len(t))
    return out


def combo_metrics(ecs):
    """50/50, each normalized, independent compounding."""
    df = pd.concat(ecs, axis=1).ffill().dropna()
    comb = 0.5 * df.iloc[:, 0] / df.iloc[0, 0] + 0.5 * df.iloc[:, 1] / df.iloc[0, 1]
    return comb


def report(tag, **kw):
    res = {}
    ecs = []
    for sym in SYMS:
        ec, tr = run_symbol(sym, **kw)
        sh, m = monthly_sharpe(ec)
        ys = yearly_stats(ec, tr)
        res[sym] = dict(sharpe=sh, ec=ec, tr=tr, yearly=ys)
        ecs.append(ec.rename(sym))
    comb = combo_metrics(ecs)
    sh_c, m_c = monthly_sharpe(comb)
    # OOS split at 2023-01
    oos = comb[comb.index >= "2023-01-01"]
    ins = comb[comb.index < "2023-01-01"]
    sh_oos, _ = monthly_sharpe(oos / oos.iloc[0])
    sh_ins, _ = monthly_sharpe(ins / ins.iloc[0])
    line = f"{tag:38s} full={sh_c:5.2f} IS={sh_ins:5.2f} OOS={sh_oos:5.2f} " \
           f"BTC={res['BTC']['sharpe']:5.2f} ETH={res['ETH']['sharpe']:5.2f} " \
           f"totret={100*(comb.iloc[-1]-1):6.1f}% nT={len(res['BTC']['tr'])+len(res['ETH']['tr'])}"
    print(line, flush=True)
    return res, comb, m_c


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "base"
    if which == "base":
        res, comb, m_c = report("SPEC base (adjust=True)", fs=3, fl=30, ps=200)
        print("\nPer-year (BTC | ETH | combo ret%):")
        yb, ye = res["BTC"]["yearly"], res["ETH"]["yearly"]
        cy = comb.resample("YE").last()
        prevc = comb.iloc[0]
        for ts, v in cy.items():
            y = ts.year
            cr = (v / prevc - 1) * 100
            prevc = v
            b = yb.get(y, (np.nan, np.nan, 0)); e = ye.get(y, (np.nan, np.nan, 0))
            print(f"{y} | BTC PF {b[0]:5.2f} ret {b[1]:5.1f}% ({b[2]:3d}T) | "
                  f"ETH PF {e[0]:5.2f} ret {e[1]:5.1f}% ({e[2]:3d}T) | combo {cr:5.1f}%")
        m_c.rename("ret").to_frame().assign(ret_pct=lambda x: x.ret * 100)[["ret_pct"]] \
           .to_csv(os.path.join(SCRATCH, "verify_funding_combo_monthly.csv"),
                   index_label="month")
        print("\nmonthly csv saved")
    elif which == "variants":
        report("SPEC adjust=False EMAs", fs=3, fl=30, ps=200, adjust_ema=False)
        report("full-history pEMA200", fs=3, fl=30, ps=200, full_hist_pema=True)
        report("no costs", fs=3, fl=30, ps=200, costs=False)
        report("CONTROL trend-only (no funding)", mode="trendonly")
        report("CONTROL funding-only (no gate)", mode="fundonly")
        report("CONTROL inverted funding bias", invert=True)
    elif which == "neighbors":
        for fs in [2, 3, 4, 5]:
            report(f"fs={fs}", fs=fs, fl=30, ps=200)
        for fl in [20, 25, 30, 40]:
            report(f"fl={fl}", fs=3, fl=fl, ps=200)
        for ps in [100, 150, 200, 250]:
            report(f"ps={ps}", fs=3, fl=30, ps=ps)
        for sm in [1.5, 2.0, 2.5, 3.0, 4.0]:
            report(f"stop={sm}", fs=3, fl=30, ps=200, stop_mult=sm)
