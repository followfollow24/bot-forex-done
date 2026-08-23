"""Cross-asset BTC<->ETH information as an ENTRY FILTER on the frozen live
trend-pullback config (H1, ADX_MIN=10, TOUCH_TOLERANCE=0.012, sl_atr=3, no TP,
max_hold=64). RESEARCH ONLY -- new file, touches nothing live.

Usage:
  python3 _uc_crossasset2.py base          # aligned-window unfiltered baselines
  python3 _uc_crossasset2.py eth           # ETH-side battery
  python3 _uc_crossasset2.py btc           # BTC-side battery
  python3 _uc_crossasset2.py follow <variant> [...]   # OOS half-split + neighbors
"""
import sys, os
sys.path.insert(0, os.getcwd())
import numpy as np, pandas as pd
from forex_config import ForexConfig
from backtest_forex import DataLoader, prepare_data, BacktestEngine, FastHybridTrendPullback, compute_metrics
from forex_indicators import Signal
from _all_paths import to_monthly, perf, START


class Filtered(FastHybridTrendPullback):
    _mask_long = None    # numpy bool arrays aligned to H1 df rows; True = allow
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


def stats(m, tr, yrs):
    p = perf(to_monthly(tr)); sh = p["sharpe"] if p else float("nan")
    t = m["total_return_pct"]; cg = -100.0 if t <= -100 else ((1 + t / 100) ** (1 / yrs) - 1) * 100
    return dict(n=m["trades"], win=m["win_rate"] * 100, pf=m["profit_factor"], sh=sh, cagr=cg, dd=m["max_dd_pct"])


def line(st):
    return "n=%d win%%=%.1f PF=%.2f Sharpe=%.2f CAGR=%+.2f%% DD=%.1f%%" % (
        st["n"], st["win"], st["pf"], st["sh"], st["cagr"], st["dd"])


def improved(b, v):
    c1 = (v["sh"] >= b["sh"] + 0.10) and (v["cagr"] >= b["cagr"])
    if b["cagr"] >= 0:
        cag_ok = v["cagr"] >= 0.9 * b["cagr"]
    else:
        cag_ok = v["cagr"] >= b["cagr"]
    c2 = (v["sh"] >= b["sh"]) and (v["dd"] <= 0.75 * b["dd"]) and cag_ok
    return c1 or c2


# ---------------------------------------------------------------- data
def load_crypto(path):
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"], format="ISO8601")
    h1 = (df.set_index("timestamp").resample("1h")
          .agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
               close=("close", "last"), volume=("volume", "sum"),
               n_trades=("n_trades", "sum"), taker_buy_vol=("taker_buy_vol", "sum"))
          .dropna(subset=["open"]).reset_index())
    return h1


btc = load_crypto("download/btcusdt-15m-vol.csv")
eth = load_crypto("download/ethusdt-15m-vol.csv")
# inner-align the two H1 frames on timestamp BEFORE building any masks
M = btc[["timestamp", "open", "high", "low", "close"]].merge(
    eth[["timestamp", "open", "high", "low", "close"]],
    on="timestamp", suffixes=("_b", "_e"), how="inner")
BTC_H1 = M[["timestamp", "open_b", "high_b", "low_b", "close_b"]].rename(
    columns={"open_b": "open", "high_b": "high", "low_b": "low", "close_b": "close"})
ETH_H1 = M[["timestamp", "open_e", "high_e", "low_e", "close_e"]].rename(
    columns={"open_e": "open", "high_e": "high", "low_e": "low", "close_e": "close"})
N = len(M)
T0, T1 = M["timestamp"].iloc[0], M["timestamp"].iloc[-1]
YRS = (T1 - T0).days / 365.25
MID = N // 2
TM = M["timestamp"].iloc[MID]

# ------------------------------------------------- causal signal arrays
# bar i values use data through bar-i close; entry fills at bar i+1 open.
cb = M["close_b"].to_numpy(float)
ce = M["close_e"].to_numpy(float)
sb = pd.Series(cb); se = pd.Series(ce)

RET = {k: sb.pct_change(k).to_numpy() for k in (12, 24, 48, 72, 96)}   # BTC k-hour returns

ratio = se / sb                                                        # ETH/BTC
REMA = {sp: ratio.ewm(span=sp, adjust=False).mean().to_numpy()
        for sp in (16, 24, 36, 48, 120, 168, 240)}

rb = sb.pct_change(); re_ = se.pct_change()
corr = rb.rolling(720).corr(re_)                                       # 720h rolling corr
CORRQ = {q: corr.rolling(2160, min_periods=720).quantile(q) for q in (0.1, 0.2, 0.3)}


def gate_ret(k, inv=False):
    """BTC leadership: longs need BTC k-h ret > 0, shorts < 0 (inv flips)."""
    r = RET[k]; nan = np.isnan(r)
    lo = np.where(nan, True, r < 0); hi = np.where(nan, True, r > 0)
    return (lo, hi) if inv else (hi, lo)     # (mask_long, mask_short)


