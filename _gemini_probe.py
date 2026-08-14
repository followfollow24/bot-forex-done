#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_gemini_probe.py -- is Gemini's 503 rate a Google-side problem or ours?

chart_ai_trader logged 6 failures in 15 calls (~40%) right after the
invert restart, all of them:
    503 UNAVAILABLE "This model is currently experiencing high demand"

That specific pairing matters. Quota exhaustion returns 429
RESOURCE_EXHAUSTED, and bad auth returns 400/403 -- so 503 + "high
demand" points at Google-side capacity for THAT MODEL, not at our key,
our billing or our request shape. This script checks that claim instead
of assuming it, and tests whether a different model id is healthier,
because the bot currently asks for "gemini-flash-latest": a floating
alias that can resolve to whatever build is under the most load.

It sends small text-only prompts (a few tokens each, negligible cost) --
enough to measure availability without exercising the vision path.

Usage (on the VPS):  python _gemini_probe.py [rounds]
"""
import os
import sys
import time
from collections import Counter

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

KEY = os.environ.get("GEMINI_API_KEY", "")
if not KEY:
    print("[ERROR] GEMINI_API_KEY not set (expected in .env next to this file)")
    sys.exit(1)

# the alias the bot uses, plus pinned/alternate ids to compare against.
CANDIDATES = [
    os.environ.get("GEMINI_MODEL", "gemini-flash-latest"),
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-flash-lite-latest",
]
ROUNDS = int(sys.argv[1]) if len(sys.argv) > 1 else 5


def classify(exc: Exception) -> str:
    m = str(exc)
    if "503" in m or "UNAVAILABLE" in m:
        return "503 overloaded"
    if "429" in m or "RESOURCE_EXHAUSTED" in m:
        return "429 quota"
    if "404" in m or "NOT_FOUND" in m:
        return "404 no such model"
    if "403" in m or "PERMISSION" in m:
        return "403 auth/permission"
    if "400" in m:
        return "400 bad request"
    return f"other: {m[:60]}"


def main():
    from google import genai
    client = genai.Client(api_key=KEY)

    print("=" * 72)
    print(f" GEMINI PROBE -- {ROUNDS} calls per model, small text prompts")
    print("=" * 72)
    print(f"{'model':<28}{'ok':>4}{'503':>6}{'429':>6}{'other':>7}{'avg ms':>9}")
    print("-" * 72)

    seen = []
    for model in CANDIDATES:
        if model in seen:
            continue
        seen.append(model)
        res = Counter()
        lat = []
        for _ in range(ROUNDS):
            t0 = time.time()
            try:
                client.models.generate_content(model=model, contents="Reply with: ok")
                res["ok"] += 1
                lat.append((time.time() - t0) * 1000)
            except Exception as e:
                res[classify(e)] += 1
            time.sleep(1.0)          # be a polite citizen while measuring
        avg = f"{sum(lat)/len(lat):.0f}" if lat else "-"
        other = sum(v for k, v in res.items()
                    if k not in ("ok", "503 overloaded", "429 quota"))
        print(f"{model:<28}{res['ok']:>4}{res['503 overloaded']:>6}"
              f"{res['429 quota']:>6}{other:>7}{avg:>9}")
        for k, v in res.items():
            if k.startswith("other") or k.startswith("40"):
                print(f"    -> {k} x{v}")

    print("-" * 72)
    print("  503 on the alias but ok on a pinned id  -> switch GEMINI_MODEL")
    print("  503 everywhere                          -> Google-side, wait it out")
    print("  429                                     -> our quota, not capacity")
    print("  404                                     -> that id is not available")


if __name__ == "__main__":
    main()
