#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_flow_signal_test.py -- do funding rate, open interest and the long/short
                        ratio predict BTC direction at all?

Every input chart_ai currently gets is derived from price: EMAs, ATR, the
20-bar range, the candles themselves. Adding more of the same measured
nothing -- the exhaustion A/B put four fresh stretch figures in front of
the models and the anti-predictive signature simply flattened to a coin
flip (+2.5, p=0.75) rather than turning into an edge.

These three are different in kind. They are not computed from price:

    funding rate      what longs pay shorts to hold -- crowd positioning cost
    open interest     how much money is in the market, rising or leaving
    long/short ratio  how the account base is actually positioned

Before any of it goes near a bot, the honest question is whether it
predicts anything on its own. That is answered with arithmetic on free
public data and ZERO AI calls, which is why this runs first.

METHOD, mirroring _signal_accuracy.py so the numbers are comparable:
  - the signal at time T is known at T (funding is settled, OI/LS are
    observations), so predicting T -> T+H carries no lookahead
  - samples are split into quintiles by signal value; a real relationship
    should be MONOTONIC across them, not a lump in one bucket
  - the number reported is the hit rate MINUS the base rate on the same
    samples, because in a market that drifted up 60% of the time a
    permanently-long rule scores 60% while predicting nothing
  - history splits in half: any pattern is found on the FIRST half and
    scored on the SECOND

Prior art worth remembering: funding_contrarian was built on exactly this
intuition and lost 1,319.63 over 2 trades before being stopped. That bot
was never measured this way first. This is that missing step.