def gate_ratio(fast, slow, inv=False):
    """ETH/BTC ratio trend: EMAfast>EMAslow required for longs, < for shorts."""
    up = REMA[fast] > REMA[slow]; dn = REMA[fast] < REMA[slow]
    nan = np.isnan(REMA[slow])
    l = np.where(nan, True, dn if inv else up)
    s = np.where(nan, True, up if inv else dn)
    return l.astype(bool), s.astype(bool)


def gate_ratio_side(fast, slow, side, inv=False):
    """Gate only one direction, other side ungated.
    side='L' normal: longs require ratio rising (EMAfast>EMAslow); inv: falling.
    side='S' normal: shorts require ratio falling; inv: rising."""
    up = REMA[fast] > REMA[slow]; dn = REMA[fast] < REMA[slow]
    nan = np.isnan(REMA[slow])
    if side == "L":
        cond = dn if inv else up
        return np.where(nan, True, cond).astype(bool), None
    cond = up if inv else dn
    return None, np.where(nan, True, cond).astype(bool)


def gate_corr(q, inv=False):
    """Block entries when 720h BTC-ETH corr in lowest rolling quintile (inv: allow only then)."""
    thr = CORRQ[q]
    valid = (corr.notna() & thr.notna()).to_numpy()
    low = (corr <= thr).to_numpy()           # NaN comparisons -> False, masked by valid
    allow = np.where(valid, low if inv else ~low, True).astype(bool)
    return allow, allow.copy()               # same gate both directions


MKT = {
    "BTC": dict(h1=BTC_H1, sym="BTCUSDc", spread=10.0, comm=0.0, risk=1.00, ps=1.0, pv=0.01),
    "ETH": dict(h1=ETH_H1, sym="ETHUSDc", spread=1.0, comm=0.0, risk=1.00, ps=1.0, pv=0.01),
}

VARIANTS = {
    # ETH side ---------------------------------------------------------
    "eth_lead24":     ("ETH", lambda: gate_ret(24)),
    "eth_lead24_inv": ("ETH", lambda: gate_ret(24, inv=True)),
    "eth_lead72":     ("ETH", lambda: gate_ret(72)),
    "eth_lead72_inv": ("ETH", lambda: gate_ret(72, inv=True)),
    "eth_ratio":      ("ETH", lambda: gate_ratio(24, 168)),
    "eth_ratio_inv":  ("ETH", lambda: gate_ratio(24, 168, inv=True)),
    "eth_corrblock":  ("ETH", lambda: gate_corr(0.2)),
    "eth_corronly":   ("ETH", lambda: gate_corr(0.2, inv=True)),
    # BTC side ---------------------------------------------------------
    "btc_ratio":      ("BTC", lambda: gate_ratio(24, 168)),
    "btc_ratio_inv":  ("BTC", lambda: gate_ratio(24, 168, inv=True)),
    "btc_corrblock":  ("BTC", lambda: gate_corr(0.2)),
    "btc_corronly":   ("BTC", lambda: gate_corr(0.2, inv=True)),
    # BTC side, one-direction refinements (follow-ups if two-sided shows signal)
    "btc_r24L":       ("BTC", lambda: gate_ratio_side(24, 168, "L")),
    "btc_r24S":       ("BTC", lambda: gate_ratio_side(24, 168, "S")),
    "btc_r24Li":      ("BTC", lambda: gate_ratio_side(24, 168, "L", inv=True)),
    "btc_r24Si":      ("BTC", lambda: gate_ratio_side(24, 168, "S", inv=True)),
}

