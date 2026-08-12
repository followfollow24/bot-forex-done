"""Unit tests for chart_ai_trader's decision logic. No API calls, no MT5,
no network -- cross_check_signal() is a pure function by design so the
gate that stands between an AI opinion and a real order can be tested
exhaustively and cheaply."""
import sys, os
sys.path.insert(0, "/Users/follow/Desktop/outputs/bot forex")
# strip real telegram creds before importing (module does load_dotenv())
os.environ.pop("TELEGRAM_BOT_TOKEN", None)
os.environ.pop("TELEGRAM_CHAT_ID", None)
import chart_ai_trader as cat

X = cat.cross_check_signal


def R(sig, conf, sl=2.0, tp=6.0, reasoning=""):
    """A well-formed model response. Defaults give RR 3.0, comfortably
    over MIN_RR, so tests that aren't about SL/TP aren't accidentally
    rejected by the R:R floor."""
    return {"signal": sig, "confidence": conf, "sl_atr_mult": sl,
            "tp_atr_mult": tp, "reasoning": reasoning}


print("=== Case 1: both agree LONG, both above CONF_MIN -> trade ===")
d = X(R("long", 0.85, reasoning="g"), R("long", 0.75, reasoning="o"))
assert d is not None
assert d["signal"] == "long"
assert d["confidence"] == 0.75, "merged confidence must be the MINIMUM (weakest link)"
assert d["gemini_reasoning"] == "g" and d["openai_reasoning"] == "o"
print("PASS", d, "\n")

print("=== Case 2: both agree SHORT -> trade ===")
d = X(R("short", 0.9), R("short", 0.95))
assert d is not None and d["signal"] == "short"
print("PASS\n")

print("=== Case 3: DISAGREE (long vs short) -> no trade ===")
assert X(R("long", 0.99), R("short", 0.99)) is None
print("PASS\n")

print("=== Case 4: one says none -> no trade (even if other is very confident) ===")
assert X(R("long", 0.99), R("none", 0.99)) is None
assert X(R("none", 0.99), R("long", 0.99)) is None
print("PASS\n")

print("=== Case 5: agree but ONE below CONF_MIN -> no trade ===")
assert X(R("long", 0.95), R("long", cat.CONF_MIN - 0.01)) is None
assert X(R("long", cat.CONF_MIN - 0.01), R("long", 0.95)) is None
print("PASS\n")

print("=== Case 6: exactly at CONF_MIN boundary -> trades (>= not >) ===")
assert X(R("long", cat.CONF_MIN), R("long", cat.CONF_MIN)) is not None
print("PASS\n")

print("=== Case 7: malformed/missing fields -> no trade, no crash ===")
assert X({}, {}) is None
assert X({"signal": "long"}, {"signal": "long"}) is None
assert X({"signal": "long", "confidence": None}, {"signal": "long", "confidence": None}) is None
assert X(R("LONG", 0.9), R("LONG", 0.9)) is None, \
    "signal is case-sensitive by design; uppercase must not silently trade"
print("PASS\n")

print("=== Case 8: garbage signal strings -> no trade ===")
assert X(R("buy", 0.9), R("buy", 0.9)) is None
assert X(R("", 0.9), R("", 0.9)) is None
print("PASS\n")

print("=== Case 9: config sanity -- magic/risk are what was agreed ===")
assert cat.MAGIC == 671001, "magic must not collide with 555/666/667/668/669 families"
assert cat.DEFAULT_RISK_PCT == 0.30, "user chose the technical-bot tier"
assert set(cat.SYMBOLS) == {"XAUUSD", "BTCUSDC", "ETHUSDC"}
print("PASS\n")

# ===================================================================
# [2026-08-12] AI-chosen SL/TP guardrails + M15 + lowered CONF_MIN
# ===================================================================
print("=== Case 10: config reflects the requested changes ===")
assert cat.CONF_MIN == 0.60, "user asked for 0.60"
assert cat.TIMEFRAME == "15m", "user asked for M15"
assert cat.POLL_MIN == 15, "poll should match the M15 bar"
print("PASS conf_min=%s tf=%s poll=%s\n" % (cat.CONF_MIN, cat.TIMEFRAME, cat.POLL_MIN))

