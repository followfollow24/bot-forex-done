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


def stats(m, tr, yrs):
    p = perf(to_monthly(tr)); sh = p["sharpe"] if p else float("nan")
    t = m["total_return_pct"]; cg = -100.0 if t <= -100 else ((1 + t / 100) ** (1 / yrs) - 1) * 100
    return dict(sharpe=sh, cagr=cg, dd=m["max_dd_pct"], n=m["trades"])


# ── load BTC H1 with volume columns ─────────────────────────────────────
m15 = pd.read_csv("download/btcusdt-15m-vol.csv")
m15["timestamp"] = pd.to_datetime(m15["timestamp"], format="mixed")  # file mixes sec / .000 ms formats
h1 = (m15.set_index("timestamp").resample("1h")
      .agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"),
           volume=("volume", "sum"), n_trades=("n_trades", "sum"), taker_buy_vol=("taker_buy_vol", "sum"))
      .dropna(subset=["open"]).reset_index())

vol = h1["volume"].to_numpy(float)
ntr = h1["n_trades"].to_numpy(float)
close = h1["close"].to_numpy(float)
N = len(h1)
yrs_full = (h1["timestamp"].iloc[-1] - h1["timestamp"].iloc[0]).days / 365.25
print("H1 bars=%d  %s .. %s  (%.2f yrs)" % (N, h1["timestamp"].iloc[0], h1["timestamp"].iloc[-1], yrs_full))

sv = pd.Series(vol); sn = pd.Series(ntr)


def sma(s, w):
    return s.rolling(w, min_periods=w).mean().to_numpy()


rv = vol / sma(sv, 168)          # relative volume vs 1-week SMA, window ends at i
rn = ntr / sma(sn, 168)          # relative trade count

# OBV from H1 closes + volume
direc = np.sign(np.diff(close, prepend=close[0]))
obv = np.cumsum(direc * vol)


def obv_slope(lb):
    sl = np.full(N, np.nan)
    sl[lb:] = obv[lb:] - obv[:-lb]
    return sl


def gate(cond, nanvals):
    """NaN (warmup) -> allow, so gated window == baseline window."""
    out = cond.copy()
    out[np.isnan(nanvals)] = True
    return out


def make_variant(kind, p):
    """returns (mlong, mshort)"""
    if kind == "rv_gt":
        m = gate(rv > p, rv); return m, m
    if kind == "rv_lt":
        m = gate(rv < p, rv); return m, m
    if kind == "rv_band":
        lo, hi = p; m = gate((rv > lo) & (rv < hi), rv); return m, m
    if kind == "rn_gt":
        m = gate(rn > p, rn); return m, m
    if kind == "rn_lt":
        m = gate(rn < p, rn); return m, m
    if kind == "rn_band":
        lo, hi = p; m = gate((rn > lo) & (rn < hi), rn); return m, m
    if kind == "obv_agree":
        sl = obv_slope(p)
        return gate(sl > 0, sl), gate(sl < 0, sl)
    if kind == "obv_disagree":
        sl = obv_slope(p)
        return gate(sl < 0, sl), gate(sl > 0, sl)
    if kind == "vreg_hi":
        fast = p; m = gate(sma(sv, fast) > sma(sv, 168), sma(sv, 168)); return m, m
    if kind == "vreg_lo":
        fast = p; m = gate(sma(sv, fast) < sma(sv, 168), sma(sv, 168)); return m, m
    raise ValueError(kind)


SYM, SPREAD, COMM, RISK, PS, PV = "BTCUSDc", 10.0, 0.0, 1.00, 1.0, 0.01

VARIANTS = [
    ("rv>1.0",        "rv_gt", 1.0),
    ("rv>1.5",        "rv_gt", 1.5),
    ("rv<1.0",        "rv_lt", 1.0),
    ("0.5<rv<2.0",    "rv_band", (0.5, 2.0)),
    ("ntr>1.0",       "rn_gt", 1.0),
    ("ntr>1.5",       "rn_gt", 1.5),
    ("ntr<1.0",       "rn_lt", 1.0),
    ("0.5<ntr<2.0",   "rn_band", (0.5, 2.0)),
    ("obv24_agree",   "obv_agree", 24),
    ("obv24_disagree","obv_disagree", 24),
    ("vSMA24>SMA168", "vreg_hi", 24),
    ("vSMA24<SMA168", "vreg_lo", 24),
]

