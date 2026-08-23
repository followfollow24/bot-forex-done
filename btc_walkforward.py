#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 btc_walkforward.py  --  Higher-frequency BTC Trend-Pullback: WALK-FORWARD FIRST
================================================================================
Goal (per the research brief): design + VALIDATE a higher-frequency BTC strategy
(M15/H1 entry, gold-bot cadence) using the REAL HybridTrendPullback architecture
(H1 EMA50/200 + ADX filter + M15 EMA20 pullback + ATR SL/TP) and the REAL
BacktestEngine SL/TP execution -- NOT a vectorized continuous-position proxy.

Methodology guardrails baked in (both past mistakes):
  * THE PARTIAL-TP BUG: partial-TP is forced OFF (frac=0), matching the live
    gold bots. Every cost/param is set explicitly here and echoed in the banner
    so it can be diffed against the live config, never trusted from memory.
  * THE FLIP-FLOP PATTERN: no full-sample number is used to *decide* anything.
    The harness runs TRUE walk-forward first (train picks config on prior data
    only; config locked; applied unseen to the test window) and always prints
    TRAIN and TEST numbers together in the same row. Full-sample is shown LAST,
    clearly flagged IN-SAMPLE, as context only.

Costs (STEP 0/2, verified from the live MT5 BTCUSDc symbol_info snapshot):
  * spread  = $10 absolute  (spread_price=10.0; half applied as entry slippage)
  * commission = 0          (Exness Cent crypto is spread-only)
  * swap    = long pays $0.1248/lot/night (=1247.9 pts x point, ~6.9%/yr of
              notional), Friday night x3; short = FREE (asymmetric carry).
  * weekends TRADE with real range (0.66x weekday) -> Binance 24/7 kept as-is.

BTC price = Binance 15m SHAPE only (8.87yr). Costs come from the real Exness
snapshot, never from Binance. Only ~2 distinct bears in sample (2018, 2022) ->
OOS on bears is thin; treat conclusions as provisional.

Run:
  python3 btc_walkforward.py --csv download/btcusdt-15m-binance-2017-08-17-2026-06-30.csv
================================================================================
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forex_config import ForexConfig
from backtest_forex import (DataLoader, prepare_data, BacktestEngine,
                            FastHybridTrendPullback, compute_metrics)

# ── Account / cost constants (BTCUSDc, from the verified MT5 snapshot) ────────
START     = 10_000.0
RISK_PCT  = 0.30          # same risk-per-trade as the live gold bots
SPREAD    = 10.0          # $10 absolute spread (price units); half = slippage/side
COMM      = 0.0           # Cent crypto = spread-only, no separate commission
SWAP_USD_PER_LOT_NIGHT = 0.12479   # long swap per night (=1247.9 pts x 0.01 point)
FRIDAY_SWAP_MULT       = 3.0       # rollover3days=5 -> triple on Friday night
BPY_CAL   = 365           # BTC trades 24/7 -> annualize on 365 calendar days

# P&L units for BTCUSDc: 1 lot = 0.01 BTC -> $0.01 per $1 move per lot.
PIP_SIZE  = 1.0           # 1 "pip" = $1 of BTC price
PIP_VALUE = 0.01          # USD per pip per lot  (pip_value/pip_size = 0.01)


# =============================================================================
#  BTC engine: real BacktestEngine + asymmetric overnight swap
# =============================================================================
class BTCEngine(BacktestEngine):
    """Adds BTCUSDc overnight swap on top of the real SL/TP execution engine.

    Swap is charged per night held (each 00:00-UTC boundary the position spans),
    Friday night x3, longs only (shorts are carry-free). It is deducted from
    equity AND from the trade's net_pnl so the engine's own reconciliation
    assertion (sum(net_pnl)==equity_change) still holds exactly.
    """

    @staticmethod
    def _nights_weighted(entry_ts: str, exit_ts: str) -> float:
        """Weighted count of 00:00-UTC rollovers spanned; Friday night x3."""
        try:
            e = pd.Timestamp(entry_ts); x = pd.Timestamp(exit_ts)
        except Exception:
            return 0.0
        if x <= e:
            return 0.0
        first = (e.normalize() + pd.Timedelta(days=1))
        w = 0.0
        d = first
        while d <= x:
            # weekday(): Mon=0 .. Fri=4 ; MT5 rollover3days=5 = Friday
            w += FRIDAY_SWAP_MULT if d.weekday() == 4 else 1.0
            d += pd.Timedelta(days=1)
        return w

    def _close(self, i: int, exit_px: float, reason: str):
        was_long = self.position is not None and self.position.side == "long"
        super()._close(i, exit_px, reason)
        if not self.trades:
            return
        t = self.trades[-1]
        swap = 0.0
        if was_long:
            nights = self._nights_weighted(t["entry_ts"], t["exit_ts"])
            swap = SWAP_USD_PER_LOT_NIGHT * t["lot"] * nights
        if swap:
            self.equity        -= swap
            t["net_pnl"]        = round(t["net_pnl"] - swap, 2)
            t["costs_usd"]      = round(t.get("costs_usd", 0.0) + swap, 2)
            t["equity_after"]   = round(self.equity, 2)
        t["swap_usd"] = round(swap, 4)


