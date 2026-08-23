import sys, os
sys.path.insert(0, os.getcwd())
import numpy as np, pandas as pd
from forex_config import ForexConfig
from backtest_forex import prepare_data, BacktestEngine, FastHybridTrendPullback, compute_metrics
from forex_indicators import Signal
from _all_paths import to_monthly, perf, START

class Filtered(FastHybridTrendPullback):
    _mask_long = None    # numpy bool arrays aligned to the H1 dataframe rows; True = allow
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

# ---------------- data ----------------
m15 = pd.read_csv("download/btcusdt-15m-vol.csv")
m15["timestamp"] = pd.to_datetime(m15["timestamp"], format="mixed")
h1 = (m15.set_index("timestamp").resample("1h")
      .agg(open=("open","first"), high=("high","max"), low=("low","min"), close=("close","last"),
           volume=("volume","sum"), n_trades=("n_trades","sum"), taker_buy_vol=("taker_buy_vol","sum"))
      .dropna(subset=["open"]).reset_index())
h1 = h1[h1["timestamp"] >= "2019-09-11"].reset_index(drop=True)

fund = pd.read_csv("download/btc_funding.csv")
fund["timestamp"] = pd.to_datetime(fund["timestamp"], format="mixed")
fund = fund.sort_values("timestamp").reset_index(drop=True)
f = fund["funding_rate"]
W = 90  # rolling window in funding EVENTS (~30 days)
fund["q10"] = f.rolling(W).quantile(0.10); fund["q90"] = f.rolling(W).quantile(0.90)
fund["q20"] = f.rolling(W).quantile(0.20); fund["q80"] = f.rolling(W).quantile(0.80)
fund["q30"] = f.rolling(W).quantile(0.30); fund["q70"] = f.rolling(W).quantile(0.70)
fund["a60"] = f.abs().rolling(W).quantile(0.60)
fund["a70"] = f.abs().rolling(W).quantile(0.70)
fund["a80"] = f.abs().rolling(W).quantile(0.80)
fund["a90"] = f.abs().rolling(W).quantile(0.90)
for k in (1, 2, 3):   # funding trend over last k+1 events (d2 = "last 3 events")
    dk = f.diff(k)
    fund["rise%d" % k] = dk > 0
    fund["fall%d" % k] = dk < 0

h1 = pd.merge_asof(h1.sort_values("timestamp"), fund.sort_values("timestamp"),
                   on="timestamp", direction="backward").reset_index(drop=True)

fr  = h1["funding_rate"].to_numpy(float)
q10 = h1["q10"].to_numpy(float); q90 = h1["q90"].to_numpy(float)
q20 = h1["q20"].to_numpy(float); q80 = h1["q80"].to_numpy(float)
q30 = h1["q30"].to_numpy(float); q70 = h1["q70"].to_numpy(float)
a60 = h1["a60"].to_numpy(float); a70 = h1["a70"].to_numpy(float)
a80 = h1["a80"].to_numpy(float); a90 = h1["a90"].to_numpy(float)
rises = {k: h1["rise%d" % k].fillna(False).to_numpy(bool) for k in (1, 2, 3)}
falls = {k: h1["fall%d" % k].fillna(False).to_numpy(bool) for k in (1, 2, 3)}
rise, fall = rises[2], falls[2]

def gt(a, b):   # a > b, NaN -> False (condition not triggered -> allow)
    with np.errstate(invalid="ignore"):
        r = a > b
    return np.where(np.isnan(a) | np.isnan(b), False, r)
def lt(a, b):
    with np.errstate(invalid="ignore"):
        r = a < b
    return np.where(np.isnan(a) | np.isnan(b), False, r)

