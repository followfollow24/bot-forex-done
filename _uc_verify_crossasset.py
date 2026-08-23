"""ADVERSARIAL VERIFICATION of finder claim btc_r36S (block BTC shorts unless
ETH/BTC ratio EMA36 > EMA168). Independent re-implementation of the mask.
Modes: full | halves | nbr | shift
"""
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
    d = prepare_data(h1df[["timestamp", "open", "high", "low", "close"]].copy())
    s = Filtered(); s.ADX_MIN = 10; s.TIMEFRAME_SECONDS = 3600; s.TOUCH_TOLERANCE = 0.012
    s.sl_atr = 3.0; s.tp_atr = 999.0; s.trail_atr_mult = 999.0; s.trail_activation_atr = 999.0
    s.precompute(d); s._mask_long = mlong; s._mask_short = mshort
    eng = BacktestEngine(d, cfg(risk, sym, ps, pv), s, spread_price=spread, commission_per_lot=comm, symbol=sym)
    eng.run(quiet=True, do_precompute=False)
    return compute_metrics(eng.trades, eng.equity_curve, START), eng.trades


def line(m, tr, yrs):
    p = perf(to_monthly(tr)); sh = p["sharpe"] if p else float("nan")
    t = m["total_return_pct"]; cg = -100.0 if t <= -100 else ((1 + t / 100) ** (1 / yrs) - 1) * 100
    return "n=%d win%%=%.1f PF=%.2f Sharpe=%.2f CAGR=%+.2f%% DD=%.1f%%" % (
        m["trades"], m["win_rate"] * 100, m["profit_factor"], sh, cg, m["max_dd_pct"])


def load_crypto(path):
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"], format="ISO8601")
    h1 = (df.set_index("timestamp").resample("1h")
          .agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
               close=("close", "last"))
          .dropna(subset=["open"]).reset_index())
    return h1


btc = load_crypto("download/btcusdt-15m-vol.csv")
eth = load_crypto("download/ethusdt-15m-vol.csv")
M = btc.merge(eth[["timestamp", "close"]].rename(columns={"close": "close_e"}),
              on="timestamp", how="inner")
N = len(M)
YRS = (M["timestamp"].iloc[-1] - M["timestamp"].iloc[0]).days / 365.25
MID = N // 2
print("window %s .. %s  N=%d yrs=%.2f mid=%s" % (
    M["timestamp"].iloc[0], M["timestamp"].iloc[-1], N, YRS, M["timestamp"].iloc[MID]), flush=True)

ratio = pd.Series(M["close_e"].to_numpy(float) / M["close"].to_numpy(float))


def short_mask(fast, slow, shift=0):
    e_f = ratio.ewm(span=fast, adjust=False).mean()
    e_s = ratio.ewm(span=slow, adjust=False).mean()
    m = (e_f > e_s).to_numpy()
    if shift:
        m = np.concatenate([np.ones(shift, dtype=bool), m[:-shift]])
    return m.astype(bool)


BTC_H1 = M[["timestamp", "open", "high", "low", "close"]]
K = dict(sym="BTCUSDc", spread=10.0, comm=0.0, risk=1.00, ps=1.0, pv=0.01)


def do(ms=None, sl=None, lab=""):
    h1 = BTC_H1; yrs = YRS
    if sl is not None:
        a, b = sl
        h1 = h1.iloc[a:b].reset_index(drop=True)
        ms = None if ms is None else ms[a:b]
        yrs = (h1["timestamp"].iloc[-1] - h1["timestamp"].iloc[0]).days / 365.25
    m, tr = run(h1, K["sym"], K["spread"], K["comm"], K["risk"], mshort=ms, ps=K["ps"], pv=K["pv"])
    print("%-24s %s" % (lab, line(m, tr, yrs)), flush=True)


mode = sys.argv[1] if len(sys.argv) > 1 else "full"

if mode == "full":
    do(None, None, "BASE full")
    do(short_mask(36, 168), None, "CAND S36_168 full")
elif mode == "shift":
    do(short_mask(36, 168, shift=1), None, "SHIFT1 S36_168 full")
    do(short_mask(36, 168, shift=2), None, "SHIFT2 S36_168 full")
elif mode == "halves":
    for lab, sl in (("H1", (0, MID)), ("H2", (MID, N))):
        do(None, sl, "BASE %s" % lab)
        do(short_mask(36, 168), sl, "CAND %s" % lab)
elif mode == "nbr":
    for f, s in ((24, 168), (48, 168), (36, 120), (36, 240)):
        do(short_mask(f, s), None, "NBR S%d_%d" % (f, s))
