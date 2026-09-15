"""Start scalp_bot in PAPER mode, in its own console window.

Settings fixed on 2026-09-15 BEFORE the paper run, so the forward result is
a real out-of-sample test and cannot be re-tuned after the fact:

  follow a >= 6-point 5-minute candle, 19:40-20:00 Thai only,
  and only on days the 19:30 candle moved >= 5 points;
  TP 1, SL 5, time stop 10 min; day guard +50 / -30; max 10 trades.

Why paper and not live: on 86 evenings of May-Sep 2026 ticks this won
(+$33.72 at 0.01 lot, 97% of 37 trades), but on 13 years of M5 gold the
same window LOSES (-0.70 pts/trade, 253 trades). Only the forward run can
say which one the next weeks look like.

There is no --allow-real here, on purpose.
"""
import os
import subprocess
import sys

ARGS = ["--mode", "follow", "--min-move", "6", "--tp", "1", "--sl", "5",
        "--max-hold-min", "10", "--session", "19:40-20:00",
        "--gate-at", "19:30", "--day-gate", "5"]
assert "--allow-real" not in ARGS

here = os.path.dirname(os.path.abspath(__file__))
subprocess.Popen([sys.executable, os.path.join(here, "scalp_bot.py")] + ARGS,
                 cwd=here, creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
print("scalp_bot started in PAPER mode in a new window:")
print("  " + " ".join(ARGS))
print("stop it: create a file named STOP_SCALP in bot_repo, or close its window")
