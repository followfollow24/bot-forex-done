"""Control test: does the entry prompt actually DISCRIMINATE, or does it
just say the same thing regardless of what's on the chart?

Feeds three synthetic charts to both providers:
  1. strong clean uptrend      -> expect "long" (or a cautious "none")
  2. strong clean downtrend    -> expect "short" (or a cautious "none")
  3. pure random noise/chop    -> expect "none" (MUST NOT be a confident
                                  directional call -- that would mean the
                                  bot invents setups out of noise, which
                                  is the single most dangerous failure
                                  mode for this whole design)
"""
import sys, os, math, random
sys.path.insert(0, "/Users/follow/Desktop/outputs/bot forex")
os.environ.pop("TELEGRAM_BOT_TOKEN", None)
os.environ.pop("TELEGRAM_CHAT_ID", None)

from dotenv import load_dotenv
load_dotenv("/Users/follow/Desktop/outputs/bot forex/.env")

import chart_ai_trader as cat
from news_gemini_bot import render_chart_png

GK = os.environ.get("GEMINI_API_KEY", "")
OK_ = os.environ.get("OPENAI_API_KEY", "")
print("gemini key present:", bool(GK), " openai key present:", bool(OK_))
if not GK or not OK_:
    # A control test that silently makes zero API calls and then prints
    # "LOOKS SANE" is worse than no test -- it manufactures false
    # confidence in exactly the check that matters most here (does the
    # prompt actually discriminate, or does it hallucinate setups out of
    # noise?). Fail loudly instead. Both keys live in the VPS .env, not
    # the local one, so this must be run ON THE VPS.
    print("\nABORT: this control test requires BOTH GEMINI_API_KEY and "
         "OPENAI_API_KEY. They are only present in the VPS .env, not "
         "locally -- run this script on the VPS.")
    sys.exit(1)

def make_candles(kind, n=120, start=100.0):
    random.seed(42)
    out, px = [], start
    for i in range(n):
        if kind == "up":
            drift = 0.45
        elif kind == "down":
            drift = -0.45
        else:
            drift = 0.0
        noise = random.uniform(-0.5, 0.5)
        o = px
        px = max(1.0, px + drift + noise)
        c = px
        h = max(o, c) + abs(random.uniform(0, 0.3))
        l = min(o, c) - abs(random.uniform(0, 0.3))
        out.append([1700000000000 + i * 3600000, o, h, l, c, 1000])
    return out

cases = [
    ("CLEAN UPTREND", "up", ("long", "none")),
    ("CLEAN DOWNTREND", "down", ("short", "none")),
    ("PURE CHOP/NOISE", "chop", ("none",)),
]

results = {}
for label, kind, acceptable in cases:
    candles = make_candles(kind)
    png = render_chart_png(candles, "TESTSYM", "")
    price = float(candles[-1][4])
    print("\n" + "=" * 62)
    print(f"{label}  (last price {price:.2f})")
    print("=" * 62)
    row = {}
    if GK:
        try:
            g = cat.gemini_chart_signal(GK, "gemini-flash-latest", png, "TESTSYM", price, len(candles))
            print(f"  gemini: {g.get('signal'):6} conf={g.get('confidence')}  :: {str(g.get('reasoning'))[:150]}")
            row["gemini"] = g
        except Exception as e:
            print(f"  gemini FAILED: {str(e)[:200]}")
    if OK_:
        try:
            o = cat.openai_chart_signal(OK_, "gpt-5-mini", png, "TESTSYM", price, len(candles))
            print(f"  openai: {o.get('signal'):6} conf={o.get('confidence')}  :: {str(o.get('reasoning'))[:150]}")
            row["openai"] = o
        except Exception as e:
            print(f"  openai FAILED: {str(e)[:200]}")
    if "gemini" in row and "openai" in row:
        d = cat.cross_check_signal(row["gemini"], row["openai"])
        print(f"  --> CONSENSUS: {d['signal'] + ' conf=' + str(round(d['confidence'],2)) if d else 'NO TRADE'}")
        row["consensus"] = d
    row["acceptable"] = acceptable
    results[label] = row

print("\n" + "#" * 62)
print("VERDICT")
print("#" * 62)
ok = True
for label, row in results.items():
    acc = row["acceptable"]
    for prov in ("gemini", "openai"):
        if prov in row:
            sig = row[prov].get("signal")
            good = sig in acc
            print(f"  {label:18} {prov:7} -> {sig:6} {'OK' if good else 'UNEXPECTED (wanted ' + '/'.join(acc) + ')'}")
            if not good:
                ok = False
chop = results.get("PURE CHOP/NOISE", {})
if chop.get("consensus"):
    print("\n  !! CRITICAL: the bot would TRADE pure random noise -- that is the")
    print("     dangerous failure mode. Do not deploy without fixing the prompt.")
    ok = False
else:
    print("\n  Noise chart correctly produced NO TRADE consensus.")
print("\nOVERALL:", "LOOKS SANE" if ok else "NEEDS REVIEW")
