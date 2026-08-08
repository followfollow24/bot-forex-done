#!/usr/bin/env python3
"""FROZEN reference for the funding-contrarian live bot build.

Re-runs the exact verified rule (see verify_funding_standalone.py, adversarially
confirmed 2026-08-07: combo monthly Sharpe full=0.94, IS(<2023)=1.23,
OOS(>=2023)=1.13, BTC=0.68, ETH=0.98, totret=23.2%, nT=431) and emits
ref_funding_signals.csv with one row PER SYMBOL-DAY.

CSV columns:
  date                 UTC calendar day D (YYYY-MM-DD)
  symbol               BTC | ETH
  f_mean               mean of day D's 8h funding prints (UTC 00/08/16), ffilled if day missing
  fema3                EMA(span=3, adjust=True) of f_mean, as of day D
  fema30               EMA(span=30, adjust=True) of f_mean, as of day D
  pema200              EMA(span=200, adjust=True) of daily close (funding-era series), day D
  close                day D UTC close (last 15m close of the day)
  atr14                Wilder ATR14 (ewm alpha=1/14, adjust=False) as of day D
  bias                 GATED desired position from day D close-state (-1/0/+1);
                       this is the order target executed at day D+1 UTC open.
                       raw contrarian funding bias = +1 if fema3<fema30, -1 if >, 0 if ==
                       gate: long allowed only if close>pema200, short only if close<pema200
  position_after_today position actually held at end of day D (-1/0/+1)
  stop_level           active hard stop of the open position at end of day D (nan if flat)
  action               final state-change of day D: enter_long/enter_short/exit/hold/flat
                       (flip days = exit@open + re-enter@open -> recorded as enter_*;
                        enter@open + stopped same day -> recorded as exit)

Sanity: replays equity from the CSV columns (bias, atr14) + daily OHLC ONLY
(no indicator recomputation) and confirms combo monthly Sharpe/CAGR match the
verified numbers.
"""
import os
import sys
import numpy as np
import pandas as pd

REPO = "/Users/follow/Desktop/outputs/bot forex"
SCRATCH = os.path.dirname(os.path.abspath(__file__))
OUT_CSV = os.path.join(SCRATCH, "ref_funding_signals.csv")

# ---- frozen constants -------------------------------------------------------
FS, FL, PS, ATR_N = 3, 30, 200, 14          # fEMA fast/slow spans, pEMA span, ATR length
STOP_MULT = 2.5                              # stop distance = 2.5 x ATR14(signal day)
RISK = 0.01                                  # 1% of equity risked per trade on stop distance
LEVCAP = 3.0                                 # notional <= 3x equity
WARMUP = 15                                  # drop first 15 daily rows after indicator build
SWAP_LONG = 0.069                            # 6.9%/yr, LONG holds only, pro-rata days/365
OOS_SPLIT = "2023-01-01"

SYMS = {
    "BTC": dict(price="download/btcusdt-15m-binance-2017-08-17-2026-06-30.csv",
                fund="download/btc_funding.csv", spread=10.0),
    "ETH": dict(price="download/ethusdt-15m-binance-2017-08-17-2026-06-30.csv",
                fund="download/eth_funding.csv", spread=1.0),
}

# verified reference metrics (from verify_funding_standalone.py run 2026-08-08)
VERIFIED = dict(full=0.94, ins=1.23, oos=1.13, totret=23.2)
TOL = 0.02  # Sharpe tolerance for pass/fail


def load_daily(sym):
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
    d["f_mean"] = d["f_mean"].ffill()
    d = d.dropna(subset=["f_mean"])
    return d


def build_ind(d):
    d = d.copy()
    d["fe_f"] = d["f_mean"].ewm(span=FS, adjust=True).mean()
    d["fe_s"] = d["f_mean"].ewm(span=FL, adjust=True).mean()
    d["pema"] = d["close"].ewm(span=PS, adjust=True).mean()
    pc = d["close"].shift(1)
    tr = np.maximum(d["high"] - d["low"],
                    np.maximum((d["high"] - pc).abs(), (d["low"] - pc).abs()))
    d["atr"] = tr.ewm(alpha=1.0 / ATR_N, adjust=False).mean()
    return d


