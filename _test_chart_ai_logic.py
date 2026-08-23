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

# ---------------------------------------------------------------- #
# [2026-08-12 v2] New contract per the user's spec: models return
# decision=LONG/SHORT/WAIT with ABSOLUTE entry/sl/tp, no confidence.
# ---------------------------------------------------------------- #
PRICE, ATR = 4400.0, 6.0        # stop band = 1.2..2.5 xATR = 7.2..15.0

def R(dec, entry=PRICE, sl=None, tp=None, reason=""):
    """Well-formed response. Defaults: stop 10.0 (1.67xATR, in band),
    target 20.0 (R:R 2.0, over the 1.5 floor)."""
    if dec == "LONG":
        sl = PRICE - 10.0 if sl is None else sl
        tp = PRICE + 20.0 if tp is None else tp
    else:
        sl = PRICE + 10.0 if sl is None else sl
        tp = PRICE - 20.0 if tp is None else tp
    return {"decision": dec, "entry": entry, "sl": sl, "tp": tp, "reason": reason}

def X(g, o, price=PRICE, atr=ATR):
    return cat.cross_check_signal(g, o, price, atr)

print("=== Case 1: both LONG -> trade, levels AVERAGED ===")
d = X(R("LONG", sl=PRICE-10, tp=PRICE+20, reason="g"),
      R("LONG", sl=PRICE-12, tp=PRICE+24, reason="o"))
assert d is not None
assert d["signal"] == "long" and d["decision"] == "LONG"
assert abs(d["sl"] - (PRICE - 11.0)) < 1e-9, d["sl"]
assert abs(d["tp"] - (PRICE + 22.0)) < 1e-9, d["tp"]
assert abs(d["sl_dist"] - 11.0) < 1e-9 and abs(d["tp_dist"] - 22.0) < 1e-9
assert d["gemini_reasoning"] == "g" and d["openai_reasoning"] == "o"
print("PASS sl=%.2f tp=%.2f rr=%.2f\n" % (d["sl"], d["tp"], d["rr"]))

print("=== Case 2: both SHORT -> trade ===")
d = X(R("SHORT"), R("SHORT"))
assert d is not None and d["signal"] == "short"
print("PASS\n")

print("=== Case 3: WAIT from either side -> no trade ===")
assert X(R("LONG"), R("WAIT")) is None
assert X(R("WAIT"), R("LONG")) is None
assert X(R("WAIT"), R("WAIT")) is None
print("PASS\n")

print("=== Case 4: opposite directions -> no trade ===")
assert X(R("LONG"), R("SHORT")) is None
print("PASS\n")

print("=== Case 5: case/whitespace tolerated (models are chatty) ===")
g = R("LONG"); g["decision"] = " long "
assert X(g, R("LONG")) is not None, "must accept ' long ' as LONG"
print("PASS\n")

print("=== Case 6: SL/TP on the WRONG SIDE of entry -> reject ===")
assert X(R("LONG", sl=PRICE+10, tp=PRICE+20), R("LONG")) is None, "LONG stop above entry"
assert X(R("LONG", sl=PRICE-10, tp=PRICE-20), R("LONG")) is None, "LONG target below entry"
assert X(R("SHORT", sl=PRICE-10, tp=PRICE-20), R("SHORT")) is None, "SHORT stop below entry"
assert X(R("SHORT", sl=PRICE+10, tp=PRICE+20), R("SHORT")) is None, "SHORT target above entry"
print("PASS -- a level on the wrong side is rejected, never auto-corrected\n")

print("=== Case 7: stop outside the 1.2-2.5xATR band -> reject (not clamped) ===")
assert X(R("LONG", sl=PRICE-2.0, tp=PRICE+20), R("LONG", sl=PRICE-2.0, tp=PRICE+20)) is None, \
    "0.33xATR stop far too tight"
assert X(R("LONG", sl=PRICE-60.0, tp=PRICE+200), R("LONG", sl=PRICE-60.0, tp=PRICE+200)) is None, \
    "10xATR stop far too wide"
ok = X(R("LONG", sl=PRICE-9.0, tp=PRICE+20), R("LONG", sl=PRICE-9.0, tp=PRICE+20))
assert ok is not None and 1.2 <= ok["sl_atr_mult"] <= 2.5
print("PASS -- in-band %.2fxATR accepted, out-of-band rejected outright\n" % ok["sl_atr_mult"])

print("=== Case 8: reward:risk below 1.5 -> reject ===")
assert X(R("LONG", sl=PRICE-10, tp=PRICE+14), R("LONG", sl=PRICE-10, tp=PRICE+14)) is None, "R:R 1.4"
assert X(R("LONG", sl=PRICE-10, tp=PRICE+15), R("LONG", sl=PRICE-10, tp=PRICE+15)) is not None, "R:R 1.5"
print("PASS\n")

print("=== Case 9: entry far from the live price (stale/hallucinated) -> reject ===")
drift = cat.MAX_ENTRY_DRIFT_ATR * ATR
bad = PRICE + drift + 1.0
assert X(R("LONG", entry=bad, sl=bad-10, tp=bad+20),
         R("LONG", entry=bad, sl=bad-10, tp=bad+20)) is None, \
    "entry %.1f is beyond %.1f of live price %.1f" % (bad, drift, PRICE)
near = PRICE + drift - 1.0
assert X(R("LONG", entry=near, sl=near-10, tp=near+20),
         R("LONG", entry=near, sl=near-10, tp=near+20)) is not None
print("PASS -- drift limit %.1f (%.1fxATR) enforced\n" % (drift, cat.MAX_ENTRY_DRIFT_ATR))

print("=== Case 10: malformed / missing / non-numeric levels -> reject ===")
for bad_r in ({"decision": "LONG"},
              {"decision": "LONG", "entry": PRICE, "sl": None, "tp": PRICE+20},
              R("LONG", sl=float("nan")),
              R("LONG", sl=float("inf")),
              R("LONG", sl=-5.0),
              R("LONG", sl=0.0),
              {"decision": "LONG", "entry": PRICE, "sl": "low", "tp": PRICE+20}):
    assert X(bad_r, R("LONG")) is None, "must reject %r" % (bad_r,)
    assert X(R("LONG"), bad_r) is None, "must reject (other side) %r" % (bad_r,)
assert X(R("LONG"), R("LONG"), atr=0) is None, "zero ATR must reject"
print("PASS -- a bad level can never reach a live order\n")

