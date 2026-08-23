#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Three untested directions, chosen because each attacks a specific thing we
learned rather than being another random variation.

  A) CRYPTO INTRADAY (M15 / H1 / H4)
     Every crypto edge found so far beat every gold edge, and the reason gold
     M15 died was cost/ATR (2.85 on a ~6 ATR = 45%). BTCUSDc pays ~$10 spread
     on an M15 ATR of several hundred dollars -- a few percent. So the exact
     thing that killed gold intraday may simply not apply to crypto intraday,
     and it has never been tested.

  B) VOLATILITY REGIME FILTER
     Applied to the Donchian book we already have. Breakouts are known to fail
     in low-volatility chop and pay in expansions. If gating on realised vol
     lifts Sharpe, that is a free improvement to an edge we already hold, which
     is worth more than another marginal new market.

  C) SEASONALITY / DAY-OF-WEEK
     Cheap to test and occasionally real (month-end flows, weekend effects in
     crypto). Reported honestly -- most of these are data-mining artefacts and
     the test is designed to show that if so.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import (DataLoader, prepare_data, BacktestEngine,
                             FastHybridTrendPullback, compute_metrics)
from _idea_search import DonchianBreakout, resample
from _all_paths import to_monthly, perf, START, RISK
from _daily_multi_market import MARKETS, load_daily

TRADING_DAYS = 252

CRYPTO = {
    "BTC": ("download/btcusdt-15m-binance-2017-08-17-2026-06-30.csv", "BTCUSDc", 10.0, 1.0, 0.01),
    "ETH": ("download/ethusdt-15m-binance-2017-08-17-2026-06-30.csv", "ETHUSDc",  5.0, 1.0, 0.01),
}


def cfg(sym, ps, pv, hold):
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = RISK
    c.partial_tp_atr = 999.0
    c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False
    c.max_hold_bars = hold
    c.pip_size[sym] = ps
    c.pip_value_usd_approx[sym] = pv
    return c


def run(cls, d, sym, spread, ps, pv, hold, **ov):
    s = cls()
    for k, v in ov.items():
        setattr(s, k, v)
    if not hasattr(s, "sl_atr"):
        return None, None
    s.sl_atr = getattr(s, "sl_atr", 3.0)
    s.tp_atr = ov.get("tp_atr", 7.0)
    s.sl_atr = 3.0
    s.trail_atr_mult = s.trail_activation_atr = 999.0
    s.precompute(d)
    eng = BacktestEngine(d, cfg(sym, ps, pv, hold), s, spread_price=spread,
                          commission_per_lot=0.0, symbol=sym)
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START), eng.trades


