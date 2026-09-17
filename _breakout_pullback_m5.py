#!/usr/bin/env python3
"""Breakout Pullback M5 on XAUUSD -- run inside the ADX20_TP7 M5 harness.

Rules exactly as specified (assumptions marked ASSUME):
  1 range      = high/low of the last 20 CLOSED bars (the breakout bar excluded)
  2 breakout   BUY  close > range_high + 0.1*ATR14   SELL close < range_low - 0.1*ATR14
               the broken level (range_high / range_low) and that bar's ATR are locked
  3 retest     within the next 6 bars:
               BUY  bar low  <= level + 0.2*ATR(breakout bar)   (came back into the band)
               SELL bar high >= level - 0.2*ATR(breakout bar)
               ASSUME a touch that closes on the wrong side keeps the setup alive
               until the 6 bars are used up
  4 entry      the retest bar must CLOSE beyond the level; fill at the next bar's open
  5 SL         BUY retest low - 0.2*ATR   SELL retest high + 0.2*ATR  (ATR of the retest bar)
  6 TP         2 x (entry - SL); ASSUME no time stop (none specified)
  7 one trade per breakout, no pyramid; an opposite breakout cancels the pending
    setup and starts its own; a same-side breakout while one is pending is ignored
    Setups that complete while a position is open are dropped.

Engine: backtest_forex.BacktestEngine, same as backtest_m5.py (next-bar-open
fills, SL checked before TP inside a bar, risk-% lot sizing with the 2% risk
cap, 0.3% risk, $10,000). Only _enter is overridden, to place SL/TP from the
setup instead of from ATR multiples.

Two things about the harness this file reports honestly rather than hides:
  * "Calmar" in backtest_m5.period_slice is TOTAL return % over the period /
    MaxDD, not annualised, summed from compounded trade P&L restarted at
    $10,000. A 4.4-year OOS number is inflated several times by that alone.
  * The engine charges HALF the spread (fill = open +/- spread/2). The M5
    baseline used SPREAD=0.10, i.e. $0.05 per trade; XAUUSDm's real spread is
    0.26. And 32.7% of the M5 file is flat filler bars (weekends, daily break)
    that collapse a 20-bar range and ATR14.
So it prints the requested table on the baseline's own terms, then again with
filler removed, the real spread, and an annualised Calmar on fixed-notional P&L.
"""
from __future__ import annotations

import math
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import backtest_m5 as B                                   # noqa: E402
from backtest_forex import BacktestEngine, BTPosition, prepare_data  # noqa: E402
from forex_indicators import Signal                        # noqa: E402
from walk_forward_regime import build_windows              # noqa: E402

CSV = os.path.join(HERE, "download", "xauusd-m5-bid-2013-01-01-2026-06-01.csv")
START, RISK = 10_000.0, 0.30
PERIODS = [("FULL 2013-2026", "2013-01-01", "2026-06-01"),
           ("IS   2013-2019", "2013-01-01", "2020-01-01"),
           ("VAL  2020-2021", "2020-01-01", "2022-01-01"),
           ("OOS  2022-2026", "2022-01-01", "2026-06-01")]
COSTS = {"harness": (0.10, 3.50),        # (engine spread_price, commission/side per lot)
         "real XAUUSDm": (0.52, 0.0)}    # engine charges half -> 0.26 per trade


class BreakoutPullbackM5:
    MIN_BARS = 60
    sl_atr = tp_atr = 1.0                 # unused: SL/TP come from the setup
    trail_atr_mult = trail_activation_atr = 999.0
    RANGE, BRK, BAND, WIN, SLBUF, RR = 20, 0.1, 0.2, 6, 0.2, 2.0

    def precompute(self, d):
        h = np.asarray(d["h"], float); l = np.asarray(d["l"], float)
        c = np.asarray(d["c"], float); atr = np.asarray(d["atr"], float)
        n = len(c)
        rh = pd.Series(h).rolling(self.RANGE).max().shift(1).to_numpy()
        rl = pd.Series(l).rolling(self.RANGE).min().shift(1).to_numpy()
        sig = np.zeros(n, np.int8)
        slp = np.full(n, np.nan)
        active, lvl, atr_b, expiry = 0, 0.0, 0.0, -1
        n_brk = n_cancel_opp = n_expired = 0
        for i in range(n):
            a = atr[i]
            if active and i > expiry:
                active = 0; n_expired += 1
            if not (a > 0) or math.isnan(rh[i]) or math.isnan(rl[i]):
                continue
            up = c[i] > rh[i] + self.BRK * a
            dn = c[i] < rl[i] - self.BRK * a
            if active == 1:
                if dn:
                    active = 0; n_cancel_opp += 1
                elif l[i] <= lvl + self.BAND * atr_b and c[i] > lvl:
                    sig[i] = 1; slp[i] = l[i] - self.SLBUF * a; active = 0
                    continue
            elif active == -1:
                if up:
                    active = 0; n_cancel_opp += 1
                elif h[i] >= lvl - self.BAND * atr_b and c[i] < lvl:
                    sig[i] = -1; slp[i] = h[i] + self.SLBUF * a; active = 0
                    continue
            if active == 0:
                if up:
                    active, lvl, atr_b, expiry = 1, rh[i], a, i + self.WIN; n_brk += 1
                elif dn:
                    active, lvl, atr_b, expiry = -1, rl[i], a, i + self.WIN; n_brk += 1
        self.sig, self.slp = sig, slp
        self.stats = dict(breakouts=n_brk, retest_entries=int((sig != 0).sum()),
                          cancelled_by_opposite=n_cancel_opp, expired=n_expired)

    def signal(self, d, i):
        s = self.sig[i]
        return Signal("BUY" if s == 1 else "SELL" if s == -1 else "HOLD", "bp")


