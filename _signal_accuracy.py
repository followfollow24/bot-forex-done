#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_signal_accuracy.py -- does the AI's direction call predict anything?

27 live trades, 0 wins, and three explanations we could not separate:
the AI picks the wrong direction; the stop gets clipped by noise; or the
market simply went one way for the whole sample. Trade outcomes conflate
all three. This isolates the first one.

Method: replay history through the EXACT live pipeline -- the same
payload builders, the same rendered chart, the same prompt, the same
cross_check_signal -- ask both models what they would have done, then
look at what price actually did next. No stops, no targets, no costs.
Just: was the direction right?

NO LOOKAHEAD. Every sample is built from bars strictly at or before the
decision bar, including the H4/H1/daily context, which is filtered by
timestamp rather than by index. Getting that wrong would manufacture an
edge out of nothing, so it is the one thing worth being fussy about.

The number that matters is not the raw hit rate but the hit rate MINUS
the base rate on the same samples. If price rose in 60% of the windows,
a 60% long-biased model has predicted nothing. Both are reported, with a
significance test on the difference.

Reading the result:
    edge ~= 0            no directional information -> stop trading it
    edge clearly < 0     systematically inverted -> inverting has a basis
    edge clearly > 0     the signal works; the loss came from execution

Usage (on the VPS):  python _signal_accuracy.py [symbol] [samples] [horizon_bars]
  e.g.               python _signal_accuracy.py XAUUSDc 40 16