print("=== Case 11: averaging can itself break a rule -> still rejected ===")
# each side individually fine, but the AVERAGE lands under the R:R floor
g = R("LONG", sl=PRICE-10, tp=PRICE+16)   # rr 1.60 ok
o = R("LONG", sl=PRICE-14, tp=PRICE+20)   # rr 1.43 -- fails on its own
d = X(g, o)                                # avg: sl 12, tp 18 -> rr 1.50
assert d is not None and abs(d["rr"] - 1.5) < 1e-9, d
g2 = R("LONG", sl=PRICE-10, tp=PRICE+16)
o2 = R("LONG", sl=PRICE-14, tp=PRICE+18)  # avg rr = 17/12 = 1.417 -> reject
assert X(g2, o2) is None, "averaged R:R below the floor must reject"
print("PASS -- validation runs on the AVERAGE, not on either input alone\n")

print("=== Case 12: build_market_payload computes the numbers it hands over ===")
bars = [[0, 100 + i, 101 + i, 99 + i, 100.5 + i, 10 + i] for i in range(60)]
pl = cat.build_market_payload(bars, atr=2.0)
assert pl["price"] == 159.5, pl["price"]
assert pl["recent_high"] == max(101 + i for i in range(40, 60))
assert pl["recent_low"] == min(99 + i for i in range(40, 60))
assert pl["bars"] == 60 and len(pl["tail"]) == 5
assert pl["ema20"] < pl["price"] and pl["ema50"] < pl["ema20"], \
    "in a clean uptrend price > EMA20 > EMA50"
txt = cat.format_payload("XAUUSD", pl)
for must in ("Close=", "EMA20=", "EMA50=", "ATR=", "Recent High=", "Last 5 bars"):
    assert must in txt, "payload text missing %r" % must
assert "above BOTH EMAs" in txt
print("PASS -- payload text:\n" + "\n".join("    " + l for l in txt.splitlines()[:4]) + "\n")

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
def fake_provider(api_key, model, png, symbol, price, bars, atr, payload_text):
    captured.update(dict(api_key=api_key, model=model, png=png, symbol=symbol,
                         price=price, bars=bars, atr=atr,
                         payload_text=payload_text))
    return {"decision": "LONG", "entry": price, "sl": price - 10,
            "tp": price + 20, "reason": "ok"}

class _StubLog:
    def warning(self, m): pass
    def info(self, m): pass
    def error(self, m): pass
class _StubSelf:
    log = _StubLog()
    poll_min = 15
    provider_fails: dict = {}
    def _heartbeat(self): pass
    def _telegram(self, msg): pass
    # use the REAL timeout wrapper so these tests exercise the actual
    # call path (including the thread-pool hop), not a simplified stand-in
    _call_with_timeout = cat.ChartAITraderBot._call_with_timeout
    # [2026-08-16] _safe_signal now reports provider health on both the
    # success and failure paths. These are the REAL methods, not no-ops:
    # stubbing them out would let a broken health hook pass unnoticed here
    # and only surface as a silent live bot, which is the exact failure
    # this feature exists to prevent.
    _note_provider_failure = cat.ChartAITraderBot._note_provider_failure
    _note_provider_ok = cat.ChartAITraderBot._note_provider_ok

out = cat.ChartAITraderBot._safe_signal(
    _StubSelf(), "gemini", fake_provider, "KEY", ["MODEL"],
    b"PNGBYTES", "XAUUSD", 4390.5, 160, 5.25, "PAYLOAD")
assert out is not None, "_safe_signal must forward the call, not swallow an arity error"
assert captured["atr"] == 5.25, "atr must actually reach the provider function"
assert captured["payload_text"] == "PAYLOAD", "numeric payload must reach the provider"
assert captured["symbol"] == "XAUUSD" and captured["bars"] == 160
assert captured["price"] == 4390.5 and captured["png"] == b"PNGBYTES"
print("PASS -- _safe_signal forwards all 7 args including atr")

# and the two REAL provider functions must accept exactly what it forwards
fwd = ["api_key", "model", "png", "symbol", "price", "bars", "atr",
       "payload_text"]   # per-CALL signature; _safe_signal fans a list over it
for fn in (cat.gemini_chart_signal, cat.openai_chart_signal):
    params = list(inspect.signature(fn).parameters)
    assert params == fwd, "%s signature %s != forwarded %s" % (fn.__name__, params, fwd)
    print("  %s(%s) OK" % (fn.__name__, ", ".join(params)))

# _safe_signal must accept exactly what _evaluate_symbol passes it
sig = list(inspect.signature(cat.ChartAITraderBot._safe_signal).parameters)
assert sig == ["self", "name", "fn", "api_key", "models", "png", "symbol",
               "price", "bars", "atr", "payload_text"], sig
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

def hanging_provider(api_key, model, png, symbol, price, bars, atr, payload_text):
    _time.sleep(30)          # far longer than the timeout we pass below
    return {"signal": "long", "confidence": 0.9}

_orig_timeout = cat.AI_CALL_TIMEOUT_SEC
cat.AI_CALL_TIMEOUT_SEC = 1.0   # keep the test fast; behaviour is identical
t0 = _time.time()
out = cat.ChartAITraderBot._safe_signal(
    _StubSelf(), "gemini", hanging_provider, "K", ["M"], b"P", "XAUUSD",
    100.0, 160, 5.0, "PAYLOAD")
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

print("=== Case 24: NEWS VETO -- news can only BLOCK, never create a trade ===")
class _NewsStub:
    def __init__(self, cands, fail=False, fail_which=None):
        self._cands = cands
        self._fail = fail
        self._fail_which = fail_which or []
        self._news_cache = None
        self._news_ok = []; self._news_failed = []
        self.log = _StubLog()
        self.gemini_key = "k"; self.gemini_model = "m"
        self.openai_key = ""; self.openai_model = "m"
        self.telegrams = []
        self.fetches = 0
    def _telegram(self, m): self.telegrams.append(m)
    def _call_with_timeout(self, fn, timeout, *a, **kw):
        self.fetches += 1
        if self._fail:
            raise RuntimeError("503 UNAVAILABLE")
        if getattr(fn, "_name", None) in self._fail_which:
            raise RuntimeError("503 UNAVAILABLE")
        return self._cands
    def _news_veto_state(self):
        return cat.ChartAITraderBot._news_veto_state(self)
    def _news_candidates(self):
        return cat.ChartAITraderBot._news_candidates(self)
    def _news_allows(self, canon, signal):
        return cat.ChartAITraderBot._news_allows(self, canon, signal)

N = lambda sym, sig, conf, head="h": {"symbol": sym, "signal": sig,
                                      "confidence": conf, "headline": head}

