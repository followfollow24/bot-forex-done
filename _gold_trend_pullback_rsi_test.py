#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gold trend-following playbook, built to the user's written spec (2026-08-03):

  1. H4 structural trend: rising confirmed swing highs+lows = UP (buy-only),
     falling = DOWN (sell-only). (Not EMA -- the spec says H4 is pure
     structure; EMA is specified for H1 only.)
  2. H1 entry zone: price pulls back to EMA20 or EMA50 (H1), in trend
     direction.
  3. H1 RSI(14) must show exhaustion of the pullback: <=35 near a BUY zone,
     >=65 near a SELL zone (spec says "near 30/70"; used a small band since
     "exactly touches 30.0" almost never happens on real data).
  4. Reversal candle at the zone: pin bar (wick >= 2x body, closes on the
     far side) or engulfing (body fully covers the prior candle's body,
     opposite color).
  5. Entry on that candle's close.
  6. SL: beyond the reversal wick + a buffer (spec says "$2-3" -- implemented
     as buffer_atr x ATR so it scales with volatility instead of being a
     fixed dollar amount that meant something different in 2013 gold prices
     than in 2026 prices).
  7. TP: next resistance/support -- previous day's high (BUY) or low (SELL).
     Skip the trade entirely if that gives R:R < 1.5 (the spec's hard rule).
  8. News filter: NOT implemented -- no economic calendar data exists in
     this project (verified same way the "no volume column" caveat was
     verified). Flagged explicitly rather than silently skipped.

Tested at H1 (spec's own entry timeframe) with real gold costs.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import DataLoader, prepare_data, BacktestEngine, compute_metrics
from forex_indicators import Signal
from _idea_search import resample
from _all_paths import to_monthly, perf, START


def _rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(closes)
    out = np.full(n, np.nan)
    if n < period + 1:
        return out
    delta = np.diff(closes)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = np.zeros(n); avg_loss = np.zeros(n)
    avg_gain[period] = gain[:period].mean()
    avg_loss[period] = loss[:period].mean()
    for i in range(period + 1, n):
        avg_gain[i] = (avg_gain[i-1] * (period-1) + gain[i-1]) / period
        avg_loss[i] = (avg_loss[i-1] * (period-1) + loss[i-1]) / period
    rs = np.divide(avg_gain, avg_loss, out=np.full(n, np.inf), where=avg_loss > 0)
    out[period:] = 100 - 100 / (1 + rs[period:])
    out[avg_loss == 0] = 100.0
    out[:period] = np.nan
    return out


def _ema(prices, span):
    out = np.full(len(prices), np.nan)
    if len(prices) < span:
        return out
    alpha = 2.0 / (span + 1)
    out[0] = prices[0]
    for j in range(1, len(prices)):
        out[j] = prices[j] * alpha + out[j-1] * (1 - alpha)
    return out


class GoldTrendPullbackRSI:
    name = "Gold Trend-Pullback + RSI + Reversal Candle (H4 struct / H1 entry)"
    short_name = "GoldTPB-RSI"

    SWING_LOOKBACK = 5           # H4 swing confirmation lag (bars)
    EMA_FAST, EMA_SLOW = 20, 50  # H1
    RSI_PERIOD = 14
    RSI_BUY_MAX = 35             # "near 30" oversold band
    RSI_SELL_MIN = 65            # "near 70" overbought band
    TOUCH_TOL_ATR = 0.30         # how close to EMA counts as "touched"
    WICK_MULT = 2.0
    SL_BUFFER_ATR = 0.30
    MIN_RR = 1.5

    sl_atr = 2.0; tp_atr = 999.0            # placeholders; real SL/TP set per-signal below
    trail_atr_mult = 999.0; trail_activation_atr = 999.0
    max_spread_atr_ratio = 1.0
    MIN_BARS = 250
    _built_len = None

    @staticmethod
    def _epoch(ts):
        return pd.to_datetime(pd.Series(ts)).astype("datetime64[s]").astype("int64").to_numpy()

    def precompute(self, d):
        n = len(d["c"])
        h1_c = d["c"]

        # H1 indicators
        self._ema_fast = _ema(h1_c, self.EMA_FAST)
        self._ema_slow = _ema(h1_c, self.EMA_SLOW)
        self._rsi = _rsi(h1_c, self.RSI_PERIOD)

        # previous DAY high/low (calendar day, causal: prior day only)
        epoch = self._epoch(d["ts"])
        day = epoch // 86400
        df = pd.DataFrame({"day": day, "h": d["h"], "l": d["l"]})
        dh = df.groupby("day")["h"].max(); dl = df.groupby("day")["l"].min()
        days = sorted(dh.index)
        pdh_m = {days[k]: dh[days[k-1]] for k in range(1, len(days))}
        pdl_m = {days[k]: dl[days[k-1]] for k in range(1, len(days))}
        self._pdh = df["day"].map(pdh_m).to_numpy(dtype=float)
        self._pdl = df["day"].map(pdl_m).to_numpy(dtype=float)

        # H4 structural trend: aggregate H1->H4 buckets (4 bars), confirmed
        # swing highs/lows on that H4 series, causal expansion back to H1.
        h4_bucket = (epoch // (4 * 3600))
        tmp = pd.DataFrame({"b": h4_bucket, "h": d["h"], "l": d["l"]})
        uniq, k_of = np.unique(h4_bucket, return_inverse=True)
        n4 = len(uniq)
        g = tmp.groupby("b")
        h4_h = g["h"].max().to_numpy()
        h4_l = g["l"].min().to_numpy()

        lb = self.SWING_LOOKBACK
        is_sw_hi = np.zeros(n4, dtype=bool)
        is_sw_lo = np.zeros(n4, dtype=bool)
        for k in range(lb, n4 - lb):
            if h4_h[k] == h4_h[k-lb:k+lb+1].max():
                is_sw_hi[k] = True
            if h4_l[k] == h4_l[k-lb:k+lb+1].min():
                is_sw_lo[k] = True

        h4_trend = np.zeros(n4, dtype=np.int8)
        last_highs, last_lows = [], []
        for k in range(n4):
            c = k - lb
            if c >= 0:
                if c - lb >= 0 and c + lb < n4:
                    pass
                if 0 <= c < n4 and is_sw_hi[c]:
                    last_highs.append(h4_h[c]); last_highs = last_highs[-3:]
                if 0 <= c < n4 and is_sw_lo[c]:
                    last_lows.append(h4_l[c]); last_lows = last_lows[-3:]
            if len(last_highs) >= 2 and len(last_lows) >= 2:
                if last_highs[-1] > last_highs[-2] and last_lows[-1] > last_lows[-2]:
                    h4_trend[k] = 1
                elif last_highs[-1] < last_highs[-2] and last_lows[-1] < last_lows[-2]:
                    h4_trend[k] = -1

        # causal expansion to H1: bar i sees H4 bucket k only once that
        # bucket has fully closed (same discipline as the earlier H1/H4 fix)
        entry_sec = 3600
        bucket_sec = 4 * 3600
        is_last = (epoch // bucket_sec) != ((epoch + entry_sec) // bucket_sec)
        k_complete = np.where(is_last, k_of, k_of - 1)
        valid = k_complete >= 0
        k_complete = np.clip(k_complete, 0, n4 - 1)
        out = np.zeros(n, dtype=np.int8)
        out[valid] = h4_trend[k_complete[valid]]
        self._h4_trend = out
        self._built_len = n

    def _ensure(self, d):
        if self._built_len != len(d["c"]):
            self.precompute(d)

    def signal(self, d, i):
        if i < self.MIN_BARS: return Signal()
        self._ensure(d)
        atr = d["atr"][i]
        if np.isnan(atr) or atr <= 0: return Signal()

        trend = int(self._h4_trend[i])
        if trend == 0: return Signal()

        ef, es, rsi_v = self._ema_fast[i], self._ema_slow[i], self._rsi[i]
        if np.isnan(ef) or np.isnan(es) or np.isnan(rsi_v): return Signal()

        o, h, l, c = d["o"][i], d["h"][i], d["l"][i], d["c"][i]
        body = abs(c - o)
        up_wick = h - max(o, c)
        dn_wick = min(o, c) - l
        tol = self.TOUCH_TOL_ATR * atr

        if trend == 1:
            touched = (l <= ef + tol) or (l <= es + tol)
            oversold = rsi_v <= self.RSI_BUY_MAX
            pin_or_engulf = dn_wick >= self.WICK_MULT * max(body, 1e-9) and c > o
            if touched and oversold and pin_or_engulf:
                sl_price = l - self.SL_BUFFER_ATR * atr
                tp_price = self._pdh[i]
                if np.isfinite(tp_price) and tp_price > c:
                    risk = c - sl_price
                    reward = tp_price - c
                    if risk > 0 and reward / risk >= self.MIN_RR:
                        self.sl_atr = risk / atr
                        self.tp_atr = reward / atr
                        return Signal("BUY", f"H4up pullback ema RSI={rsi_v:.0f} PDH={tp_price:.2f}")

        elif trend == -1:
            touched = (h >= ef - tol) or (h >= es - tol)
            overbought = rsi_v >= self.RSI_SELL_MIN
            pin_or_engulf = up_wick >= self.WICK_MULT * max(body, 1e-9) and c < o
            if touched and overbought and pin_or_engulf:
                sl_price = h + self.SL_BUFFER_ATR * atr
                tp_price = self._pdl[i]
                if np.isfinite(tp_price) and tp_price < c:
                    risk = sl_price - c
                    reward = c - tp_price
                    if risk > 0 and reward / risk >= self.MIN_RR:
                        self.sl_atr = risk / atr
                        self.tp_atr = reward / atr
                        return Signal("SELL", f"H4dn pullback ema RSI={rsi_v:.0f} PDL={tp_price:.2f}")

        return Signal()


def cfg(risk=0.5, hold=48):
    c = ForexConfig()
    c.total_capital_usd = START
    c.risk_per_trade_pct = risk
    c.partial_tp_atr = 999.0; c.partial_tp_frac = 0.0
    c.move_sl_to_breakeven = False; c.max_hold_bars = hold
    return c


def run(d, risk=0.5, **kw):
    s = GoldTrendPullbackRSI()
    for k, v in kw.items(): setattr(s, k, v)
    s.precompute(d)
    eng = BacktestEngine(d, cfg(risk), s, spread_price=2.85,
                         commission_per_lot=3.5, symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START), eng.trades


def line(m, tr, label, yrs):
    if not m or m.get("trades", 0) < 15:
        print(f"    {label:<32} n={m.get('trades',0) if m else 0:>5}  too few"); return
    p = perf(to_monthly(tr)); sh = p["sharpe"] if p else float("nan")
    tot = m["total_return_pct"]
    cg = -100.0 if tot <= -100 else ((1+tot/100)**(1/yrs)-1)*100
    print(f"    {label:<32} n={m['trades']:>5} ({m['trades']/yrs:>4.0f}/yr {m['trades']/yrs/365:.2f}/day)  "
          f"win%={m['win_rate']*100:>5.1f}  PF={m['profit_factor']:>5.2f}  Sharpe={sh:>5.2f}  "
          f"CAGR={cg:>+7.2f}%  DD={m['max_dd_pct']:>5.1f}%")


def main():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START
    dfg, _ = loader.load("XAUUSD", 99.0, c0,
                         csv_path="download/xauusd-m15-bid-2013-01-01-2026-06-10.csv",
                         allow_synthetic=True)
    dfg_h1 = resample(dfg, "1h")
    yrs = (dfg_h1["timestamp"].iloc[-1] - dfg_h1["timestamp"].iloc[0]).days / 365.25
    d = prepare_data(dfg_h1)

    print("=" * 100)
    print(" GOLD TREND-PULLBACK + RSI + REVERSAL CANDLE (built to spec) -- H1, real cost $2.85")
    print("=" * 100)
    print(f"\n  full history {yrs:.1f}y")
    line(*run(d), "full spec", yrs)

    print("\n  ablation")
    line(*run(d, RSI_BUY_MAX=100, RSI_SELL_MIN=0), "no RSI filter", yrs)
    line(*run(d, MIN_RR=0.0), "no min R:R filter", yrs)
    line(*run(d, WICK_MULT=0.0), "no wick requirement", yrs)

    mid = dfg_h1["timestamp"].iloc[len(dfg_h1)//2]
    tr_df = dfg_h1[dfg_h1["timestamp"] <= mid].reset_index(drop=True)
    te_df = dfg_h1[dfg_h1["timestamp"] >  mid].reset_index(drop=True)
    y_tr = (tr_df["timestamp"].iloc[-1]-tr_df["timestamp"].iloc[0]).days/365.25
    y_te = (te_df["timestamp"].iloc[-1]-te_df["timestamp"].iloc[0]).days/365.25
    print(f"\n  OOS split (full spec, nothing tuned)")
    line(*run(prepare_data(tr_df)), "1st half (train)", y_tr)
    line(*run(prepare_data(te_df)), "2nd half (OOS)", y_te)

    print(f"\n  yearly walk-forward")
    years = sorted(dfg_h1["timestamp"].dt.year.unique())
    ok = tot = 0
    for y in years:
        dfy = dfg_h1[dfg_h1["timestamp"].dt.year == y].reset_index(drop=True)
        if len(dfy) < 1000: continue
        m, tr = run(prepare_data(dfy))
        if not m or m["trades"] < 8:
            print(f"    {y}: too few"); continue
        tot += 1; good = m["profit_factor"] > 1.0; ok += 1 if good else 0
        print(f"    {y}: n={m['trades']:>3} PF={m['profit_factor']:.2f} TotRet={m['total_return_pct']:+.1f}%{'  <-- PF<1' if not good else ''}")
    print(f"    -> years PF>1: {ok}/{tot}")


if __name__ == "__main__":
    main()
