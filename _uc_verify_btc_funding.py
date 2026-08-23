import sys, os
sys.path.insert(0, os.getcwd())
import numpy as np, pandas as pd
from forex_config import ForexConfig
from backtest_forex import prepare_data, BacktestEngine, FastHybridTrendPullback, compute_metrics
from forex_indicators import Signal
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

def cfg(risk, sym, ps=None, pv=None):
    c = ForexConfig(); c.total_capital_usd = START; c.risk_per_trade_pct = risk
    c.partial_tp_atr = 999.0; c.partial_tp_frac = 0.0; c.move_sl_to_breakeven = False; c.max_hold_bars = 64
    if ps is not None: c.pip_size[sym] = ps; c.pip_value_usd_approx[sym] = pv
    return c

def run(h1df, sym, spread, comm, risk, mlong=None, mshort=None, ps=None, pv=None):
    d = prepare_data(h1df[["timestamp","open","high","low","close"]].copy())
    s = Filtered(); s.ADX_MIN = 10; s.TIMEFRAME_SECONDS = 3600; s.TOUCH_TOLERANCE = 0.012
    s.sl_atr = 3.0; s.tp_atr = 999.0; s.trail_atr_mult = 999.0; s.trail_activation_atr = 999.0
    s.precompute(d); s._mask_long = mlong; s._mask_short = mshort
    eng = BacktestEngine(d, cfg(risk, sym, ps, pv), s, spread_price=spread, commission_per_lot=comm, symbol=sym)
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START), eng.trades

def stats(m, tr, yrs):
    p = perf(to_monthly(tr)); sh = p["sharpe"] if p else float("nan")
    t = m["total_return_pct"]; cg = -100.0 if t <= -100 else ((1 + t/100.0)**(1.0/yrs) - 1) * 100
    ln = "n=%d win%%=%.1f PF=%.2f Sharpe=%.2f CAGR=%+.2f%% DD=%.1f%%" % (
        m["trades"], m["win_rate"]*100, m["profit_factor"], sh, cg, m["max_dd_pct"])
    return {"n": m["trades"], "pf": m["profit_factor"], "sh": sh, "cg": cg, "dd": m["max_dd_pct"], "line": ln}

# ---------------- data (independent re-implementation) ----------------
m15 = pd.read_csv("download/btcusdt-15m-vol.csv")
m15["timestamp"] = pd.to_datetime(m15["timestamp"], format="mixed")
h1 = (m15.set_index("timestamp").resample("1h")
      .agg(open=("open","first"), high=("high","max"), low=("low","min"), close=("close","last"))
      .dropna(subset=["open"]).reset_index())
h1 = h1[h1["timestamp"] >= "2019-09-11"].reset_index(drop=True)

fund = pd.read_csv("download/btc_funding.csv")
fund["timestamp"] = pd.to_datetime(fund["timestamp"], format="mixed")
fund = fund.sort_values("timestamp").reset_index(drop=True)
fa = fund["funding_rate"].abs()
W = 90
for q in (0.50, 0.60, 0.65, 0.70, 0.75, 0.80, 0.90):
    fund["a%02d" % int(q*100)] = fa.rolling(W).quantile(q)
# window-size neighbors at p70
fund["a70_w60"]  = fa.rolling(60).quantile(0.70)
fund["a70_w120"] = fa.rolling(120).quantile(0.70)

h1 = pd.merge_asof(h1.sort_values("timestamp"), fund.sort_values("timestamp"),
                   on="timestamp", direction="backward").reset_index(drop=True)

fr = h1["funding_rate"].to_numpy(float)
afr = np.abs(fr)

def block_mask(thr_col):
    thr = h1[thr_col].to_numpy(float)
    with np.errstate(invalid="ignore"):
        blocked = afr > thr
    blocked = np.where(np.isnan(afr) | np.isnan(thr), False, blocked)  # NaN warmup -> allow
    return ~blocked  # True = allow

SYM, SPREAD, COMM, RISK, PS, PV = "BTCUSDc", 10.0, 0.0, 1.00, 1.0, 0.01
yrs = (h1["timestamp"].iloc[-1] - h1["timestamp"].iloc[0]).days / 365.25
print("WINDOW %s -> %s  (%.2f yrs, %d H1 bars)" % (h1["timestamp"].iloc[0], h1["timestamp"].iloc[-1], yrs, len(h1)))

m, tr = run(h1, SYM, SPREAD, COMM, RISK, None, None, PS, PV)
base = stats(m, tr, yrs)
print("%-18s %s" % ("BASELINE", base["line"]))

masks = {}
for name in ("a50","a60","a65","a70","a75","a80","a90","a70_w60","a70_w120"):
    masks[name] = block_mask(name)

res = {}
for name, mk in masks.items():
    m, tr = run(h1, SYM, SPREAD, COMM, RISK, mk, mk, PS, PV)
    res[name] = stats(m, tr, yrs)
    print("%-18s %s" % (name, res[name]["line"]))

# ---------------- shift test: bar i gated by mask value at i-1 ----------------
mk = masks["a70"]
mk_shift = np.empty_like(mk); mk_shift[0] = True; mk_shift[1:] = mk[:-1]
m, tr = run(h1, SYM, SPREAD, COMM, RISK, mk_shift, mk_shift, PS, PV)
sh1 = stats(m, tr, yrs)
print("%-18s %s" % ("a70 SHIFT+1bar", sh1["line"]))
# harsher shift: delay by 8 bars (one full funding period)
mk_s8 = np.empty_like(mk); mk_s8[:8] = True; mk_s8[8:] = mk[:-8]
m, tr = run(h1, SYM, SPREAD, COMM, RISK, mk_s8, mk_s8, PS, PV)
sh8 = stats(m, tr, yrs)
print("%-18s %s" % ("a70 SHIFT+8bar", sh8["line"]))

# ---------------- OOS half-split ----------------
n2 = len(h1) // 2
print("\nsplit at", h1["timestamp"].iloc[n2])
for hname, sl in (("H1_first", slice(0, n2)), ("H2_second", slice(n2, len(h1)))):
    hdf = h1.iloc[sl].reset_index(drop=True)
    yrs_h = (hdf["timestamp"].iloc[-1] - hdf["timestamp"].iloc[0]).days / 365.25
    m, tr = run(hdf, SYM, SPREAD, COMM, RISK, None, None, PS, PV)
    bh = stats(m, tr, yrs_h)
    print("%-24s %s" % ("BASE " + hname, bh["line"]))
    for name in ("a60","a70","a80"):
        m, tr = run(hdf, SYM, SPREAD, COMM, RISK, masks[name][sl], masks[name][sl], PS, PV)
        st = stats(m, tr, yrs_h)
        flag = "BEATS" if st["sh"] > bh["sh"] else "loses"
        print("%-24s %s  [%s half-base Sh %.2f]" % (name + " " + hname, st["line"], flag, bh["sh"]))

# blocked fraction sanity
for name in ("a60","a70","a80"):
    print("blocked frac %s: %.3f" % (name, 1 - masks[name].mean()))
print("DONE")