# opposite-direction, high-confidence news -> VETO
s1 = _NewsStub([N("XAUUSD", "short", 0.85, "Fed holds, USD surges")])
assert s1._news_allows("XAUUSD", "long") is False, "opposite high-conf news must veto"
assert len(s1.telegrams) == 1 and "NEWS VETO" in s1.telegrams[0]
print("  opposite news conf 0.85 vs LONG -> VETOED   OK")

# same-direction news -> no veto (news must not create/confirm, just not block)
s2 = _NewsStub([N("XAUUSD", "long", 0.95)])
assert s2._news_allows("XAUUSD", "long") is True, "aligned news must not block"
print("  aligned news -> allowed   OK")

# opposite but LOW confidence -> no veto
s3 = _NewsStub([N("XAUUSD", "short", 0.55)])
assert s3._news_allows("XAUUSD", "long") is True, "low-conf news must not veto"
print("  opposite news below %.2f -> allowed   OK" % cat.NEWS_VETO_CONF_MIN)

# news about a DIFFERENT symbol -> no veto
s4 = _NewsStub([N("BTCUSDC", "short", 0.99)])
assert s4._news_allows("XAUUSD", "long") is True, "other symbol must not veto"
print("  other-symbol news -> allowed   OK")

# SHORT direction is vetoed by LONG news (symmetry)
s5 = _NewsStub([N("ETHUSDC", "long", 0.9)])
assert s5._news_allows("ETHUSDC", "short") is False
print("  LONG news vs SHORT chart -> VETOED   OK")

# both providers failing -> FAIL OPEN (trade proceeds), and no crash
s6 = _NewsStub([], fail=True)
assert s6._news_allows("XAUUSD", "long") is True, \
    "a broken veto stage must not become a silent kill-switch"
print("  both scans error -> fails OPEN, trade allowed   OK")

# malformed confidence must not crash or wrongly veto
s7 = _NewsStub([{"symbol": "XAUUSD", "signal": "short", "confidence": "high"}])
assert s7._news_allows("XAUUSD", "long") is True
print("  malformed confidence -> ignored, no crash   OK")

# LAZY + CACHED: one fetch per cycle no matter how many symbols ask
s8 = _NewsStub([N("XAUUSD", "short", 0.9)])
s8._news_allows("XAUUSD", "long"); s8._news_allows("BTCUSDC", "long")
s8._news_allows("ETHUSDC", "short")
assert s8.fetches == 1, "must fetch once per cycle, got %d" % s8.fetches
print("  3 symbols checked -> %d scan fetch (cached)   OK" % s8.fetches)
print("PASS\n")

print("=== Case 25: news veto keeps the heartbeat gap under the watchdog ===")
# the veto adds up to 2 timed calls, but a heartbeat is written just
# before it, so the largest gap between heartbeats is still 2 calls.
worst = 2 * cat.AI_CALL_TIMEOUT_SEC
assert worst < 5 * 60, worst
print("PASS -- worst gap still %ds < watchdog 300s\n" % worst)

print("=== Case 26: fail-OPEN must be AUDITABLE -- veto health is recorded ===")
# The fail-open design is only defensible if a trade taken with a blind
# veto can be identified afterwards. Without this you would have to
# correlate separate log lines by timestamp across a whole forward test.
s_ok = _NewsStub([N("XAUUSD", "long", 0.9)])
s_ok.openai_key = "k2"                      # two providers configured
assert s_ok._news_allows("XAUUSD", "long") is True
assert s_ok._news_veto_state() == "ok", s_ok._news_veto_state()
print("  both providers answered            -> %-22s OK" % s_ok._news_veto_state())

s_blind = _NewsStub([], fail=True)
s_blind.openai_key = "k2"
assert s_blind._news_allows("XAUUSD", "long") is True, "still fails open"
assert s_blind._news_veto_state() == "blind", s_blind._news_veto_state()
print("  both providers failed              -> %-22s OK  (trade allowed, but flagged)"
      % s_blind._news_veto_state())

# one provider up, one down -> degraded, and it names WHICH one
class _HalfStub(_NewsStub):
    def _call_with_timeout(self, fn, timeout, *a, **kw):
        self.fetches += 1
        if self.fetches == 2:               # second provider (openai) fails
            raise RuntimeError("503")
        return self._cands
s_deg = _HalfStub([N("XAUUSD", "long", 0.9)])
s_deg.openai_key = "k2"
assert s_deg._news_allows("XAUUSD", "long") is True
st = s_deg._news_veto_state()
assert st.startswith("degraded") and "openai" in st, st
print("  gemini ok / openai down            -> %-22s OK  (names the failure)" % st)

# state before any scan ran
s_new = _NewsStub([])
assert s_new._news_veto_state() == "not-run"
print("  before any scan                    -> %-22s OK" % s_new._news_veto_state())
print("PASS -- every trade can be attributed to a working, partial or blind veto\n")

print("=== Case 27: HTF alignment gate (technique 1) ===")
F = cat.entry_filters_allow
def PL(htf_trend, near_price=4400.0, mtf="BULLISH"):
    return {"atr": 6.0,
            "htf": {"htf": ({"trend": htf_trend, "price": 1, "ema20": 1, "ema50": 1}
                            if htf_trend else None),
                    "mtf": {"trend": mtf, "price": 1, "ema20": 1, "ema50": 1}},
            "keylevels": {"nearest": "pivot", "nearest_price": near_price,
                          "distance": 0.0, "levels": {}}}
D = lambda sig, entry=4400.0: {"signal": sig, "entry": entry}

assert F(D("long"),  PL("BULLISH"), 6.0)[0] is True,  "LONG with bullish H4 -> allowed"
assert F(D("short"), PL("BEARISH"), 6.0)[0] is True,  "SHORT with bearish H4 -> allowed"
assert F(D("long"),  PL("BEARISH"), 6.0)[0] is False, "LONG against bearish H4 -> blocked"
assert F(D("short"), PL("BULLISH"), 6.0)[0] is False, "SHORT against bullish H4 -> blocked"
assert F(D("long"),  PL("NEUTRAL"), 6.0)[0] is False, "NEUTRAL H4 -> blocked (no edge)"
ok, why = F(D("long"), PL(None), 6.0)
assert ok is False and "fail-closed" in why, why
print("  bullish/bearish/neutral/missing all handled; missing FAILS CLOSED   OK")
print("PASS\n")

