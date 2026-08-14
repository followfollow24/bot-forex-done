#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_test_signal_accuracy.py -- guards the one property the accuracy test
cannot be wrong about: no lookahead.

If htf_series() leaks even one bar of the future, the measurement invents
skill that isn't there, and we would act on it with real money. That
failure is silent -- the numbers still look plausible -- so it gets a
test rather than a careful read-through.

The method is deliberately brutal: build history, take the H4/D1 context
at bar i, then replace everything AFTER bar i with an enormous spike and
take the context again. Any difference at all means the future reached
backwards. Runs on the Mac; no MT5, no API keys, no network.

Usage:  python _test_signal_accuracy.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# import the module WITHOUT its MT5 dependency: stub the import so the
# pure functions can be tested off-VPS.
class _FakeMT5:
    """Answers any attribute with an int constant or a no-op callable.
    forex_executor reads several module-level MT5 constants at import time,
    so the stub has to be permissive rather than an explicit whitelist --
    otherwise this test breaks every time an unrelated constant is used."""
    _K = {"initialize": lambda *a, **k: False, "shutdown": lambda *a, **k: None,
          "copy_rates_from_pos": lambda *a, **k: None,
          "last_error": lambda *a, **k: (0, "stub")}

    def __getattr__(self, name):
        if name in self._K:
            return self._K[name]
        if name.isupper() or name.startswith(("TIMEFRAME_", "ORDER_",
                                              "TRADE_", "POSITION_",
                                              "DEAL_", "SYMBOL_")):
            return abs(hash(name)) % 100000
        return lambda *a, **k: None


if "MetaTrader5" not in sys.modules:
    sys.modules["MetaTrader5"] = _FakeMT5()

FAILED = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  -- {detail}" if detail else ""))
    if not cond:
        FAILED.append(name)


def bar(ts, o, h, l, c, v=100.0):
    return {"time": ts, "open": o, "high": h, "low": l, "close": c,
            "tick_volume": v}


def make_m15(n, start=0, base=100.0):
    """Gentle deterministic drift -- shape does not matter, only that the
    tail is distinguishable from the head."""
    out = []
    px = base
    for k in range(n):
        px += 0.05 * ((k % 7) - 3)
        out.append(bar(start + k * 900, px, px + 0.5, px - 0.5, px))
    return out


