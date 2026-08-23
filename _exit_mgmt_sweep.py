#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Exit-management alternatives to a flat SL2.5/TP15 — the user's actual complaint
is "เสียไม้ใหญ่ ได้เล็กๆ" (big losses, small wins), so every scheme here is a
different way to cap the loss tail and/or let winners run:

  flat SL/TP        : the pending deploy (control) + tighter-SL variants
  chandelier trail  : engine's _update_trail — ratchets SL below the highest
                      high once price is +activation×ATR in profit. NOTE: live
                      path needs modify_sl() wiring (not built yet) but each
                      trailed SL would sit BROKER-side once modified, so it
                      is hang-resistant after the first ratchet.
  breakeven move    : SL jumps to entry once +X×ATR reached. Implemented via
                      the engine's partial-TP hook with frac=0.0 (closes zero
                      lots, books zero pnl, but fires move_sl_to_breakeven).
                      Same live-code caveat as trailing.
  partial TP        : bank half at +2×ATR, run the rest — included mostly to
                      SHOW it recreates the small-win pattern, not fix it.

All schemes share the live entry logic (FastHybridTrendPullback), live entry
configs, real costs (incl. the corrected $0.24 gold spread), max_hold_bars=64.

Usage: python _exit_mgmt_sweep.py btc|gold|eth
Prints a ranked table + OOS half-split for the top schemes + a JSON tail line.
"""
from __future__ import annotations
import os, sys, json
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import (DataLoader, prepare_data, BacktestEngine,
                            FastHybridTrendPullback, compute_metrics)
from _idea_search import resample
from _all_paths import to_monthly, perf, START


MARKETS = {
    "btc":  dict(sym="BTCUSDc", csv="download/btcusdt-15m-binance-2017-08-17-2026-06-30.csv",
                 synth=False, adx=10, touch=0.012, spread=10.0, comm=0.0, risk=1.00,
                 ps=1.0, pv=0.01),
    "gold": dict(sym="XAUUSD", csv="download/xauusd-m15-bid-2013-01-01-2026-06-10.csv",
                 synth=True, adx=10, touch=0.012, spread=0.24, comm=3.5, risk=0.30,
                 ps=None, pv=None),
    "eth":  dict(sym="ETHUSDc", csv="download/ethusdt-15m-binance-2017-08-17-2026-06-30.csv",
                 synth=False, adx=18, touch=0.0015, spread=1.0, comm=0.0, risk=1.90,
                 ps=1.0, pv=0.01),
}

# name -> (sl, tp, trail_act, trail_mult, be_atr, ptp_atr, ptp_frac)
#   trail_act/trail_mult = 999 -> no trail
#   be_atr   = None -> no breakeven move (else partial-frac-0 trick)
#   ptp_atr  = None -> no real partial TP
#   NOTE: be_atr and ptp_atr are mutually exclusive except "ptp+BE", where the
#   real partial IS the BE trigger (engine couples them in _check_partial_tp).
SCHEMES = [
    # -- flat SL/TP family ---------------------------------------------------
    ("live_now  SL3/TP5",      (3.0, 5.0,  999, 999, None, None, 0.0)),
    ("pending   SL2.5/TP15",   (2.5, 15.0, 999, 999, None, None, 0.0)),
    ("SL2.5/noTP",             (2.5, 999.0,999, 999, None, None, 0.0)),
    ("SL2.0/TP15",             (2.0, 15.0, 999, 999, None, None, 0.0)),
    ("SL1.5/TP15",             (1.5, 15.0, 999, 999, None, None, 0.0)),
    # -- chandelier trail (SL2.5, TP15 cap kept as broker-side backstop) -----
    ("trail a2/m1.5",          (2.5, 15.0, 2.0, 1.5, None, None, 0.0)),
    ("trail a2/m2",            (2.5, 15.0, 2.0, 2.0, None, None, 0.0)),
    ("trail a2/m3",            (2.5, 15.0, 2.0, 3.0, None, None, 0.0)),
    ("trail a3/m1.5",          (2.5, 15.0, 3.0, 1.5, None, None, 0.0)),
    ("trail a3/m2",            (2.5, 15.0, 3.0, 2.0, None, None, 0.0)),
    ("trail a3/m3",            (2.5, 15.0, 3.0, 3.0, None, None, 0.0)),
    ("trail a5/m2",            (2.5, 15.0, 5.0, 2.0, None, None, 0.0)),
    ("trail a5/m3",            (2.5, 15.0, 5.0, 3.0, None, None, 0.0)),
    ("trail a3/m2 noTP",       (2.5, 999.0,3.0, 2.0, None, None, 0.0)),
    # -- breakeven move ------------------------------------------------------
    ("BE @+1.5ATR",            (2.5, 15.0, 999, 999, 1.5,  None, 0.0)),
    ("BE @+2ATR",              (2.5, 15.0, 999, 999, 2.0,  None, 0.0)),
    ("BE @+3ATR",              (2.5, 15.0, 999, 999, 3.0,  None, 0.0)),
    ("BE@2 + trail a3/m2",     (2.5, 15.0, 3.0, 2.0, 2.0,  None, 0.0)),
    # -- real partial TP (expected WORSE: it re-creates the small-win habit) -
    # for "ptp + BE" the real partial IS the BE trigger (be_atr set = BE flag)
    ("ptp 50%@2 + BE",         (2.5, 15.0, 999, 999, 2.0,  2.0,  0.5)),
    ("ptp 50%@3",              (2.5, 15.0, 999, 999, None, 3.0,  0.5)),
]


def make_cfg(mk, sl, tp, be_atr, ptp_atr, ptp_frac):
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = mk["risk"]
    c.move_sl_to_breakeven = False
    c.partial_tp_atr = 999.0
    c.partial_tp_frac = 0.0
    c.max_hold_bars = 64
    if ptp_atr is not None:                # real partial TP (+BE if be_atr set)
        c.partial_tp_atr = ptp_atr
        c.partial_tp_frac = ptp_frac
        c.move_sl_to_breakeven = be_atr is not None
    elif be_atr is not None:               # frac-0 trick: BE move only
        c.partial_tp_atr = be_atr
        c.partial_tp_frac = 0.0
        c.move_sl_to_breakeven = True
    if mk["ps"] is not None:
        c.pip_size[mk["sym"]] = mk["ps"]
        c.pip_value_usd_approx[mk["sym"]] = mk["pv"]
    return c


def run_scheme(dh1, mk, params):
    sl, tp, act, mult, be_atr, ptp_atr, ptp_frac = params
    d = prepare_data(dh1)
    s = FastHybridTrendPullback()
    s.ADX_MIN = mk["adx"]; s.TIMEFRAME_SECONDS = 3600
    s.TOUCH_TOLERANCE = mk["touch"]
    s.sl_atr = sl; s.tp_atr = tp
    s.trail_atr_mult = mult; s.trail_activation_atr = act
    s.precompute(d)
    cfg = make_cfg(mk, sl, tp, be_atr, ptp_atr, ptp_frac)
    eng = BacktestEngine(d, cfg, s, spread_price=mk["spread"],
                         commission_per_lot=mk["comm"], symbol=mk["sym"])
    eng.run(quiet=True, do_precompute=False)
    return eng.trades, eng.equity_curve


def trade_stats(trades):
    if not trades:
        return None
    wins  = [t for t in trades if t["net_pnl"] > 0]
    loses = [t for t in trades if t["net_pnl"] <= 0]
    def atr_move(t):
        d = 1 if t["side"] == "long" else -1
        return (t["exit_price"] - t["entry_price"]) * d / t["entry_atr"]
    aw = np.mean([atr_move(t) for t in wins]) if wins else 0.0
    al = np.mean([atr_move(t) for t in loses]) if loses else 0.0
    mix = {}
    for t in trades:
        mix[t["reason"]] = mix.get(t["reason"], 0) + 1
    gp = sum(t["net_pnl"] for t in wins); gl = -sum(t["net_pnl"] for t in loses)
    pf = gp / gl if gl > 0 else float("inf")
    return dict(n=len(trades), wr=100.0 * len(wins) / len(trades), pf=pf,
                avg_win_atr=aw, avg_loss_atr=al, mix=mix)


def headline(trades, equity_curve, yrs):
    m = compute_metrics(trades, equity_curve, START)
    if not m or m.get("trades", 0) < 20:
        return None
    p = perf(to_monthly(trades))
    sh = p["sharpe"] if p else float("nan")
    tot = m["total_return_pct"]
    cg = -100.0 if tot <= -100 else ((1 + tot / 100) ** (1 / yrs) - 1) * 100
    dd = m["max_dd_pct"]
    return dict(sharpe=sh, cagr=cg, dd=dd, calmar=(cg / dd if dd > 0 else 0))


def main():
    key = sys.argv[1]
    mk = MARKETS[key]
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START
    df, _ = loader.load(mk["sym"], 99.0, c0, csv_path=mk["csv"],
                        allow_synthetic=mk["synth"])
    dh1 = resample(df, "1h")
    yrs = (dh1["timestamp"].iloc[-1] - dh1["timestamp"].iloc[0]).days / 365.25
    mid = len(dh1) // 2
    h1a, h1b = dh1.iloc[:mid].reset_index(drop=True), dh1.iloc[mid:].reset_index(drop=True)
    yrs_a = (h1a["timestamp"].iloc[-1] - h1a["timestamp"].iloc[0]).days / 365.25
    yrs_b = (h1b["timestamp"].iloc[-1] - h1b["timestamp"].iloc[0]).days / 365.25

    print("=" * 110)
    print(f" {key.upper()}  exit-management sweep  ({yrs:.1f}y H1, real costs)")
    print("=" * 110)
    print(f"{'scheme':<22}{'Sharpe':>7}{'CAGR':>8}{'DD':>7}{'Calmar':>7}"
          f"{'PF':>6}{'N':>6}{'WR%':>6}{'avgW':>7}{'avgL':>7}  exit-mix")
    print("-" * 110)

    results = {}
    for name, params in SCHEMES:
        tr, eq = run_scheme(dh1, mk, params)
        hd = headline(tr, eq, yrs)
        st = trade_stats(tr)
        if hd is None or st is None:
            print(f"{name:<22}   too few trades")
            continue
        results[name] = dict(**hd, **{k: v for k, v in st.items() if k != "mix"},
                             mix=st["mix"])
        mixs = " ".join(f"{k}:{v}" for k, v in sorted(st["mix"].items()))
        print(f"{name:<22}{hd['sharpe']:>7.2f}{hd['cagr']:>7.2f}%{hd['dd']:>6.1f}%"
              f"{hd['calmar']:>7.2f}{st['pf']:>6.2f}{st['n']:>6}{st['wr']:>6.1f}"
              f"{st['avg_win_atr']:>7.2f}{st['avg_loss_atr']:>7.2f}  {mixs}")

    # ---- OOS half-split on top-6 by full-sample Sharpe (+ the two controls)
    ranked = sorted(results, key=lambda k: results[k]["sharpe"], reverse=True)
    keep = list(dict.fromkeys(ranked[:6] + ["live_now  SL3/TP5", "pending   SL2.5/TP15"]))
    print()
    print(f"{'OOS half-split':<22}{'trainSh':>8}{'trainCG':>9}{'oosSh':>7}{'oosCG':>8}{'oosDD':>7}{'oosPF':>7}")
    print("-" * 70)
    oos = {}
    by_name = dict(SCHEMES)
    for name in keep:
        if name not in results:
            continue
        params = by_name[name]
        tra, eqa = run_scheme(h1a, mk, params)
        trb, eqb = run_scheme(h1b, mk, params)
        ha, hb = headline(tra, eqa, yrs_a), headline(trb, eqb, yrs_b)
        stb = trade_stats(trb)
        if ha is None or hb is None:
            print(f"{name:<22}   too few")
            continue
        oos[name] = dict(train_sharpe=ha["sharpe"], train_cagr=ha["cagr"],
                         oos_sharpe=hb["sharpe"], oos_cagr=hb["cagr"],
                         oos_dd=hb["dd"], oos_pf=stb["pf"])
        print(f"{name:<22}{ha['sharpe']:>8.2f}{ha['cagr']:>8.2f}%{hb['sharpe']:>7.2f}"
              f"{hb['cagr']:>7.2f}%{hb['dd']:>6.1f}%{stb['pf']:>7.2f}")

    print()
    print("JSON_RESULT " + json.dumps({"market": key, "full": results, "oos": oos},
                                      default=float))


if __name__ == "__main__":
    main()