NEIGHBORS = {
    "eth_lead24":     [("lead12", lambda: gate_ret(12)), ("lead48", lambda: gate_ret(48))],
    "eth_lead24_inv": [("lead12i", lambda: gate_ret(12, True)), ("lead48i", lambda: gate_ret(48, True))],
    "eth_lead72":     [("lead48", lambda: gate_ret(48)), ("lead96", lambda: gate_ret(96))],
    "eth_lead72_inv": [("lead48i", lambda: gate_ret(48, True)), ("lead96i", lambda: gate_ret(96, True))],
    "eth_ratio":      [("r16_168", lambda: gate_ratio(16, 168)), ("r36_168", lambda: gate_ratio(36, 168)),
                       ("r24_120", lambda: gate_ratio(24, 120)), ("r24_240", lambda: gate_ratio(24, 240))],
    "eth_ratio_inv":  [("r16_168i", lambda: gate_ratio(16, 168, True)), ("r36_168i", lambda: gate_ratio(36, 168, True)),
                       ("r24_120i", lambda: gate_ratio(24, 120, True)), ("r24_240i", lambda: gate_ratio(24, 240, True))],
    "eth_corrblock":  [("q10", lambda: gate_corr(0.1)), ("q30", lambda: gate_corr(0.3))],
    "eth_corronly":   [("q10i", lambda: gate_corr(0.1, True)), ("q30i", lambda: gate_corr(0.3, True))],
    "btc_ratio":      [("r16_168", lambda: gate_ratio(16, 168)), ("r36_168", lambda: gate_ratio(36, 168)),
                       ("r24_120", lambda: gate_ratio(24, 120)), ("r24_240", lambda: gate_ratio(24, 240))],
    "btc_ratio_inv":  [("r16_168i", lambda: gate_ratio(16, 168, True)), ("r36_168i", lambda: gate_ratio(36, 168, True)),
                       ("r24_120i", lambda: gate_ratio(24, 120, True)), ("r24_240i", lambda: gate_ratio(24, 240, True))],
    "btc_corrblock":  [("q10", lambda: gate_corr(0.1)), ("q30", lambda: gate_corr(0.3))],
    "btc_corronly":   [("q10i", lambda: gate_corr(0.1, True)), ("q30i", lambda: gate_corr(0.3, True))],
    "btc_r24S":       [("S16_168", lambda: gate_ratio_side(16, 168, "S")), ("S36_168", lambda: gate_ratio_side(36, 168, "S")),
                       ("S24_120", lambda: gate_ratio_side(24, 120, "S")), ("S24_240", lambda: gate_ratio_side(24, 240, "S"))],
    "btc_r24Si":      [("S16i", lambda: gate_ratio_side(16, 168, "S", True)), ("S36i", lambda: gate_ratio_side(36, 168, "S", True)),
                       ("S120i", lambda: gate_ratio_side(24, 120, "S", True)), ("S240i", lambda: gate_ratio_side(24, 240, "S", True))],
    "btc_r24L":       [("L16_168", lambda: gate_ratio_side(16, 168, "L")), ("L36_168", lambda: gate_ratio_side(36, 168, "L")),
                       ("L24_120", lambda: gate_ratio_side(24, 120, "L")), ("L24_240", lambda: gate_ratio_side(24, 240, "L"))],
    "btc_r24Li":      [("L16i", lambda: gate_ratio_side(16, 168, "L", True)), ("L36i", lambda: gate_ratio_side(36, 168, "L", True)),
                       ("L120i", lambda: gate_ratio_side(24, 120, "L", True)), ("L240i", lambda: gate_ratio_side(24, 240, "L", True))],
}


def do_run(mkt, ml=None, ms=None, sl=None):
    k = MKT[mkt]
    h1 = k["h1"]; yrs = YRS
    if sl is not None:
        a, b = sl
        h1 = h1.iloc[a:b].reset_index(drop=True)
        ml = None if ml is None else ml[a:b]
        ms = None if ms is None else ms[a:b]
        yrs = (h1["timestamp"].iloc[-1] - h1["timestamp"].iloc[0]).days / 365.25
    m, tr = run(h1, k["sym"], k["spread"], k["comm"], k["risk"], mlong=ml, mshort=ms, ps=k["ps"], pv=k["pv"])
    return stats(m, tr, yrs)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "base"
    print("aligned window: %s .. %s  (%d H1 bars, %.2f yrs)  mid=%s" % (T0, T1, N, YRS, TM), flush=True)

    if mode == "base":
        for mkt in ("BTC", "ETH"):
            st = do_run(mkt)
            print("BASE %-4s %s" % (mkt, line(st)), flush=True)

    elif mode in ("eth", "btc"):
        want = mode.upper()
        base = do_run(want)
        print("BASE %-4s %s" % (want, line(base)), flush=True)
        for name, (mkt, g) in VARIANTS.items():
            if mkt != want: continue
            ml, ms = g()
            st = do_run(mkt, ml, ms)
            tag = "IMPROVED" if improved(base, st) else ""
            print("VAR %-16s %s  %s" % (name, line(st), tag), flush=True)

    elif mode == "follow":
        for name in sys.argv[2:]:
            mkt, g = VARIANTS[name]
            ml, ms = g()
            print("== follow-up %s (%s) ==" % (name, mkt), flush=True)
            for lab, sl in (("H1(first)", (0, MID)), ("H2(second)", (MID, N))):
                b = do_run(mkt, sl=sl)
                v = do_run(mkt, ml, ms, sl=sl)
                tag = "IMPROVED" if improved(b, v) else ""
                print("  %-10s base %s" % (lab, line(b)), flush=True)
                print("  %-10s filt %s  %s" % (lab, line(v), tag), flush=True)
            base = do_run(mkt)
            for lab, ng in NEIGHBORS.get(name, []):
                nl, ns = ng()
                st = do_run(mkt, nl, ns)
                tag = "IMPROVED" if improved(base, st) else ""
                print("  NBR %-10s %s  %s" % (lab, line(st), tag), flush=True)


if __name__ == "__main__":
    main()