class BPEngine(BacktestEngine):
    """Identical engine; entry places SL at the setup level and TP at 2R."""

    def _enter(self, i, bar_open, ts):
        long = self._pending.action == "BUY"
        sl = float(self.strat.slp[i - 1])
        atr = float(self.d["atr"][i - 1])
        if math.isnan(sl) or math.isnan(atr) or atr <= 0:
            return
        hs = self.spread_price / 2.0
        fill = bar_open + hs if long else bar_open - hs
        risk_px = (fill - sl) if long else (sl - fill)
        if risk_px <= 0:                                   # opened through the stop
            return
        tp = fill + self.strat.RR * risk_px if long else fill - self.strat.RR * risk_px
        sl_pips = risk_px / self.pip_size
        risk_cash = self.equity * self.cfg.risk_per_trade_pct / 100.0
        lot = max(self.cfg.min_lot, round(risk_cash / (sl_pips * self.pip_value), 2))
        lot = min(lot, self.cfg.max_lot)
        actual = (sl_pips * self.pip_value * lot) / self.equity * 100.0 if self.equity > 0 else float("inf")
        if actual > self.cfg.max_risk_per_trade_pct:
            self.skipped_risk_cap += 1
            return
        entry_comm = self.commission_per_lot * lot
        self.equity -= entry_comm
        far = 1e9
        self.position = BTPosition(
            side="long" if long else "short", entry=fill, lot=lot, sl=sl, tp=tp,
            partial_tp=far if long else -far, trail_sl=-far if long else far,
            highest=fill, lowest=fill, entry_atr=atr, entry_ts=ts, entry_bar=i,
            entry_comm=entry_comm)


def cfg(no_timeout):
    c = B._cfg()
    if no_timeout:
        c.max_hold_bars = 10 ** 9
    return c


def run(d, strat, eng_cls, spread, comm, no_timeout):
    strat.precompute(d)
    eng = eng_cls(d, cfg(no_timeout), strat, spread_price=spread,
                  commission_per_lot=comm, symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)
    return eng


def idx_range(d, pfrom, pto):
    ts = pd.to_datetime(pd.Series(d["ts"]))
    a = int(np.searchsorted(ts.to_numpy(), np.datetime64(pd.Timestamp(pfrom))))
    b = int(np.searchsorted(ts.to_numpy(), np.datetime64(pd.Timestamp(pto))))
    return a, b, ts


def fresh(d, make, eng_cls, spread, comm, no_timeout, pfrom, pto):
    """independent run from $10,000 inside [pfrom, pto): a strategy that dies
    in one period cannot silently empty the next one"""
    st = make()
    st.precompute(d)
    a, b, ts = idx_range(d, pfrom, pto)
    if b - a < 500:
        return None
    eng = eng_cls(d, cfg(no_timeout), st, spread_price=spread,
                  commission_per_lot=comm, symbol="XAUUSD")
    eng.run(start_i=max(a, st.MIN_BARS), end_i=b, quiet=True, do_precompute=False)
    tr = eng.trades
    if not tr:
        return dict(n=0)
    pn = np.array([t["net_pnl"] for t in tr])
    eq = np.array(eng.equity_curve)
    peak = np.maximum.accumulate(np.r_[START, eq])
    dd = float(np.max((peak - np.r_[START, eq]) / peak) * 100) or 0.001
    final = float(eq[-1]) if len(eq) else START
    yrs = max((ts.iloc[b - 1] - ts.iloc[a]).days / 365.25, 0.05)
    ret = (final - START) / START * 100
    cagr = ((max(final, 1e-9) / START) ** (1 / yrs) - 1) * 100
    gw, gl = pn[pn > 0].sum(), -pn[pn <= 0].sum()
    return dict(n=len(tr), pf=gw / gl if gl > 0 else float("inf"), win=(pn > 0).mean() * 100,
                dd=dd, ret=ret, cagr=cagr, cal=cagr / dd, cal_h=ret / dd, final=final,
                skipped=eng.skipped_risk_cap)