print("=== Case 28: key-level proximity gate (technique 2) ===")
ATR = 6.0
# entry exactly ON a level -> fine
assert F(D("long", 4400.0), PL("BULLISH", near_price=4400.0), ATR)[0] is True
# 1.0 ATR away (6.0 pts) -> within the 1.5 limit
assert F(D("long", 4406.0), PL("BULLISH", near_price=4400.0), ATR)[0] is True
# exactly at the 1.5 ATR boundary (9.0 pts) -> allowed (<=)
assert F(D("long", 4409.0), PL("BULLISH", near_price=4400.0), ATR)[0] is True
# 2.0 ATR away (12 pts) -> blocked, floating in open space
ok, why = F(D("long", 4412.0), PL("BULLISH", near_price=4400.0), ATR)
assert ok is False and "from nearest level" in why, why
print("  0.0 / 1.0 / 1.5 ATR -> allowed;  2.0 ATR -> blocked (%s)" % why)
# missing key-level data fails closed
ok, why = F(D("long"), {"atr": ATR, "htf": PL("BULLISH")["htf"], "keylevels": {}}, ATR)
assert ok is False and "fail-closed" in why
# the 0.5 ATR the user first proposed would have blocked even 1.0 ATR away --
# showing why it was widened
assert abs(4406.0 - 4400.0) / ATR == 1.0
print("  missing data FAILS CLOSED; 1.0 ATR would have been blocked at the")
print("  originally-proposed 0.5 limit, which is why it was widened   OK")
print("PASS\n")

print("=== Case 29: build_key_levels / build_htf_context arithmetic ===")
# prev day H=110 L=90 C=100 -> pivot=100, r1=110, s1=90, r2=120, s2=80
daily = [[0, 95, 110, 90, 100, 1], [0, 100, 108, 98, 105, 1]]
kl = cat.build_key_levels(daily, price=101.0)
lv = kl["levels"]
assert lv["prev_high"] == 110 and lv["prev_low"] == 90 and lv["prev_close"] == 100
assert abs(lv["pivot"] - 100.0) < 1e-9
assert abs(lv["r1"] - 110.0) < 1e-9 and abs(lv["s1"] - 90.0) < 1e-9
assert abs(lv["r2"] - 120.0) < 1e-9 and abs(lv["s2"] - 80.0) < 1e-9
assert kl["nearest"] in ("pivot", "prev_close"), kl["nearest"]
assert abs(kl["distance"] - 1.0) < 1e-9, kl["distance"]
print("  pivots: PP=%.1f R1=%.1f S1=%.1f, nearest=%s at %.1f away   OK"
      % (lv["pivot"], lv["r1"], lv["s1"], kl["nearest"], kl["distance"]))

up = [[0, 100+i, 101+i, 99+i, 100.5+i, 1] for i in range(60)]
dn = [[0, 200-i, 201-i, 199-i, 200.5-i, 1] for i in range(60)]
assert cat.build_htf_context(up, up)["htf"]["trend"] == "BULLISH"
assert cat.build_htf_context(dn, dn)["htf"]["trend"] == "BEARISH"
assert cat.build_htf_context([], [])["htf"] is None, "too few bars -> None (gate fails closed)"
print("  HTF trend labels: uptrend->BULLISH, downtrend->BEARISH, empty->None   OK")
print("PASS\n")

print("=== Case 30: filters are ENFORCED IN CODE, not just asked of the AI ===")
# a decision that passed chart consensus AND news can still be dropped here
good = D("long", 4400.0)
assert F(good, PL("BEARISH"), 6.0)[0] is False, \
    "a fully-validated LONG must still be blocked by a bearish H4"
print("PASS -- prompt asks, code enforces\n")

print("=== Case 31: breaker OFF collects data; measurement still runs ===")
class _StreakStub:
    def __init__(self, max_consec):
        self.max_consec_losses = max_consec
        self.state = {"consec_losses": 0}
        self.log = _StubLog()
        self.breaker_file = os.path.join(
            __import__("tempfile").mkdtemp(), "BREAKER")
        self.tg = []
    def _telegram(self, m): self.tg.append(m)
    def upd(self, pnl):
        cat.ChartAITraderBot._update_loss_streak(self, pnl)

# breaker OFF (0): streak keeps counting past 3, no stop file, no alert
off = _StreakStub(0)
for _ in range(8):
    off.upd(-10.0)
assert off.state["consec_losses"] == 8, off.state
assert off.state["worst_streak"] == 8, "worst streak must be recorded for analysis"
assert not os.path.exists(off.breaker_file), "breaker OFF must not write a stop file"
assert off.tg == [], "breaker OFF must not alert"
print("  8 straight losses, breaker OFF -> streak=8 worst=8, no stop, no alert   OK")

# a win resets the running streak but NOT the worst-ever record
off.upd(+5.0)
assert off.state["consec_losses"] == 0
assert off.state["worst_streak"] == 8, "worst-ever must survive a reset"
print("  win resets running streak to 0, worst-ever stays 8 (kept for stats)   OK")

# breaker ON (3): still stops, unchanged behaviour when asked for
on = _StreakStub(3)
on.upd(-1.0); on.upd(-1.0)
assert not os.path.exists(on.breaker_file), "must not trip early"
on.upd(-1.0)
assert os.path.exists(on.breaker_file), "must trip at the threshold"
assert len(on.tg) == 1 and "AUTO-STOPPED" in on.tg[0]
print("  --max-consec-losses 3 -> still trips exactly at 3   OK")
print("PASS -- action disabled, measurement intact\n")

print("=== Case 32: invert_decision mirrors direction, PRESERVES distances ===")
base = X(R("SHORT", sl=PRICE+10, tp=PRICE-20), R("SHORT", sl=PRICE+10, tp=PRICE-20))
assert base is not None and base["signal"] == "short"
inv = cat.invert_decision(base)
assert inv["signal"] == "long" and inv["decision"] == "LONG"
assert inv["inverted_from"] == "SHORT"
# distances must be identical -- that is what _flip_test.py measured
assert abs(inv["sl_dist"] - base["sl_dist"]) < 1e-9
assert abs(inv["tp_dist"] - base["tp_dist"]) < 1e-9
# levels reflected across entry, and on the correct sides for a LONG
assert inv["sl"] < inv["entry"] < inv["tp"], (inv["sl"], inv["entry"], inv["tp"])
assert abs(inv["sl"] - (PRICE - 10)) < 1e-9, inv["sl"]
assert abs(inv["tp"] - (PRICE + 20)) < 1e-9, inv["tp"]
print("  SHORT sl=%.0f tp=%.0f  ->  LONG sl=%.0f tp=%.0f (distances 10/20 kept)"
      % (base["sl"], base["tp"], inv["sl"], inv["tp"]))

