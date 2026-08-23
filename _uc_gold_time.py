"""Time-of-day / day-of-week entry filter test for GOLD trend-pullback (frozen live config).

Stages:
  python3 _uc_gold_time.py base      -> baseline + hour/dow diagnostics + half-stability
  python3 _uc_gold_time.py battery   -> full-window battery of time masks
  python3 _uc_gold_time.py oos NAME [NAME...] -> half-split OOS + neighbors for candidates
"""
import sys, os, pickle, time
sys.path.insert(0, os.getcwd())
import numpy as np, pandas as pd
from forex_config import ForexConfig
from backtest_forex import DataLoader, prepare_data, BacktestEngine, FastHybridTrendPullback, compute_metrics
from forex_indicators import Signal
from _idea_search import resample
from _all_paths import to_monthly, perf, START

CACHE = "/private/tmp/claude-501/-Users-follow-Desktop-outputs/5c1c9348-7d96-4348-98bf-858abc3aadf3/scratchpad/_uc_gold_time_cache.pkl"

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
    assert len(d["c"]) == len(h1df), "row-count mismatch: mask misaligned"
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

# ---------------------------------------------------------------- data
def load_gold():
    loader = DataLoader(log_fn=lambda *a, **k: None)
    c0 = ForexConfig(); c0.total_capital_usd = START
    dfg, _ = loader.load("XAUUSD", 99.0, c0,
                         csv_path="download/xauusd-m15-bid-2013-01-01-2026-06-10.csv",
                         allow_synthetic=True)
    h1 = resample(dfg, "1h").reset_index(drop=True)
    return h1

def time_arrays(h1):
    """entry hour/dow for a signal at row i = timestamp of row i+1 (fill bar).
    Causal: the bar calendar is known in advance; uses no price info."""
    ts = pd.to_datetime(h1["timestamp"]).reset_index(drop=True)
    nxt = ts.shift(-1)
    nxt.iloc[-1] = ts.iloc[-1] + pd.Timedelta(hours=1)
    return nxt.dt.hour.to_numpy(), nxt.dt.dayofweek.to_numpy(), ts

# ---------------------------------------------------------------- masks
# each entry: name -> (description, fn(eh, ed) -> BLOCK boolean array)
def hour_block(lo, hi):        # block entries with hour in [lo, hi)
    return lambda eh, ed: (eh >= lo) & (eh < hi)
def single_hour(h):
    return lambda eh, ed: eh == h
def day_block(dw):
    return lambda eh, ed: ed == dw
def fri_after(h):
    return lambda eh, ed: (ed == 4) & (eh >= h)
def mon_first(h):
    return lambda eh, ed: (ed == 0) & (eh < h)

MASKS = {
    "BLK00_06": ("block entries 00-06h", hour_block(0, 6)),
    "BLK06_12": ("block entries 06-12h", hour_block(6, 12)),
    "BLK12_18": ("block entries 12-18h", hour_block(12, 18)),
    "BLK18_24": ("block entries 18-24h", hour_block(18, 24)),
    "FRI_GE12": ("block Fri entries hour>=12", fri_after(12)),
    "FRI_GE10": ("block Fri entries hour>=10", fri_after(10)),
    "FRI_GE14": ("block Fri entries hour>=14", fri_after(14)),
    "MON_LT6":  ("block Mon entries hour<6", mon_first(6)),
    "MON_LT4":  ("block Mon entries hour<4", mon_first(4)),
    "MON_LT8":  ("block Mon entries hour<8", mon_first(8)),
    # neighbors of 6h blocks (shift +-1h) - used in oos stage
    "BLK11_17": ("block entries 11-17h", hour_block(11, 17)),
    "BLK13_19": ("block entries 13-19h", hour_block(13, 19)),
    "BLK01_07": ("block entries 01-07h", hour_block(1, 7)),
    "BLK23_05": ("block entries 23-05h", lambda eh, ed: (eh >= 23) | (eh < 5)),
    "BLK05_11": ("block entries 05-11h", hour_block(5, 11)),
    "BLK07_13": ("block entries 07-13h", hour_block(7, 13)),
    "BLK17_23": ("block entries 17-23h", hour_block(17, 23)),
    "BLK19_01": ("block entries 19-01h", lambda eh, ed: (eh >= 19) | (eh < 1)),
    "BLK20_02": ("block entries 20-02h", lambda eh, ed: (eh >= 20) | (eh < 2)),
    "BLK21_03": ("block entries 21-03h", lambda eh, ed: (eh >= 21) | (eh < 3)),
    "BLK20_01": ("block entries 20-01h", lambda eh, ed: (eh >= 20) | (eh < 1)),
    "BLK21_02": ("block entries 21-02h", lambda eh, ed: (eh >= 21) | (eh < 2)),
}
for h in range(24):
    MASKS["HR%02d" % h] = ("block single entry hour %02d" % h, single_hour(h))
