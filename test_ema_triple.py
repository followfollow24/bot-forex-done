#!/usr/bin/env python3
"""
test_ema_triple.py — เทียบ EMA50/200 (เดิม) vs EMA50/100/200 (ใหม่)
บน full history 2013-2026

รัน: python3 test_ema_triple.py --csv download/xauusd-m15-bid-2013-01-01-2026-06-10.csv
"""
import argparse, math, sys, os, time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import DataLoader, prepare_data, BacktestEngine, FastHybridTrendPullback, compute_metrics

START    = 10_000.0
RISK_PCT = 0.30
SPREAD   = 0.28
COMM     = 3.50

# ── Triple-EMA subclass ────────────────────────────────────────────────────────
class TripleEMAStrategy(FastHybridTrendPullback):
    """EMA50 > EMA100 > EMA200 triple alignment (+ price above/below all 3)."""
    EMA_H1_MID = 100   # เพิ่ม EMA100

    def _build_h1_trend_array(self, d: dict):
        # [FIX 2026-07-30] was position-based (idx = arange(n_h1)*H1_BARS) --
        # look-ahead bias near bucket boundaries, and the final expansion
        # used the bucket bar i falls INSIDE rather than the last COMPLETED
        # one (real look-ahead: every bar in a bucket saw that bucket's
        # full, eventually-final EMA/ADX value). Rebuilt on the same
        # calendar/timestamp-anchored bucket ids as
        # forex_hybrid_strategy.HybridTrendPullback. Research-only file (not
        # used by any live bot); any numbers this script printed before this
        # fix used the buggy array and should not be trusted without rerunning.
        n    = len(d["c"])
        out  = np.zeros(n, dtype=np.int8)

        bucket_id = self._bucket_ids(d["ts"], self._bucket_seconds())
        uniq, k_of_bar = np.unique(bucket_id, return_inverse=True)
        n_h1 = len(uniq)
        if n_h1 < self.EMA_H1_SLOW + 5:
            return out

        tmp = pd.DataFrame({"k": k_of_bar, "c": d["c"], "h": d["h"], "l": d["l"]})
        g = tmp.groupby("k")
        h1_c = g["c"].last().reindex(range(n_h1)).to_numpy()
        h1_h = g["h"].max().reindex(range(n_h1)).to_numpy()
        h1_l = g["l"].min().reindex(range(n_h1)).to_numpy()

        ema_f = self._ema(h1_c, self.EMA_H1_FAST)   # EMA50
        ema_m = self._ema(h1_c, self.EMA_H1_MID)    # EMA100
        ema_s = self._ema(h1_c, self.EMA_H1_SLOW)   # EMA200
        adx_a = self._adx_array(h1_h, h1_l, h1_c, self.ADX_PERIOD)

        h1_trend = np.zeros(n_h1, dtype=np.int8)
        for k in range(n_h1):
            ef, em, es, adx = ema_f[k], ema_m[k], ema_s[k], adx_a[k]
            if any(math.isnan(x) for x in [ef, em, es, adx]):
                continue
            if adx < self.ADX_MIN:
                continue
            c = h1_c[k]
            if   c > ef > em > es:  h1_trend[k] =  1   # bull: price>EMA50>EMA100>EMA200
            elif c < ef < em < es:  h1_trend[k] = -1   # bear: price<EMA50<EMA100<EMA200

        entry_bar_seconds = getattr(self, "TIMEFRAME_SECONDS", 900)
        bucket_seconds = self._bucket_seconds()
        epoch = self._epoch_seconds(d["ts"])
        is_last_in_bucket = (epoch // bucket_seconds) != ((epoch + entry_bar_seconds) // bucket_seconds)
        k_complete = np.where(is_last_in_bucket, k_of_bar, k_of_bar - 1)
        valid = k_complete >= 0
        k_complete = np.clip(k_complete, 0, n_h1 - 1)
        out[valid] = h1_trend[k_complete[valid]]
        return out


def run_strat(d, strat, sl, tp, label):
    cfg = ForexConfig()
    cfg.total_capital_usd    = START
    cfg.risk_per_trade_pct   = RISK_PCT
    cfg.partial_tp_atr       = 999.0
    cfg.partial_tp_frac      = 0.0
    cfg.move_sl_to_breakeven = False

    strat.sl_atr               = sl
    strat.tp_atr               = tp
    strat.trail_atr_mult       = 999.0
    strat.trail_activation_atr = 999.0

    t0 = time.time()
    strat.precompute(d)
    eng = BacktestEngine(d, cfg, strat, spread_price=SPREAD,
                         commission_per_lot=COMM, symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)
    ov  = compute_metrics(eng.trades, eng.equity_curve, START)
    dt  = time.time() - t0

    trades = eng.trades
    wins   = [t for t in trades if t["net_pnl"] > 0]
    ret    = (ov.get("final_equity", START) - START) / START * 100
    dd     = ov.get("max_dd_pct", 0) or 0.01
    cal    = ret / dd

    print(f"  {label:<30} | trades={len(trades):>5} WR={len(wins)/max(len(trades),1)*100:>5.1f}%"
          f" | PF={ov.get('profit_factor',0):>6.3f} Ret={ret:>+7.1f}%"
          f" DD={ov.get('max_dd_pct',0):>5.1f}% Calmar={cal:>6.1f}  ({dt:.1f}s)")
    return ov, len(trades)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    args = ap.parse_args()

    print()
    print("=" * 90)
    print(" EMA COMPARISON — EMA50/200 (เดิม)  vs  EMA50/100/200 (ใหม่)")
    print(f"  spread={SPREAD}  commission={COMM}/lot  risk={RISK_PCT}%  capital=${START:,.0f}")
    print("=" * 90)

    loader = DataLoader(log_fn=lambda *a, **k: None)
    cfg0 = ForexConfig(); cfg0.total_capital_usd = START
    df, _ = loader.load("XAUUSD", 99.0, cfg0, csv_path=args.csv, allow_synthetic=True)
    print(f"  [data] {len(df):,} bars  {df['timestamp'].iloc[0]} → {df['timestamp'].iloc[-1]}")
    d = prepare_data(df)
    print()

    VARIANTS = [
        # label,                   sl,  tp
        ("C_wider  SL2.5 TP5.0",   2.5, 5.0),
        ("Mix_A    SL2.5 TP7.0",   2.5, 7.0),
    ]

    print(f"  {'Variant':<30} | {'trades':>7} {'WR':>6} | {'PF':>6} {'Ret':>8} {'DD':>6} {'Calmar':>7}")
    print("  " + "-" * 82)

    print("  ── EMA50/200 (เดิม) ──────────────────────────────────────────────────")
    strat_old = FastHybridTrendPullback()
    results_old = {}
    for label, sl, tp in VARIANTS:
        ov, n = run_strat(d, strat_old, sl, tp, label)
        results_old[label] = (ov, n)

    print()
    print("  ── EMA50/100/200 (ใหม่) ─────────────────────────────────────────────")
    strat_new = TripleEMAStrategy()
    results_new = {}
    for label, sl, tp in VARIANTS:
        ov, n = run_strat(d, strat_new, sl, tp, label)
        results_new[label] = (ov, n)

    print()
    print("=" * 90)
    print("  DELTA (ใหม่ - เดิม)")
    print("  " + "-" * 82)
    for label, _, _ in VARIANTS:
        ov_o, n_o = results_old[label]
        ov_n, n_n = results_new[label]
        dpf  = ov_n.get("profit_factor",0) - ov_o.get("profit_factor",0)
        dret = ((ov_n.get("final_equity",START)-START)/START*100) - ((ov_o.get("final_equity",START)-START)/START*100)
        ddd  = ov_n.get("max_dd_pct",0) - ov_o.get("max_dd_pct",0)
        dn   = n_n - n_o
        sign = lambda x: "+" if x >= 0 else ""
        print(f"  {label:<30} | trades={sign(dn)}{dn:>4}  |"
              f" PF={sign(dpf)}{dpf:.3f}  Ret={sign(dret)}{dret:.1f}%  DD={sign(ddd)}{ddd:.1f}%")
    print("=" * 90)
    print()


if __name__ == "__main__":
    main()