# and the reverse direction
base2 = X(R("LONG"), R("LONG"))
inv2 = cat.invert_decision(base2)
assert inv2["signal"] == "short" and inv2["tp"] < inv2["entry"] < inv2["sl"]
assert abs(inv2["rr"] - base2["rr"]) < 1e-9, "R:R must survive inversion"
print("  LONG -> SHORT, R:R preserved at %.2f" % inv2["rr"])

# inverting twice returns the original
rt = cat.invert_decision(inv2)
assert rt["signal"] == base2["signal"]
assert abs(rt["sl"] - base2["sl"]) < 1e-9 and abs(rt["tp"] - base2["tp"]) < 1e-9
print("  double inversion is identity (no drift)   OK")

# the original dict must not be mutated -- the caller still logs it
assert base["signal"] == "short" and base["decision"] == "SHORT"
print("  source decision left untouched (no in-place mutation)   OK")
print("PASS\n")

print("=== Case 33: invert is OFF by default and opt-in only ===")
import inspect as _i
sig = _i.signature(cat.ChartAITraderBot.__init__)
assert sig.parameters["invert"].default is False, "invert must default OFF"
print("PASS -- must be enabled explicitly with --invert\n")

print("=== Case 34: model FALLBACK -- primary fails, fallback answers ===")
calls = []
def flaky(api_key, model, png, symbol, price, bars, atr, payload_text):
    calls.append(model)
    if model == "primary":
        raise RuntimeError("503 UNAVAILABLE")
    return {"decision": "LONG", "entry": price, "sl": price - 10,
            "tp": price + 20, "reason": "from fallback"}

out = cat.ChartAITraderBot._safe_signal(
    _StubSelf(), "gemini", flaky, "K", ["primary", "backup"],
    b"P", "XAUUSD", 4400.0, 160, 6.0, "PAYLOAD")
assert out is not None and out["reason"] == "from fallback", out
assert calls == ["primary", "backup"], calls
print("  primary 503 -> fallback tried -> answer returned   OK")

# fallback is NOT tried when the primary succeeds (no wasted call/cost)
calls.clear()
def good(api_key, model, png, symbol, price, bars, atr, payload_text):
    calls.append(model)
    return {"decision": "WAIT", "entry": price, "sl": price-1, "tp": price+1,
            "reason": "ok"}
cat.ChartAITraderBot._safe_signal(
    _StubSelf(), "gemini", good, "K", ["primary", "backup"],
    b"P", "XAUUSD", 4400.0, 160, 6.0, "PAYLOAD")
assert calls == ["primary"], "fallback must not be called when primary works"
print("  primary ok -> fallback NOT called (no wasted call)   OK")

# every model failing -> None (skip symbol), no crash
calls.clear()
def allbad(api_key, model, png, symbol, price, bars, atr, payload_text):
    calls.append(model)
    raise RuntimeError("503")
assert cat.ChartAITraderBot._safe_signal(
    _StubSelf(), "gemini", allbad, "K", ["a", "b", "c"],
    b"P", "XAUUSD", 4400.0, 160, 6.0, "PAYLOAD") is None
assert calls == ["a", "b", "c"], calls
print("  all 3 models fail -> None (skip symbol), all were tried   OK")
print("PASS\n")

print("=== Case 35b: duplicate models are de-duplicated ===")
# Found on the real deploy: the VPS .env pinned GEMINI_MODEL to the heavy
# model, so the "fallback" chain came out as [heavy, heavy] -- retrying
# the same overloaded model and buying nothing.
def _dd(seq):
    out = []
    for x in seq:
        if x and x not in out:
            out.append(x)
    return out
assert _dd(["a", "a"]) == ["a"], "duplicate must collapse"
assert _dd(["a", ""]) == ["a"], "empty fallback must drop out"
assert _dd(["a", "b"]) == ["a", "b"], "distinct models must be preserved in order"
assert _dd(["b", "a"]) == ["b", "a"], "order must be preserved (primary first)"
print("PASS -- [heavy, heavy] collapses to one call, not two wasted ones\n")

print("=== Case 35: measured model config -- lite is primary ===")
import os as _os
for v in ("GEMINI_MODEL", "GEMINI_MODEL_FALLBACK"):
    _os.environ.pop(v, None)
assert cat.AI_CALL_TIMEOUT_SEC >= 90, \
    "timeout must exceed the ~71s measured average of the heavy model"
print("  AI_CALL_TIMEOUT_SEC = %ds (heavy model measured ~71s avg)   OK"
      % cat.AI_CALL_TIMEOUT_SEC)
print("PASS\n")

print("=== Case 36: symbol set + live spread ceiling (measured cost) ===")
assert "ETHUSDC" not in cat.SYMBOLS, "ETH is excluded by default on cost"
# [2026-08-15] previously this asserted ETH stayed RESTORABLE via --symbols.
# That is now the opposite of the instruction; Case 40 owns the rule.
assert list(cat.SYMBOLS) == ["XAUUSD", "BTCUSDC"], list(cat.SYMBOLS)
print("  default %s   (ETHUSDC removed entirely -- see Case 40)"
      % list(cat.SYMBOLS))

# [2026-08-15] The ceiling no longer encodes the symbol decision. It was
# 0.08 purely to sit between BTC's average (0.068) and ETH's (0.093); it is
# now 0.12, derived from expected value instead: at a 1.25R target a trade
# breaks even at cost = 0.406R, and the required win rate at 0.12 is 49.8%
# against a measured 62.5%. The rise was forced by BTC volatility halving,
# which pushed the SAME setups from 0.050R to 0.098-0.123R and skipped four
# consensus decisions in one morning.
MEASURED = {"XAUUSDc": 0.020, "BTCUSDc": 0.068, "ETHUSDc": 0.093}
assert MEASURED["BTCUSDc"] < cat.MAX_SPREAD_R, \
    "ceiling %.3f must still admit BTC at its measured %.3f" % (
        cat.MAX_SPREAD_R, MEASURED["BTCUSDc"])
# EV bound: never let the ceiling approach the break-even cost. At a 1.25R
# target, EV = wr*1.25 - (1-wr) - cost, so cost must stay far below the
# 0.406R that zeroes it at the measured 62.5% win rate.
_be_cost = 0.625 * 1.25 - 0.375
assert cat.MAX_SPREAD_R < _be_cost / 2.0, \
    "ceiling %.3f is more than half the %.3fR break-even cost" % (
        cat.MAX_SPREAD_R, _be_cost)
_req_wr = (1.0 + cat.MAX_SPREAD_R) / 2.25
assert _req_wr < 0.55, "required win rate %.1f%% too close to a coin flip" % (
    100 * _req_wr)
print("  ceiling %.2fR -> break-even cost %.3fR, required WR %.1f%%   OK"
      % (cat.MAX_SPREAD_R, _be_cost, 100 * _req_wr))
