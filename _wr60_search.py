"""Search the history for a chart pattern that wins >= 60% AND makes money.

READ THIS FIRST -- it decides the whole design.
Win rate is not a property of a pattern. It is set by the ruler: put the
take-profit close and the stop far away and RANDOM entries win 70%+.
So "WR >= 60%" alone is free, and every candidate here is judged against
RANDOM ENTRIES WITH THE SAME TP/SL, SAME SIDE, SAME TREND FILTER, SAME HALF
OF HISTORY. A pattern is worth something only if it beats that.

Carried from every search this session:
  * causal: decided on bar close, filled at the NEXT bar's open
  * non-overlapping: one position at a time per candidate
  * pessimistic fills: TP and SL touched in the same bar counts as SL, and
    a gap through the stop fills at the open, not at the stop price
  * long and short reported SEPARATELY, so gold's 1675->4400 drift and
    BTC's 30x cannot hide inside a net-long total
  * two-way: must pass in BOTH halves of history
  * the whole search is re-run on FAKE patterns (random signals with the
    same count and the same filter) -- the number of fake "survivors" is
    what luck alone produces, and the real count has to beat it
"""
from __future__ import annotations
import os, sys, time, zlib
from multiprocessing import Pool
import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view as swv

HERE = os.path.dirname(os.path.abspath(__file__))
DL = os.path.join(HERE, "download")
GOLD = os.path.join(DL, "xauusd-m15-bid-2013-01-01-2026-06-10.csv")
BTC = os.path.join(DL, "btcusdt-15m-binance-2017-08-17-2026-06-30.csv")

SERIES = [  # name, file, rule, hold bars, cost kind, cost value
    ("GOLD M15", GOLD, "15min", 48, "abs", 0.25),
    ("GOLD H1",  GOLD, "1h",    24, "abs", 0.25),
    ("GOLD H4",  GOLD, "4h",    18, "abs", 0.25),
    ("GOLD D1",  GOLD, "1D",    10, "abs", 0.25),
    ("BTC M15",  BTC,  "15min", 48, "pct", 0.0012),
    ("BTC H1",   BTC,  "1h",    24, "pct", 0.0012),
    ("BTC H4",   BTC,  "4h",    18, "pct", 0.0012),
    ("BTC D1",   BTC,  "1D",    10, "pct", 0.0012),
]
GEOMS = [(0.5, 1.0), (0.5, 1.5), (0.75, 1.5), (1.0, 1.0),
         (1.0, 1.5), (1.0, 2.0), (1.5, 2.0)]          # (TP, SL) in ATR
MIN_N, WR_BAR, Z_BAR = 30, 0.60, 2.0
N_FAKE = 5
WARM = 210


def load(path, rule, drop_flat):
    d = pd.read_csv(path, low_memory=False)
    d.columns = [str(c).strip().lower() for c in d.columns]
    t = d.columns[0]
    num = pd.to_numeric(d[t], errors="coerce")
    if num.notna().mean() > 0.9:                 # epoch ms, NOT nanoseconds
        d = d[num.notna()].copy()
        d["dt"] = pd.to_datetime(num[num.notna()].astype("int64"),
                                 unit="ms", utc=True)
    else:
        d["dt"] = pd.to_datetime(d[t], utc=True, errors="coerce")
    for col in ("open", "high", "low", "close"):
        d[col] = pd.to_numeric(d[col], errors="coerce")
    d = d.dropna(subset=["dt", "open", "high", "low", "close"])
    d = d.set_index("dt").sort_index()
    if drop_flat:        # the gold feed fills its daily break with flat bars
        d = d[d["high"] > d["low"]]
    if rule != "15min":
        d = d.resample(rule).agg({"open": "first", "high": "max",
                                  "low": "min", "close": "last"}).dropna()
    return d