DAYN = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
for dw in range(7):
    MASKS["DAY_%s" % DAYN[dw]] = ("block all %s entries" % DAYN[dw], day_block(dw))

NEIGHBORS = {
    "BLK00_06": ["BLK23_05", "BLK01_07"],
    "BLK06_12": ["BLK05_11", "BLK07_13"],
    "BLK12_18": ["BLK11_17", "BLK13_19"],
    "BLK18_24": ["BLK17_23", "BLK19_01"],
    "FRI_GE12": ["FRI_GE10", "FRI_GE14"],
    "MON_LT6":  ["MON_LT4", "MON_LT8"],
}
def hr_neighbors(name):
    if name.startswith("HR"):
        h = int(name[2:])
        return ["HR%02d" % ((h - 1) % 24), "HR%02d" % ((h + 1) % 24)]
    return NEIGHBORS.get(name, [])

def run_mask(h1, eh, ed, name, yrs, tag=""):
    desc, fn = MASKS[name]
    allow = ~fn(eh, ed)
    m, tr = run(h1, "XAUUSD", 0.24, 3.5, 0.30, mlong=allow, mshort=allow)
    print("%-10s %-32s %s%s" % (name, desc, line(m, tr, yrs), tag))
    return m, tr

# ---------------------------------------------------------------- stages
def stage_base():
    h1 = load_gold()
    eh, ed, ts = time_arrays(h1)
    yrs = (ts.iloc[-1] - ts.iloc[0]).days / 365.25
    print("window: %s .. %s  (%.2f yrs, %d H1 bars)" % (ts.iloc[0], ts.iloc[-1], yrs, len(h1)))
    t0 = time.time()
    m, tr = run(h1, "XAUUSD", 0.24, 3.5, 0.30)
    print("BASELINE   %-32s %s   [%.0fs]" % ("(unfiltered)", line(m, tr, yrs), time.time() - t0))
    with open(CACHE, "wb") as f:
        pickle.dump({"metrics": m, "trades": tr, "yrs": yrs,
                     "t0": str(ts.iloc[0]), "t1": str(ts.iloc[-1])}, f)

    tdf = pd.DataFrame(tr)
    tdf["ets"] = pd.to_datetime(tdf["entry_ts"])
    tdf["hour"] = tdf["ets"].dt.hour
    tdf["dow"] = tdf["ets"].dt.dayofweek
    mid = ts.iloc[0] + (ts.iloc[-1] - ts.iloc[0]) / 2
    tdf["half"] = np.where(tdf["ets"] < mid, 0, 1)
    print("\nmid split at %s" % mid)

    print("\n-- entry hour diagnostic (n, netPnL$, and per-half netPnL) --")
    print("hour   n   netPnL   h1PnL   h2PnL")
    h1pnl, h2pnl = {}, {}
    for h in range(24):
        sub = tdf[tdf["hour"] == h]
        a = sub[sub["half"] == 0]["net_pnl"].sum(); b = sub[sub["half"] == 1]["net_pnl"].sum()
        h1pnl[h] = a; h2pnl[h] = b
        print("%02d   %4d  %+8.1f  %+7.1f  %+7.1f" % (h, len(sub), sub["net_pnl"].sum(), a, b))
    ra = pd.Series(h1pnl).rank(); rb = pd.Series(h2pnl).rank()
    print("hour-ranking spearman corr between halves: %.3f" % ra.corr(rb))
    print("sign agreement (same sign of hour PnL in both halves): %d/24"
          % sum(1 for h in range(24) if np.sign(h1pnl[h]) == np.sign(h2pnl[h]) and h1pnl[h] != 0))

    print("\n-- 6h-block diagnostic --")
    for lo in (0, 6, 12, 18):
        sub = tdf[(tdf["hour"] >= lo) & (tdf["hour"] < lo + 6)]
        a = sub[sub["half"] == 0]["net_pnl"].sum(); b = sub[sub["half"] == 1]["net_pnl"].sum()
        print("%02d-%02dh  n=%4d  netPnL=%+9.1f  h1=%+8.1f  h2=%+8.1f" % (lo, lo + 6, len(sub), sub["net_pnl"].sum(), a, b))

    print("\n-- day-of-week diagnostic --")
    for dw in range(7):
        sub = tdf[tdf["dow"] == dw]
        a = sub[sub["half"] == 0]["net_pnl"].sum(); b = sub[sub["half"] == 1]["net_pnl"].sum()
        print("%s  n=%4d  netPnL=%+9.1f  h1=%+8.1f  h2=%+8.1f" % (DAYN[dw], len(sub), sub["net_pnl"].sum(), a, b))