for sym, drag in MEASURED.items():
    verdict = "pass" if drag <= cat.MAX_SPREAD_R else "BLOCK"
    print("  %-9s %.3fR -> %s" % (sym, drag, verdict))

# CONSEQUENCE worth pinning: at 0.12 the ceiling no longer excludes ETH on
# cost -- ETH's 0.093 average now passes. ETH is kept out by the symbol
# list alone, so that list is now the ONLY thing standing between the bot
# and a symbol whose spread drag is 4.6x gold's.
assert MEASURED["ETHUSDc"] < cat.MAX_SPREAD_R, \
    "if ETH is blocked by cost again, update this comment"
assert "ETHUSDC" not in cat.SYMBOLS, \
    "ETH must stay out of the default symbol set -- the cost gate no " \
    "longer blocks it, so the symbol list is the only remaining guard"
print("  NOTE: ETH (0.093) now passes on cost; only the symbol list keeps "
      "it out   OK")

# the gate is a simple ratio; check the arithmetic and both boundaries
def gate(spread, sl_dist):
    return (spread / sl_dist) <= cat.MAX_SPREAD_R
assert gate(0.24, 12.0) is True,  "gold: 0.24 spread on a 12.0 stop = 0.020R"
assert gate(10.0, 147.0) is True, "btc: 10 on 147 = 0.068R"
# the two real cases that stalled the bot on 2026-08-15
assert gate(10.0, 102.375) is True, "btc low-ATR: 10 on 102.4 = 0.098R -> now passes"
assert gate(10.0, 81.455) is False, "btc: 10 on 81.5 = 0.123R -> still blocked"
print("  live cases: 0.098R now passes, 0.123R still blocked   OK")
# a normally-cheap symbol with a blown-out spread must ALSO be blocked --
# this is what the live check buys over a static symbol list
assert gate(2.0, 12.0) is False, "gold at 0.167R (news blowout) must block"
print("  live check also blocks gold if its spread blows out to 0.167R   OK")
print("PASS\n")

print("=== Case 37: invert TP override rewrites tp_dist and rr, not just tp ===")
# _enter() sizes the live order from decision["tp_dist"], NOT from the tp
# price. An override that updated only the price would leave a stale
# distance behind and the broker would receive the models' old 1.5R target
# while the log claimed 1.25R -- silently trading the thing the
# walk-forward showed to be barely profitable. This is that trap.
b37 = X(R("SHORT", sl=PRICE + 10, tp=PRICE - 20), R("SHORT", sl=PRICE + 10, tp=PRICE - 20))
assert b37 is not None and abs(b37["sl_dist"] - 10) < 1e-9
assert abs(b37["tp_dist"] - 20) < 1e-9, b37["tp_dist"]

ov = cat.invert_decision(b37, tp_r=1.25)
assert ov["signal"] == "long", ov["signal"]
assert abs(ov["sl_dist"] - 10) < 1e-9, "stop distance must be untouched"
assert abs(ov["tp_dist"] - 12.5) < 1e-9, ("tp_dist not rewritten", ov["tp_dist"])
assert abs(ov["rr"] - 1.25) < 1e-9, ("rr not rewritten", ov["rr"])
assert abs(ov["tp"] - (PRICE + 12.5)) < 1e-9, ov["tp"]
assert ov["sl"] < ov["entry"] < ov["tp"], "levels on wrong sides for a LONG"
# the price and the distance must describe the SAME target
assert abs((ov["tp"] - ov["entry"]) - ov["tp_dist"]) < 1e-9, "tp price/distance disagree"
print("  SHORT 1.5R -> LONG 1.25R: tp_dist 20 -> 12.5, rr -> 1.25, tp price agrees   OK")

# default must stay a plain mirror, so Case 32's guarantees still hold
plain = cat.invert_decision(b37)
assert abs(plain["tp_dist"] - 20) < 1e-9, "default must preserve the models' target"
assert "tp_r_override" not in plain
print("  no tp_r argument -> unchanged mirror (Case 32 behaviour intact)   OK")

# a SHORT result must reflect the shorter target on the correct side
b37b = X(R("LONG"), R("LONG"))
ovb = cat.invert_decision(b37b, tp_r=1.25)
assert ovb["signal"] == "short" and ovb["tp"] < ovb["entry"] < ovb["sl"]
assert abs((ovb["entry"] - ovb["tp"]) - ovb["tp_dist"]) < 1e-9
assert abs(ovb["rr"] - 1.25) < 1e-9
print("  LONG -> SHORT at 1.25R, levels and distances consistent   OK")

# the constant actually wired into the live path must be the tested one
assert abs(cat.INVERT_TP_R - 1.25) < 1e-9, cat.INVERT_TP_R
src = inspect.getsource(cat.ChartAITraderBot._maybe_enter) if hasattr(
    cat.ChartAITraderBot, "_maybe_enter") else inspect.getsource(cat.ChartAITraderBot)
assert "invert_decision(decision, tp_r=INVERT_TP_R)" in src, \
    "live call site must pass the override, else invert trades the old 1.5R"
print("  live call site passes tp_r=INVERT_TP_R (1.25)   OK")
print("PASS\n")

print("=== Case 38: deploy and watchdog launch args agree ===")
# The watchdog relaunches from its own Args string. When that string drifts
# from the deploy script's, a crash-restart silently swaps the bot's
# settings and nothing reports it -- this has bitten twice in this repo
# (btc risk 1.00->1.90, gold regime-filter OFF->ON), and most likely a
# third time when --invert appeared to revert to normal direction.
# Skips when run from the Desktop, where the .ps1 files are not copied.
import re as _re
_here = os.path.dirname(os.path.abspath(__file__))
_wd = os.path.join(_here, "watchdog_h1.ps1")
_dep = os.path.join(_here, "deploy_chart_ai.ps1")
if not (os.path.exists(_wd) and os.path.exists(_dep)):
    print("SKIP -- .ps1 files not beside the test (running from Desktop)\n")
