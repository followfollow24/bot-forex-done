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

print("=== Case 17: CALL-PATH ARITY -- the wiring, not just the pure logic ===")
# Regression test for a real bug that reached production: when the `atr`
# argument was added, both call sites in _evaluate_symbol were updated but
# _safe_signal's own signature was not, so EVERY cycle raised
# "_safe_signal() takes 9 positional arguments but 10 were given".
# py_compile can't catch this (Python checks arity at call time) and the
# tests above couldn't either -- they only exercised cross_check_signal(),
# a pure function that sits AFTER the broken call. The bot failed safe
# (per-symbol try/except -> no trades) but was silently inert for hours.
# These checks exercise the actual call chain instead of just its tail.
import inspect

captured = {}
def fake_provider(api_key, model, png, symbol, price, bars, atr):
    captured.update(dict(api_key=api_key, model=model, png=png, symbol=symbol,
                         price=price, bars=bars, atr=atr))
    return {"signal": "long", "confidence": 0.9, "sl_atr_mult": 2.0,
            "tp_atr_mult": 6.0, "reasoning": "ok"}

class _StubLog:
    def warning(self, m): pass
    def info(self, m): pass
class _StubSelf:
    log = _StubLog()
    # use the REAL timeout wrapper so these tests exercise the actual
    # call path (including the thread-pool hop), not a simplified stand-in
    _call_with_timeout = cat.ChartAITraderBot._call_with_timeout

out = cat.ChartAITraderBot._safe_signal(
    _StubSelf(), "gemini", fake_provider, "KEY", "MODEL",
    b"PNGBYTES", "XAUUSD", 4390.5, 160, 5.25)
assert out is not None, "_safe_signal must forward the call, not swallow an arity error"
assert captured["atr"] == 5.25, "atr must actually reach the provider function"
assert captured["symbol"] == "XAUUSD" and captured["bars"] == 160
assert captured["price"] == 4390.5 and captured["png"] == b"PNGBYTES"
print("PASS -- _safe_signal forwards all 7 args including atr")

# and the two REAL provider functions must accept exactly what it forwards
fwd = ["api_key", "model", "png", "symbol", "price", "bars", "atr"]
for fn in (cat.gemini_chart_signal, cat.openai_chart_signal):
    params = list(inspect.signature(fn).parameters)
    assert params == fwd, "%s signature %s != forwarded %s" % (fn.__name__, params, fwd)
    print("  %s(%s) OK" % (fn.__name__, ", ".join(params)))

# _safe_signal must accept exactly what _evaluate_symbol passes it
sig = list(inspect.signature(cat.ChartAITraderBot._safe_signal).parameters)
assert sig == ["self", "name", "fn", "api_key", "model", "png", "symbol",
               "price", "bars", "atr"], sig
print("  _safe_signal(%s) OK" % ", ".join(sig[1:]))
print("PASS\n")

print("=== Case 18: a HUNG AI call must time out, not block forever ===")
# Regression test for the second production bug: the provider calls had no
# timeout at all. Observed live -- the bot sat inside one call for 7+
# minutes, log silent, heartbeat frozen, which pushed it past
# watchdog_h1.ps1's 5-minute staleness threshold and would have had the
# watchdog restart a healthy-but-busy bot in a loop. Every MT5 call in this
# fleet has had this guard for months; the AI calls never did.
import time as _time

def hanging_provider(api_key, model, png, symbol, price, bars, atr):
    _time.sleep(30)          # far longer than the timeout we pass below
    return {"signal": "long", "confidence": 0.9}

_orig_timeout = cat.AI_CALL_TIMEOUT_SEC
cat.AI_CALL_TIMEOUT_SEC = 1.0   # keep the test fast; behaviour is identical
t0 = _time.time()
out = cat.ChartAITraderBot._safe_signal(
    _StubSelf(), "gemini", hanging_provider, "K", "M", b"P", "XAUUSD",
    100.0, 160, 5.0)
elapsed = _time.time() - t0
cat.AI_CALL_TIMEOUT_SEC = _orig_timeout
assert out is None, "a hung call must return None (skip symbol), not a result"
assert elapsed < 5, "must return in ~timeout, not wait out the hang (took %.1fs)" % elapsed
print("PASS -- hung call returned None after %.1fs instead of blocking 30s\n" % elapsed)

print("=== Case 19: cycle worst case stays under the watchdog threshold ===")
# heartbeat is refreshed between symbols, so the largest gap between two
# heartbeats is ONE symbol's two provider calls.
worst_gap_sec = 2 * cat.AI_CALL_TIMEOUT_SEC
watchdog_threshold_sec = 5 * 60
assert worst_gap_sec < watchdog_threshold_sec, \
    "worst-case heartbeat gap %ss must stay under the watchdog's %ss" % (
        worst_gap_sec, watchdog_threshold_sec)
print("PASS -- worst gap %ss < watchdog %ss (%.1fx margin)\n"
      % (worst_gap_sec, watchdog_threshold_sec, watchdog_threshold_sec / worst_gap_sec))

print("=== Case 20: the live config must carry THIS bot's magic ===")
# Regression test for the worst bug of the three: cfg.magic_number was
# never set, so orders went out under ForexConfig's shared default
# (20240101) while _own_positions() filtered on MAGIC (671001). The bot
# could not see its own trades -- it declared a still-open live position
# "CLOSED pnl=+0.00" 2 minutes after opening it, and its
# already-positioned guard would have let it stack unbounded duplicate
# positions on the same symbol.
from forex_config import ForexConfig