def run_symbol(sym):
    """Exact verified engine + per-day signal recording."""
    d = build_ind(load_daily(sym)).iloc[WARMUP:]
    o = d["open"].values; h = d["high"].values; l = d["low"].values; c = d["close"].values
    fe_f = d["fe_f"].values; fe_s = d["fe_s"].values; pema = d["pema"].values
    atr = d["atr"].values; fmean = d["f_mean"].values
    idx = d.index; n = len(d)
    spread = SYMS[sym]["spread"]

    eq = 1.0
    pos = 0; size = 0.0; entry = stop = np.nan; entry_i = -1
    equity_curve = np.full(n, np.nan)
    rows = []

    def desired_at(i):
        if fe_f[i] < fe_s[i]:
            b = 1
        elif fe_f[i] > fe_s[i]:
            b = -1
        else:
            b = 0
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
            pnl -= size * entry * SWAP_LONG * days / 365.0
        eq += pnl
        pos = 0; size = 0.0; entry = stop = np.nan; entry_i = -1

    for i in range(n):
        entered_today = False
        exited_today = False
        # --- execution at day-i open, from day i-1 close signal ---
        if i > 0:
            want = desired_at(i - 1)
            if pos != 0 and want != pos:
                close_trade(o[i], i)
                exited_today = True
            if pos == 0 and want != 0 and not np.isnan(atr[i - 1]) and atr[i - 1] > 0:
                sd = STOP_MULT * atr[i - 1]
                sz = RISK * eq / sd
                sz = min(sz, LEVCAP * eq / o[i])
                pos = want; size = sz; entry = o[i]
                stop = entry - pos * sd
                entry_i = i
                entered_today = True
        # --- intraday stop check on day i (incl. entry day) ---
        if pos == 1:
            if entry_i < i and o[i] <= stop:
                close_trade(o[i], i); exited_today = True
            elif l[i] <= stop:
                close_trade(stop, i); exited_today = True
        elif pos == -1:
            if entry_i < i and o[i] >= stop:
                close_trade(o[i], i); exited_today = True
            elif h[i] >= stop:
                close_trade(stop, i); exited_today = True
        # --- mark to market ---
        m = eq
        if pos != 0:
            m += size * (c[i] - entry) * pos
            if pos == 1:
                m -= size * entry * SWAP_LONG * (i - entry_i) / 365.0
        equity_curve[i] = m
        # --- record row (natural state; end-of-data flatten NOT encoded) ---
        if pos != 0:
            action = ("enter_long" if pos == 1 else "enter_short") if entered_today else "hold"
        else:
            action = "exit" if exited_today else "flat"
        rows.append(dict(date=idx[i].strftime("%Y-%m-%d"), symbol=sym,
                         f_mean=fmean[i], fema3=fe_f[i], fema30=fe_s[i],
                         pema200=pema[i], close=c[i], atr14=atr[i],
                         bias=desired_at(i), position_after_today=pos,
                         stop_level=stop if pos != 0 else np.nan, action=action))

    # end-of-data flatten (backtest artifact, equity only — not in CSV rows)
    if pos != 0:
        close_trade(c[n - 1], n - 1)
        equity_curve[n - 1] = eq

    return pd.Series(equity_curve, index=idx), pd.DataFrame(rows)