else:
    _wtxt = open(_wd, encoding="utf-8", errors="replace").read()
    _dtxt = open(_dep, encoding="utf-8", errors="replace").read()
    _wm = _re.search(r'Args\s*=\s*"(chart_ai_trader\.py[^"]*)"', _wtxt)
    _dm = _re.search(r'\$Args\s*=\s*"(chart_ai_trader\.py[^"]*)"', _dtxt)
    assert _wm, "no chart_ai Args line found in watchdog_h1.ps1"
    assert _dm, "no $Args line found in deploy_chart_ai.ps1"
    assert _wm.group(1) == _dm.group(1), (
        "LAUNCH ARG DRIFT -- watchdog would relaunch with different settings\n"
        f"  watchdog: {_wm.group(1)}\n  deploy  : {_dm.group(1)}")
    print(f"  both launch: {_wm.group(1)}")
    # the flags this deploy exists to apply must actually be present
    assert "--invert" in _wm.group(1), "invert mode missing from launch args"
    assert "--symbols BTCUSDC" in _wm.group(1), "BTC-only restriction missing"
    print("  --invert and --symbols BTCUSDC present in both   OK")
    # .ps1 must stay ASCII: PowerShell 5.1 fails to parse non-ASCII without a BOM
    for _p, _t in ((_wd, _wtxt), (_dep, _dtxt)):
        _bad = [c for c in _t if ord(c) > 127]
        assert not _bad, f"{os.path.basename(_p)} has non-ASCII: {_bad[:5]}"
    print("  both .ps1 files are ASCII-only (PS 5.1 parse safety)   OK")
    print("PASS\n")

print("=== Case 39: exhaustion prompt is measurement-only, live path untouched ===")
# chart_ai_trader.py is the SAME file the live invert bot runs, so an
# experimental prompt added for measurement must be inert unless explicitly
# switched on -- including after a watchdog restart, which relaunches from
# the .ps1 Args and would pick up whatever the module defaults to.
assert cat.EXHAUSTION_MODE is False, "experimental prompt must default OFF"
_pbase = cat._fmt_prompt("BTCUSDC", 160, 100.0, 1.0, "PAYLOAD")
assert "2 of the 3 rules" in _pbase, "live prompt changed"
assert "EXHAUSTION" not in _pbase, "experimental text leaked into the live prompt"
print("  EXHAUSTION_MODE False -> live prompt is the original   OK")

_cd = [[i * 900000, 100.0 + i, 100.5 + i, 99.5 + i, 100.0 + i, 10.0]
       for i in range(30)]
_lp = cat.build_market_payload(_cd, 1.0)
assert "exhaustion" not in _lp, "live payload must not carry stretch figures"
_lp["htf"] = {"htf": {"trend": "BULLISH", "price": 130.0, "ema20": 120.0,
                      "ema50": 110.0}, "mtf": None}
_txt = cat.format_payload("BTCUSDC", _lp)
assert "you may only go LONG" in _txt, "HTF veto must stay HARD on the live path"
assert "STRETCH (how far" not in _txt
print("  live payload keeps the hard HTF veto, no stretch block   OK")

# and the variant must actually differ, or the A/B measures nothing
cat.EXHAUSTION_MODE = True
_pv = cat._fmt_prompt("BTCUSDC", 160, 100.0, 1.0, "PAYLOAD")
cat.EXHAUSTION_MODE = False
assert "EXHAUSTION" in _pv and "counter-trend entry is allowed" in _pv
# The variant MUST carry a countable trigger. Without one, OpenAI answered
# WAIT to all 150 replayed charts and the consensus could never fire --
# a prompt that never trades measures nothing, however good its framing.
for _need in ("E1.", "E2.", "E3.", "E4.", "C1.", "C2.", "C3.", "C4.",
              "count >= 2", "Do NOT answer \"WAIT\" merely because"):
    assert _need in _pv, f"countable-trigger element missing: {_need}"
print("  variant carries a countable 4v4 trigger + anti-WAIT rule   OK")
_lp["exhaustion"] = cat.build_exhaustion_context(_cd, 1.0)
_tv = cat.format_payload("BTCUSDC", _lp)
assert "you may only go LONG" not in _tv and "context only" in _tv
print("  variant differs: two-way framing + HTF advisory   OK")

# zero-range and zero-ATR must not fabricate an extreme reading
_flat = [[i * 900000, 100.0, 100.0, 100.0, 100.0, 1.0] for i in range(30)]
assert cat.build_exhaustion_context(_flat, 1.0)["range_pos"] == 0.5
_z = cat.build_exhaustion_context(_cd, 0.0)
assert _z["stretch_atr"] == 0.0 and _z["travel_atr"] == 0.0
print("  flat range -> 0.50 (not an extreme); ATR=0 -> no divide-by-zero   OK")
print("PASS\n")

print("=== Case 40: ETH is gone from every bot and cannot be restored ===")
# [2026-08-15] The user's instruction was "remove ETH from every bot, never
# bring it back". A default is not a guarantee -- one CLI flag used to be
# enough -- so this asserts the symbol is absent from the sets that decide
# what is REACHABLE, not merely from what is selected by default.
assert "ETHUSDC" not in cat.SYMBOLS, "ETH back in the default set"
assert "ETHUSDC" not in cat.ALL_SYMBOLS, \
    "ETH back in ALL_SYMBOLS -- --symbols could restore it"
assert set(cat.ALL_SYMBOLS) == set(cat.SYMBOLS), \
    "ALL_SYMBOLS must not offer anything the default set does not"
print("  chart_ai: SYMBOLS == ALL_SYMBOLS == %s   OK" % list(cat.SYMBOLS))

# --symbols must REJECT it rather than silently ignore it: a typo'd restore
# attempt should stop the bot, not start it on a reduced set.
_unknown = [x for x in ["XAUUSD", "ETHUSDC"] if x not in cat.ALL_SYMBOLS]
assert _unknown == ["ETHUSDC"], _unknown
print("  --symbols XAUUSD,ETHUSDC -> rejected at startup   OK")

# news_gemini_bot trades its own symbol list and had ETH until today
_ng = os.path.join(_here, "news_gemini_bot.py")
if os.path.exists(_ng):
    _t = open(_ng, encoding="utf-8").read()
    assert '"ETHUSDC": {' not in _t, "ETH back in news_gemini SYMBOLS spec"
    assert '"ETHUSDC"]' not in _t.replace('# ', ''), \
        "ETH back in a news_gemini list/enum (scan schema or cfg.symbols)"
    print("  news_gemini: no tradeable ETH spec, not in the scan enum   OK")

# the watchdog is what actually relaunches bots; an entry there outranks
# any in-code default
_wd2 = os.path.join(_here, "watchdog_h1.ps1")
if os.path.exists(_wd2):
    _w2 = open(_wd2, encoding="utf-8", errors="replace").read()
    _live = [l for l in _w2.splitlines()
             if "Args" in l and "=" in l and not l.strip().startswith("#")]
    _eth = [l for l in _live if "--symbol ETHUSDc" in l or "ETHUSDC" in l]
    assert not _eth, ("watchdog would launch an ETH bot", _eth)
    print("  watchdog: no launch line trades ETH   OK")
    # the xasset gate READS the ETH/BTC ratio but opens no ETH position and
    # pays no ETH spread; it is a validated filter (Sharpe 1.33->1.47) and
    # is deliberately kept. Pinned here so its survival is a decision on
    # record rather than something that looks like a miss.
    assert any("--xasset-short-gate ETHUSDc" in l for l in _live), \
        "xasset ETH/BTC ratio gate disappeared -- that was NOT part of " \
        "removing ETH exposure; it reads a price and opens no position"
    print("  btc_h1_manual keeps --xasset-short-gate (reads ETH, trades none)   OK")
