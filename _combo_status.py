"""Read-only status for btc_combo_lb (and its sibling funding_contrarian).

Written because this bot once sat kill-switched for 12.9 days while every
casual check said it was healthy. That is not an accident of that one
incident -- it is how the bot is built. Look at the main loop:

    self._heartbeat()
    self._watch_positions()
    ...
    if os.path.exists(self.stop_file):
        log("[KILL-SWITCH] present -- skipping decision")

The heartbeat and the position watcher run BEFORE the kill-switch test, so
a blocked bot keeps writing a fresh heartbeat, keeps a live console window,
and keeps managing whatever it already holds. The only thing it stops doing
is opening anything new -- and nothing about its outward health says so.

So this script reads four sources that can disagree, and says which:

  1  the kill-switch file itself          -- decisive
  2  the heartbeat file's age             -- process scheduled or not
  3  the log: when it last DECIDED, and how often it said KILL-SWITCH
  4  the broker: open positions on magic 668002

Nothing here places, closes or modifies an order.
"""
import os, sys, json, time
from datetime import datetime, timezone, timedelta

BASE = sys.argv[1] if len(sys.argv) > 1 else \
    os.path.join(os.path.expanduser("~"), "Desktop")

SLEEVES = [
    ("btc_combo_lb",       "btcusdc", 668002, "combo"),
    ("funding_contrarian", "crypto",  668001, "funding"),
]


def age(path):
    if not os.path.exists(path):
        return None
    return (time.time() - os.path.getmtime(path)) / 60.0


def human(mins):
    if mins is None:
        return "missing"
    if mins < 90:
        return f"{mins:.0f} min ago"
    if mins < 60 * 48:
        return f"{mins/60:.1f} h ago"
    return f"{mins/1440:.1f} days ago"


def tail(path, n=12):
    try:
        with open(path, "r", errors="replace") as fh:
            return fh.read().splitlines()[-n:]
    except Exception:
        return []


def main():
    print("=" * 78)
    print(f" DAILY SLEEVES STATUS   base = {BASE}")
    print(f" now {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC"
          f"  /  {datetime.now(timezone.utc)+timedelta(hours=7):%H:%M} Thai")
    print("=" * 78)
    print("\n  decision window is 00:01-00:50 UTC = 07:01-07:50 Thai,"
          " once a day.")

    for tag, slug, magic, sleeve in SLEEVES:
        up = f"{slug.upper()}_{tag.upper()}"
        stop = os.path.join(BASE, f"STOP_{up}")
        beat = os.path.join(BASE, f"HEARTBEAT_{up}")
        log = os.path.join(BASE, f"forex_bot_{slug}_{tag}.log")
        state = os.path.join(BASE, f"{slug}_{tag}_state.json")

        print(f"\n{'='*78}\n {tag}   (sleeve {sleeve}, magic {magic})\n{'='*78}")

        # 1 -- the decisive one
        blocked = os.path.exists(stop)
        print(f"\n  1  KILL-SWITCH  {os.path.basename(stop)}")
        if blocked:
            print(f"     *** PRESENT -- this bot opens NOTHING NEW ***")
            print(f"     set {human(age(stop))}")
        else:
            print(f"     absent -- free to decide")

        # 2 -- process liveness, which proves less than it looks
        print(f"\n  2  heartbeat   {human(age(beat))}")
        if blocked and age(beat) is not None and age(beat) < 90:
            print("     NOTE: fresh heartbeat + kill-switch present."
                  " The process is")
            print("     alive and doing nothing. This is the 12.9-day"
                  " failure mode.")

        # 3 -- the log
        print(f"\n  3  log         {human(age(log))}")
        if os.path.exists(log):
            try:
                with open(log, "r", errors="replace") as fh:
                    lines = fh.read().splitlines()
            except Exception as exc:
                lines = []
                print(f"     unreadable: {exc!r}")
            ks = [l for l in lines if "KILL-SWITCH" in l]
            dec = [l for l in lines if "DECISION" in l.upper()
                   or "[TRADE]" in l.upper() or "OPEN" in l.upper()]
            print(f"     {len(lines)} lines,"
                  f" {len(ks)} mention the kill-switch")
            if ks:
                print(f"     first KILL-SWITCH line: {ks[0][:96]}")
                print(f"     last  KILL-SWITCH line: {ks[-1][:96]}")
            if dec:
                print(f"     last decision-ish line: {dec[-1][:96]}")
            print("     --- tail ---")
            for l in tail(log, 10):
                print(f"     {l[:110]}")

        # 4 -- state file
        if os.path.exists(state):
            try:
                st = json.load(open(state))
                print(f"\n  4  state       last_decision_day ="
                      f" {st.get('last_decision_day','?')!r}"
                      f"   positions held = {list(st.get('positions',{}))}")
            except Exception as exc:
                print(f"\n  4  state       unreadable: {exc!r}")
        else:
            print("\n  4  state       missing")

    # 5 -- the broker, which is the only source that cannot be stale
    print(f"\n{'='*78}\n BROKER\n{'='*78}")
    try:
        import MetaTrader5 as mt5
    except Exception:
        print("  MetaTrader5 not importable here -- skipping broker check")
        return 0
    if not mt5.initialize():
        print(f"  MT5 init failed: {mt5.last_error()}")
        return 0
    info = mt5.account_info()
    if info:
        print(f"  account {info.login}  {info.server}"
              f"  equity {info.equity:,.2f} {info.currency}")
    res = mt5.positions_get()
    if res is None:
        print(f"  positions_get returned None ({mt5.last_error()})"
              f" -- UNKNOWN, not flat")
    else:
        for tag, slug, magic, sleeve in SLEEVES:
            mine = [p for p in res if getattr(p, "magic", None) == magic]
            if not mine:
                print(f"  {tag}: no open position")
            for p in mine:
                print(f"  {tag}: {p.symbol} {'BUY' if p.type==0 else 'SELL'}"
                      f" {p.volume} @ {p.price_open}  P/L {p.profit:+.2f}")
    mt5.shutdown()

    print(f"\n{'='*78}")
    print(" A fresh heartbeat is NOT evidence this bot is working --")
    print(" line 1 is the one that decides. Clearing a kill-switch is an")
    print(" operator action; this script only reports.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