def indicators(d):
    o, h, l, c = (d[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    pc = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    atr = pd.Series(tr).rolling(14).mean().to_numpy()
    cs = pd.Series(c)
    ema20 = cs.ewm(span=20, adjust=False).mean().to_numpy()
    ema200 = cs.ewm(span=200, adjust=False).mean().to_numpy()
    dlt = cs.diff()
    up = dlt.clip(lower=0).ewm(alpha=0.5, adjust=False).mean()
    dn = (-dlt.clip(upper=0)).ewm(alpha=0.5, adjust=False).mean()
    rsi2 = (100 - 100 / (1 + up / dn.replace(0, np.nan))).fillna(50).to_numpy()
    sd20 = cs.rolling(20).std().to_numpy()
    ma20 = cs.rolling(20).mean().to_numpy()
    hi20 = pd.Series(h).rolling(20).max().shift(1).to_numpy()
    lo20 = pd.Series(l).rolling(20).min().shift(1).to_numpy()
    return dict(o=o, h=h, l=l, c=c, atr=atr, ema20=ema20, ema200=ema200,
                rsi2=rsi2, up_bb=ma20 + 2 * sd20, lo_bb=ma20 - 2 * sd20,
                hi20=hi20, lo20=lo20)


def signals(x):
    o, h, l, c = x["o"], x["h"], x["l"], x["c"]
    n = len(c)

    def lag(a, k):
        out = np.full(n, np.nan)
        out[k:] = a[:-k]
        return out
    o1, c1, h1, l1, h2, l2 = lag(o, 1), lag(c, 1), lag(h, 1), lag(l, 1), lag(h, 2), lag(l, 2)
    rng = h - l
    body = np.abs(c - o)
    lower = np.minimum(o, c) - l
    upper = h - np.maximum(o, c)
    S = {}
    S["engulf"] = ((c1 < o1) & (c > o) & (c >= o1) & (o <= c1) & (body > 0),
                   (c1 > o1) & (c < o) & (c <= o1) & (o >= c1) & (body > 0))
    S["pinbar"] = ((rng > 0) & (lower >= 2 * body) & (lower >= 0.6 * rng) & (upper <= 0.2 * rng),
                   (rng > 0) & (upper >= 2 * body) & (upper >= 0.6 * rng) & (lower <= 0.2 * rng))
    inside = (h1 < h2) & (l1 > l2)
    S["inside_break"] = (inside & (c > h2), inside & (c < l2))
    for N in (3, 4, 5):
        dn_ = np.ones(n, bool); up_ = np.ones(n, bool)
        for k in range(N):
            ck, ck1 = lag(c, k) if k else c, lag(c, k + 1)
            dn_ &= ck < ck1
            up_ &= ck > ck1
        S[f"{N}_in_a_row_fade"] = (dn_, up_)
    for T in (5, 10, 15):
        S[f"rsi2<{T}"] = (x["rsi2"] < T, x["rsi2"] > 100 - T)
    for k in (1.5, 2.0, 2.5):
        S[f"stretch{k}atr"] = (c < x["ema20"] - k * x["atr"], c > x["ema20"] + k * x["atr"])
    S["bollinger_fade"] = (c < x["lo_bb"], c > x["up_bb"])
    S["breakout20"] = (c > x["hi20"], c < x["lo20"])
    for key in S:
        L_, Sh_ = S[key]
        L_ = np.nan_to_num(L_.astype(float)).astype(bool)
        Sh_ = np.nan_to_num(Sh_.astype(float)).astype(bool)
        L_[:WARM] = False; Sh_[:WARM] = False
        S[key] = (L_, Sh_)
    return S


def outcomes(idx, side, x, H, ckind, cval):
    """R per trade for every geometry, plus exit bar, for entries at idx+1."""
    o, h, l, c, atr = x["o"], x["h"], x["l"], x["c"], x["atr"]
    n = len(c)
    idx = idx[(idx + 1 + H <= n - 1) & ~np.isnan(atr[idx]) & (atr[idx] > 0)]
    if len(idx) == 0:
        return idx, {}
    E = idx + 1
    ent, a = o[E], atr[idx]
    HW, LW, OW = swv(h, H)[E], swv(l, H)[E], swv(o, H)[E]
    last_c = c[E + H - 1]
    cost = cval if ckind == "abs" else ent * cval
    res = {}
    for tp, sl in GEOMS:
        if side > 0:
            tpx, slx = ent + tp * a, ent - sl * a
            htp, hsl = HW >= tpx[:, None], LW <= slx[:, None]
        else:
            tpx, slx = ent - tp * a, ent + sl * a
            htp, hsl = LW <= tpx[:, None], HW >= slx[:, None]
        ftp = np.where(htp.any(1), htp.argmax(1), H)
        fsl = np.where(hsl.any(1), hsl.argmax(1), H)
        is_sl = (fsl < H) & (fsl <= ftp)              # same bar -> stop
        is_tp = (ftp < H) & (ftp < fsl)
        k = np.where(is_sl, fsl, 0)
        gap_open = np.take_along_axis(OW, k[:, None], 1)[:, 0]
        if side > 0:
            sl_fill = np.minimum(slx, gap_open)       # gap through the stop
        else:
            sl_fill = np.maximum(slx, gap_open)
        pnl = np.where(is_tp, tp * a,
              np.where(is_sl, (sl_fill - ent) * side, (last_c - ent) * side))
        R = (pnl - cost) / (sl * a)
        off = np.where(is_sl, fsl, np.where(is_tp, ftp, H - 1))
        res[(tp, sl)] = (R, E + off)
    return idx, res


def non_overlap(E, ex):
    keep, last = [], -1
    for j in range(len(E)):
        if E[j] > last:
            keep.append(j)
            last = ex[j]
    return np.asarray(keep, int)


def cell_stats(idx, side, x, H, ck, cv):
    idx, res = outcomes(idx, side, x, H, ck, cv)
    out = {}
    for g, (R, ex) in res.items():
        k = non_overlap(idx + 1, ex)
        r = R[k]
        if len(r) == 0:
            continue
        out[g] = (len(r), float((r > 0).mean()), float(r.mean()))
    return out


def run_series(spec):
    name, path, rule, H, ck, cv = spec
    t0 = time.time()
    rng = np.random.default_rng(zlib.crc32(name.encode()))   # hash() is salted per process
    d = load(path, rule, drop_flat=name.startswith("GOLD"))
    x = indicators(d)
    n = len(x["c"])
    mid = n // 2
    years = (d.index[-1] - d.index[0]).days / 365.25
    halves = [(WARM, mid), (mid, n)]
    trend_L = x["c"] > x["ema200"]
    trend_S = x["c"] < x["ema200"]

    # random-entry null per (half, filter, side, geometry)
    null = {}
    for hi, (a0, a1) in enumerate(halves):
        for filt in ("none", "trend"):
            for side in (1, -1):
                mask = np.zeros(n, bool); mask[a0:a1 - H - 2] = True
                if filt == "trend":
                    mask &= trend_L if side > 0 else trend_S
                pool = np.flatnonzero(mask)
                if len(pool) < 100:
                    continue
                pick = np.sort(rng.choice(pool, min(4000, len(pool)), replace=False))
                idx, res = outcomes(pick, side, x, H, ck, cv)
                for g, (R, _) in res.items():
                    null[(hi, filt, side, g)] = (float((R > 0).mean()),
                                                 float(R.mean()), float(R.std()))

    sig = signals(x)

    def evaluate(masks):
        rows = []
        for key, (Lm, Sm) in masks.items():
            for filt in ("none", "trend"):
                for side, m in ((1, Lm), (-1, Sm)):
                    mm = m & (trend_L if side > 0 else trend_S) if filt == "trend" else m
                    per_half = []
                    for hi, (a0, a1) in enumerate(halves):
                        idx = np.flatnonzero(mm[a0:a1]) + a0
                        per_half.append(cell_stats(idx, side, x, H, ck, cv))
                    for g in GEOMS:
                        rec = []
                        for hi in (0, 1):
                            st = per_half[hi].get(g)
                            nl = null.get((hi, filt, side, g))
                            if st is None or nl is None:
                                rec = None; break
                            nn, wr, ex = st
                            z = (ex - nl[1]) / (nl[2] / np.sqrt(nn)) if nl[2] > 0 else 0.0
                            rec.append((nn, wr, ex, z, nl[0], nl[1]))
                        if rec:
                            rows.append((key, filt, side, g, rec))
        return rows

    def verdicts(rows):
        wr_only = wr_prof = surv = 0
        winners = []
        for key, filt, side, g, rec in rows:
            ok = [r[0] >= MIN_N and r[1] >= WR_BAR and r[2] > 0 and r[2] > r[5] for r in rec]
            strong = [ok[i] and rec[i][3] >= Z_BAR for i in (0, 1)]
            if rec[0][0] >= MIN_N and rec[0][1] >= WR_BAR:
                wr_only += 1
            if ok[0]:
                wr_prof += 1
            if ok[0] and ok[1] and (strong[0] or strong[1]):
                surv += 1
                winners.append((key, filt, side, g, rec))
        return wr_only, wr_prof, surv, winners

    real_rows = evaluate(sig)
    real = verdicts(real_rows)

    fakes = []
    for _ in range(N_FAKE):
        fm = {}
        for key, (Lm, Sm) in sig.items():
            fl, fs = np.zeros(n, bool), np.zeros(n, bool)
            for (a0, a1) in halves:
                for src, dst in ((Lm, fl), (Sm, fs)):
                    cnt = int(src[a0:a1].sum())
                    if cnt:
                        dst[rng.choice(np.arange(a0, a1), min(cnt, a1 - a0), replace=False)] = True
            fm[key] = (fl, fs)
        fakes.append(verdicts(evaluate(fm))[2])

    # what a random entry wins, by geometry (full-sample illustration)
    wr_by_geom = {}
    for g in GEOMS:
        v = [null[(hi, "none", s, g)] for hi in (0, 1) for s in (1, -1)
             if (hi, "none", s, g) in null]
        if v:
            wr_by_geom[g] = (np.mean([q[0] for q in v]), np.mean([q[1] for q in v]))

    return dict(name=name, bars=n, years=years, cands=len(real_rows),
                real=real, fakes=fakes, wr_by_geom=wr_by_geom,
                secs=time.time() - t0)


def main():
    print("=" * 100)
    print(" WR >= 60% AND PROFITABLE -- pattern search across gold and BTC")
    print(f" {len(GEOMS)} TP/SL shapes x 16 patterns x 2 filters x long/short x"
          f" {len(SERIES)} charts;  judged vs random entries + {N_FAKE} fake-pattern re-runs")
    print("=" * 100, flush=True)
    with Pool(min(8, len(SERIES))) as p:
        results = p.map(run_series, SERIES)

    print("\n" + "=" * 100)
    print(" STEP 1 -- WIN RATE IS FREE: what RANDOM entries win, by TP/SL shape")
    print("=" * 100)
    print(f"\n{'chart':>10}" + "".join(f"{f'TP{tp}/SL{sl}':>13}" for tp, sl in GEOMS))
    for r in results:
        line = f"{r['name']:>10}"
        for g in GEOMS:
            wr, ex = r["wr_by_geom"].get(g, (np.nan, np.nan))
            line += f"{wr*100:>6.0f}% {ex:>+5.2f}R"
        print(line)
    print("\n  Each cell: win rate and average R of a RANDOM entry after cost.")
    print("  High win rates appear with no pattern at all -- and the R beside")
    print("  them is what those win rates are worth.")

    print("\n" + "=" * 100)
    print(" STEP 2 -- THE SEARCH, REAL PATTERNS vs FAKE PATTERNS")
    print("=" * 100)
    print(f"\n{'chart':>10}{'bars':>9}{'years':>7}{'cands':>7}"
          f"{'WR>=60%':>9}{'+profit':>9}{'SURVIVE':>9}"
          f"{'fake survivors (5 runs)':>26}{'secs':>6}")
    tot_real = tot_fake = 0
    all_win = []
    for r in results:
        wr_only, wr_prof, surv, winners = r["real"]
        tot_real += surv
        tot_fake += np.mean(r["fakes"])
        for w in winners:
            all_win.append((r["name"],) + w)
        print(f"{r['name']:>10}{r['bars']:>9,}{r['years']:>7.1f}{r['cands']:>7}"
              f"{wr_only:>9}{wr_prof:>9}{surv:>9}"
              f"{str(r['fakes']):>26}{r['secs']:>6.0f}")
    print(f"\n  real survivors {tot_real}   vs   fake survivors per run"
          f" {tot_fake:.1f} on average")
    print("  WR>=60% = win rate cleared in the 1st half; +profit = also net"
          " positive and above random;")
    print("  SURVIVE = both of those in BOTH halves, and z>=2 vs random in"
          " at least one.")

    print("\n" + "=" * 100)
    print(" STEP 3 -- EVERY SURVIVOR, both halves shown")
    print("=" * 100)
    if not all_win:
        print("\n  none")
    else:
        print(f"\n{'chart':>9}{'pattern':>20}{'filter':>7}{'side':>6}{'TP/SL':>9}"
              f"{'n1':>5}{'WR1':>6}{'R1':>7}{'z1':>6}{'n2':>5}{'WR2':>6}{'R2':>7}{'z2':>6}")
        for nm, key, filt, side, g, rec in sorted(all_win, key=lambda w: -(w[5][0][3] + w[5][1][3])):
            a, b = rec
            print(f"{nm:>9}{key:>20}{filt:>7}{('LONG' if side>0 else 'SHORT'):>6}"
                  f"{f'{g[0]}/{g[1]}':>9}"
                  f"{a[0]:>5}{a[1]*100:>5.0f}%{a[2]:>+7.3f}{a[3]:>6.1f}"
                  f"{b[0]:>5}{b[1]*100:>5.0f}%{b[2]:>+7.3f}{b[3]:>6.1f}")
    print("\n" + "=" * 100)
    return 0


if __name__ == "__main__":
    sys.exit(main())
