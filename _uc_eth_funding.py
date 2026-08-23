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

# ---------------- data ----------------
m15 = pd.read_csv("download/ethusdt-15m-vol.csv")
m15["timestamp"] = pd.to_datetime(m15["timestamp"], format="mixed")
h1 = (m15.set_index("timestamp").resample("1h")
      .agg(open=("open","first"),high=("high","max"),low=("low","min"),close=("close","last"),
           volume=("volume","sum"),n_trades=("n_trades","sum"),taker_buy_vol=("taker_buy_vol","sum"))
      .dropna(subset=["open"]).reset_index())

# restrict to funding availability
h1 = h1[h1["timestamp"] >= pd.Timestamp("2019-11-28")].reset_index(drop=True)

fund = pd.read_csv("download/eth_funding.csv", parse_dates=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

# rolling stats on the funding EVENT series (window ends at that event -> causal after backward merge)
W = 90  # ~30 days of 8h events
fund["p80"] = fund["funding_rate"].rolling(W, min_periods=30).quantile(0.80)
fund["p20"] = fund["funding_rate"].rolling(W, min_periods=30).quantile(0.20)
fund["p90"] = fund["funding_rate"].rolling(W, min_periods=30).quantile(0.90)
fund["p10"] = fund["funding_rate"].rolling(W, min_periods=30).quantile(0.10)
fund["p75"] = fund["funding_rate"].rolling(W, min_periods=30).quantile(0.75)
fund["p25"] = fund["funding_rate"].rolling(W, min_periods=30).quantile(0.25)
fund["p85"] = fund["funding_rate"].rolling(W, min_periods=30).quantile(0.85)
fund["p15"] = fund["funding_rate"].rolling(W, min_periods=30).quantile(0.15)
fund["p95"] = fund["funding_rate"].rolling(W, min_periods=30).quantile(0.95)
fund["p05"] = fund["funding_rate"].rolling(W, min_periods=30).quantile(0.05)
fund["absf"] = fund["funding_rate"].abs()
fund["abs_p90"] = fund["absf"].rolling(W, min_periods=30).quantile(0.90)
fund["abs_p80"] = fund["absf"].rolling(W, min_periods=30).quantile(0.80)
fund["abs_p95"] = fund["absf"].rolling(W, min_periods=30).quantile(0.95)
fund["trend3"] = fund["funding_rate"] - fund["funding_rate"].shift(3)   # change over last 3 events (24h)

h1s = h1.sort_values("timestamp")
mg = pd.merge_asof(h1s, fund, on="timestamp", direction="backward")
assert len(mg) == len(h1) and (mg["timestamp"].values == h1["timestamp"].values).all()

f    = mg["funding_rate"].values
p80  = mg["p80"].values; p20 = mg["p20"].values
p90  = mg["p90"].values; p10 = mg["p10"].values
p75  = mg["p75"].values; p25 = mg["p25"].values
p85  = mg["p85"].values; p15 = mg["p15"].values
p95  = mg["p95"].values; p05 = mg["p05"].values
absf = mg["absf"].values
ap90 = mg["abs_p90"].values; ap80 = mg["abs_p80"].values; ap95 = mg["abs_p95"].values
tr3  = mg["trend3"].values

valid = ~np.isnan(f) & ~np.isnan(p80)   # rolling stats warmed up

def mk(cond):
    # allow when cond True; bars without valid funding stats -> allow (no opinion)
    return np.where(valid, cond, True)

yrs = (h1["timestamp"].iloc[-1] - h1["timestamp"].iloc[0]).days / 365.25
print("WINDOW: %s -> %s  (%.2f yrs, %d H1 bars)" % (h1["timestamp"].iloc[0], h1["timestamp"].iloc[-1], yrs, len(h1)))

SYM="ETHUSDc"; SP=1.0; CM=0.0; RISK=1.00; PS=1.0; PV=0.01

results = {}
def report(name, mlong, mshort):
    m, tr = run(h1, SYM, SP, CM, RISK, mlong, mshort, ps=PS, pv=PV)
    results[name] = (m, tr)
    print("%-34s %s" % (name, line(m, tr, yrs)))
    return m, tr

# 1. baseline on this exact window
report("BASELINE (unfiltered)", None, None)

# 2. battery
# crowd-fade: high funding = crowded longs -> block LONG; low funding = crowded shorts -> block SHORT
report("fade p80/p20",  mk(f <= p80), mk(f >= p20))
report("fade p90/p10",  mk(f <= p90), mk(f >= p10))
# inverse = funding-momentum: only trade WITH the crowd
report("momo p80/p20 (inverse)", mk(f > p80), mk(f < p20))
report("momo p90/p10 (inverse)", mk(f > p90), mk(f < p10))
# funding trend over last 3 events
vt = valid & ~np.isnan(tr3)
report("trend3 agree",   np.where(vt, tr3 > 0, True), np.where(vt, tr3 < 0, True))
report("trend3 inverse", np.where(vt, tr3 < 0, True), np.where(vt, tr3 > 0, True))
# |funding| extreme blocks both directions
report("absF<p90 both", mk(absf <= ap90), mk(absf <= ap90))

print("\n--- neighbors / OOS for any winner are run separately below ---")

base_m, base_tr = results["BASELINE (unfiltered)"]
bp = perf(to_monthly(base_tr)); base_sh = bp["sharpe"] if bp else float("nan")
bt = base_m["total_return_pct"]; base_cg = -100.0 if bt <= -100 else ((1+bt/100)**(1/yrs)-1)*100

def stats(m, tr):
    p = perf(to_monthly(tr)); sh = p["sharpe"] if p else float("nan")
    t = m["total_return_pct"]; cg = -100.0 if t <= -100 else ((1+t/100)**(1/yrs)-1)*100
    return sh, cg, m["max_dd_pct"]

winners = []
for name,(m,tr) in results.items():
    if name.startswith("BASELINE"): continue
    sh, cg, dd = stats(m, tr)
    ok = (sh >= base_sh + 0.10 and cg >= base_cg) or (sh >= base_sh and dd <= base_m["max_dd_pct"]*0.75 and cg >= 0.9*base_cg)
    if ok: winners.append(name)
print("IMPROVED candidates vs baseline (Sharpe %.2f CAGR %+.2f%% DD %.1f%%): %s" % (base_sh, base_cg, base_m["max_dd_pct"], winners if winners else "NONE"))

# neighbors for percentile-gate winners
if any(w.startswith("fade p80") for w in winners):
    report("nbr fade p75/p25", mk(f <= p75), mk(f >= p25))
    report("nbr fade p85/p15", mk(f <= p85), mk(f >= p15))
if any(w.startswith("fade p90") for w in winners):
    report("nbr fade p85/p15", mk(f <= p85), mk(f >= p15))
    report("nbr fade p95/p05", mk(f <= p95), mk(f >= p05))
if any(w.startswith("momo p80") for w in winners):
    report("nbr momo p75/p25", mk(f > p75), mk(f < p25))
    report("nbr momo p85/p15", mk(f > p85), mk(f < p15))
if any(w.startswith("momo p90") for w in winners):
    report("nbr momo p85/p15", mk(f > p85), mk(f < p15))
    report("nbr momo p95/p05", mk(f > p95), mk(f < p05))
if any(w.startswith("absF") for w in winners):
    report("nbr absF<p80 both", mk(absf <= ap80), mk(absf <= ap80))
    report("nbr absF<p95 both", mk(absf <= ap95), mk(absf <= ap95))

# OOS half-split for winners
if winners:
    mid = len(h1)//2
    h1a = h1.iloc[:mid].reset_index(drop=True)
    h1b = h1.iloc[mid:].reset_index(drop=True)
    ya = (h1a["timestamp"].iloc[-1]-h1a["timestamp"].iloc[0]).days/365.25
    yb = (h1b["timestamp"].iloc[-1]-h1b["timestamp"].iloc[0]).days/365.25
    print("\nHALF-SPLIT: A=%s..%s  B=%s..%s" % (h1a["timestamp"].iloc[0].date(), h1a["timestamp"].iloc[-1].date(), h1b["timestamp"].iloc[0].date(), h1b["timestamp"].iloc[-1].date()))
    ma_b, ta_b = run(h1a, SYM, SP, CM, RISK, None, None, ps=PS, pv=PV)
    mb_b, tb_b = run(h1b, SYM, SP, CM, RISK, None, None, ps=PS, pv=PV)
    print("%-34s %s" % ("half A BASELINE", line(ma_b, ta_b, ya)))
    print("%-34s %s" % ("half B BASELINE", line(mb_b, tb_b, yb)))

    def masks_for(name):
        if name == "fade p80/p20":  return mk(f <= p80), mk(f >= p20)
        if name == "fade p90/p10":  return mk(f <= p90), mk(f >= p10)
        if name == "momo p80/p20 (inverse)": return mk(f > p80), mk(f < p20)
        if name == "momo p90/p10 (inverse)": return mk(f > p90), mk(f < p10)
        if name == "trend3 agree":   return np.where(vt, tr3 > 0, True), np.where(vt, tr3 < 0, True)
        if name == "trend3 inverse": return np.where(vt, tr3 < 0, True), np.where(vt, tr3 > 0, True)
        if name == "absF<p90 both":  return mk(absf <= ap90), mk(absf <= ap90)
        raise KeyError(name)

    for w in winners:
        ml, ms = masks_for(w)
        ma, ta = run(h1a, SYM, SP, CM, RISK, ml[:mid], ms[:mid], ps=PS, pv=PV)
        mb, tb = run(h1b, SYM, SP, CM, RISK, ml[mid:], ms[mid:], ps=PS, pv=PV)
        print("%-34s %s" % ("half A "+w, line(ma, ta, ya)))
        print("%-34s %s" % ("half B "+w, line(mb, tb, yb)))
