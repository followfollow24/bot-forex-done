#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_edge_watch.py -- has the anti-predictive signature the invert bot lives on
                  quietly disappeared?

chart_ai_trader is profitable only if the models keep being WRONG about BTC
direction in a systematic way. That is not a property of our code -- it is a
property of models we do not control, reached through floating aliases
("gemini-flash-lite-latest", "gpt-5-mini"). Google or OpenAI can swap what
sits behind those names on any given day. If the habit we are exploiting
goes away, nothing in the bot changes, no error is logged, and no alert
fires: it simply starts losing, and the only signal is the P&L, weeks later.

So this re-measures the edge on a small sample and compares it to the
baseline recorded when the strategy was adopted:

    BTC consensus edge -35.9 points, p=0.001  (150 samples, 2026-08-15)

and shouts if it has moved toward zero. Deliberately NOT a re-derivation of
the strategy -- 40 samples cannot re-prove an edge. It is a smoke alarm: it
answers "is the thing we bet on still there", not "how big is it".

COST: 40 samples x 2 providers = ~80 API calls per run. That is stated
here, in the scheduler entry, and in the alert, because an unattended job
spending API budget is exactly what silently stopped both AI bots on
2026-08-15.

Verdicts:
  HOLDS  edge still clearly negative -> invert premise intact, do nothing
  DRIFT  edge moved toward 0 -> the models likely changed under the alias;
         re-run at 150 samples before trusting the bot with more trades
  FLIP   edge turned positive -> inverting is now backwards; stop the bot

Usage:  python _edge_watch.py [samples]
"""
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
SAMPLES = int(sys.argv[1]) if len(sys.argv) > 1 else 40
SYMBOL = "BTCUSDc"
HORIZON = 16

# Recorded when the invert strategy was adopted. Edge is hit% minus the
# base rate on the same samples, so it is already drift-corrected.
BASELINE_EDGE = -35.9
BASELINE_P = 0.001
# How far back toward zero the edge may move before it stops being the
# thing we are betting on. -15 keeps a wide margin: the walk-forward needs
# the models to be wrong often, not merely wrong on average.
DRIFT_THRESHOLD = -15.0


def telegram(msg):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("[warn] no telegram credentials -- printing only")
        return
    try:
        data = urllib.parse.urlencode({"chat_id": chat, "text": msg}).encode()
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/sendMessage", data, timeout=10)
    except Exception as e:
        print(f"[warn] telegram failed: {e}")


def main():
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(BASE, ".env"))
        load_dotenv()
    except ImportError:
        pass

    print(f"[edge-watch] {SAMPLES} samples x 2 providers = ~{SAMPLES*2} API calls")
    cmd = [sys.executable, os.path.join(BASE, "_signal_accuracy.py"),
           SYMBOL, str(SAMPLES), str(HORIZON)]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=BASE)
    out = (r.stdout or "") + (r.stderr or "")
    print(out[-2500:])

    # the consensus row: name, calls, hits, hit%, base%, edge, p
    m = re.search(r"^consensus\s+(\d+)\s+(\d+)\s+([\d.]+)%\s+([\d.]+)%\s+"
                  r"([+-][\d.]+)\s+([\d.]+)", out, re.M)
    if not m:
        msg = ("⚠️ edge-watch: could not read the consensus row -- "
               "the measurement did not complete. No conclusion drawn.")
        print(msg)
        telegram(msg)
        return 2

    calls, edge, p = int(m.group(1)), float(m.group(5)), float(m.group(6))
    print(f"\n  consensus: {calls} calls, edge {edge:+.1f} (baseline "
          f"{BASELINE_EDGE:+.1f}), p={p:.3f}")

    if calls < 8:
        msg = (f"⚠️ edge-watch: only {calls} consensus calls in "
               f"{SAMPLES} samples -- too few to judge. Not a verdict.")
        print(msg)
        telegram(msg)
        return 2

    if edge > 0:
        verdict, note = "FLIP", ("Edge turned POSITIVE. Inverting is now "
                                 "backwards -- consider stopping chart_ai.")
    elif edge > DRIFT_THRESHOLD:
        verdict, note = "DRIFT", (f"Edge moved from {BASELINE_EDGE:+.1f} to "
                                  f"{edge:+.1f}, past the {DRIFT_THRESHOLD:+.1f} "
                                  f"line. The models may have changed under "
                                  f"their alias. Re-run at 150 samples before "
                                  f"trusting more trades.")
    else:
        verdict, note = "HOLDS", "Anti-predictive signature intact."

    icon = "\u2705" if verdict == "HOLDS" else "\U0001F6A8"
    line = (f"{icon} edge-watch {verdict}: BTC consensus edge {edge:+.1f} "
            f"(baseline {BASELINE_EDGE:+.1f}), {calls} calls, p={p:.3f}"
            + "\n" + note)
    print("\n" + line)
    if verdict != "HOLDS":
        telegram(line)
    return 0 if verdict == "HOLDS" else 1


if __name__ == "__main__":
    sys.exit(main())