Usage:  python _flow_signal_test.py [symbol] [horizon_hours]
"""
import json
import math
import subprocess
import sys
import urllib.error
import urllib.request

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
HORIZON_H = int(sys.argv[2]) if len(sys.argv) > 2 else 8
FAPI = "https://fapi.binance.com"


def get(path, **params):
    """urllib first, curl as fallback.

    This machine sits behind a TLS-intercepting proxy: Python ships its own
    CA bundle and rejects the substituted certificate, while curl uses the
    system keychain and succeeds. Falling back to curl keeps full
    certificate verification -- it just uses the store that actually has
    the proxy root. Never disable verification to get past this.
    """
    q = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{FAPI}{path}?{q}"
    try:
        with urllib.request.urlopen(url, timeout=25) as r:
            return json.loads(r.read().decode())
    except (urllib.error.URLError, OSError):
        r = subprocess.run(["curl", "-sS", "--max-time", "30", url],
                           capture_output=True, text=True)
        if r.returncode != 0 or not r.stdout.strip():
            raise RuntimeError(f"fetch failed for {path}: "
                               f"{(r.stderr or 'empty response')[:200]}")
        return json.loads(r.stdout)


def klines(interval, limit):
    """[openTime_ms, ..., close, ...] -> {ms: close}"""
    rows = get("/fapi/v1/klines", symbol=SYMBOL, interval=interval, limit=limit)
    return {int(r[0]): float(r[4]) for r in rows}


def fwd_return(px, t_ms, horizon_ms):
    """Close-to-close return from t to t+horizon, or None if unavailable.
    Nearest bar within one hour, so a missing candle drops the sample
    instead of silently pairing the wrong two prices."""
    tol = 3600_000
    a = _nearest(px, t_ms, tol)
    b = _nearest(px, t_ms + horizon_ms, tol)
    if a is None or b is None or a <= 0:
        return None
    return (b - a) / a


def _nearest(px, t, tol):
    if t in px:
        return px[t]
    best, bd = None, None
    for k, v in px.items():
        d = abs(k - t)
        if d <= tol and (bd is None or d < bd):
            best, bd = v, d
    return best


def binom_p(k, n, p0):
    if n == 0:
        return 1.0
    sd = math.sqrt(p0 * (1 - p0) * n)
    if sd == 0:
        return 1.0
    return math.erfc(abs(k - p0 * n) / sd / math.sqrt(2))


def analyse(name, pairs, note=""):
    """pairs: [(signal_value, forward_return)]. Quintiles + train/test."""
    pairs = [(s, r) for s, r in pairs if r is not None]
    n = len(pairs)
    print("\n" + "=" * 78)
    print(f" {name}   n={n}   horizon={HORIZON_H}h" + (f"   [{note}]" if note else ""))
    print("=" * 78)
    if n < 40:
        print("  too few samples to say anything")
        return None
    base_up = sum(1 for _, r in pairs if r > 0) / n
    print(f"  base rate: price rose in {100*base_up:.1f}% of windows")
    print(f"  {'quintile':<12}{'signal range':>26}{'n':>6}{'up%':>8}"
          f"{'edge':>8}{'mean ret':>10}")
    print("  " + "-" * 74)

    order = sorted(pairs, key=lambda x: x[0])
    q = max(1, n // 5)
    edges = []
    for i in range(5):
        lo = i * q
        hi = (i + 1) * q if i < 4 else n
        chunk = order[lo:hi]
        if not chunk:
            continue
        up = sum(1 for _, r in chunk if r > 0)
        wr = up / len(chunk)
        mean = sum(r for _, r in chunk) / len(chunk)
        edges.append(100 * (wr - base_up))
        print(f"  Q{i+1:<11}{chunk[0][0]:>12.6f}..{chunk[-1][0]:<12.6f}"
              f"{len(chunk):>6}{100*wr:>7.1f}%{100*(wr-base_up):>+8.1f}"
              f"{100*mean:>+9.2f}%")

    spread = edges[-1] - edges[0] if len(edges) >= 2 else 0.0
    mono = all(edges[i] <= edges[i+1] for i in range(len(edges)-1)) or \
           all(edges[i] >= edges[i+1] for i in range(len(edges)-1))
    print("  " + "-" * 74)
    print(f"  Q5 minus Q1 edge: {spread:+.1f} points"
          f"   monotonic across quintiles: {'YES' if mono else 'no'}")

    # train on the first half by TIME (pairs arrive in time order), score
    # the extreme-quintile rule on the second
    mid = n // 2
    tr, te = pairs[:mid], pairs[mid:]
    tr_sorted = sorted(tr, key=lambda x: x[0])
    cut_lo = tr_sorted[max(0, len(tr)//5 - 1)][0]
    cut_hi = tr_sorted[min(len(tr)-1, 4*len(tr)//5)][0]
    tr_base = sum(1 for _, r in tr if r > 0) / max(len(tr), 1)
    tr_hi = [r for s, r in tr if s >= cut_hi]
    direction = -1 if (sum(1 for r in tr_hi if r > 0) / max(len(tr_hi), 1)) < tr_base else 1
    te_base = sum(1 for _, r in te if r > 0) / max(len(te), 1)
    hits = tot = 0
    for s, r in te:
        if s >= cut_hi:
            pred = direction
        elif s <= cut_lo:
            pred = -direction
        else:
            continue
        tot += 1
        if (r > 0 and pred > 0) or (r < 0 and pred < 0):
            hits += 1
    if tot >= 20:
        wr = hits / tot
        exp = te_base if direction > 0 else (1 - te_base)
        p = binom_p(hits, tot, 0.5)
        print(f"\n  OUT-OF-SAMPLE (rule fitted on first half, scored on second)")
        print(f"    traded {tot} of {len(te)} windows   win {100*wr:.1f}%"
              f"   vs coin flip 50.0%   p={p:.3f}")
        verdict = ("USABLE" if (wr - 0.5) * 100 >= 4 and p < 0.10 else
                   "no edge")
        print(f"    -> {verdict}")
        return {"name": name, "n": n, "spread": spread, "mono": mono,
                "oos_wr": wr, "oos_n": tot, "p": p, "verdict": verdict}
    print("\n  out-of-sample sample too small to score")
    return None


def main():
    hz = HORIZON_H * 3600_000
    print(f"fetching {SYMBOL} ... (free public endpoints, no API key, no AI)")
    px1h = klines("1h", 1500)
    print(f"  price bars: {len(px1h)}")

    # --- funding: 8h cadence, deepest history of the three ---
    fr = get("/fapi/v1/fundingRate", symbol=SYMBOL, limit=1000)
    fpairs = [(float(x["fundingRate"]),
               fwd_return(px1h, int(x["fundingTime"]), hz)) for x in fr]
    r1 = analyse("FUNDING RATE (positive = longs paying shorts)", fpairs,
                 "~333 days")

    # --- open interest: 1h, only ~30 days of history available ---
    oi = get("/futures/data/openInterestHist", symbol=SYMBOL, period="1h", limit=500)
    ovals = [(float(x["sumOpenInterest"]), int(x["timestamp"])) for x in oi]
    # level means nothing across a growing market; the CHANGE is the signal
    opairs = []
    for i in range(6, len(ovals)):
        prev = ovals[i-6][0]
        if prev > 0:
            opairs.append(((ovals[i][0] - prev) / prev,
                           fwd_return(px1h, ovals[i][1], hz)))
    r2 = analyse("OPEN INTEREST 6h CHANGE (positive = money entering)",
                 opairs, "~20 days only")

    ls = get("/futures/data/globalLongShortAccountRatio", symbol=SYMBOL,
             period="1h", limit=500)
    lpairs = [(float(x["longShortRatio"]),
               fwd_return(px1h, int(x["timestamp"]), hz)) for x in ls]
    r3 = analyse("LONG/SHORT ACCOUNT RATIO (>1 = crowd is long)", lpairs,
                 "~20 days only")

    print("\n" + "=" * 78)
    print("  SUMMARY")
    print("=" * 78)
    any_use = False
    for r in (r1, r2, r3):
        if not r:
            continue
        print(f"  {r['name'][:44]:<46}{r['verdict']:<10}"
              f"OOS {100*r['oos_wr']:.1f}% on {r['oos_n']}  p={r['p']:.3f}")
        if r["verdict"] == "USABLE":
            any_use = True
    print()
    if not any_use:
        print("  Nothing here predicts direction out of sample. Feeding these")
        print("  to the AI would add data, not information -- the same result")
        print("  the exhaustion A/B produced. Do NOT build a stream on this.")
    else:
        print("  Something survived. Next step is NOT to trade it: confirm on")
        print("  a longer history and on the exchange we actually trade before")
        print("  any of it reaches a live bot.")
    print("\n  Note: OI and long/short carry only ~20 days of history from this")
    print("  endpoint, so their verdicts are weaker than funding's by design.")


if __name__ == "__main__":
    main()