# =============================================================================
#  Config + data
# =============================================================================
def make_cfg() -> ForexConfig:
    c = ForexConfig()
    c.total_capital_usd    = START
    c.risk_per_trade_pct   = RISK_PCT
    c.partial_tp_atr       = 999.0     # partial-TP OFF (matches live) -- THE BUG guard
    c.partial_tp_frac      = 0.0
    c.move_sl_to_breakeven = False
    # BTCUSDc P&L units (override fallbacks so gross P&L is contract-correct)
    c.pip_size["BTCUSDc"]              = PIP_SIZE
    c.pip_value_usd_approx["BTCUSDc"]  = PIP_VALUE
    return c


def load_prepared(csv_path: str, resample_h1: bool):
    """Load Binance BTC CSV once; optionally resample to H1; return prepared d + df."""
    loader = DataLoader(log_fn=lambda *a, **k: None)
    cfg0 = ForexConfig(); cfg0.total_capital_usd = START
    df, _ = loader.load("BTCUSDc", 99.0, cfg0, csv_path=csv_path, allow_synthetic=False)
    if resample_h1:
        s = df.set_index("timestamp")
        o = s["open"].resample("1h").first(); h = s["high"].resample("1h").max()
        l = s["low"].resample("1h").min();    c = s["close"].resample("1h").last()
        df = pd.DataFrame({"timestamp": o.index, "open": o.values, "high": h.values,
                           "low": l.values, "close": c.values}).dropna().reset_index(drop=True)
    d = prepare_data(df)
    return d, df


# =============================================================================
#  Metrics on a trade slice (fixed-notional, calendar-correct, cost-inclusive)
# =============================================================================
def normalize_trades(trades):
    """Attach _norm_pnl (rescaled to $START notional -> additive, no compounding)
    and _entry_dt to each trade. net_pnl already includes spread+swap."""
    eq = START
    for t in trades:
        t["_norm_pnl"] = (t["net_pnl"] * (START / eq)) if eq > 0 else t["net_pnl"]
        t["_entry_dt"] = pd.Timestamp(t["entry_ts"])
        eq = t["equity_after"]
    return trades


def slice_metrics(trades):
    """PF / return% / MaxDD% / Sharpe / n / winrate on a slice, using _norm_pnl.

    Sharpe + MaxDD are computed on a CALENDAR-DAILY equity curve (flat days
    filled with 0), realized on each trade's EXIT date -- the honest denominator
    (counts no-trade days) and portfolio-comparable with the gold/BTC sleeves.
    PF/return/winrate are trade-based (calendar-independent)."""
    n = len(trades)
    if n == 0:
        return dict(n=0, pf=0.0, ret=0.0, dd=0.0, sh=0.0, wr=0.0)
    pnl = np.array([t["_norm_pnl"] for t in trades])
    wins = pnl[pnl > 0].sum(); loss = -pnl[pnl <= 0].sum()
    pf = (wins / loss) if loss > 0 else float("inf")
    ret = pnl.sum() / START * 100.0
    wr = float((pnl > 0).mean())
    # calendar-daily realized P&L (by exit date), flat days = 0
    s = pd.Series(pnl, index=[pd.Timestamp(t["exit_ts"]).normalize() for t in trades])
    s = s.groupby(s.index).sum()
    full = pd.date_range(s.index.min(), s.index.max(), freq="D")
    dpnl = s.reindex(full, fill_value=0.0)
    dret = dpnl / START
    sh = (dret.mean() / dret.std() * math.sqrt(BPY_CAL)) if dret.std() > 0 else 0.0
    eq = START + dpnl.cumsum().values
    peak = np.maximum.accumulate(np.concatenate([[START], eq]))
    dd = float(((peak[1:] - eq) / peak[1:]).max() * 100.0)
    return dict(n=n, pf=float(pf), ret=float(ret), dd=dd, sh=float(sh), wr=wr)


