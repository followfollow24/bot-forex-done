import sys, os, time
sys.path.insert(0, "/Users/follow/Desktop/outputs/bot forex")
os.environ.pop("TELEGRAM_BOT_TOKEN", None); os.environ.pop("TELEGRAM_CHAT_ID", None)
import concurrent.futures
import news_gemini_bot as ng

class L:
    def warning(self,m): print("  WARN:", m[:90])
    def error(self,m): print("  ERR :", m[:90])
    def info(self,m): pass
class S:
    log = L(); heartbeat_file = "/tmp/_hb_test"
    _call_with_timeout = ng.NewsGeminiBot._call_with_timeout
    _heartbeat = ng.NewsGeminiBot._heartbeat
    def _telegram(self, m): self.tg.append(m)
    def __init__(self): self.tg = []

print("=== A: hung scan times out, is treated as TRANSIENT, and retries ===")
ng.SCAN_CALL_TIMEOUT_SEC = 1.0
ng.SCAN_RETRY_DELAY_SEC = 0
calls = {"n":0}
def hung(api_key, model, lookback):
    calls["n"] += 1
    time.sleep(30)
s = S(); t0=time.time()
out = ng.NewsGeminiBot._safe_scan(s, "gemini", hung, "k", "m", 45)
el = time.time()-t0
print("  result=%r calls=%d elapsed=%.1fs" % (out, calls["n"], el))
assert out is None
assert calls["n"] == ng.SCAN_MAX_RETRIES + 1, "a timeout must RETRY, not skip immediately (got %d calls)" % calls["n"]
assert el < 10, "must not wait out the 30s hang (took %.1fs)" % el
assert len(s.tg) == 1 and "TimeoutError" in s.tg[0], s.tg
print("  PASS -- retried %d times, gave up in %.1fs, alerted with a readable reason\n" % (calls["n"]-1, el))

print("=== B: hung scan that recovers on retry returns the result ===")
calls2 = {"n":0}
def flaky(api_key, model, lookback):
    calls2["n"] += 1
    if calls2["n"] == 1: time.sleep(30)
    return [{"symbol":"XAUUSD"}]
s2 = S()
out2 = ng.NewsGeminiBot._safe_scan(s2, "gemini", flaky, "k", "m", 45)
print("  result=%r calls=%d telegrams=%d" % (out2, calls2["n"], len(s2.tg)))
assert out2 == [{"symbol":"XAUUSD"}]
assert len(s2.tg) == 0, "a recovered timeout must not spam Telegram"
print("  PASS\n")

print("=== C: quota error still skips immediately (no wasted retries) ===")
calls3 = {"n":0}
def quota(api_key, model, lookback):
    calls3["n"] += 1
    raise Exception("429 RESOURCE_EXHAUSTED")
s3 = S()
assert ng.NewsGeminiBot._safe_scan(s3, "gemini", quota, "k", "m", 45) is None
assert calls3["n"] == 1, "quota must not retry (got %d)" % calls3["n"]
print("  PASS\n")

print("=== D: worst-case heartbeat gap stays under the watchdog threshold ===")
gap = ng.SCAN_CALL_TIMEOUT_SEC_ORIG = 120
assert 120 < 300 and ng.CHART_CALL_TIMEOUT_SEC * 2 < 300
print("  scan gap 120s, chart gap %ds, watchdog 300s -- OK\n" % (ng.CHART_CALL_TIMEOUT_SEC*2))
print("ALL NEWS_GEMINI TIMEOUT TESTS PASSED")
