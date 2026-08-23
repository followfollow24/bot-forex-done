#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_log_cadence.py -- how long does each bot NORMALLY go without writing?

Exists because of a near-miss. The watchdog was about to gain a
"restart if the log is stale" rule with one flat 90-minute threshold.
That threshold is fine for a bot that polls every 30 seconds and fatal
for a daily one: btc_combo_lb had not written for 334 minutes at the
time, entirely normally, and a flat rule would have killed and
relaunched it every hour forever.

So the threshold has to come from each bot's own measured cadence
rather than from a number that felt about right. This reads the real
inter-write gaps out of the logs and reports the distribution.

The recommendation is max_gap x3, floored at 90 min: the largest gap
actually observed is the strongest evidence of what "quiet but fine"
looks like for that bot, and 3x leaves room for a quiet stretch longer
than anything in the sample. Erring long is the cheap direction -- a
late restart costs some downtime, an early one costs a restart loop on
a healthy bot.

Usage (on the VPS):  python _log_cadence.py [days]
"""
import os
import re
import sys
from datetime import datetime, timedelta

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 14
DESK = os.path.join(os.environ.get("USERPROFILE", os.path.expanduser("~")), "Desktop")

# every bot log in this repo opens its lines with an ISO-ish stamp
TS = re.compile(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})")


def stamps(path, cutoff):
    out = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = TS.match(line)
                if not m:
                    continue
                try:
                    t = datetime.strptime(m.group(1) + " " + m.group(2),
                                          "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
                if t >= cutoff:
                    out.append(t)
    except OSError:
        return []
    return out


def main():
    cutoff = datetime.now() - timedelta(days=DAYS)
    logs = sorted(f for f in os.listdir(DESK)
                  if f.startswith("forex_") and f.endswith(".log"))
    print("=" * 78)
    print(f" LOG CADENCE -- real inter-write gaps, last {DAYS} days")
    print("=" * 78)
    print(f"{'bot':<26}{'writes':>8}{'median':>9}{'p95':>8}{'MAX gap':>10}{'suggest':>10}")
    print("-" * 78)
    for f in logs:
        ts = stamps(os.path.join(DESK, f), cutoff)
        name = re.sub(r"^forex_(bot_)?", "", f[:-4])
        if len(ts) < 3:
            print(f"{name:<26}{len(ts):>8}      -- too few timestamped lines --")
            continue
        ts.sort()
        gaps = sorted((ts[i + 1] - ts[i]).total_seconds() / 60.0
                      for i in range(len(ts) - 1))
        med = gaps[len(gaps) // 2]
        p95 = gaps[int(len(gaps) * 0.95)]
        mx = gaps[-1]
        # 3x the worst gap actually seen, never below 90 min, rounded up
        # to something readable
        sug = max(90, int(round(mx * 3 / 30.0)) * 30)
        print(f"{name:<26}{len(ts):>8}{med:>8.1f}m{p95:>7.1f}m{mx:>9.1f}m{sug:>9}m")
    print("-" * 78)
    print("  suggest = max(90, observed_max_gap x3), rounded to 30 min.")
    print("  A bot whose log is quiet for longer than this has stopped working:")
    print("  it is past three times the longest quiet stretch it has ever had.")
    print()
    print("  NOTE: gaps are measured over the whole window, so a bot that was")
    print("  deliberately STOPPED shows one enormous gap. Ignore the suggestion")
    print("  for stopped bots -- read their number as the downtime, not a cadence.")


if __name__ == "__main__":
    main()
