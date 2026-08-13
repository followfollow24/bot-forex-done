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
    # use the REAL timeout wrapper so these tests exercise the actual
    # call path (including the thread-pool hop), not a simplified stand-in
    _call_with_timeout = cat.ChartAITraderBot._call_with_timeout

out = cat.ChartAITraderBot._safe_signal(
    _StubSelf(), "gemini", fake_provider, "KEY", "MODEL",
    b"PNGBYTES", "XAUUSD", 4390.5, 160, 5.25, "PAYLOAD")
assert out is not None, "_safe_signal must forward the call, not swallow an arity error"
assert captured["atr"] == 5.25, "atr must actually reach the provider function"
assert captured["payload_text"] == "PAYLOAD", "numeric payload must reach the provider"
assert captured["symbol"] == "XAUUSD" and captured["bars"] == 160
assert captured["price"] == 4390.5 and captured["png"] == b"PNGBYTES"
print("PASS -- _safe_signal forwards all 7 args including atr")

# and the two REAL provider functions must accept exactly what it forwards
fwd = ["api_key", "model", "png", "symbol", "price", "bars", "atr",
       "payload_text"]
for fn in (cat.gemini_chart_signal, cat.openai_chart_signal):
    params = list(inspect.signature(fn).parameters)
    assert params == fwd, "%s signature %s != forwarded %s" % (fn.__name__, params, fwd)
    print("  %s(%s) OK" % (fn.__name__, ", ".join(params)))

# _safe_signal must accept exactly what _evaluate_symbol passes it
sig = list(inspect.signature(cat.ChartAITraderBot._safe_signal).parameters)
assert sig == ["self", "name", "fn", "api_key", "model", "png", "symbol",
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
    _StubSelf(), "gemini", hanging_provider, "K", "M", b"P", "XAUUSD",
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

print("ALL TESTS PASSED")