# ---------------- C) vol-regime filtered Donchian ----------------
class VolFilteredDonchian(DonchianBreakout):
    """Donchian, but only take breakouts when realised vol is in a chosen band.

    VOL_MODE 'high' : trade only when ATR/price is above its rolling median
             'low'  : only below
             'off'  : no filter (baseline)
    """
    VOL_MODE = "off"
    VOL_WIN = 100
    _volflag = None

    def precompute(self, d):
        super().precompute(d)
        atr = np.asarray(d["atr"], dtype=float)
        c = np.asarray(d["c"], dtype=float)
        rel = np.where(c > 0, atr / c, np.nan)
        s = pd.Series(rel)
        med = s.rolling(self.VOL_WIN, min_periods=self.VOL_WIN // 2).median()
        if self.VOL_MODE == "high":
            self._volflag = (s > med).to_numpy()
        elif self.VOL_MODE == "low":
            self._volflag = (s < med).to_numpy()
        else:
            self._volflag = np.ones(len(s), dtype=bool)

    def signal(self, d, i):
        if self._volflag is not None and i < len(self._volflag) and not self._volflag[i]:
            from forex_indicators import Signal
            return Signal()
        return super().signal(d, i)


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START

    # =============== A) crypto intraday ===============
    print("=" * 104)
    print(" A) CRYPTO INTRADAY -- does the cost/ATR problem that killed gold M15 apply here?")
    print("=" * 104)
    print(f"  {'market':<8}{'TF':<6}{'strategy':<16}{'trades':>8}{'PF':>7}{'win%':>7}"
          f"{'Sharpe(mo)':>12}{'CAGR%':>9}{'DD%':>7}")
    crypto_series = {}
    for nm, (csv, sym, spread, ps, pv) in CRYPTO.items():
        df, _ = loader.load(sym, 99.0, c0, csv_path=csv, allow_synthetic=False)
        # report the cost/ATR ratio that decided gold's fate
        for tf, rule, hold in [("M15", None, 64), ("H1", "1h", 64), ("H4", "4h", 32)]:
            dfx = df if rule is None else resample(df, rule)
            d = prepare_data(dfx)
            atr_med = float(np.nanmedian(d["atr"]))
            ratio = spread / atr_med * 100 if atr_med > 0 else float("nan")
            for sname, cls, kw in [("donch100", DonchianBreakout, dict(CHANNEL=100)),
                                   ("pullback18", FastHybridTrendPullback, dict(ADX_MIN=18))]:
                try:
                    m, tr = run(cls, d, sym, spread, ps, pv, hold, **kw)
                except Exception as e:
                    continue
                if not m or m.get("trades", 0) < 30:
                    continue
                mr = to_monthly(tr); p = perf(mr)
                sh = p["sharpe"] if p else float("nan")
                star = "  <==" if p and p["sharpe"] > 1.0 else ""
                print(f"  {nm:<8}{tf:<6}{sname:<16}{m['trades']:>8}{m['profit_factor']:>7.2f}"
                      f"{m['win_rate']*100:>7.1f}{sh:>12.2f}"
                      f"{(p['cagr'] if p else 0):>9.2f}{(p['dd'] if p else 0):>7.1f}{star}")
                if p and p["sharpe"] > 0.5:
                    crypto_series[f"{nm}-{tf}-{sname}"] = mr
            print(f"  {'':<8}{tf:<6}{'(cost/ATR = ' + f'{ratio:.1f}%' + ')':<16}"
                  f"{'  gold M15 was 45% -> died' if tf=='M15' else ''}")

    # =============== B) vol regime filter ===============
    print("\n" + "=" * 104)
    print(" B) VOLATILITY REGIME FILTER on the existing Donchian book")
    print("=" * 104)
    print(f"  {'filter':<12}{'markets':>9}{'months':>8}{'Sharpe':>9}{'CAGR%':>9}{'DD%':>8}")
    DAILY_CH = {"EURUSD": 100, "USDJPY": 200, "AUDUSD": 100, "USDCAD": 100,
                "SPX": 100, "NDX": 55, "WTI": 200, "GOLDFUT": 100}
    for mode in ["off", "high", "low"]:
        series = {}
        for mkt, ch in DAILY_CH.items():
            csv, spread, ps, pv, comm = MARKETS[mkt]
            try:
                d = prepare_data(load_daily(csv))
            except Exception:
                continue
            s = VolFilteredDonchian()
            s.CHANNEL = ch; s.VOL_MODE = mode
            s.sl_atr, s.tp_atr = 3.0, 7.0
            s.trail_atr_mult = s.trail_activation_atr = 999.0
            s.precompute(d)
            eng = BacktestEngine(d, cfg(mkt, ps, pv, 100), s, spread_price=spread,
                                  commission_per_lot=comm, symbol=mkt)
            eng.run(quiet=True, do_precompute=False)
            m = compute_metrics(eng.trades, eng.equity_curve, START)
            if m and m.get("trades", 0) >= 20:
                mr = to_monthly(eng.trades)
                if len(mr) >= 24:
                    series[mkt] = mr
        if len(series) >= 5:
            port = pd.concat(series, axis=1, sort=True).mean(axis=1, skipna=True).dropna()
            p = perf(port)
            if p:
                print(f"  {mode:<12}{len(series):>9}{p['n']:>8}{p['sharpe']:>9.2f}"
                      f"{p['cagr']:>9.2f}{p['dd']:>8.1f}")

    # =============== C) seasonality ===============
    print("\n" + "=" * 104)
    print(" C) SEASONALITY -- day-of-week and month effects (buy-and-hold conditional returns)")
    print("=" * 104)
    from _new_signal_classes import load_all_daily, build_panel
    px = build_panel(load_all_daily())
    rets = px.pct_change()
    avg = rets.mean(axis=1).dropna()
    print("  day-of-week mean daily return across all markets:")
    dow = avg.groupby(avg.index.dayofweek).agg(["mean", "count"])
    names = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}
    for k, row in dow.iterrows():
        t = row["mean"] / (avg.std() / np.sqrt(row["count"])) if row["count"] > 1 else 0
        flag = "  significant" if abs(t) > 2.5 else ""
        print(f"    {names.get(k,k):<5} mean={row['mean']*100:>+7.4f}%  n={int(row['count']):>5}  t={t:>+5.2f}{flag}")
    print("\n  month-of-year mean daily return:")
    moy = avg.groupby(avg.index.month).agg(["mean", "count"])
    for k, row in moy.iterrows():
        t = row["mean"] / (avg.std() / np.sqrt(row["count"])) if row["count"] > 1 else 0
        flag = "  significant" if abs(t) > 2.5 else ""
        print(f"    month {k:>2}  mean={row['mean']*100:>+7.4f}%  n={int(row['count']):>5}  t={t:>+5.2f}{flag}")


if __name__ == "__main__":
    main()
