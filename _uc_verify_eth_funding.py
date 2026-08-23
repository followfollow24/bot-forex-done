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

def cfg(risk, sym, ps=None, pv=None):
    c=ForexConfig(); c.total_capital_usd=START; c.risk_per_trade_pct=risk
    c.partial_tp_atr=999.0; c.partial_tp_frac=0.0; c.move_sl_to_breakeven=False; c.max_hold_bars=64
    if ps is not None: c.pip_size[sym]=ps; c.pip_value_usd_approx[sym]=pv
    return c

def run(h1df, sym, spread, comm, risk, mlong=None, mshort=None, ps=None, pv=None):
    d = prepare_data(h1df[["timestamp","open","high","low","close"]].copy())
    s = Filtered(); s.ADX_MIN=10; s.TIMEFRAME_SECONDS=3600; s.TOUCH_TOLERANCE=0.012
    s.sl_atr=3.0; s.tp_atr=999.0; s.trail_atr_mult=999.0; s.trail_activation_atr=999.0
    s.precompute(d); s._mask_long=mlong; s._mask_short=mshort
    eng = BacktestEngine(d, cfg(risk,sym,ps,pv), s, spread_price=spread, commission_per_lot=comm, symbol=sym)
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START), eng.trades

def line(m, tr, yrs):
    p = perf(to_monthly(tr)); sh = p["sharpe"] if p else float("nan")
    t = m["total_return_pct"]; cg = -100.0 if t <= -100 else ((1+t/100)**(1/yrs)-1)*100
    return "n=%d win%%=%.1f PF=%.2f Sharpe=%.2f CAGR=%+.2f%% DD=%.1f%%" % (m["trades"], m["win_rate"]*100, m["profit_factor"], sh, cg, m["max_dd_pct"])

# ---------------- data (independent re-implementation) ----------------
m15 = pd.read_csv("download/ethusdt-15m-vol.csv")
m15["timestamp"] = pd.to_datetime(m15["timestamp"], format="mixed")
h1 = (m15.set_index("timestamp").resample("1h")
      .agg(open=("open","first"),high=("high","max"),low=("low","min"),close=("close","last"),
           volume=("volume","sum"),n_trades=("n_trades","sum"),taker_buy_vol=("taker_buy_vol","sum"))
      .dropna(subset=["open"]).reset_index())
h1 = h1[h1["timestamp"] >= pd.Timestamp("2019-11-28")].reset_index(drop=True)