def main():
    t0 = time.time()
    W = 104
    raw = B.load_data(CSV)
    flat = (raw["open"] == raw["high"]) & (raw["high"] == raw["low"]) & (raw["low"] == raw["close"])
    d_clean = prepare_data(raw.loc[~flat].reset_index(drop=True))
    d_raw = prepare_data(raw)
    wins = build_windows(raw["timestamp"].iloc[0], raw["timestamp"].iloc[-1], 6)
    STRATS = [("BreakoutPullback", BreakoutPullbackM5, BPEngine, True),
              ("ADX20_TP7 M5", B._make_strat_m5, BacktestEngine, False)]

    print("=" * W)
    print(f" BREAKOUT PULLBACK M5 vs ADX20_TP7 M5 -- XAUUSD {raw['timestamp'].iloc[0].date()} .. "
          f"{raw['timestamp'].iloc[-1].date()} -- every period and window starts FRESH at ${START:,.0f}, risk {RISK}%")
    print(f" {int(flat.sum()):,} of {len(raw):,} M5 bars ({flat.mean()*100:.1f}%) are flat filler"
          f" (weekends / daily break) and are removed; {len(wins)} windows x 6 months")
    print("=" * W)

    oos = {}
    for cname, (spread, comm) in [("real XAUUSDm (0.26 spread/trade)", COSTS["real XAUUSDm"]),
                                  ("harness costs (0.05 spread + $3.50/side)", COSTS["harness"])]:
        print(f"\n{'#'*W}\n COSTS: {cname}\n Calmar = CAGR % / MaxDD %  (harness-style = total return % / MaxDD, shown for comparison)\n{'#'*W}")
        print(f" {'strategy':<17}{'period':<16}{'PF':>6}{'Calmar':>8}{'h-Cal':>7}{'Win%':>7}{'MaxDD':>8}{'Trades':>8}{'CAGR':>8}{'$10k->':>10}")
        for sname, make, ecls, nt in STRATS:
            for plabel, pf_, pt_ in PERIODS:
                m = fresh(d_clean, make, ecls, spread, comm, nt, pf_, pt_)
                if not m or m.get("n", 0) == 0:
                    print(f" {sname:<17}{plabel:<16}  no trades"); continue
                print(f" {sname:<17}{plabel:<16}{m['pf']:>6.2f}{m['cal']:>8.2f}{m['cal_h']:>7.2f}{m['win']:>6.1f}%"
                      f"{m['dd']:>7.1f}%{m['n']:>8,}{m['cagr']:>+7.1f}%{m['final']:>10,.0f}")
                if plabel.startswith("OOS"):
                    oos[(cname, sname)] = m
            ok = n_w = 0
            for ws, we in wins:
                m = fresh(d_clean, make, ecls, spread, comm, nt, str(ws.date()), str(we.date()))
                if m and m.get("n", 0) > 0:
                    n_w += 1
                    ok += m["pf"] > 1.0
            print(f" {sname:<17}{'27 windows':<16}  PF > 1 in {ok} of {n_w} windows with trades")

    print(f"\n{'#'*W}\n OOS YEAR BY YEAR -- Breakout Pullback, real XAUUSDm spread, fresh $10k each year\n{'#'*W}")
    print(f" {'year':<8}{'trades':>8}{'PF':>7}{'Win%':>7}{'return':>9}{'MaxDD':>8}")
    for y in range(2022, 2027):
        m = fresh(d_clean, BreakoutPullbackM5, BPEngine, *COSTS["real XAUUSDm"], True,
                  f"{y}-01-01", f"{y+1}-01-01")
        if not m or m.get("n", 0) == 0:
            print(f" {y:<8}  no trades"); continue
        print(f" {y:<8}{m['n']:>8,}{m['pf']:>7.2f}{m['win']:>6.1f}%{m['ret']:>+8.1f}%{m['dd']:>7.1f}%")

    print(f"\n{'#'*W}\n THE CLAIMED BASELINE: ADX20_TP7 M5 'OOS Calmar 53.3, MaxDD 14%, Win 42.9%'\n{'#'*W}")
    trades = run(d_raw, B._make_strat_m5(), BacktestEngine, *COSTS["harness"], no_timeout=False).trades
    m = B.period_slice(trades, "2022-01-01", "2026-06-01")
    print(f" re-run exactly as backtest_m5.py does it (raw bars incl. filler, SPREAD 0.10, COMM 3.50,"
          f" period_slice):\n   OOS PF {m['pf']:.3f}  Calmar {m['calmar']:.1f}  MaxDD {m['dd']:.1f}%  Win {m['win_pct']:.1f}%  trades {m['n']:,}"
          f"  -> {'REPRODUCED' if abs(m['calmar'] - 53.3) < 5 else 'NOT reproduced'}")

    print(f"\n{'#'*W}\n DECISION RULE: OOS Calmar >= 20 AND MaxDD < 25%\n{'#'*W}")
    for (cname, sname), m in oos.items():
        cal, dd = m["cal"], m["dd"]
        go = cal >= 20 and dd < 25
        why = ([] if cal >= 20 else [f"Calmar {cal:.2f} < 20"]) + ([] if dd < 25 else [f"MaxDD {dd:.1f}% >= 25%"])
        print(f"  {sname:<17} {cname:<42} Calmar {cal:>6.2f}  MaxDD {dd:>5.1f}%  -> "
              f"{'CONTINUE' if go else 'STOP'}{'' if go else '  (' + '; '.join(why) + ')'}")
    print(f"\n total {time.time()-t0:.0f}s")
    print("=" * W)


if __name__ == "__main__":
    main()
