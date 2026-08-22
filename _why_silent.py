#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_why_silent.py -- [B] why have six bots never opened a trade?

Five technical bots and news_gemini have produced zero trades. I proposed
"prove or remove" without ever checking WHY they are quiet, which is a
guess dressed as a policy. A bot silent because its entry filter is strict
is working as designed; a bot silent because of a crash loop, a missing
symbol, a stuck gate or a permanently-failing API is broken. Those need
opposite responses and the logs already distinguish them.

For each bot this counts what its log actually contains:

  signals evaluated   is the strategy loop even reaching a decision?
  rejects by reason   which gate is doing the blocking, and how often
  errors / restarts   is it failing rather than declining?
  last activity       is the log live or frozen?

The distinction that matters is between "evaluated many, entered none"
(strict, working) and "never evaluated anything" (broken, or never
reaching the decision point at all).

Usage (on the VPS):  python _why_silent.py
"""
import os
import re
import sys
from collections import Counter
from datetime import datetime

DESK = os.path.join(os.path.expanduser("~"), "Desktop")
BOTS = [
    ("gold_daily_breakout", "forex_xauusdc_gold_daily_breakout.log"),
    ("gold_momentum_rsi",   "forex_xauusdc_gold_momentum_rsi.log"),
    ("btc_lqsweep",         "forex_btcusdc_btc_lqsweep.log"),
    ("btc_tpo",             "forex_btcusdc_btc_tpo.log"),
    ("btc_amd",             "forex_btcusdc_btc_amd.log"),
    ("news_gemini",         "forex_bot_news_gemini.log"),
]

PATS = [
    ("OPENED",            re.compile(r"\[OPEN\]")),
    ("signal evaluated",  re.compile(r"\[SIGNAL\]|gemini=|\[SCAN\]")),
    ("no signal/flat",    re.compile(r"action=FLAT|no signal|NONE")),
    ("blocked: trend",    re.compile(r"regime|adx|trend.*block|HTF")),
    ("blocked: time",     re.compile(r"block-hours|time.?block|killzone")),
    ("blocked: spread",   re.compile(r"spread.*ceiling|SKIP -- spread")),
    ("blocked: consensus", re.compile(r"no consensus|0 candidate")),
    ("blocked: other",    re.compile(r"SKIP|REJECT|blocked")),
    ("ERROR",             re.compile(r"\[ERROR\]|Traceback")),
    ("restart/banner",    re.compile(r"REAL-MONEY MODE|=== |START ")),
]


def find(name_hint):
    """Logs are not consistently named; glob for anything matching."""
    import glob
    hits = glob.glob(os.path.join(DESK, f"*{name_hint}*.log"))
    hits.sort(key=lambda p: os.path.getsize(p), reverse=True)
    return hits[0] if hits else None


def main():
    print("=" * 84)
    print(" [B] WHY ARE THESE BOTS SILENT -- strict by design, or broken?")
    print("=" * 84)
    for label, _ in BOTS:
        path = find(label)
        print(f"\n  {label}")
        print("  " + "-" * 80)
        if not path:
            print("    no log file found on the Desktop -> cannot tell; check the")
            print("    process is actually writing one")
            continue
        try:
            lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
        except OSError as e:
            print(f"    could not read: {e}")
            continue
        c = Counter()
        for ln in lines[-6000:]:
            for tag, pat in PATS:
                if pat.search(ln):
                    c[tag] += 1
        last = ""
        for ln in reversed(lines[-50:]):
            m = re.match(r"^(2026-\d\d-\d\d \d\d:\d\d:\d\d)", ln)
            if m:
                last = m.group(1)
                break
        age = ""
        if last:
            try:
                age = f"  ({(datetime.now() - datetime.strptime(last, '%Y-%m-%d %H:%M:%S')).total_seconds()/60:.0f} min ago)"
            except ValueError:
                pass
        print(f"    log {os.path.basename(path)}  {len(lines)} lines"
              f"   last entry {last or '?'}{age}")
        for tag, _ in PATS:
            if c[tag]:
                print(f"      {tag:<20}{c[tag]:>7}")
        # the verdict that matters
        if c["OPENED"]:
            print("    -> HAS traded; not silent")
        elif c["signal evaluated"] == 0:
            print("    -> NEVER reaches a decision. Not strictness -- something")
            print("       upstream is stopping it. INVESTIGATE.")
        elif c["ERROR"] > 20:
            print(f"    -> {c['ERROR']} errors. Failing, not declining. INVESTIGATE.")
        else:
            blocks = sum(c[t] for t in ("blocked: trend", "blocked: time",
                                        "blocked: spread", "blocked: consensus",
                                        "blocked: other", "no signal/flat"))
            print(f"    -> evaluates ({c['signal evaluated']}) but never enters"
                  f" ({blocks} declines). Strict by design, working.")
    print("\n" + "=" * 84)
    print("  'never reaches a decision' or many errors = a bug worth fixing.")
    print("  'evaluates but declines' = the filter is doing its job; the only")
    print("  question there is whether the filter is too tight to be useful.")


if __name__ == "__main__":
    main()
