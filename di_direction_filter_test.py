#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
di_direction_filter_test.py -- research ONLY, no live-bot changes.

Question: does adding a +DI/-DI direction gate (require PDI>MDI for BUY,
MDI>PDI for SELL) on top of the existing H1 EMA50/200 + ADX>=threshold
trend filter reduce losses, without meaningfully cutting the edge?

Motivation: h1_trend_diagnostic.py found 4 of the ~19 recent real-money
losses (Jul 6-10 2026) had H1 EMA trend=BULL (correct per current design)
but H1 ADX-direction already BEAR. This is ONE cluster from ONE week --
this script checks whether the idea generalizes across the full 13.4yr
history, not just the window that inspired it (same discipline as
gold_losing_streak.py / oos_validation.py).

Everything else about the strategy is held IDENTICAL to the live bots:
  spread=0.28 (real live/demo spread), commission=$3.50/lot/side,
  risk=0.30%/trade, partial-TP OFF, SL=3.0xATR, TP=7.0xATR,
  ADX_MIN in {18, 20} (both live variants tested).

Baseline vs DI-gated compared on:
  TRAIN 2013-2019 / TEST(OOS) 2020-2026 / FULL 2013-2026
plus: which trades the gate removes (win vs loss), and whether it also
would have shortened/avoided OTHER historical loss-streak clusters
(not just the Jul 2026 one, which isn't even in-sample for OOS since
data ends 2026-06-10).
ASCII-only.
"""
import sys, os, math
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import (DataLoader, prepare_data, BacktestEngine,
                             FastHybridTrendPullback, compute_metrics)
from forex_hybrid_strategy import HybridTrendPullback

CSV = "download/xauusd-m15-bid-2013-01-01-2026-06-10.csv"
SPREAD = 0.28          # real live/demo spread (matches gold_losing_streak.py)
COMM = 3.50
START = 10_000.0
RISK_PCT = 0.30

TRAIN_TO = pd.Timestamp("2020-01-01")


def make_cfg():
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = RISK_PCT
    c.partial_tp_atr = 999.0
    c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False
    return c


# =============================================================================
# DI-GATED VARIANT -- identical to FastHybridTrendPullback except the H1
# trend array also requires +DI/-DI direction to agree with the EMA trend.
# All SL/TP/entry/pullback logic untouched (isolated single-condition test).
# =============================================================================
class DIGatedHybrid(FastHybridTrendPullback):
    name = "Hybrid Trend-Pullback + DI-direction gate"
    short_name = "Hybrid-TPB-DIGate"

    def _build_h1_trend_array(self, d: dict) -> np.ndarray:
        # [FIX 2026-07-30] was position-based (idx = arange(n_h1)*H1_BARS)
        # with a look-ahead expansion (out[i] = h1_trend[i // H1_BARS], the
        # bucket bar i falls INSIDE rather than the last COMPLETED one).
        # Rebuilt on the same calendar/timestamp-anchored bucket ids as
        # forex_hybrid_strategy.HybridTrendPullback. Research-only file (not
        # used by any live bot -- see module docstring); the DI-direction
        # gate conclusions this script produced before this fix used the
        # buggy array and should be rerun before being trusted again.
        n = len(d["c"])
        out = np.zeros(n, dtype=np.int8)

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

        ema_f = self._ema(h1_c, self.EMA_H1_FAST)
        ema_s = self._ema(h1_c, self.EMA_H1_SLOW)
        adx_a, pdi_a, mdi_a = self._adx_pdi_mdi_array(h1_h, h1_l, h1_c, self.ADX_PERIOD)

        h1_trend = np.zeros(n_h1, dtype=np.int8)
        for k in range(n_h1):
            ef, es, adx, pdi, mdi = ema_f[k], ema_s[k], adx_a[k], pdi_a[k], mdi_a[k]
            if any(math.isnan(x) for x in (ef, es, adx, pdi, mdi)):
                continue
            if adx < self.ADX_MIN:
                continue
            c = h1_c[k]
            if c > ef > es and pdi > mdi:
                h1_trend[k] = 1
            elif c < ef < es and mdi > pdi:
                h1_trend[k] = -1

        entry_bar_seconds = getattr(self, "TIMEFRAME_SECONDS", 900)
        bucket_seconds = self._bucket_seconds()
        epoch = self._epoch_seconds(d["ts"])
        is_last_in_bucket = (epoch // bucket_seconds) != ((epoch + entry_bar_seconds) // bucket_seconds)
        k_complete = np.where(is_last_in_bucket, k_of_bar, k_of_bar - 1)
        valid = k_complete >= 0
        k_complete = np.clip(k_complete, 0, n_h1 - 1)
        out[valid] = h1_trend[k_complete[valid]]
        return out

    @classmethod
    def _adx_pdi_mdi_array(cls, high, low, close, period=14):
        """Same math as HybridTrendPullback._adx_array but also returns pdi/mdi."""
        n = len(close)
        adx_out = np.full(n, np.nan)
        pdi_out = np.full(n, np.nan)
        mdi_out = np.full(n, np.nan)
        if n < period * 3:
            return adx_out, pdi_out, mdi_out

        prev_c = np.empty(n)
        prev_c[0] = close[0]
        prev_c[1:] = close[:-1]

        tr = np.maximum(high - low,
                         np.maximum(np.abs(high - prev_c), np.abs(low - prev_c)))
        up = np.diff(high, prepend=high[0])
        dn = -np.diff(low, prepend=low[0])
        pdm = np.where((up > dn) & (up > 0), up, 0.0)
        mdm = np.where((dn > up) & (dn > 0), dn, 0.0)

        atr_w = cls._wilder_smooth(tr, period)
        safe = np.where(atr_w > 0, atr_w, 1.0)
        pdi = 100.0 * cls._wilder_smooth(pdm, period) / safe
        mdi = 100.0 * cls._wilder_smooth(mdm, period) / safe
        dsum = np.where(pdi + mdi > 0, pdi + mdi, 1.0)
        dx = 100.0 * np.abs(pdi - mdi) / dsum
        adx = cls._wilder_smooth(dx, period)

        valid = period * 3
        adx_out[valid:] = adx[valid:]
        pdi_out[valid:] = pdi[valid:]
        mdi_out[valid:] = mdi[valid:]
        return adx_out, pdi_out, mdi_out


def run(strat_cls, adx_min, df_full, date_from=None, date_to=None):
    df = df_full.copy()
    if date_from:
        df = df[df["timestamp"] >= pd.Timestamp(date_from)]
    if date_to:
        df = df[df["timestamp"] < pd.Timestamp(date_to)]
    df = df.reset_index(drop=True)
    if len(df) < 1000:
        return None, []

    d = prepare_data(df)
    if d is None:
        return None, []

    strat = strat_cls()
    strat.ADX_MIN = adx_min
    strat.sl_atr = 3.0
    strat.tp_atr = 7.0
    strat.trail_atr_mult = 999.0
    strat.trail_activation_atr = 999.0
    strat.precompute(d)

    eng = BacktestEngine(d, make_cfg(), strat, spread_price=SPREAD,
                          commission_per_lot=COMM, symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)

    trades = eng.trades
    ov = compute_metrics(trades, eng.equity_curve, START)
    n = len(trades)
    wins = sum(1 for t in trades if t.get("net_pnl", 0) > 0)
    pf = ov.get("profit_factor", 0) or 0
    ret = (ov.get("final_equity", START) - START) / START * 100
    dd = ov.get("max_dd_pct", 0) or 0.001
    sharpe = ov.get("sharpe", 0) or 0
    return dict(n=n, win_pct=(wins / n * 100 if n else 0), pf=pf, ret=ret,
                dd=dd, calmar=(ret / dd if dd else 0), sharpe=sharpe), trades


def streaks(trades):
    runs, cur = [], 0
    for t in trades:
        if t["net_pnl"] <= 0:
            cur += 1
        else:
            if cur > 0: runs.append(cur)
            cur = 0
    if cur > 0: runs.append(cur)
    return runs


def find_streak_dates(trades, min_len):
    out, cur_start, cur_len = [], None, 0
    for t in trades:
        if t["net_pnl"] <= 0:
            if cur_len == 0: cur_start = t["entry_ts"]
            cur_len += 1
            cur_end = t["exit_ts"]
        else:
            if cur_len >= min_len:
                out.append((cur_start, cur_end, cur_len))
            cur_len = 0
    if cur_len >= min_len:
        out.append((cur_start, cur_end, cur_len))
    return out


def trade_key(t):
    return (t["entry_ts"], t.get("side", ""), round(t.get("net_pnl", 0), 2))


def print_row(lbl, m):
    if m is None:
        print(f"  {lbl:<28} (no data)")
        return
    print(f"  {lbl:<28} n={m['n']:<5} win%={m['win_pct']:>5.1f} PF={m['pf']:>5.2f} "
          f"ret={m['ret']:>+7.1f}% DD={m['dd']:>5.1f}% Calmar={m['calmar']:>6.1f} "
          f"Sharpe={m['sharpe']:>5.2f}")


def main():
    print("=" * 96)
    print(" DI-DIRECTION GATE TEST -- research only, NOT a deploy recommendation")
    print(f" spread={SPREAD}  commission=${COMM}/lot  risk={RISK_PCT}%  "
          f"SL=3.0xATR TP=7.0xATR  partial-TP OFF")
    print("=" * 96)

    loader = DataLoader(log_fn=lambda *a, **k: None)
    cfg0 = ForexConfig(); cfg0.total_capital_usd = START
    df_full, _ = loader.load("XAUUSD", 99.0, cfg0, csv_path=CSV, allow_synthetic=False)
    print(f"\n  data: {len(df_full):,} bars  "
          f"{df_full['timestamp'].iloc[0].date()} -> {df_full['timestamp'].iloc[-1].date()}")

    periods = [
        ("TRAIN 2013-2019", "2013-01-01", "2020-01-01"),
        ("TEST/OOS 2020-2026", "2020-01-01", None),
        ("FULL 2013-2026", None, None),
    ]

    for adx_min in (20, 18):
        print("\n" + "-" * 96)
        print(f" ADX_MIN = {adx_min}  (live bot: {'adx20tp7' if adx_min==20 else 'adx18tp7'})")
        print("-" * 96)
        print(f"  {'window / variant':<28} {'n':<7} {'win%':<10} {'PF':<8} {'ret':<10} "
              f"{'DD':<8} {'Calmar':<8} {'Sharpe'}")

        all_trades = {}
        for lbl, df_from, df_to in periods:
            m_base, tr_base = run(FastHybridTrendPullback, adx_min, df_full, df_from, df_to)
            m_gate, tr_gate = run(DIGatedHybrid, adx_min, df_full, df_from, df_to)
            print_row(f"{lbl} [baseline]", m_base)
            print_row(f"{lbl} [+DI-gate]", m_gate)
            all_trades[lbl] = (tr_base, tr_gate)

        # ── which trades did the gate remove? ──
        lbl_full = "FULL 2013-2026"
        tr_base, tr_gate = all_trades[lbl_full]
        base_keys = {trade_key(t) for t in tr_base}
        gate_keys = {trade_key(t) for t in tr_gate}
        removed = [t for t in tr_base if trade_key(t) not in gate_keys]
        rem_wins = sum(1 for t in removed if t["net_pnl"] > 0)
        rem_losses = sum(1 for t in removed if t["net_pnl"] <= 0)
        rem_win_pnl = sum(t["net_pnl"] for t in removed if t["net_pnl"] > 0)
        rem_loss_pnl = sum(t["net_pnl"] for t in removed if t["net_pnl"] <= 0)
        print(f"\n  Trades REMOVED by DI-gate (FULL history, ADX_MIN={adx_min}): "
              f"{len(removed)} total")
        print(f"    -> {rem_wins} would-have-been WINS removed (sum pnl {rem_win_pnl:+.0f})")
        print(f"    -> {rem_losses} would-have-been LOSSES removed (sum pnl {rem_loss_pnl:+.0f})")
        net_effect = -(rem_win_pnl + rem_loss_pnl)  # pnl NOT incurred, positive=good
        print(f"    -> net PnL the gate avoided taking on: {rem_win_pnl+rem_loss_pnl:+.0f} "
              f"(negative = gate mostly filtered LOSSES = good; positive = filtered WINS = bad)")

        # ── streak comparison: does gate shorten/avoid historical loss clusters? ──
        print(f"\n  Loss-streak comparison (ADX_MIN={adx_min}):")
        for lbl in ("TRAIN 2013-2019", "TEST/OOS 2020-2026", "FULL 2013-2026"):
            tb, tg = all_trades[lbl]
            rb = streaks(tb); rg = streaks(tg)
            maxb = max(rb) if rb else 0
            maxg = max(rg) if rg else 0
            print(f"    {lbl:<22} baseline max streak={maxb:<4} DI-gate max streak={maxg}")

        print(f"\n  Historical streaks >=11 (baseline, ADX_MIN={adx_min}) and what DI-gate did to them:")
        tb_full, tg_full = all_trades[lbl_full]
        hits = find_streak_dates(tb_full, 11)
        if not hits:
            print("    (none >= 11 in baseline FULL history)")
        for (s, e, ln) in hits:
            # how long is the DI-gated streak covering roughly the same window?
            gate_hits_here = [g for g in find_streak_dates(tg_full, 1)
                               if g[0] >= s and g[0] <= e]
            gate_max_overlap = max((g[2] for g in gate_hits_here), default=0)
            print(f"    baseline {ln}-loss streak {s} -> {e}  |  "
                  f"DI-gate longest overlapping streak in same window: {gate_max_overlap}")

    print("\n" + "=" * 96)
    print(" VERDICT GUIDE (fill in after reading the numbers above):")
    print("  - Compare TEST/OOS [+DI-gate] vs [baseline] Calmar/PF/Sharpe -- gate must win")
    print("    OUT OF SAMPLE, not just on TRAIN or FULL, to be considered a real improvement.")
    print("  - If 'trades removed' skews heavily toward LOSSES (net PnL avoided is very")
    print("    negative i.e. gate mostly blocked losers) AND OOS Calmar/PF improves -> positive signal.")
    print("  - If it cuts a meaningful chunk of WINNERS too, or OOS Calmar/PF doesn't improve,")
    print("    the July 2026 cluster is more likely unavoidable trend-following reversal risk,")
    print("    not a fixable filter gap -- a valid and useful research conclusion either way.")
    print("  - This script does NOT deploy anything. Any change to forex_live_bot_gold_cwider.py")
    print("    requires a separate, explicit approval step regardless of outcome here.")
    print("=" * 96)


if __name__ == "__main__":
    main()