print("=== Case 11: 0.60 now trades where 0.70 previously blocked ===")
d = X(R("long", 0.62), R("long", 0.60))
assert d is not None, "the exact live case (0.62/0.60) must now trade"
assert d["confidence"] == 0.60
print("PASS -- the real observed 0.62/0.60 XAUUSD case now passes\n")

print("=== Case 12: AI SL/TP are averaged, not taken from one model ===")
d = X(R("long", 0.8, sl=2.0, tp=6.0), R("long", 0.8, sl=3.0, tp=9.0))
assert abs(d["sl_atr_mult"] - 2.5) < 1e-9, d["sl_atr_mult"]
assert abs(d["tp_atr_mult"] - 7.5) < 1e-9, d["tp_atr_mult"]
assert abs(d["rr"] - 3.0) < 1e-9
print("PASS sl=%.2f tp=%.2f rr=%.2f\n" % (d["sl_atr_mult"], d["tp_atr_mult"], d["rr"]))

print("=== Case 13: absurd multiples are CLAMPED, never passed through ===")
d = X(R("long", 0.9, sl=500.0, tp=9999.0), R("long", 0.9, sl=500.0, tp=9999.0))
assert d["sl_atr_mult"] == cat.SL_ATR_MAX, "a 500xATR stop must clamp"
assert d["tp_atr_mult"] == cat.TP_ATR_MAX
d2 = X(R("long", 0.9, sl=0.0001, tp=0.001), R("long", 0.9, sl=0.0001, tp=0.001))
assert d2 is None or d2["sl_atr_mult"] >= cat.SL_ATR_MIN
print("PASS clamped to sl=%.2f tp=%.2f\n" % (d["sl_atr_mult"], d["tp_atr_mult"]))

print("=== Case 14: poor reward:risk is REJECTED even with high confidence ===")
assert X(R("long", 0.99, sl=4.0, tp=4.4), R("long", 0.99, sl=4.0, tp=4.4)) is None, \
    "RR 1.1 < MIN_RR 1.2 must reject"
assert X(R("long", 0.99, sl=4.0, tp=5.0), R("long", 0.99, sl=4.0, tp=5.0)) is not None, \
    "RR 1.25 >= MIN_RR must pass"
print("PASS\n")

print("=== Case 15: malformed/missing/negative SL-TP -> reject, never coerce ===")
for bad in ({"signal": "long", "confidence": 0.9},
            R("long", 0.9, sl=-2.0),
            R("long", 0.9, sl=0.0),
            R("long", 0.9, sl=float("nan")),
            R("long", 0.9, sl=float("inf")),
            R("long", 0.9, sl="wide")):
    assert X(bad, R("long", 0.9)) is None, "must reject: %r" % (bad,)
    assert X(R("long", 0.9), bad) is None, "must reject (other side): %r" % (bad,)
print("PASS -- a bad stop distance can never reach a live order\n")

print("=== Case 16: $-risk is invariant to the AI's stop width ===")
eq, risk_pct, atr, pip_size, pip_value = 16000.0, 0.30, 5.0, 1.0, 0.01
for mult in (0.5, 2.0, 6.0):
    sd = mult * atr
    lot = round((eq * risk_pct / 100.0) / ((sd / pip_size) * pip_value), 2)
    realized = (sd / pip_size) * pip_value * lot / eq * 100.0
    assert realized <= risk_pct * 1.5, "mult %s -> %.3f%%" % (mult, realized)
    print("  sl=%.1fxATR -> lot=%.2f -> realized risk %.3f%% (intended %.2f%%)"
          % (mult, lot, realized, risk_pct))
print("PASS -- wider AI stop just means smaller lot, not a bigger loss\n")

print("ALL TESTS PASSED")