NEIGHBORS = {
    "rv>1.0":         [("rv>0.75", "rv_gt", 0.75), ("rv>1.25", "rv_gt", 1.25)],
    "rv>1.5":         [("rv>1.25", "rv_gt", 1.25), ("rv>1.75", "rv_gt", 1.75)],
    "rv<1.0":         [("rv<0.75", "rv_lt", 0.75), ("rv<1.25", "rv_lt", 1.25)],
    "0.5<rv<2.0":     [("0.4<rv<1.6", "rv_band", (0.4, 1.6)), ("0.6<rv<2.4", "rv_band", (0.6, 2.4))],
    "ntr>1.0":        [("ntr>0.75", "rn_gt", 0.75), ("ntr>1.25", "rn_gt", 1.25)],
    "ntr>1.5":        [("ntr>1.25", "rn_gt", 1.25), ("ntr>1.75", "rn_gt", 1.75)],
    "ntr<1.0":        [("ntr<0.75", "rn_lt", 0.75), ("ntr<1.25", "rn_lt", 1.25)],
    "0.5<ntr<2.0":    [("0.4<ntr<1.6", "rn_band", (0.4, 1.6)), ("0.6<ntr<2.4", "rn_band", (0.6, 2.4))],
    "obv24_agree":    [("obv16_agree", "obv_agree", 16), ("obv32_agree", "obv_agree", 32)],
    "obv24_disagree": [("obv16_disagree", "obv_disagree", 16), ("obv32_disagree", "obv_disagree", 32)],
    "vSMA24>SMA168":  [("vSMA16>SMA168", "vreg_hi", 16), ("vSMA32>SMA168", "vreg_hi", 32)],
    "vSMA24<SMA168":  [("vSMA16<SMA168", "vreg_lo", 16), ("vSMA32<SMA168", "vreg_lo", 32)],
}

# ── 1. baseline on the full window (vol data spans the whole price file) ─
mb, trb = run(h1, SYM, SPREAD, COMM, RISK, ps=PS, pv=PV)
sb = stats(mb, trb, yrs_full)
print("BASELINE            " + line(mb, trb, yrs_full), flush=True)

# ── 2. battery ──────────────────────────────────────────────────────────
results = {}
for name, kind, p in VARIANTS:
    ml, ms = make_variant(kind, p)
    m, tr = run(h1, SYM, SPREAD, COMM, RISK, mlong=ml, mshort=ms, ps=PS, pv=PV)
    results[name] = (stats(m, tr, yrs_full), (kind, p))
    print("%-18s  %s" % (name, line(m, tr, yrs_full)), flush=True)


def improved(s, b):
    if np.isnan(s["sharpe"]):
        return False
    a = s["sharpe"] >= b["sharpe"] + 0.10 and s["cagr"] >= b["cagr"]
    c = s["sharpe"] >= b["sharpe"] and s["dd"] <= 0.75 * b["dd"] and s["cagr"] >= 0.9 * b["cagr"]
    return a or c


winners = [n for n, (s, _) in results.items() if improved(s, sb)]
print("\nWinners vs baseline: %s" % (winners if winners else "NONE"), flush=True)

# ── 3. OOS half-split + neighbors for winners ───────────────────────────
if winners:
    mid = N // 2
    halves = [("H1(first)", slice(0, mid)), ("H2(second)", slice(mid, N))]
    half_base = {}
    for hname, sl in halves:
        hdf = h1.iloc[sl].reset_index(drop=True)
        hyrs = (hdf["timestamp"].iloc[-1] - hdf["timestamp"].iloc[0]).days / 365.25
        m, tr = run(hdf, SYM, SPREAD, COMM, RISK, ps=PS, pv=PV)
        half_base[hname] = (stats(m, tr, hyrs), hyrs)
        print("BASE %-11s  %s" % (hname, line(m, tr, hyrs)), flush=True)

    for wname in winners:
        kind, p = results[wname][1]
        ml, ms = make_variant(kind, p)
        for hname, sl in halves:
            hdf = h1.iloc[sl].reset_index(drop=True)
            hyrs = half_base[hname][1]
            m, tr = run(hdf, SYM, SPREAD, COMM, RISK,
                        mlong=ml[sl] if ml is not None else None,
                        mshort=ms[sl] if ms is not None else None, ps=PS, pv=PV)
            print("%-18s %-11s  %s" % (wname, hname, line(m, tr, hyrs)), flush=True)
        for nb_name, nb_kind, nb_p in NEIGHBORS[wname]:
            nml, nms = make_variant(nb_kind, nb_p)
            m, tr = run(h1, SYM, SPREAD, COMM, RISK, mlong=nml, mshort=nms, ps=PS, pv=PV)
            print("NBR %-15s  %s" % (nb_name, line(m, tr, yrs_full)), flush=True)

print("\nDONE", flush=True)
