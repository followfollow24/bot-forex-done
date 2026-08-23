"""Adversarial verification of BLK20_01 time-of-day gate on GOLD (frozen live config).
Independent re-implementation; do not import the finder's mask code."""
import sys, os
sys.path.insert(0, os.getcwd())
import numpy as np, pandas as pd
from forex_config import ForexConfig
from backtest_forex import DataLoader, prepare_data, BacktestEngine, FastHybridTrendPullback, compute_metrics
from forex_indicators import Signal
from _idea_search import resample
from _all_paths import to_monthly, perf, START

class Filtered(FastHybridTrendPullback):
    _mask_long = None
    _mask_short = None
    def signal(self, d, i):
        s = super().signal(d, i)
        if s.action == "BUY" and self._mask_long is not None and not self._mask_long[i]:
            return Signal()
        if s.action == "SELL" and self._mask_short is not None and not self._mask_short[i]:
            return Signal()
        return s

def cfg(risk, sym):
    c = ForexConfig(); c.total_capital_usd = START; c.risk_per_trade_pct = risk
    c.partial_tp_atr = 999.0; c.partial_tp_frac = 0.0; c.move_sl_to_breakeven = False; c.max_hold_bars = 64
    return c

def run(h1df, mlong=None, mshort=None):
    d = prepare_data(h1df[["timestamp", "open", "high", "low", "close"]].copy())
    s = Filtered(); s.ADX_MIN = 10; s.TIMEFRAME_SECONDS = 3600; s.TOUCH_TOLERANCE = 0.012
    s.sl_atr = 3.0; s.tp_atr = 999.0; s.trail_atr_mult = 999.0; s.trail_activation_atr = 999.0
    s.precompute(d); s._mask_long = mlong; s._mask_short = mshort
    eng = BacktestEngine(d, cfg(0.30, "XAUUSD"), s, spread_price=0.24, commission_per_lot=3.5, symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START), eng.trades

def line(m, tr, yrs):
    p = perf(to_monthly(tr)); sh = p["sharpe"] if p else float("nan")
    t = m["total_return_pct"]; cg = -100.0 if t <= -100 else ((1 + t / 100) ** (1 / yrs) - 1) * 100
    return "n=%d win%%=%.1f PF=%.2f Sharpe=%.2f CAGR=%+.2f%% DD=%.1f%%" % (
        m["trades"], m["win_rate"] * 100, m["profit_factor"], sh, cg, m["max_dd_pct"])

def load_gold():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START
    dfg, _ = loader.load("XAUUSD", 99.0, c0,
                         csv_path="download/xauusd-m15-bid-2013-01-01-2026-06-10.csv",
                         allow_synthetic=True)
    return resample(dfg, "1h").reset_index(drop=True)

def fill_hour(h1):
    """hour of the NEXT H1 bar (the fill bar) for a signal at row i."""
    ts = pd.to_datetime(h1["timestamp"]).reset_index(drop=True)
    nxt = ts.shift(-1)
    nxt.iloc[-1] = ts.iloc[-1] + pd.Timedelta(hours=1)
    return nxt.dt.hour.to_numpy(), ts

def block_allow(eh, lo, hi):
    """allow-mask: block fill hours in wrapped interval [lo, hi)."""
    if lo < hi:
        blk = (eh >= lo) & (eh < hi)
    else:
        blk = (eh >= lo) | (eh < hi)
    return ~blk

VARIANTS = {
    "BLK20_01": (20, 1), "BLK19_01": (19, 1), "BLK21_02": (21, 2),
    "BLK20_02": (20, 2), "BLK21_03": (21, 3),
    # extra adjacent variants the finder did NOT feature, to probe the plateau edges
    "BLK19_00": (19, 0), "BLK20_00": (20, 0), "BLK22_01": (22, 1),
}

def main():
    h1 = load_gold()
    eh, ts = fill_hour(h1)
    yrs = (ts.iloc[-1] - ts.iloc[0]).days / 365.25
    print("window: %s .. %s  (%.2f yrs, %d bars)" % (ts.iloc[0], ts.iloc[-1], yrs, len(h1)))

    print("\n== 1. FULL WINDOW ==")
    mb, trb = run(h1)
    print("BASELINE   %s" % line(mb, trb, yrs))
    for name, (lo, hi) in VARIANTS.items():
        allow = block_allow(eh, lo, hi)
        m, tr = run(h1, allow, allow)
        print("%-10s %s" % (name, line(m, tr, yrs)))

    print("\n== 2. SHIFT TEST (mask delayed 1 bar: bar i gated by allow[i-1]) ==")
    allow = block_allow(eh, 20, 1)
    sh = np.empty_like(allow); sh[0] = True; sh[1:] = allow[:-1]
    m, tr = run(h1, sh, sh)
    print("BLK20_01_SHIFT1 %s" % line(m, tr, yrs))

    print("\n== 3. OOS HALVES ==")
    mid = ts.iloc[0] + (ts.iloc[-1] - ts.iloc[0]) / 2
    print("mid = %s" % mid)
    for tag, sel in (("H1", (ts < mid).to_numpy()), ("H2", (ts >= mid).to_numpy())):
        hh = h1[sel].reset_index(drop=True)
        ehh, tsh = fill_hour(hh)
        yh = (tsh.iloc[-1] - tsh.iloc[0]).days / 365.25
        mb2, trb2 = run(hh)
        print("%s BASE     %s..%s %s" % (tag, str(tsh.iloc[0])[:10], str(tsh.iloc[-1])[:10], line(mb2, trb2, yh)))
        for name in ("BLK20_01", "BLK19_01", "BLK21_02"):
            lo, hi = VARIANTS[name]
            allow = block_allow(ehh, lo, hi)
            m, tr = run(hh, allow, allow)
            print("%s %-8s %s" % (tag, name, line(m, tr, yh)))

    print("\n== 4. THIRDS (extra split the finder did not tune on) ==")
    span = (ts.iloc[-1] - ts.iloc[0]) / 3
    edges = [ts.iloc[0] + span * k for k in range(4)]
    for q in range(3):
        sel = ((ts >= edges[q]) & (ts < edges[q + 1])).to_numpy() if q < 2 else (ts >= edges[q]).to_numpy()
        hh = h1[sel].reset_index(drop=True)
        ehh, tsh = fill_hour(hh)
        yh = (tsh.iloc[-1] - tsh.iloc[0]).days / 365.25
        mb3, trb3 = run(hh)
        print("T%d BASE     %s..%s %s" % (q + 1, str(tsh.iloc[0])[:10], str(tsh.iloc[-1])[:10], line(mb3, trb3, yh)))
        allow = block_allow(ehh, 20, 1)
        m, tr = run(hh, allow, allow)
        print("T%d BLK20_01 %s" % (q + 1, line(m, tr, yh)))

if __name__ == "__main__":
    main()