def pf_str(pf):
    return "inf " if math.isinf(pf) else f"{pf:5.2f}"


def score_of(m, objective):
    """Selection score for a TRAIN metric dict. objective in {pf,sharpe,ret}."""
    if objective == "pf":
        return m["pf"] if not math.isinf(m["pf"]) else 5.0
    if objective == "sharpe":
        return m["sh"]
    return m["ret"]        # 'ret' = maximize train return


# =============================================================================
#  Run every config once over full history (trades sliced later for WF)
# =============================================================================
def run_grid(d, grid, tf_label):
    """Return {cfg_key: normalized_trades}. Re-precompute only when adx changes."""
    strat = FastHybridTrendPullback()
    out, cur_adx = {}, None
    t0 = time.time()
    # group by adx so precompute (adx-only) is reused across sl/tp
    for k, (adx, sl, tp) in enumerate(sorted(grid, key=lambda g: g[0]), 1):
        if adx != cur_adx:
            strat.ADX_MIN = adx
            strat.precompute(d)
            cur_adx = adx
        strat.sl_atr = sl; strat.tp_atr = tp
        strat.trail_atr_mult = 999.0; strat.trail_activation_atr = 999.0
        eng = BTCEngine(d, make_cfg(), strat, spread_price=SPREAD,
                        commission_per_lot=COMM, symbol="BTCUSDc")
        eng.run(quiet=True, do_precompute=False)
        out[(adx, sl, tp)] = normalize_trades(eng.trades)
        print(f"    [{tf_label} {k:2d}/{len(grid)}] ADX{adx} SL{sl} TP{tp:<4} "
              f"-> {len(eng.trades):5d} trades  ({time.time()-t0:5.1f}s)", flush=True)
    return out


# =============================================================================
#  Walk-forward A: yearly OOS (train = all prior data; test = the year)
# =============================================================================
def wf_yearly(grid_trades, years, min_train_trades=25, objective="pf"):
    print("\n" + "=" * 92)
    print(f" WALK-FORWARD A: yearly OOS -- train picks config (by TRAIN {objective.upper()}) "
          "on PRIOR data only, applied to year")
    print("=" * 92)
    print(f"  {'test yr':<8} {'picked cfg (ADX/SL/TP)':<22} | "
          f"{'TRAIN pf/sh/n':>20} | {'TEST pf':>7} {'ret%':>8} {'DD%':>6} "
          f"{'sh':>6} {'n':>5}")
    print("  " + "-" * 88)
    oos_all, picks, pf_gt1, n_win = [], [], 0, 0
    for ty in years:
        ystart = pd.Timestamp(f"{ty}-01-01"); yend = pd.Timestamp(f"{ty+1}-01-01")
        # select best config by TRAIN (entry_dt < ystart) profit factor
        best, best_key, best_tr = -1e9, None, None
        for key, trades in grid_trades.items():
            tr = [t for t in trades if t["_entry_dt"] < ystart]
            m = slice_metrics(tr)
            if m["n"] < min_train_trades:
                continue
            score = score_of(m, objective)
            if score > best:
                best, best_key, best_tr = score, key, m
        if best_key is None:
            continue
        te = [t for t in grid_trades[best_key] if ystart <= t["_entry_dt"] < yend]
        mt = slice_metrics(te)
        if mt["n"] == 0:
            continue
        picks.append(best_key); oos_all.extend(te); n_win += 1
        if math.isinf(mt["pf"]) or mt["pf"] > 1.0:
            pf_gt1 += 1
        a, s, t = best_key
        print(f"  {ty:<8} {f'ADX{a}/SL{s}/TP{t}':<22} | "
              f"{pf_str(best_tr['pf'])}/{best_tr['sh']:4.2f}/{best_tr['n']:4d}    | "
              f"{pf_str(mt['pf'])} {mt['ret']:+7.1f} {mt['dd']:5.1f} "
              f"{mt['sh']:5.2f} {mt['n']:5d}")
    agg = slice_metrics(normalize_trades(oos_all)) if oos_all else dict(n=0)
    print("  " + "-" * 88)
    if oos_all:
        print(f"  STITCHED OOS: PF {pf_str(agg['pf'])}  ret {agg['ret']:+.1f}%  "
              f"MaxDD {agg['dd']:.1f}%  Sharpe {agg['sh']:.2f}  trades {agg['n']}  "
              f"| PF>1 in {pf_gt1}/{n_win} yrs")
        # config stability
        from collections import Counter
        cnt = Counter(picks)
        common = cnt.most_common(1)[0]
        print(f"  config stability: {len(cnt)} distinct configs picked across "
              f"{n_win} yrs; most-picked {common[0]} x{common[1]}")
    return agg


