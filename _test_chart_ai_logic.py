import sys, os
sys.path.insert(0, "/Users/follow/Desktop/outputs/bot forex")
# strip real telegram creds before importing (module does load_dotenv())
os.environ.pop("TELEGRAM_BOT_TOKEN", None)
os.environ.pop("TELEGRAM_CHAT_ID", None)
import chart_ai_trader as cat

X = cat.cross_check_signal

print("=== Case 1: both agree LONG, both above CONF_MIN -> trade ===")
d = X({"signal": "long", "confidence": 0.85, "reasoning": "g"},
      {"signal": "long", "confidence": 0.75, "reasoning": "o"})
assert d is not None
assert d["signal"] == "long"
assert d["confidence"] == 0.75, "merged confidence must be the MINIMUM (weakest link), got %s" % d["confidence"]
assert d["gemini_reasoning"] == "g" and d["openai_reasoning"] == "o"
print("PASS", d, "\n")

print("=== Case 2: both agree SHORT -> trade ===")
d = X({"signal": "short", "confidence": 0.9, "reasoning": ""},
      {"signal": "short", "confidence": 0.95, "reasoning": ""})
assert d is not None and d["signal"] == "short"
print("PASS\n")

print("=== Case 3: DISAGREE (long vs short) -> no trade ===")
assert X({"signal": "long", "confidence": 0.99, "reasoning": ""},
         {"signal": "short", "confidence": 0.99, "reasoning": ""}) is None
print("PASS\n")

print("=== Case 4: one says none -> no trade (even if other is very confident) ===")
assert X({"signal": "long", "confidence": 0.99, "reasoning": ""},
         {"signal": "none", "confidence": 0.99, "reasoning": ""}) is None
assert X({"signal": "none", "confidence": 0.99, "reasoning": ""},
         {"signal": "long", "confidence": 0.99, "reasoning": ""}) is None
print("PASS\n")

print("=== Case 5: agree but ONE below CONF_MIN -> no trade ===")
assert X({"signal": "long", "confidence": 0.95, "reasoning": ""},
         {"signal": "long", "confidence": 0.69, "reasoning": ""}) is None
assert X({"signal": "long", "confidence": 0.69, "reasoning": ""},
         {"signal": "long", "confidence": 0.95, "reasoning": ""}) is None
print("PASS\n")

print("=== Case 6: exactly at CONF_MIN boundary -> trades (>= not >) ===")
d = X({"signal": "long", "confidence": cat.CONF_MIN, "reasoning": ""},
      {"signal": "long", "confidence": cat.CONF_MIN, "reasoning": ""})
assert d is not None, "confidence exactly == CONF_MIN must pass (documented as >=)"
print("PASS\n")

print("=== Case 7: malformed/missing fields -> no trade, no crash ===")
assert X({}, {}) is None
assert X({"signal": "long"}, {"signal": "long"}) is None, "missing confidence must default to 0 -> reject"
assert X({"signal": "long", "confidence": None}, {"signal": "long", "confidence": None}) is None
assert X({"signal": "LONG", "confidence": 0.9}, {"signal": "LONG", "confidence": 0.9}) is None, \
    "signal is case-sensitive by design; uppercase must not silently trade"
print("PASS\n")

print("=== Case 8: garbage signal strings -> no trade ===")
assert X({"signal": "buy", "confidence": 0.9}, {"signal": "buy", "confidence": 0.9}) is None
assert X({"signal": "", "confidence": 0.9}, {"signal": "", "confidence": 0.9}) is None
print("PASS\n")

print("=== Case 9: config sanity -- magic/risk/SL/TP are what was agreed ===")
assert cat.MAGIC == 671001, "magic must not collide with 555/666/667/668/669 families"
assert cat.DEFAULT_RISK_PCT == 0.30, "user chose the technical-bot tier (0.30-0.50), conservative end"
assert cat.SL_ATR_MULT == 2.5 and cat.TP_ATR_MULT == 15.0, "reuses the validated SL2.5/TP15 shape"
assert cat.CONF_MIN == 0.70
assert set(cat.SYMBOLS) == {"XAUUSD", "BTCUSDC", "ETHUSDC"}
print("PASS\n")

print("ALL TESTS PASSED")