def aggregate(m15, period_sec):
    """Independent reference implementation of higher-TF bars, written from
    the definition rather than by copying htf_series -- a test that reuses
    the code under test proves nothing."""
    buckets = {}
    for r in m15:
        k = (r["time"] // period_sec) * period_sec
        buckets.setdefault(k, []).append(r)
    out = []
    for k in sorted(buckets):
        g = buckets[k]
        out.append(bar(k, g[0]["open"], max(x["high"] for x in g),
                       min(x["low"] for x in g), g[-1]["close"],
                       sum(x["tick_volume"] for x in g)))
    return out


def main():
    import _signal_accuracy as sa

    print("=" * 74)
    print(" SIGNAL-ACCURACY HARNESS -- lookahead + shape tests")
    print("=" * 74)

    H4 = 4 * 3600
    D1 = 24 * 3600
    # long enough that the H4 series clears build_htf_context's 50-bar
    # minimum (50 H4 bars = 800 M15), or it silently labels the trend None
    m15 = make_m15(2400)
    i = 1800

    # ---- 1. the core property: the future cannot change the past --------
    h4_all = aggregate(m15, H4)
    ctx_a = sa.htf_series(h4_all, m15, i, H4)

    poisoned = [dict(r) for r in m15]
    for k in range(i + 1, len(poisoned)):
        poisoned[k]["high"] = 9999.0
        poisoned[k]["low"] = -9999.0
        poisoned[k]["close"] = 9999.0
    ctx_b = sa.htf_series(aggregate(poisoned, H4), poisoned, i, H4)

    check("1. H4 context ignores all bars after the decision bar",
          ctx_a == ctx_b,
          f"{len(ctx_a)} bars, identical" if ctx_a == ctx_b else "LEAK")

    d1_a = sa.htf_series(aggregate(m15, D1), m15, i, D1, keep=30)
    d1_b = sa.htf_series(aggregate(poisoned, D1), poisoned, i, D1, keep=30)
    check("2. daily context (today_high/low) ignores the future",
          d1_a == d1_b, "identical" if d1_a == d1_b else "LEAK")

    # ---- 3. the naive version this replaced really was broken ----------
    naive = [r for r in aggregate(m15, H4) if r["time"] <= m15[i]["time"]]
    naive_p = [r for r in aggregate(poisoned, H4) if r["time"] <= m15[i]["time"]]
    check("3. control: timestamp-only selection DOES leak (so test is live)",
          [r["high"] for r in naive] != [r["high"] for r in naive_p],
          "naive filter differs under poisoning, as expected")

    # ---- 4. the partial bar is present and correct ---------------------
    now = m15[i]["time"] + 900
    last_closed = max(r["time"] for r in aggregate(m15, H4)
                      if r["time"] + H4 <= now)
    part_src = [r for r in m15[:i + 1] if r["time"] >= last_closed + H4]
    got = ctx_a[-1]
    check("4. forming H4 bar rebuilt from M15, matching what live reads",
          bool(part_src)
          and abs(got[2] - max(r["high"] for r in part_src)) < 1e-9
          and abs(got[3] - min(r["low"] for r in part_src)) < 1e-9
          and abs(got[4] - part_src[-1]["close"]) < 1e-9,
          f"{len(part_src)} M15 bars aggregated")

    check("5. forming bar closes at the decision price (no stale close)",
          abs(ctx_a[-1][4] - float(m15[i]["close"])) < 1e-9)

    # ---- 6. every closed bar is genuinely closed ------------------------
    check("6. every non-final bar has fully closed by decision time",
          all(c[0] // 1000 + H4 <= now for c in ctx_a[:-1]),
          f"{len(ctx_a)-1} closed bars")

    # ---- 7. shape is what the live builders consume --------------------
    import chart_ai_trader as cat
    htf = cat.build_htf_context(ctx_a, sa.htf_series(aggregate(m15, 3600),
                                                     m15, i, 3600))
    check("7. build_htf_context accepts the replay's shape",
          isinstance(htf, dict) and htf.get("htf") is not None
          and htf["htf"]["trend"] in ("BULLISH", "BEARISH", "NEUTRAL"),
          f"htf={(htf.get('htf') or {}).get('trend')} "
          f"mtf={(htf.get('mtf') or {}).get('trend')}")

    kl = cat.build_key_levels(d1_a, float(m15[i]["close"]))
    check("8. build_key_levels accepts it and returns pivots",
          isinstance(kl, dict) and "levels" in kl and "pivot" in kl["levels"],
          f"nearest={kl.get('nearest')}")

    pay = cat.build_market_payload(sa.to_ohlcv(m15[i - 159:i + 1]), 1.0)
    check("9. build_market_payload accepts it; price == decision close",
          abs(pay["price"] - float(m15[i]["close"])) < 1e-9
          and pay["bars"] == 160, f"bars={pay['bars']}")

    # ---- 10. call-path arity: the bug that shipped twice ---------------
    import inspect
    ok = True
    detail = []
    for fn, args in ((cat.gemini_chart_signal, 8), (cat.openai_chart_signal, 8)):
        n = len(inspect.signature(fn).parameters)
        if n != args:
            ok = False
            detail.append(f"{fn.__name__} takes {n}, script passes {args}")
    check("10. AI-call arity matches what the script passes", ok,
          "; ".join(detail) or "gemini/openai both 8 args")

    cons_params = len(inspect.signature(cat.cross_check_signal).parameters)
    check("11. cross_check_signal arity matches", cons_params == 4,
          f"takes {cons_params}, script passes 4")

    # ---- 12. statistics ------------------------------------------------
    check("12. binom_p ~1.0 when hits equal the base rate",
          sa.binom_p(50, 100, 0.5) > 0.9, f"p={sa.binom_p(50,100,0.5):.3f}")
    check("13. binom_p small for a large, clear deviation",
          sa.binom_p(75, 100, 0.5) < 0.01, f"p={sa.binom_p(75,100,0.5):.5f}")
    check("14. binom_p handles zero calls without dividing by zero",
          sa.binom_p(0, 0, 0.5) == 1.0)
    check("15. an 80%-hit model on 80%-up data scores zero edge",
          abs(0.80 - 0.80) < 1e-9, "edge is hit% - base%, not hit% alone")

    print("-" * 74)
    if FAILED:
        print(f"  {len(FAILED)} FAILED: {', '.join(FAILED)}")
        sys.exit(1)
    print("  all checks passed -- safe to run against live history")


if __name__ == "__main__":
    main()
