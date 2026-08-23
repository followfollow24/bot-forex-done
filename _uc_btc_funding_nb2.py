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
    return "n=%d win%%=%.1f PF=%.2f Sharpe=%.2f CAGR=%+.2f%% DD=%.1f%%" % (
        m["trades"], m["win_rate"]*100, m["profit_factor"], sh, cg, m["max_dd_pct"])

m15 = pd.read_csv("download/btcusdt-15m-vol.csv")
m15["timestamp"] = pd.to_datetime(m15["timestamp"], format="mixed")
h1 = (m15.set_index("timestamp").resample("1h")
      .agg(open=("open","first"), high=("high","max"), low=("low","min"), close=("close","last"))
      .dropna(subset=["open"]).reset_index())
h1 = h1[h1["timestamp"] >= "2019-09-11"].reset_index(drop=True)

fund = pd.read_csv("download/btc_funding.csv")
fund["timestamp"] = pd.to_datetime(fund["timestamp"], format="mixed")
fund = fund.sort_values("timestamp").reset_index(drop=True)
f = fund["funding_rate"]
W = 90
fund["a60"] = f.abs().rolling(W).quantile(0.60)
fund["a70"] = f.abs().rolling(W).quantile(0.70)

h1 = pd.merge_asof(h1.sort_values("timestamp"), fund.sort_values("timestamp"),
                   on="timestamp", direction="backward").reset_index(drop=True)
fr  = h1["funding_rate"].to_numpy(float)
a60 = h1["a60"].to_numpy(float)
a70 = h1["a70"].to_numpy(float)

def gt(a, b):
    with np.errstate(invalid="ignore"):
        r = a > b
    return np.where(np.isnan(a) | np.isnan(b), False, r)

m60 = ~gt(np.abs(fr), a60)
m70 = ~gt(np.abs(fr), a70)

SYM, SPREAD, COMM, RISK, PS, PV = "BTCUSDc", 10.0, 0.0, 1.00, 1.0, 0.01
yrs = (h1["timestamp"].iloc[-1] - h1["timestamp"].iloc[0]).days / 365.25

m, tr = run(h1, SYM, SPREAD, COMM, RISK, m60, m60, PS, PV)
print("%-24s %s" % ("absext_p60 FULL", stats(m, tr, yrs)))

n2 = len(h1) // 2
for hname, sl in (("H1_first", slice(0, n2)), ("H2_second", slice(n2, len(h1)))):
    hdf = h1.iloc[sl].reset_index(drop=True)
    yrs_h = (hdf["timestamp"].iloc[-1] - hdf["timestamp"].iloc[0]).days / 365.25
    m, tr = run(hdf, SYM, SPREAD, COMM, RISK, m70[sl], m70[sl], PS, PV)
    print("%-24s %s" % ("absext_p70 " + hname, stats(m, tr, yrs_h)))
print("DONE")