fund = pd.read_csv("download/eth_funding.csv", parse_dates=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
W = 90
for q in (0.80,0.20,0.75,0.25,0.85,0.15,0.90,0.10):
    fund["q%02d" % int(q*100)] = fund["funding_rate"].rolling(W, min_periods=30).quantile(q)

mg = pd.merge_asof(h1.sort_values("timestamp"), fund, on="timestamp", direction="backward")
assert len(mg) == len(h1) and (mg["timestamp"].values == h1["timestamp"].values).all()

f   = mg["funding_rate"].values
Q   = {k: mg["q%02d"%k].values for k in (80,20,75,25,85,15,90,10)}
valid = ~np.isnan(f) & ~np.isnan(Q[80])

def mk(cond, v=None):
    v = valid if v is None else v
    return np.where(v, cond, True)

yrs = (h1["timestamp"].iloc[-1] - h1["timestamp"].iloc[0]).days / 365.25
print("WINDOW: %s -> %s  (%.2f yrs, %d H1 bars)" % (h1["timestamp"].iloc[0], h1["timestamp"].iloc[-1], yrs, len(h1)))

SYM="ETHUSDc"; SP=1.0; CM=0.0; RISK=1.00; PS=1.0; PV=0.01

def report(name, mlong, mshort, df=None, yy=None):
    df = h1 if df is None else df; yy = yrs if yy is None else yy
    m, tr = run(df, SYM, SP, CM, RISK, mlong, mshort, ps=PS, pv=PV)
    print("%-32s %s" % (name, line(m, tr, yy)))
    return m, tr

# 1. REPRODUCE
mb, tb = report("BASELINE", None, None)
mc, tc = report("fade p80/p20", mk(f <= Q[80]), mk(f >= Q[20]))

# inverse check
report("momo p80/p20 (inverse)", mk(f > Q[80]), mk(f < Q[20]))

# 3. NEIGHBORS
report("nbr fade p75/p25", mk(f <= Q[75]), mk(f >= Q[25]))
report("nbr fade p85/p15", mk(f <= Q[85]), mk(f >= Q[15]))
report("nbr fade p90/p10", mk(f <= Q[90]), mk(f >= Q[10]))
# also different rolling window (robustness to W, not claimed but informative)
fund2 = pd.read_csv("download/eth_funding.csv", parse_dates=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
for W2, tag in ((60,"W60"),(120,"W120")):
    f2 = fund2.copy()
    f2["a"] = f2["funding_rate"].rolling(W2, min_periods=30).quantile(0.80)
    f2["b"] = f2["funding_rate"].rolling(W2, min_periods=30).quantile(0.20)
    mg2 = pd.merge_asof(h1.sort_values("timestamp"), f2, on="timestamp", direction="backward")
    ff = mg2["funding_rate"].values; aa = mg2["a"].values; bb = mg2["b"].values
    vv = ~np.isnan(ff) & ~np.isnan(aa)
    report("nbr fade p80/p20 %s" % tag, np.where(vv, ff <= aa, True), np.where(vv, ff >= bb, True))

# 4. SHIFT TEST: gate bar i by the mask value at i-1
ml = mk(f <= Q[80]); ms = mk(f >= Q[20])
ml_sh = np.roll(ml, 1); ml_sh[0] = True
ms_sh = np.roll(ms, 1); ms_sh[0] = True
report("SHIFT+1 fade p80/p20", ml_sh, ms_sh)
# and shift by 2 for good measure
ml_s2 = np.roll(ml, 2); ml_s2[:2] = True
ms_s2 = np.roll(ms, 2); ms_s2[:2] = True
report("SHIFT+2 fade p80/p20", ml_s2, ms_s2)

# 2. OOS half-split
mid = len(h1)//2
h1a = h1.iloc[:mid].reset_index(drop=True)
h1b = h1.iloc[mid:].reset_index(drop=True)
ya = (h1a["timestamp"].iloc[-1]-h1a["timestamp"].iloc[0]).days/365.25
yb = (h1b["timestamp"].iloc[-1]-h1b["timestamp"].iloc[0]).days/365.25
print("\nHALF A: %s..%s | HALF B: %s..%s" % (h1a["timestamp"].iloc[0].date(), h1a["timestamp"].iloc[-1].date(), h1b["timestamp"].iloc[0].date(), h1b["timestamp"].iloc[-1].date()))
report("half A BASELINE", None, None, h1a, ya)
report("half A fade p80/p20", ml[:mid], ms[:mid], h1a, ya)
report("half B BASELINE", None, None, h1b, yb)
report("half B fade p80/p20", ml[mid:], ms[mid:], h1b, yb)

# extra OOS granularity: thirds (unclaimed, adversarial)
t1 = len(h1)//3; t2 = 2*len(h1)//3
for lo, hi, tag in ((0,t1,"T1"),(t1,t2,"T2"),(t2,len(h1),"T3")):
    hh = h1.iloc[lo:hi].reset_index(drop=True)
    yy = max((hh["timestamp"].iloc[-1]-hh["timestamp"].iloc[0]).days/365.25, 0.01)
    report("%s BASELINE" % tag, None, None, hh, yy)
    report("%s fade p80/p20" % tag, ml[lo:hi], ms[lo:hi], hh, yy)

# blocked-bar stats
print("\nmask stats: long blocked %.1f%% of valid bars, short blocked %.1f%%, warmup(all-allow) bars=%d" % (
    100*np.mean(~ml[valid]), 100*np.mean(~ms[valid]), int((~valid).sum())))