"""
import math
import os
import sys
import time
from datetime import datetime

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)")
    sys.exit(1)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chart_ai_trader as cat
from news_gemini_bot import render_chart_png

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "XAUUSDc"
N_SAMPLES = int(sys.argv[2]) if len(sys.argv) > 2 else 40
HORIZON = int(sys.argv[3]) if len(sys.argv) > 3 else 16     # 16 M15 bars = 4h
# 4th arg "exh" swaps in the exhaustion prompt + stretch figures, to A/B
# against the measured baseline (BTC consensus edge -25.2 points, p=0.004).
# Everything else about the run is held identical -- same symbol, same
# sample indices, same horizon -- so any change in edge is attributable to
# the prompt and not to a different slice of history.
EXHAUSTION = len(sys.argv) > 4 and sys.argv[4].lower().startswith("exh")
LOOKBACK = cat.CHART_BARS                                    # 160, as live
STRIDE_MIN = 24                                              # >=6h apart


def atr14(rates, i):
    trs = []
    for j in range(i - 13, i + 1):
        h, l = float(rates[j]["high"]), float(rates[j]["low"])
        pc = float(rates[j - 1]["close"])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs)


def to_ohlcv(rates_slice):
    """MT5 rate rows -> the [ts_ms, o, h, l, c, v] shape the live builders
    expect, so the replay feeds them exactly what fetch_ohlcv would."""
    return [[int(r["time"]) * 1000, float(r["open"]), float(r["high"]),
             float(r["low"]), float(r["close"]), float(r["tick_volume"])]
            for r in rates_slice]


M15_SEC = 900


def htf_series(htf_rates, m15, i, period_sec, keep=140):
    """Higher-timeframe history as it looked AT bar i -- the one place this
    script could quietly cheat, so it is built explicitly.

    Selecting H4 bars by `time <= t` is wrong: the H4 bar stamped 08:00 is
    still forming at 10:15, but the stored row holds the finished 08:00-12:00
    high, low and close. Feeding that to build_htf_context would let the
    model see four hours into its own future, and build_key_levels'
    today_high/today_low would leak the whole rest of the day. An edge
    produced that way is pure artefact.

    So: keep only bars whose period has fully CLOSED by `now`, then rebuild
    the still-forming bar by aggregating the M15 bars since that close --
    which is exactly the partial bar the live bot reads from MT5. Same
    information, no leak.

    `now` is the END of decision bar i: the bot acts on closed M15 bars, so
    everything up to t+900 is legitimately known at decision time.
    """
    now = int(m15[i]["time"]) + M15_SEC
    closed = [r for r in htf_rates if int(r["time"]) + period_sec <= now]
    if not closed:
        return []
    out = to_ohlcv(closed[-keep:])
    start = int(closed[-1]["time"]) + period_sec
    part = [r for r in m15[:i + 1] if int(r["time"]) >= start]
    if part:
        out.append([start * 1000,
                    float(part[0]["open"]),
                    max(float(r["high"]) for r in part),
                    min(float(r["low"]) for r in part),
                    float(part[-1]["close"]),
                    sum(float(r["tick_volume"]) for r in part)])
    return out


RETRIES = 3
RETRY_SLEEP = 8.0


def call_with_retry(name, fn, *args):
    """Returns the model's answer, or None once the retries are spent.
    Never raises: one dead sample must not end a run that has already
    spent real API calls on the samples before it."""
    last = ""
    for attempt in range(RETRIES):
        try:
            return fn(*args)
        except Exception as e:
            last = str(e)[:70] or type(e).__name__
            if attempt < RETRIES - 1:
                time.sleep(RETRY_SLEEP * (attempt + 1))
    print(f"    {name} gave up after {RETRIES} tries: {last}")
    return None


def binom_p(k, n, p0):
    """Two-sided normal approximation. Enough to tell 'indistinguishable
    from the base rate' from 'clearly different'; not a substitute for a
    proper test at tiny n, which is why the sample count is printed."""
    if n == 0:
        return 1.0
    sd = math.sqrt(p0 * (1 - p0) * n)
    if sd == 0:
        return 1.0
    z = abs(k - p0 * n) / sd
    return math.erfc(z / math.sqrt(2))


def main():
    # chart_ai_trader calls load_dotenv() at import, so the keys are
    # already in os.environ by the time we get here.
    if not os.environ.get("GEMINI_API_KEY"):
        print("[ERROR] GEMINI_API_KEY not set (expected in .env beside this file)")
        sys.exit(1)
    if not mt5.initialize():
        print("[ERROR] MT5 init failed")
        sys.exit(1)

    need = LOOKBACK + N_SAMPLES * STRIDE_MIN + HORIZON + 50
    m15 = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M15, 0, need)
    h4 = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_H4, 0, 3000)
    h1 = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_H1, 0, 3000)
    d1 = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_D1, 0, 400)
    if m15 is None or len(m15) < need * 0.6:
        print(f"[ERROR] not enough M15 bars for {SYMBOL}")
        mt5.shutdown()
        return

    gkeys = os.environ.get("GEMINI_API_KEY", "")
    okeys = os.environ.get("OPENAI_API_KEY", "")
    gmodel = os.environ.get("GEMINI_MODEL", "gemini-flash-lite-latest")
    omodel = os.environ.get("OPENAI_MODEL", "gpt-5-mini")

    idxs = []
    i = LOOKBACK + 20
    while i < len(m15) - HORIZON - 1 and len(idxs) < N_SAMPLES:
        idxs.append(i)
        i += STRIDE_MIN

    cat.EXHAUSTION_MODE = EXHAUSTION
    print("=" * 88)
    print(f" SIGNAL ACCURACY -- {SYMBOL} M15, {len(idxs)} samples, "
          f"{HORIZON}-bar ({HORIZON/4:.0f}h) horizon")
    which = ("EXHAUSTION (continuation-vs-exhaustion, stretch figures, "
             "HTF advisory)" if EXHAUSTION
             else "BASELINE (trend continuation, HTF veto)")
    print(f" prompt: {which}")
    print(f" replayed through the LIVE pipeline; no stops, no costs, no lookahead")
    print("=" * 88)
    print(f"{'when':<15}{'gemini':<8}{'openai':<8}{'consensus':<11}"
          f"{'moved':<8}{'g?':<4}{'o?':<4}{'c?':<4}")
    print("-" * 88)

    stat = {k: [0, 0] for k in ("gemini", "openai", "consensus")}  # [hit, calls]
    lost = {"gemini": 0, "openai": 0}
    ups = 0
    n_done = 0

    for i in idxs:
        try:
            window = m15[i - LOOKBACK + 1:i + 1]
            atr = atr14(m15, i)
            if not atr or atr <= 0:
                continue
            price = float(m15[i]["close"])
            t = int(m15[i]["time"])

            candles = to_ohlcv(window)
            payload = cat.build_market_payload(candles, atr)
            payload["htf"] = cat.build_htf_context(
                htf_series(h4, m15, i, 4 * 3600),
                htf_series(h1, m15, i, 3600))
            payload["keylevels"] = cat.build_key_levels(
                htf_series(d1, m15, i, 24 * 3600, keep=30), price)
            if EXHAUSTION:
                payload["exhaustion"] = cat.build_exhaustion_context(
                    candles, atr)
            ptext = cat.format_payload(SYMBOL, payload)
            png = render_chart_png(candles, SYMBOL, "")

            # Gemini's 503 rate ran near 40% during the invert run. Without
            # retries those samples vanish, and a sample that vanishes
            # because the API was busy is not a random one -- busy periods
            # cluster by time of day, so silently dropping them biases the
            # measurement. Retry, then report what was still lost.
            g = call_with_retry("gemini", cat.gemini_chart_signal, gkeys,
                                gmodel, png, SYMBOL, price, len(candles),
                                atr, ptext)
            o = (call_with_retry("openai", cat.openai_chart_signal, okeys,
                                 omodel, png, SYMBOL, price, len(candles),
                                 atr, ptext) if okeys else None)
            if g is None:
                lost["gemini"] += 1
            if okeys and o is None:
                lost["openai"] += 1
            if g is None and o is None:
                continue

            future = float(m15[i + HORIZON]["close"])
            moved = "up" if future > price else "down"
            if future > price:
                ups += 1
            n_done += 1

            row = {}
            for name, r in (("gemini", g), ("openai", o)):
                d = str((r or {}).get("decision", "")).strip().upper()
                row[name] = d if d in ("LONG", "SHORT", "WAIT") else "-"
                if d in ("LONG", "SHORT"):
                    stat[name][1] += 1
                    want = "up" if d == "LONG" else "down"
                    if want == moved:
                        stat[name][0] += 1

            cons = cat.cross_check_signal(g or {}, o or {}, price, atr)
            row["consensus"] = cons["decision"] if cons else "-"
            if cons:
                stat["consensus"][1] += 1
                if ("up" if cons["signal"] == "long" else "down") == moved:
                    stat["consensus"][0] += 1

            mark = lambda d: ("" if d not in ("LONG", "SHORT") else
                              ("Y" if ("up" if d == "LONG" else "down") == moved else "n"))
            print(f"{datetime.fromtimestamp(t):%m-%d %H:%M}  "
                  f"{row['gemini']:<8}{row['openai']:<8}{row['consensus']:<11}"
                  f"{moved:<8}{mark(row['gemini']):<4}{mark(row['openai']):<4}"
                  f"{mark(row['consensus']):<4}")
        except Exception as e:
            print(f"  sample {i} error: {str(e)[:80]}")

    print("-" * 88)
    if n_done == 0:
        print("  no samples completed")
        mt5.shutdown()
        return

    base_up = ups / n_done
    print(f"  samples resolved       : {n_done} of {len(idxs)}")
    if lost["gemini"] or lost["openai"]:
        print(f"  lost to API failures   : gemini {lost['gemini']}, "
              f"openai {lost['openai']}  (these cluster by time of day -- "
              f"if large, the sample is not random)")
    print(f"  price rose in          : {100*base_up:.1f}%  "
          f"(a permanently-LONG model scores this by default)")
    print()
    print(f"{'model':<12}{'calls':>7}{'hits':>7}{'hit%':>8}{'base%':>8}"
          f"{'edge':>8}{'p':>8}")
    for name in ("gemini", "openai", "consensus"):
        hit, calls = stat[name]
        if calls == 0:
            print(f"{name:<12}{0:>7}      -       -       -       -       -")
            continue
        # base rate for THIS model = how often its own directional mix
        # would have been right by drift alone
        hr = hit / calls
        p = binom_p(hit, calls, base_up)
        print(f"{name:<12}{calls:>7}{hit:>7}{100*hr:>7.1f}%"
              f"{100*base_up:>7.1f}%{100*(hr-base_up):>+7.1f}{p:>8.3f}")
    print()
    print("  edge = hit% - base%. Near zero means the call carried no")
    print("  information beyond which way the market happened to drift.")
    print("  p is a two-sided normal approximation vs the base rate; treat")
    print("  p > 0.05 as 'indistinguishable from no skill at this n'.")
    mt5.shutdown()


if __name__ == "__main__":
    main()