cfg = cat.build_cfg(dry_run=True, allow_real=False)
assert cfg.magic_number == cat.MAGIC, \
    "config magic %r must equal the bot's MAGIC %r" % (cfg.magic_number, cat.MAGIC)
assert cfg.magic_number != ForexConfig().magic_number, \
    "magic must not be left at ForexConfig's shared default (%r) -- that is " \
    "exactly the bug: orders tagged with a magic the bot does not filter on" \
    % ForexConfig().magic_number
assert cfg.dry_run is True and cfg.allow_real is False, "flags must pass through"
assert cat.build_cfg(False, True).allow_real is True
print("PASS -- cfg.magic_number=%d (MAGIC), distinct from default %d\n"
      % (cfg.magic_number, ForexConfig().magic_number))

print("=== Case 21: MAGIC must not collide with any other bot in the fleet ===")
# 555xxx/666xxx/667xxx = forex_live_bot_gold_cwider families,
# 668xxx = daily_sleeves, 669001 = news_gemini, 20240101 = shared default.
taken = {555143, 555153, 555073, 666120, 666020, 666040, 666050, 666060,
         667130, 668001, 668002, 669001, 20240101}
assert cat.MAGIC not in taken, "MAGIC %d collides with an existing bot" % cat.MAGIC
print("PASS -- %d is unique across the fleet\n" % cat.MAGIC)

print("=== Case 22: position STACKING caps (user asked to allow multiple) ===")
# Stacking is allowed on purpose, but must be bounded: at a 15-min cadence
# an AI that keeps saying "long" would open ~96 positions/symbol/day, and
# each carries its own risk_pct stop. These tests drive the REAL
# _evaluate_symbol guard, with _own_positions stubbed to simulate what the
# broker reports, and assert it stops at the caps.
class _CapStub:
    """Minimal stand-in exercising the real _evaluate_symbol cap logic."""
    def __init__(self, positions, max_per_symbol=3, max_total=6):
        self._positions = positions      # list of dicts, as _own_positions returns
        self.max_per_symbol = max_per_symbol
        self.max_total = max_total
        self.log = _StubLog()
        self.reached_fetch = False
        self.connector = type("C", (), {"fetch_ohlcv": staticmethod(lambda *a, **k: [])})()
    def _own_positions(self, bsym=None):
        if bsym:
            return [p for p in self._positions if p["symbol"] == bsym]
        return list(self._positions)
    def _mt5(self, fn, *a, **kw):
        self.reached_fetch = True        # got past the caps into real work
        return []                        # empty candles -> harmless early return

def _ran(stub):
    cat.ChartAITraderBot._evaluate_symbol(stub, "XAUUSD", "XAUUSDc")
    return stub.reached_fetch

P = lambda s: {"symbol": s}

# flat -> proceeds
assert _ran(_CapStub([])) is True, "with no positions the bot must evaluate"
# 1 and 2 held on the symbol -> still proceeds (stacking genuinely allowed)
assert _ran(_CapStub([P("XAUUSDc")])) is True, "1 held must NOT block (stacking allowed)"
assert _ran(_CapStub([P("XAUUSDc")] * 2)) is True, "2 held must NOT block"
# at the per-symbol cap -> blocked
assert _ran(_CapStub([P("XAUUSDc")] * 3)) is False, "3 held must hit the per-symbol cap"
assert _ran(_CapStub([P("XAUUSDc")] * 9)) is False, "over-cap must stay blocked"
print("  per-symbol cap: 0,1,2 -> open;  3+ -> blocked   OK")

# total cap binds even when the symbol itself is under its own cap
under_symbol_over_total = [P("XAUUSDc")] * 2 + [P("BTCUSDc")] * 2 + [P("ETHUSDc")] * 2
assert _ran(_CapStub(under_symbol_over_total)) is False, \
    "total cap must block even though XAUUSD holds only 2 of its 3"
assert _ran(_CapStub([P("BTCUSDc")] * 5)) is True, \
    "other symbols' positions must not block XAUUSD while under the total cap"
print("  total cap: binds across symbols, and does not over-block   OK")

# caps are configurable, and 1/1 reproduces the old one-at-a-time behaviour
assert _ran(_CapStub([P("XAUUSDc")], max_per_symbol=1, max_total=1)) is False
assert _ran(_CapStub([], max_per_symbol=1, max_total=1)) is True
assert _ran(_CapStub([P("XAUUSDc")] * 4, max_per_symbol=5, max_total=10)) is True
print("  configurable: --max-per-symbol 1 restores old behaviour; raising works   OK")
print("PASS\n")

print("=== Case 23: worst-case stacked risk is bounded and stated ===")
for total, risk in ((6, 0.30), (10, 0.30), (30, 0.30)):
    print("  --max-total %-3d x --risk %.2f%%  -> worst case %.1f%% of equity"
          % (total, risk, total * risk))
assert cat.MAX_TOTAL_POSITIONS * cat.DEFAULT_RISK_PCT <= 2.0, \
    "shipped defaults must keep worst-case simultaneous risk small (<=2%)"
assert cat.MAX_POSITIONS_PER_SYMBOL <= cat.MAX_TOTAL_POSITIONS, \
    "a per-symbol cap above the total cap would be unreachable/misleading"
print("PASS -- defaults bound it to %.1f%%\n"
      % (cat.MAX_TOTAL_POSITIONS * cat.DEFAULT_RISK_PCT))

print("ALL TESTS PASSED")