nanf = np.isnan(fr)
variants = {}
# (a) crowd fade: block longs when funding above rolling p80, block shorts when below p20
variants["fade_p80/p20"]  = (~gt(fr, q80), ~lt(fr, q20))
# (b) p90/p10
variants["fade_p90/p10"]  = (~gt(fr, q90), ~lt(fr, q10))
# neighbor p70/p30
variants["fade_p70/p30"]  = (~gt(fr, q70), ~lt(fr, q30))
# inverse of (a): ALLOW longs only when funding above p80, shorts only below p20
variants["inv_fade_p80"]  = (gt(fr, q80) | np.isnan(q80), lt(fr, q20) | np.isnan(q20))
# (c) funding-momentum: require funding sign to agree with direction
variants["mom_sign"]      = ((fr >= 0) | nanf, (fr <= 0) | nanf)
# inverse of sign-agreement
variants["mom_sign_inv"]  = ((fr < 0) | nanf, (fr > 0) | nanf)
# (d) funding trend momentum: rising blocks shorts, falling blocks longs
variants["ftrend_mom"]    = (~fall, ~rise)
# inverse: fade the funding trend (d2 = change over last 3 events) + diff-window neighbors
variants["ftrend_fade"]   = (~rise, ~fall)
variants["ftrend_fade_d1"] = (~rises[1], ~falls[1])
variants["ftrend_fade_d3"] = (~rises[3], ~falls[3])
# (e) |funding| extreme blocks BOTH directions + threshold neighbors
variants["absext_p90"]    = (~gt(np.abs(fr), a90), ~gt(np.abs(fr), a90))
variants["absext_p80"]    = (~gt(np.abs(fr), a80), ~gt(np.abs(fr), a80))
variants["absext_p70"]    = (~gt(np.abs(fr), a70), ~gt(np.abs(fr), a70))
variants["absext_p60"]    = (~gt(np.abs(fr), a60), ~gt(np.abs(fr), a60))

SYM, SPREAD, COMM, RISK, PS, PV = "BTCUSDc", 10.0, 0.0, 1.00, 1.0, 0.01
yrs_full = (h1["timestamp"].iloc[-1] - h1["timestamp"].iloc[0]).days / 365.25
print("WINDOW %s -> %s  (%.2f yrs, %d H1 bars)" % (h1["timestamp"].iloc[0], h1["timestamp"].iloc[-1], yrs_full, len(h1)))

m, tr = run(h1, SYM, SPREAD, COMM, RISK, None, None, PS, PV)
base = stats(m, tr, yrs_full)
print("%-16s %s" % ("BASELINE", base["line"]))

results = {}
for name, (ml, ms) in variants.items():
    m, tr = run(h1, SYM, SPREAD, COMM, RISK, ml, ms, PS, PV)
    st = stats(m, tr, yrs_full)
    results[name] = st
    print("%-16s %s" % (name, st["line"]))

def improved(st, b):
    c1 = (st["sh"] >= b["sh"] + 0.10) and (st["cg"] >= b["cg"])
    c2 = (st["sh"] >= b["sh"]) and (st["dd"] <= 0.75 * b["dd"]) and \
         (st["cg"] >= 0.9 * b["cg"] if b["cg"] > 0 else st["cg"] >= b["cg"])
    return c1 or c2

winners = [k for k, v in results.items() if improved(v, base)]
print("\nIMPROVED candidates vs baseline:", winners if winners else "NONE")

# OOS half-split for winners, each half vs its OWN unfiltered baseline
if winners:
    n2 = len(h1) // 2
    halves = [("H1_first", slice(0, n2)), ("H2_second", slice(n2, len(h1)))]
    half_base = {}
    for hname, sl in halves:
        hdf = h1.iloc[sl].reset_index(drop=True)
        yrs_h = (hdf["timestamp"].iloc[-1] - hdf["timestamp"].iloc[0]).days / 365.25
        m, tr = run(hdf, SYM, SPREAD, COMM, RISK, None, None, PS, PV)
        half_base[hname] = (stats(m, tr, yrs_h), yrs_h, hdf, sl)
        print("%-24s %s" % ("BASE " + hname, half_base[hname][0]["line"]))
    for name in winners:
        ml, ms = variants[name]
        for hname, sl in halves:
            bst, yrs_h, hdf, _ = half_base[hname]
            m, tr = run(hdf, SYM, SPREAD, COMM, RISK, ml[sl], ms[sl], PS, PV)
            st = stats(m, tr, yrs_h)
            print("%-24s %s   [half-base Sharpe %.2f CAGR %+.2f%%]" % (name + " " + hname, st["line"], bst["sh"], bst["cg"]))
print("DONE")