# =============================================================================
#  Walk-forward B: one big split (train 2017-2021 / test 2022-2026)
# =============================================================================
def wf_bigsplit(grid_trades, split="2022-01-01", min_train_trades=40, objective="pf"):
    sp = pd.Timestamp(split)
    print("\n" + "=" * 92)
    print(f" WALK-FORWARD B: single split -- TRAIN < {split} picks config (by TRAIN "
          f"{objective.upper()}), TEST >= {split} unseen")
    print("=" * 92)
    # rank all configs by TRAIN objective
    ranked = []
    for key, trades in grid_trades.items():
        tr = [t for t in trades if t["_entry_dt"] < sp]
        m = slice_metrics(tr)
        if m["n"] < min_train_trades:
            continue
        ranked.append((score_of(m, objective), key, m))
    ranked.sort(reverse=True, key=lambda r: r[0])
    if not ranked:
        print("  (no config had enough train trades)"); return None
    print(f"  {'rank':<5} {'cfg (ADX/SL/TP)':<20} | {'TRAIN pf/sh/ret%/n':>26} | "
          f"{'TEST pf':>7} {'ret%':>8} {'DD%':>6} {'sh':>6} {'n':>5}")
    print("  " + "-" * 88)
    picked = ranked[0][1]
    for rk, (score, key, mtr) in enumerate(ranked[:6], 1):
        te = [t for t in grid_trades[key] if t["_entry_dt"] >= sp]
        mte = slice_metrics(te)
        a, s, t = key
        star = "  <== TRAIN-PICKED (locked)" if key == picked else ""
        print(f"  {rk:<5} {f'ADX{a}/SL{s}/TP{t}':<20} | "
              f"{pf_str(mtr['pf'])}/{mtr['sh']:4.2f}/{mtr['ret']:+6.0f}/{mtr['n']:4d} | "
              f"{pf_str(mte['pf'])} {mte['ret']:+7.1f} {mte['dd']:5.1f} "
              f"{mte['sh']:5.2f} {mte['n']:5d}{star}")
    print("  " + "-" * 88)
    print("  NOTE: only the TRAIN-PICKED row is honest OOS. The others are shown to "
          "reveal how much\n        the ranking would have shuffled OOS (config "
          "fragility check).")
    return picked


# =============================================================================
#  Full-sample table (IN-SAMPLE -- shown LAST, context only, never decisive)
# =============================================================================
def full_sample_table(grid_trades, bh_ret, period):
    print("\n" + "=" * 92)
    print(f" FULL-SAMPLE (IN-SAMPLE, {period}) -- context only; NOT used to pick "
          f"anything. Buy&Hold {bh_ret:+.0f}%")
    print("=" * 92)
    rows = []
    for key, trades in grid_trades.items():
        m = slice_metrics(trades)
        rows.append((m["pf"] if not math.isinf(m["pf"]) else 99, key, m))
    rows.sort(reverse=True, key=lambda r: r[0])
    print(f"  {'cfg (ADX/SL/TP)':<20} {'PF':>6} {'ret%':>9} {'MaxDD%':>7} "
          f"{'Sharpe':>7} {'trades':>7} {'win%':>6}")
    print("  " + "-" * 72)
    for _, key, m in rows[:12]:
        a, s, t = key
        print(f"  {f'ADX{a}/SL{s}/TP{t}':<20} {pf_str(m['pf']):>6} {m['ret']:+8.0f} "
              f"{m['dd']:6.1f} {m['sh']:6.2f} {m['n']:7d} {m['wr']*100:5.0f}")