# ---- sanity: replay equity from the CSV alone (+ daily OHLC) ---------------
def replay_from_csv(csv_path):
    sig = pd.read_csv(csv_path, parse_dates=["date"])
    ecs = []
    for sym in SYMS:
        s = sig[sig["symbol"] == sym].set_index("date").sort_index()
        d = load_daily(sym).reindex(s.index)
        assert not d["open"].isna().any(), f"{sym}: CSV dates missing from price data"
        o = d["open"].values; h = d["high"].values; l = d["low"].values; c = d["close"].values
        bias = s["bias"].values; atr = s["atr14"].values
        csv_pos = s["position_after_today"].values; csv_stop = s["stop_level"].values
        n = len(s); spread = SYMS[sym]["spread"]
        eq = 1.0; pos = 0; size = 0.0; entry = stop = np.nan; entry_i = -1
        equity_curve = np.full(n, np.nan)

        def close_trade(exit_px, i):
            nonlocal eq, pos, size, entry, stop, entry_i
            days = i - entry_i
            pnl = size * (exit_px - entry) * pos
            pnl -= size * spread
            if pos == 1:
                pnl -= size * entry * SWAP_LONG * days / 365.0
            eq += pnl
            pos = 0; size = 0.0; entry = stop = np.nan; entry_i = -1

        for i in range(n):
            if i > 0:
                want = int(bias[i - 1])
                if pos != 0 and want != pos:
                    close_trade(o[i], i)
                if pos == 0 and want != 0 and not np.isnan(atr[i - 1]) and atr[i - 1] > 0:
                    sd = STOP_MULT * atr[i - 1]
                    sz = min(RISK * eq / sd, LEVCAP * eq / o[i])
                    pos = want; size = sz; entry = o[i]
                    stop = entry - pos * sd; entry_i = i
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
            m = eq
            if pos != 0:
                m += size * (c[i] - entry) * pos
                if pos == 1:
                    m -= size * entry * SWAP_LONG * (i - entry_i) / 365.0
            equity_curve[i] = m
            # cross-check replay state vs CSV record
            assert pos == csv_pos[i], f"{sym} {s.index[i].date()}: pos {pos} != csv {csv_pos[i]}"
            if pos != 0:
                assert abs(stop - csv_stop[i]) < 1e-6 * max(1.0, abs(stop)), \
                    f"{sym} {s.index[i].date()}: stop {stop} != csv {csv_stop[i]}"
        if pos != 0:
            close_trade(c[n - 1], n - 1)
            equity_curve[n - 1] = eq
        ecs.append(pd.Series(equity_curve, index=s.index).rename(sym))
    return ecs


def monthly_sharpe(ec):
    m = ec.resample("ME").last().pct_change().dropna()
    if len(m) < 6 or m.std() == 0:
        return np.nan
    return m.mean() / m.std() * np.sqrt(12)


def combo_stats(ecs):
    df = pd.concat(ecs, axis=1).ffill().dropna()
    comb = 0.5 * df.iloc[:, 0] / df.iloc[0, 0] + 0.5 * df.iloc[:, 1] / df.iloc[0, 1]
    full = monthly_sharpe(comb)
    oos = comb[comb.index >= OOS_SPLIT]; ins = comb[comb.index < OOS_SPLIT]
    sh_oos = monthly_sharpe(oos / oos.iloc[0])
    sh_ins = monthly_sharpe(ins / ins.iloc[0])
    yrs = (comb.index[-1] - comb.index[0]).days / 365.25
    cagr = comb.iloc[-1] ** (1.0 / yrs) - 1.0
    totret = (comb.iloc[-1] - 1) * 100
    return comb, dict(full=full, ins=sh_ins, oos=sh_oos, cagr=cagr * 100, totret=totret)


if __name__ == "__main__":
    # 1) run engine, write CSV
    ecs_engine, frames = [], []
    for sym in SYMS:
        ec, rows = run_symbol(sym)
        ecs_engine.append(ec.rename(sym))
        frames.append(rows)
    out = pd.concat(frames).sort_values(["symbol", "date"]).reset_index(drop=True)
    out.to_csv(OUT_CSV, index=False, float_format="%.10g")
    print(f"wrote {OUT_CSV}  rows={len(out)}")

    _, st_e = combo_stats(ecs_engine)
    print(f"ENGINE : full={st_e['full']:.2f} IS={st_e['ins']:.2f} OOS={st_e['oos']:.2f} "
          f"CAGR={st_e['cagr']:.2f}%/yr totret={st_e['totret']:.1f}%")

    # 2) sanity: replay from CSV only
    ecs_csv = replay_from_csv(OUT_CSV)
    _, st_c = combo_stats(ecs_csv)
    print(f"CSV    : full={st_c['full']:.2f} IS={st_c['ins']:.2f} OOS={st_c['oos']:.2f} "
          f"CAGR={st_c['cagr']:.2f}%/yr totret={st_c['totret']:.1f}%")

    ok = (abs(st_c["full"] - VERIFIED["full"]) <= TOL
          and abs(st_c["ins"] - VERIFIED["ins"]) <= TOL
          and abs(st_c["oos"] - VERIFIED["oos"]) <= TOL
          and abs(st_c["totret"] - VERIFIED["totret"]) <= 0.2)
    print("SANITY", "PASS" if ok else "FAIL",
          f"(verified: full={VERIFIED['full']} IS={VERIFIED['ins']} "
          f"OOS={VERIFIED['oos']} totret={VERIFIED['totret']}%)")
    sys.exit(0 if ok else 1)
