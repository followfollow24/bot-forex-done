import sys, os
sys.path.insert(0, os.getcwd())
import numpy as np, pandas as pd
from forex_config import ForexConfig
from backtest_forex import DataLoader, prepare_data, BacktestEngine, FastHybridTrendPullback, compute_metrics
from forex_indicators import Signal
from _idea_search import resample
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

# ── data: ETH 15m with volume -> H1 ─────────────────────────────────────────
raw = pd.read_csv("download/ethusdt-15m-vol.csv", parse_dates=["timestamp"])
raw["timestamp"] = pd.to_datetime(raw["timestamp"], errors="coerce")
raw = raw.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
h1 = (raw.set_index("timestamp").resample("1h")
      .agg(open=("open","first"), high=("high","max"), low=("low","min"), close=("close","last"),
           volume=("volume","sum"), n_trades=("n_trades","sum"), taker_buy_vol=("taker_buy_vol","sum"))
      .dropna(subset=["open"]).reset_index())

vol = h1["volume"].to_numpy(float)
tbv = h1["taker_buy_vol"].to_numpy(float)
tr_ratio = np.where(vol > 0, tbv / np.maximum(vol, 1e-12), 0.5)   # neutral when no volume
delta = 2.0 * tbv - vol
cum = np.cumsum(delta)

trs = pd.Series(tr_ratio)
zmean = trs.rolling(168).mean()
zstd  = trs.rolling(168).std()
z = ((trs - zmean) / zstd).to_numpy()

def cd_slope(nbar):
    s = np.full(len(cum), np.nan)
    s[nbar:] = cum[nbar:] - cum[:-nbar]
    return s
s16, s24, s32, s48, s72, s96 = (cd_slope(n) for n in (16, 24, 32, 48, 72, 96))

N = len(h1)
def allow(x, cond):
    """True where cond; NaN in x -> allow (warmup only)."""
    out = np.where(np.isnan(x), True, cond)
    return out.astype(bool)

ALL = np.ones(N, dtype=bool)

# variant name -> (mask_long, mask_short)
V = {
    # (a) aggressor confirmation
    "aggr_050":   (allow(tr_ratio, tr_ratio > 0.50), allow(tr_ratio, tr_ratio < 0.50)),
    # (b) stronger thresholds
    "aggr_052":   (allow(tr_ratio, tr_ratio > 0.52), allow(tr_ratio, tr_ratio < 0.48)),
    # (c) contrarian inverses
    "contra_050": (allow(tr_ratio, tr_ratio < 0.50), allow(tr_ratio, tr_ratio > 0.50)),
    "contra_052": (allow(tr_ratio, tr_ratio < 0.48), allow(tr_ratio, tr_ratio > 0.52)),
    # (d) cumulative-delta slope agreement + inverses
    "cd24":       (allow(s24, s24 > 0),  allow(s24, s24 < 0)),
    "cd24_inv":   (allow(s24, s24 < 0),  allow(s24, s24 > 0)),
    "cd72":       (allow(s72, s72 > 0),  allow(s72, s72 < 0)),
    "cd72_inv":   (allow(s72, s72 < 0),  allow(s72, s72 > 0)),
    # (e) taker_ratio z(168) extreme fade + inverse
    "zfade_2":    (allow(z, ~(z >  2.0)), allow(z, ~(z < -2.0))),
    "zfade_2inv": (allow(z, ~(z < -2.0)), allow(z, ~(z >  2.0))),
    # neighbors (run only when asked)
    "aggr_051":   (allow(tr_ratio, tr_ratio > 0.51), allow(tr_ratio, tr_ratio < 0.49)),
    "aggr_053":   (allow(tr_ratio, tr_ratio > 0.53), allow(tr_ratio, tr_ratio < 0.47)),
    "contra_051": (allow(tr_ratio, tr_ratio < 0.49), allow(tr_ratio, tr_ratio > 0.51)),
    "contra_053": (allow(tr_ratio, tr_ratio < 0.47), allow(tr_ratio, tr_ratio > 0.53)),
    "cd16":       (allow(s16, s16 > 0),  allow(s16, s16 < 0)),
    "cd32":       (allow(s32, s32 > 0),  allow(s32, s32 < 0)),
    "cd48":       (allow(s48, s48 > 0),  allow(s48, s48 < 0)),
    "cd96":       (allow(s96, s96 > 0),  allow(s96, s96 < 0)),
    "cd16_inv":   (allow(s16, s16 < 0),  allow(s16, s16 > 0)),
    "cd32_inv":   (allow(s32, s32 < 0),  allow(s32, s32 > 0)),
    "cd48_inv":   (allow(s48, s48 < 0),  allow(s48, s48 > 0)),
    "cd96_inv":   (allow(s96, s96 < 0),  allow(s96, s96 > 0)),
    "zfade_15":   (allow(z, ~(z >  1.5)), allow(z, ~(z < -1.5))),
    "zfade_25":   (allow(z, ~(z >  2.5)), allow(z, ~(z < -2.5))),
    "zfade_15inv":(allow(z, ~(z < -1.5)), allow(z, ~(z >  1.5))),
    "zfade_25inv":(allow(z, ~(z < -2.5)), allow(z, ~(z >  2.5))),
}

SYM, SPREAD, COMM, RISK, PS, PV = "ETHUSDc", 1.0, 0.0, 1.00, 1.0, 0.01

def years_of(df):
    return (df["timestamp"].iloc[-1] - df["timestamp"].iloc[0]).total_seconds() / (365.25*24*3600)

def do_run(df, ml, ms, tag, lo=0):
    yrs = years_of(df)
    m, trades = run(df, SYM, SPREAD, COMM, RISK,
                    mlong=None if ml is None else ml[lo:lo+len(df)],
                    mshort=None if ms is None else ms[lo:lo+len(df)],
                    ps=PS, pv=PV)
    print("%-22s %s" % (tag, line(m, trades, yrs)), flush=True)

if __name__ == "__main__":
    args = sys.argv[1:]
    names = [a for a in args if a not in ("--halves",)]
    halves = "--halves" in args
    mid = N // 2
    h_a = h1.iloc[:mid].reset_index(drop=True)
    h_b = h1.iloc[mid:].reset_index(drop=True)
    print("window: %s -> %s  (%d H1 bars, %.2f yrs)  half-split at %s" % (
        h1["timestamp"].iloc[0], h1["timestamp"].iloc[-1], N, years_of(h1), h1["timestamp"].iloc[mid]), flush=True)
    for nm in names:
        if nm == "baseline":
            ml = ms = None
        else:
            ml, ms = V[nm]
        if halves:
            do_run(h_a, ml, ms, nm + "  [H1st]", lo=0)
            do_run(h_b, ml, ms, nm + "  [H2nd]", lo=mid)
        else:
            do_run(h1, ml, ms, nm)
