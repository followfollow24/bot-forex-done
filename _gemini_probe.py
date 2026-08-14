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

[2026-08-14] Now probes the VISION path too, which is the gap that made
the first version's verdict unsafe to act on. The bot sends a rendered
chart PNG on every call; the original probe sent a few tokens of text.
Those are not the same request to a capacity-limited service, and after
switching to the lite model on the text-only result the bot still logged
503s -- exactly what you would expect if image calls are rationed
separately. So each model is now measured BOTH ways and the two rates
printed side by side. If vision fails far more than text, the fix is not
a different model id.

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


def make_chart_png() -> bytes:
    """A PNG of roughly the size and shape the bot really sends, so the
    probe exercises the same code path and payload class rather than a
    token-sized stand-in."""
    import io
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import random
    random.seed(7)                       # fixed: the image must not vary
    px, series = 100.0, []
    for _ in range(160):
        px += random.uniform(-1, 1)
        series.append(px)
    fig, ax = plt.subplots(figsize=(12, 6), dpi=100)
    ax.plot(series, linewidth=0.9)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


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

    from google.genai import types
    png = make_chart_png()
    print("=" * 78)
    print(f" GEMINI PROBE -- {ROUNDS} calls per model per mode")
    print(f" TEXT = a few tokens.  VISION = {len(png)//1024} KB chart PNG "
          f"(what the bot actually sends)")
    print("=" * 78)
    print(f"{'model':<26}{'mode':<8}{'ok':>4}{'503':>6}{'429':>6}"
          f"{'other':>7}{'avg ms':>9}")
    print("-" * 78)

    seen = []
    for model in CANDIDATES:
        if model in seen:
            continue
        seen.append(model)
        for mode in ("text", "vision"):
            res = Counter()
            lat = []
            for _ in range(ROUNDS):
                t0 = time.time()
                try:
                    if mode == "text":
                        client.models.generate_content(
                            model=model, contents="Reply with: ok")
                    else:
                        client.models.generate_content(
                            model=model,
                            contents=[types.Part.from_bytes(
                                data=png, mime_type="image/png"),
                                "Reply with one word describing the trend."])
                    res["ok"] += 1
                    lat.append((time.time() - t0) * 1000)
                except Exception as e:
                    res[classify(e)] += 1
                time.sleep(1.0)      # be a polite citizen while measuring
            avg = f"{sum(lat)/len(lat):.0f}" if lat else "-"
            other = sum(v for k, v in res.items()
                        if k not in ("ok", "503 overloaded", "429 quota"))
            print(f"{model:<26}{mode:<8}{res['ok']:>4}"
                  f"{res['503 overloaded']:>6}{res['429 quota']:>6}"
                  f"{other:>7}{avg:>9}")
            for k, v in res.items():
                if k.startswith("other") or k.startswith("40"):
                    print(f"    -> {k} x{v}")

    print("-" * 78)
    print("  vision fails but text is fine -> image calls are rationed harder;")
    print("     changing model id will NOT fix it. Reduce image size/frequency,")
    print("     or lean on the fallback chain and accept skipped cycles.")
    print("  both fail on one id, fine on another -> switch GEMINI_MODEL")
    print("  both fail everywhere -> Google-side capacity, wait it out")
    print("  429 -> our quota after all, not capacity")


if __name__ == "__main__":
    main()