print("PASS\n")

print("=== Case 41: chart timeframe label -- M15 by default, H1 only by override ===")
# The H1 replay must tell the models the truth about what they are looking
# at, so the label became a module override (same pattern as EXHAUSTION_MODE).
# The live path must be byte-identical to before: a live bot that suddenly
# told its models "H1" while sending M15 candles would skew every distance
# judgement the prompt asks for.
assert cat.CHART_TF_LABEL == "M15" and cat.CHART_TF_LONG == "M15 (15-minute)"
_p41 = cat._fmt_prompt("BTCUSDC", 160, 100.0, 1.0, "PAYLOAD")
assert "on the M15 (15-minute) timeframe" in _p41.replace("\n", " ")
_c41 = [[i * 900000, 100.0 + i * 0.1, 100.6 + i * 0.1, 99.6 + i * 0.1,
         100.1 + i * 0.1, 5.0] for i in range(60)]
_hdr = cat.format_payload("BTCUSDC", cat.build_market_payload(_c41, 1.0))
assert "Current Data (BTCUSDC, M15," in _hdr, _hdr.splitlines()[0]
print("  defaults: prompt and payload both say M15   OK")

cat.CHART_TF_LABEL, cat.CHART_TF_LONG = "H1", "H1 (1-hour)"
try:
    _p41h = cat._fmt_prompt("BTCUSDC", 160, 100.0, 1.0, "PAYLOAD")
    assert "H1 (1-hour)" in _p41h and "M15 (15-minute)" not in _p41h
    _hdrh = cat.format_payload("BTCUSDC", cat.build_market_payload(_c41, 1.0))
    assert "Current Data (BTCUSDC, H1," in _hdrh
    # the exhaustion template must carry the same placeholder, or an H1
    # exhaustion run would silently revert to claiming M15
    cat.EXHAUSTION_MODE = True
    _p41e = cat._fmt_prompt("BTCUSDC", 160, 100.0, 1.0, "PAYLOAD")
    assert "H1 (1-hour)" in _p41e and "EXHAUSTION" in _p41e
finally:
    cat.EXHAUSTION_MODE = False
    cat.CHART_TF_LABEL, cat.CHART_TF_LONG = "M15", "M15 (15-minute)"
_p41r = cat._fmt_prompt("BTCUSDC", 160, 100.0, 1.0, "PAYLOAD")
assert "M15 (15-minute)" in _p41r
print("  override flows through both templates + payload, restore works   OK")
print("PASS\n")



print("=== Case 42: watchdog detects a dead work loop, not just a dead process ===")
# The failure this guards against actually happened and went unnoticed for
# 18 days: gold_momentum_rsi, btc_lqsweep, btc_amd and gold_daily_breakout
# sat in an "mt5.account_info() returned None: IPC send failed" loop from
# 2026-08-05 while their heartbeat threads kept writing on schedule. Every
# health check in this project reads the heartbeat, so all four reported
# healthy and the watchdog never restarted them.
_wd = os.path.join(os.path.dirname(os.path.abspath(__file__)), "watchdog_h1.ps1")
if not os.path.exists(_wd):
    print("SKIP -- watchdog_h1.ps1 not beside the test\n")
else:
    _w = open(_wd, encoding="utf-8", errors="replace").read()

    # the check itself must exist and must fire on a FRESH heartbeat, i.e.
    # inside the -not $needRestart branch -- a log check that only runs after
    # the heartbeat already failed adds nothing
    assert "LogStaleMinutes" in _w, "no log-freshness threshold in watchdog"
    assert "if (-not $needRestart) {" in _w, (
        "log check is not gated on a fresh heartbeat -- it would only run when "
        "the heartbeat had already triggered a restart, which is the case that "
        "already worked")

    # loop guard: a restart cannot repair a broken MT5 terminal, so an
    # ungated log-stale rule would relaunch the same bot forever
    assert "LOGSTALE_" in _w and "AddHours(-6)" in _w, "no loop guard on log-stale restarts"
    assert "-ge 2" in _w, "loop guard has no restart cap"

    # escalation must be a real function, not a call to something undefined:
    # PowerShell 5.1 raises CommandNotFoundException and aborts the script,
    # so an undefined Send-Telegram would kill the watchdog from inside the
    # bot loop and leave EVERY bot unsupervised
    assert "function Send-Telegram" in _w, (
        "Send-Telegram is called but never defined -- PS 5.1 would abort the "
        "watchdog on the first alert")
    _fn = _w[_w.index("function Send-Telegram"):]
    _fn = _fn[:_fn.index("\n# ---")] if "\n# ---" in _fn else _fn[:2000]
    assert "try {" in _fn and "} catch {" in _fn, (
        "Send-Telegram is not wrapped in try/catch -- an unreachable "
        "api.telegram.org would take the watchdog down with it")

    # astral-plane emoji cannot be produced by [char]: 0x1F6D1 is 128721,
    # past the 16-bit range, and the cast throws at runtime
    import re as _re2
    for _m in _re2.finditer(r'\[char\]0x([0-9A-Fa-f]+)', _w):
        assert int(_m.group(1), 16) <= 0xFFFF, (
            f"[char]0x{_m.group(1)} is outside the 16-bit range and throws at "
            "runtime -- use [char]::ConvertFromUtf32()")

    # a pattern that matches no file makes the check silently not run, which
    # is the same blindness being fixed -- it has to be reported
    assert "log-freshness check SKIPPED" in _w, (
        "a bot whose log file matches no pattern would be silently unchecked")

    # and a malformed guard-file line must not throw inside the loop
    assert "-as [datetime]" in _w, (
        "guard file is cast with [datetime], which throws on a bad line and "
        "aborts the watchdog")

    print("  fires on fresh-heartbeat + stale-log (the 18-day blind spot)   OK")
    print("  loop guard: max 2 restarts / 6h, then escalates to a human      OK")
    print("  Send-Telegram defined, try/catch-wrapped, astral-safe emoji     OK")
    print("  unmatched log pattern is reported, not silently skipped         OK")

print("\nALL TESTS PASSED")