def stage_battery(names):
    h1 = load_gold()
    eh, ed, ts = time_arrays(h1)
    with open(CACHE, "rb") as f: base = pickle.load(f)
    yrs = base["yrs"]
    print("BASELINE   %-32s %s" % ("(unfiltered)", line(base["metrics"], base["trades"], yrs)))
    for name in names:
        run_mask(h1, eh, ed, name, yrs)

def stage_oos(names):
    h1 = load_gold()
    eh, ed, ts = time_arrays(h1)
    mid = ts.iloc[0] + (ts.iloc[-1] - ts.iloc[0]) / 2
    ia = (ts < mid).to_numpy(); ib = ~ia
    h1a = h1[ia].reset_index(drop=True); h1b = h1[ib].reset_index(drop=True)
    eha, eda, tsa = time_arrays(h1a); ehb, edb, tsb = time_arrays(h1b)
    ya = (tsa.iloc[-1] - tsa.iloc[0]).days / 365.25
    yb = (tsb.iloc[-1] - tsb.iloc[0]).days / 365.25
    print("half1: %s..%s (%.2fy)  half2: %s..%s (%.2fy)" % (tsa.iloc[0], tsa.iloc[-1], ya, tsb.iloc[0], tsb.iloc[-1], yb))
    ma, tra = run(h1a, "XAUUSD", 0.24, 3.5, 0.30)
    print("BASE-H1    %-32s %s" % ("(unfiltered half1)", line(ma, tra, ya)))
    mb, trb = run(h1b, "XAUUSD", 0.24, 3.5, 0.30)
    print("BASE-H2    %-32s %s" % ("(unfiltered half2)", line(mb, trb, yb)))
    for name in names:
        desc, fn = MASKS[name]
        run_mask(h1a, eha, eda, name, ya, tag="  [half1]")
        run_mask(h1b, ehb, edb, name, yb, tag="  [half2]")

def stage_quarters(names):
    h1 = load_gold()
    eh, ed, ts = time_arrays(h1)
    span = (ts.iloc[-1] - ts.iloc[0]) / 4
    edges = [ts.iloc[0] + span * k for k in range(5)]
    for q in range(4):
        sel = ((ts >= edges[q]) & (ts < edges[q + 1])).to_numpy() if q < 3 else (ts >= edges[q]).to_numpy()
        hq = h1[sel].reset_index(drop=True)
        ehq, edq, tsq = time_arrays(hq)
        yq = (tsq.iloc[-1] - tsq.iloc[0]).days / 365.25
        mq, trq = run(hq, "XAUUSD", 0.24, 3.5, 0.30)
        print("Q%d BASE    %s..%s  %s" % (q + 1, str(tsq.iloc[0])[:10], str(tsq.iloc[-1])[:10], line(mq, trq, yq)))
        for name in names:
            run_mask(hq, ehq, edq, name, yq, tag="  [Q%d]" % (q + 1))

if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else "base"
    if stage == "base":
        stage_base()
    elif stage == "battery":
        stage_battery(sys.argv[2:])
    elif stage == "oos":
        stage_oos(sys.argv[2:])
    elif stage == "quarters":
        stage_quarters(sys.argv[2:])
    else:
        raise SystemExit("unknown stage " + stage)
