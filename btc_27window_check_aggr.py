#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
btc_27window_check_aggr.py -- same 6-month rolling-window harness as
btc_27window_check.py, applied to the AGGRESSIVE BTC-HF config
(ADX12/SL2.5/TP7.5) instead of the conservative one (ADX15/SL4/TP12).
ASCII-only.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest_forex import FastHybridTrendPullback
from walk_forward_regime import build_windows, gold_return_pct, regime_tag, window_metrics
import btc_walkforward as W

CFG = dict(adx=12, sl=2.5, tp=7.5)   # aggressive WF-picked config


def main():
    d, df = W.load_prepared(
        os.path.join("download", "btcusdt-15m-binance-2017-08-17-2026-06-30.csv"),
        resample_h1=False)

    s = FastHybridTrendPullback(); s.ADX_MIN = CFG["adx"]; s.precompute(d)
    s.sl_atr = CFG["sl"]; s.tp_atr = CFG["tp"]; s.trail_atr_mult = 999; s.trail_activation_atr = 999

    e = W.BTCEngine(d, W.make_cfg(), s, spread_price=10.0, commission_per_lot=0.0, symbol="BTCUSDc")
    e.run(quiet=True, do_precompute=False)
    trades = W.normalize_trades(e.trades)
    print(f"total trades: {len(trades)}  {df['timestamp'].iloc[0].date()}..{df['timestamp'].iloc[-1].date()}")

    ts_start = df["timestamp"].iloc[0]; ts_end = df["timestamp"].iloc[-1]
    windows = build_windows(ts_start, ts_end, months=6)

    print("=" * 96)
    print(f" BTC-HF AGGRESSIVE (ADX12/SL2.5/TP7.5)  --  6-MONTH ROLLING WINDOWS  ({len(windows)} windows)")
    print(" compare vs conservative (ADX15/SL4/TP12) = PF>1 in 15/18, vs gold baseline 24/27")
    print("=" * 96)
    print(f"  {'#':<4} {'window':<24} {'BTC ret%':>9} {'regime':<9} {'trades':>7} "
          f"{'PF':>7} {'win%':>6} {'ret%':>8} {'MaxDD%':>7}")
    print("  " + "-" * 88)

    pf_gt1 = 0; n_valid = 0
    for i, (w0, w1) in enumerate(windows, 1):
        wt = [t for t in trades if w0 <= t["_entry_dt"] < w1]
        m = window_metrics(wt, W.START)
        bret = gold_return_pct(df, w0, w1)
        reg = regime_tag(bret)
        pf = m["pf"]
        pf_str = f"{pf:.2f}" if (pf is not None and pf != float("inf")) else ("inf" if pf == float("inf") else "n/a")
        ret_pct = sum(t["_norm_pnl"] for t in wt) / W.START * 100 if wt else 0.0
        if pf is not None and pf > 1.0:
            pf_gt1 += 1
        if m["trades"] > 0:
            n_valid += 1
        print(f"  {i:<4} {w0.date()}..{w1.date()}  {bret if bret is not None else 0:>+8.1f}% "
              f"{reg:<9} {m['trades']:>7} {pf_str:>7} "
              f"{(m['win_rate']*100 if m['win_rate'] is not None else 0):>5.0f}% "
              f"{ret_pct:>+7.1f}% {m['max_dd_pct']:>6.1f}%")

    print("  " + "-" * 88)
    print(f"\n  PF>1 in {pf_gt1}/{len(windows)} windows ({n_valid} had trades)")
    print(f"  compare: conservative BTC-HF = 15/18, gold ADX20_TP7 baseline = 24/27")
    print("=" * 96)


if __name__ == "__main__":
    main()