def buy_hold_ret(df):
    return (df["close"].iloc[-1] / df["close"].iloc[0] - 1.0) * 100.0


# =============================================================================
#  Main
# =============================================================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--families", default="M15,H1", help="which entry TFs to run")
    ap.add_argument("--adx", default="15,20,25", help="ADX thresholds (comma)")
    ap.add_argument("--sl",  default="2,3,4",    help="SL x ATR (comma)")
    ap.add_argument("--rr",  default="2,2.5,3",  help="R:R multiples; tp=sl*rr (comma)")
    ap.add_argument("--select", default="pf", choices=["pf", "sharpe", "ret"],
                    help="TRAIN objective used to PICK the config in walk-forward")
    args = ap.parse_args()

    # ── Grid (STEP 1): ADX x SL x TP=SL*RR ────────────────────────────────────
    ADX = [int(x) for x in args.adx.split(",")]
    SL  = [float(x) for x in args.sl.split(",")]
    RR  = [float(x) for x in args.rr.split(",")]
    grid = [(a, sl, round(sl * rr, 1)) for a in ADX for sl in SL for rr in RR]
    OBJ = args.select

    print("=" * 92)
    print(" BTC HIGHER-FREQUENCY TREND-PULLBACK  --  WALK-FORWARD VALIDATION")
    print("=" * 92)
    print(" Strategy : real HybridTrendPullback (H1 EMA50/200 + ADX + M15 EMA20 "
          "pullback + ATR SL/TP)")
    print(" Execution: real BacktestEngine (bar-by-bar SL/TP, no look-ahead) + "
          "BTC overnight swap")
    print(" CONFIG DIFF vs live gold bots (echo for audit):")
    print(f"   partial_tp_frac = 0.0  (OFF -- matches live; THE-BUG guard)")
    print(f"   risk_per_trade  = {RISK_PCT}%   max_hold_bars = {make_cfg().max_hold_bars}"
          f"   trailing = OFF")
    print(f"   spread = ${SPREAD:.0f} (half=slippage/side)   commission = ${COMM:.0f}"
          f"   swap_long = ${SWAP_USD_PER_LOT_NIGHT}/lot/night (Fri x3), swap_short = 0")
    print(f" Grid: ADX{ADX} x SL{SL} x RR{RR}  = {len(grid)} configs/family")
    print(f" Data: Binance 15m SHAPE (costs from real Exness snapshot). ~2 bears "
          "(2018,2022) -> provisional.")

    fams = [f.strip().upper() for f in args.families.split(",")]
    for fam in fams:
        resample = (fam == "H1")
        print("\n" + "#" * 92)
        print(f"#  FAMILY: {fam} entry  ({'resampled to 1h; trend TF = H4' if resample else 'native 15m; trend TF = H1'})")
        print("#" * 92)
        t0 = time.time()
        d, df = load_prepared(args.csv, resample_h1=resample)
        period = f"{df['timestamp'].iloc[0].date()}..{df['timestamp'].iloc[-1].date()}"
        bh = buy_hold_ret(df)
        print(f"  loaded {len(df):,} {fam} bars  {period}  (B&H {bh:+.0f}%)  "
              f"({time.time()-t0:.1f}s)")
        print(f"  running {len(grid)} configs (full history, once each) ...")
        gt = run_grid(d, grid, fam)

        # WALK-FORWARD FIRST (decisive), full-sample LAST (context)
        years = list(range(2019, 2027))     # 2018 too early (warmup); 2019-2026
        wf_yearly(gt, years, objective=OBJ)
        wf_bigsplit(gt, split="2022-01-01", objective=OBJ)
        full_sample_table(gt, bh, period)

    print("\n" + "=" * 92)
    print(" DISCLAIMERS: every number above includes $10 spread + asymmetric swap. "
          "OOS bear coverage is\n thin (~2 bears). BTC 2019-21 & 2023-25 are bull -> "
          "watch for bull-beta concentration in\n the yearly table. Nothing here is "
          "deployed; DEMO forward-test is the next gate, not real money.")
    print("=" * 92)


if __name__ == "__main__":
    main()
