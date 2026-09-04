#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
clock_scalp_bot.py -- 19:30:00 Thai, watch a few SECONDS, enter with the
move, leave when it stops.

Built to the operator's specification, given after seeing the measurement
that argues against it. Their decision; recorded here so the numbers
travel with the code:

    at the 1-5 second scale the price moves 0.64-0.98 points while the
    XAUAUDm spread is 1.13. The observation is SMALLER THAN THE BID-ASK
    GAP, so the direction read at 19:30:03 is substantially which side of
    the quote last refreshed. Median tick count in one second: 3.
    (_1930_seconds.py, 174 days of tick history)

    The version that did measure well -- enter at 19:35 on the 19:30 M5
    bar -- beat a random-direction control in all six train/TEST cells
    (z +1.77 to +2.46) and still netted about zero after spread.
    (_1930_runstop.py, 364 days)

WHY THIS IS A STANDALONE SCRIPT
----------------------------------------------------------------------
Every other bot here is bar-driven: process_bar() fires once per CLOSED
bar. A decision three seconds after a minute boundary cannot live in
that loop at all, so this one owns its own timing.

THE CLOCK IS THE HARD PART, AND THE VPS CLOCK IS NOT USABLE
----------------------------------------------------------------------
A three-second decision window is meaningless if the machine's clock is
two seconds off, and Windows VPS clocks drift. So the timing here comes
from the BROKER'S OWN TICK STREAM (tick.time_msc) -- the market's clock,
the same one that stamps the candles the operator is looking at. The
local clock is used only to decide when to start paying attention, and
the offset between the two is measured at startup and logged.

SAFETY, BY CONSTRUCTION
----------------------------------------------------------------------
  - DRY RUN IS THE DEFAULT. Without --live it sends nothing, and logs
    exactly the order it would have sent. It runs fine on an empty
    account, which is what this one currently is (balance 0.00).
  - a stop-loss is attached to the order itself, broker-side, so it
    survives this process dying, the VPS rebooting, or MT5 hanging --
    the failure mode that left four bots silently dead for seven days
    earlier in this project.
  - a kill-switch file blocks new entries without needing a restart.
  - margin is checked before sending; the order is refused rather than
    partially filled into a margin call.
  - one trade per day, maximum. It cannot spiral.

GOLD AND BTC IN ONE PROCESS -- AND WHAT IS NOT KNOWN ABOUT BTC
----------------------------------------------------------------------
Both symbols fire at the same instant, so they share one loop rather
than running as two processes racing the same second. Each keeps its own
spread, ATR, gate and lot; nothing is shared but the clock.

EVERY NUMBER QUOTED ABOVE IS XAUAUDm. This rule has never been measured
on BTC -- not the gate, not the exit, not the direction read. BTC's
spread, hourly ATR and 19:30 behaviour are a different regime, and it
trades weekends when gold does not. Run it dry until it has its own
history, or run _exit_curve.py against BTCUSDm first.

Lot is per symbol because the contracts are not comparable: 0.05 lot of
XAUAUDm and 0.05 lot of BTCUSDm are wildly different exposures. The stop
is priced in account currency through the broker's own calculator before
every send, per symbol, so neither can surprise you.

Usage on the VPS
----------------------------------------------------------------------
    python clock_scalp_bot.py                            # dry run
    python clock_scalp_bot.py --live                     # real orders
    --symbols XAUAUDm,BTCUSDm
    --lot 0.05                       same size for both
    --lot XAUAUDm=0.05,BTCUSDm=0.01  per symbol
    --selftest                       check everything and exit, sending
                                     nothing -- run this before approving
                                     a live start

    --decide-after 1.0   the MINIMUM wait after 19:30:00 before an entry
                         may be taken. Not a verdict: watching continues
                         past it until the move clears the gate.
                         1 second, not 3, because the gate opens inside
                         3s on only 20% of days and inside 1s on 4%, so
                         the setting almost never binds -- but on a
                         session like 4 Sep (30 points in 1.37s) it lets
                         the bot in earlier at no cost. Below ~0.2s is
                         unreachable live: 50 ms tick polling plus the
                         round trip to the broker.
    --max-wait 900       give up on the session after this many seconds
    --lot 0.03           fixed size
    --sl-atr 3.0         stop distance in ATR(H1) -- NOT optional
    --max-risk-pct 0     refuse the trade if the stop costs more than this
                         share of equity. 0 = no limit, which is the
                         operator's explicit instruction: over-leverage on
                         purpose, blowing the account is acceptable.
                         The cost is still PRICED AND LOGGED every time --
                         no limit is not the same as no visibility.
    --exit-mode m15close  how to leave. m15close: out when the M15 candle
                          that contains the entry closes -- 19:45:00 on a
                          19:30 entry. Deterministic: no judgement about
                          whether the move "has stopped".
                          stall:N  out after N seconds with no new
                          favourable extreme.  fixed:N  out after N
                          minutes.  bars  the old 2-M5-closes-against.
    --patience 2         M5 bars closing against you, for --exit-mode bars
    --min-move-spread 2  skip the day unless the move inside the window is
                         at least this many times the spread. Set 0 to take
                         every day, as originally specified.
    --gate-money 10      set the gate in ACCOUNT CURRENCY instead: the move
                         must be worth this much at the configured lot.
                         Overrides --min-move-spread when given. Priced
                         through the broker's own calculator at the bell,
                         because the points that equal 10 USD drift with
                         AUD/USD -- a fixed point count would quietly mean
                         a different amount of money every week.

WHAT THE EXIT SWEEP FOUND (_exit_curve.py, 106 days, tick resolution)
----------------------------------------------------------------------
Every holding time from 1 to 120 minutes was priced, plus nine stall
rules. Two things came out of it and both are worth carrying here:

  - THERE IS NO BEST EXIT TIME. The curve never turns over inside two
    hours, and the halves peak 30 minutes apart (train 120 min, TEST 90
    min), so the peak is noise rather than an optimum.
  - FAST EXITS ARE CONSISTENTLY THE WORST. Every stall under two minutes
    loses in both halves, winning only 25-31% of the time. Waiting is
    not what costs money here; leaving early is.

The operator then chose the M15 candle close, which is the cleanest rule
anyone has proposed for this: it needs no judgement about whether the
move has ended, and it lands at 19:45:00 every day. Measured over the
same 106 days it returns +0.88 points a trade, 43% winners, +337 AUD at
0.05 lot -- train -1.29 and TEST +3.06, so it is NOT established, only
the best-defined of the candidates.

WHY THE GATE EXISTS (added after replaying 4 Sep tick by tick)
----------------------------------------------------------------------
On 4 Sep the move was unmistakable almost instantly: 30 points inside
1.37 seconds, and at +2s it was 25.38 points = 12.0x the spread. At +1s
it still read UP, the wrong way, which is exactly why the operator
specified 2-5 seconds and not 1.

But that was an extreme day. On the median day the price moves 0.69
points in 2 seconds -- 0.62x the spread -- and there is nothing to read.
A bot with no gate takes both, and the noise days pay for the good ones.
The gate is the mechanical version of what the operator's eye already
does: if nothing is happening, do not trade today.

    4 Sep at +2s : 25.38 pts = 12.0x spread  -> gate opens, trade
    median day   :  0.69 pts =  0.62x spread -> gate stays shut, skip
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)")
    sys.exit(1)

THAI_OFFSET = 7                 # Thailand is UTC+7 and has no DST
TARGET_H, TARGET_M = 19, 30     # Thai wall-clock time of the decision
POLL_SEC = 0.05                 # tick polling interval in the hot window
ARM_LEAD = 90                   # start paying attention this early
MAGIC = 668003
KILL_FILE = "STOP_CLOCK_SCALP"
STALE_QUOTE_SEC = 300     # older than this at the bell = market shut


def log(msg: str) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {msg}"
    print(line, flush=True)
    try:
        with open("clock_scalp.log", "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def telegram(msg: str) -> None:
    """Best effort. A notification failure must never stop a trade being
    managed, so every path here swallows its errors."""
    try:
        import urllib.parse
        import urllib.request
        token = chat = None
        for path in (".env", os.path.expanduser("~/.env")):
            if not os.path.exists(path):
                continue
            with open(path, encoding="utf-8") as fh:
                for raw in fh:
                    if "=" not in raw or raw.strip().startswith("#"):
                        continue
                    k, v = raw.split("=", 1)
                    k, v = k.strip(), v.strip().strip('"').strip("'")
                    if k == "TELEGRAM_BOT_TOKEN":
                        token = v
                    elif k == "TELEGRAM_CHAT_ID":
                        chat = v
        if not token or not chat:
            return
        data = urllib.parse.urlencode({"chat_id": chat, "text": msg}).encode()
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/sendMessage", data, timeout=10)
    except Exception:
        pass


def parse_lots(spec: str, symbols: list[str]) -> dict:
    """'0.05' -> same for all; 'XAUAUDm=0.05,BTCUSDm=0.01' -> per symbol.
    Sizes are not transferable between contracts, so a per-symbol form has
    to exist or one of them is silently wrong."""
    if "=" not in spec:
        return {s: float(spec) for s in symbols}
    out = {}
    for part in spec.split(","):
        k, _, v = part.partition("=")
        out[k.strip()] = float(v)
    return out


def resolve_symbol(want: str) -> str | None:
    if mt5.symbol_info(want) is not None:
        return want
    key = want.upper().rstrip("CM")
    for s in (mt5.symbols_get() or []):
        if s.name.upper().startswith(key):
            return s.name
    return None


def atr_h1(sym: str, n: int = 14) -> float | None:
    r = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H1, 1, n + 30)
    if r is None or len(r) < n + 1:
        return None
    trs = []
    for i in range(1, len(r)):
        h, l, pc = float(r[i]["high"]), float(r[i]["low"]), float(r[i - 1]["close"])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-n:]) / n


def broker_offset_hours(sym: str) -> int:
    """Server clock minus UTC, from a live tick. Assuming this is zero is
    how a session study ends up studying the wrong session."""
    t = mt5.symbol_info_tick(sym)
    if t is None:
        return 0
    return int(round((t.time - datetime.now(timezone.utc).timestamp()) / 3600.0))


def next_target(offset_h: int) -> datetime:
    """Next 19:30:00 Thai, expressed in BROKER server time."""
    now_utc = datetime.now(timezone.utc)
    tgt_utc_h = (TARGET_H - THAI_OFFSET) % 24
    t = now_utc.replace(hour=tgt_utc_h, minute=TARGET_M, second=0, microsecond=0)
    if t <= now_utc:
        t += timedelta(days=1)
    return t + timedelta(hours=offset_h)


def try_send(req: dict):
    """order_send with a filling-mode fallback.

    Brokers differ on which filling modes they accept and reject the rest
    with 10030 (invalid fill). IOC is right for Exness market orders, but
    a hardcoded single mode is a silent single point of failure on both
    the open and the close.
    """
    for fill in (req.get("type_filling", mt5.ORDER_FILLING_IOC),
                 mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN):
        req["type_filling"] = fill
        try:
            res = mt5.order_send(req)
        except Exception as exc:
            log(f"  order_send raised: {exc!r}")
            return None
        if res is None:
            return None
        if res.retcode != 10030:                  # not a filling problem
            return res
        log(f"  filling mode {fill} rejected (10030) -- trying the next")
    return res


def send_order(sym: str, direction: int, lot: float, sl_price: float,
               live: bool):
    tk = mt5.symbol_info_tick(sym)
    info = mt5.symbol_info(sym)
    if tk is None or info is None:
        # Refusing here is right: an entry with no quote has no price and
        # no stop distance. That is the opposite of close_position, which
        # must proceed regardless -- opening blind risks money, closing
        # blind protects it.
        log(f"  no quote/info for {sym} at the moment of entry -- not sending")
        telegram(f"clock_scalp [{sym}]: no quote at entry, order not sent")
        return None
    price = tk.ask if direction > 0 else tk.bid
    req = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": sym,
        "volume": lot,
        "type": mt5.ORDER_TYPE_BUY if direction > 0 else mt5.ORDER_TYPE_SELL,
        "price": price,
        "sl": round(sl_price, info.digits),
        "deviation": 20,
        "magic": MAGIC,
        "comment": "clock_scalp_1930",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    if not live:
        log(f"  DRY RUN -- would send: {'BUY' if direction > 0 else 'SELL'} "
            f"{lot} {sym} @ {price:.3f}  SL {req['sl']:.3f}")
        return None
    res = try_send(req)
    if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
        code = getattr(res, "retcode", "None")
        hint = ""
        if code == 10027:
            hint = "  <- AutoTrading is OFF in the terminal (button must be green)"
        elif code in (10019, 10014):
            hint = "  <- not enough money / bad volume for this account"
        log(f"  ORDER REJECTED retcode={code} {getattr(res, 'comment', '')}{hint}")
        telegram(f"clock_scalp: order REJECTED retcode={code}{hint}")
        return None
    log(f"  FILLED {'BUY' if direction > 0 else 'SELL'} {lot} @ {res.price:.3f} "
        f"ticket {res.order}")
    return res


def positions_of(sym: str):
    """Our positions on this symbol.

    mt5.positions_get() accepts symbol, group or ticket -- there is NO
    magic parameter. Passing magic= raises, and this was being called on
    the exit path, which had never executed: the first real trade would
    have crashed the bot while holding an open position, or (if the call
    had merely returned nothing) reported the position as already closed
    and walked away from it. Filtering happens here in Python instead.
    """
    try:
        return [p for p in (mt5.positions_get(symbol=sym) or [])
                if getattr(p, "magic", None) == MAGIC]
    except Exception as exc:                      # never crash the manager
        log(f"  positions_get failed: {exc!r}")
        return None                               # unknown, not "none open"


def m15_close_after(ts_srv: float) -> float:
    """Server timestamp of the close of the M15 candle containing ts_srv."""
    return (int(ts_srv) // 900 + 1) * 900


def manage_exit(sym: str, direction: int, patience: int, max_minutes: int,
                live: bool, entry_px: float, mode: str = "m15close"):
    """Leave according to --exit-mode. m15close is deterministic and needs
    no view on whether the move has ended; the others are judgement rules
    and the sweep found none of them better."""
    kind, _, arg = mode.partition(":")
    if kind in ("m15close", "fixed", "stall"):
        tk = mt5.symbol_info_tick(sym)
        now_srv = float(tk.time) if tk else time.time()
        if kind == "m15close":
            target = m15_close_after(now_srv)
            log(f"  exit at the M15 close, "
                f"{datetime.fromtimestamp(target, timezone.utc):%H:%M:%S} server "
                f"({(target - now_srv)/60.0:.1f} min away)")
        elif kind == "fixed":
            target = now_srv + float(arg or 15) * 60
            log(f"  exit after {arg or 15} minutes")
        else:
            target = None
            stall_s = float(arg or 600)
            best, best_t = entry_px, now_srv
            log(f"  exit after {stall_s:.0f}s with no new extreme")
        # The server clock stops advancing when the market shuts. Judging
        # the cap on it would spin here forever holding an open position,
        # so the backstop is wall-clock.
        hard_deadline = time.time() + max_minutes * 60
        while True:
            if time.time() > hard_deadline:
                log(f"  {max_minutes} min wall-clock cap reached -- closing")
                close_position(sym, direction, live, entry_px)
                return
            if live:
                held = positions_of(sym)
                if held is not None and not held:
                    log("  position closed elsewhere (stop-loss, or by hand)"
                        " -- done")
                    return
            tk = mt5.symbol_info_tick(sym)
            if tk is None:
                time.sleep(1); continue
            now_srv = float(tk.time)
            px = tk.bid if direction > 0 else tk.ask
            if target is not None:
                if now_srv >= target:
                    close_position(sym, direction, live, entry_px)
                    return
            else:
                if (px > best) if direction > 0 else (px < best):
                    best, best_t = px, now_srv
                elif now_srv - best_t >= stall_s:
                    close_position(sym, direction, live, entry_px)
                    return
            time.sleep(1)

    against = 0
    seen_bar = None
    deadline = time.time() + max_minutes * 60
    while time.time() < deadline:
        if live:
            held = positions_of(sym)
            if held is not None and not held:
                log("  position closed elsewhere (stop-loss, or by hand) -- done")
                return
        bars = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M5, 1, 2)
        if bars is None or len(bars) < 1:
            time.sleep(5)
            continue
        b = bars[-1]
        if seen_bar != int(b["time"]):
            seen_bar = int(b["time"])
            body = float(b["close"]) - float(b["open"])
            against = against + 1 if body * direction < 0 else 0
            log(f"  M5 {datetime.fromtimestamp(seen_bar):%H:%M} "
                f"body {body:+.2f}  against={against}/{patience}")
            if against >= patience:
                close_position(sym, direction, live, entry_px)
                return
        time.sleep(5)
    log(f"  {max_minutes} min cap reached -- closing")
    close_position(sym, direction, live, entry_px)


def close_position(sym: str, direction: int, live: bool, entry_px: float):
    tk = mt5.symbol_info_tick(sym)
    if tk is None:
        # No quote to price the log line with. Do NOT return -- a missing
        # tick is not a reason to leave a live position unmanaged.
        log("  no tick available while closing -- proceeding on the order")
        px = entry_px
    else:
        px = tk.bid if direction > 0 else tk.ask
    pts = (px - entry_px) * direction
    if not live:
        log(f"  DRY RUN -- would close at {px:.3f}  ({pts:+.2f} points)")
        telegram(f"clock_scalp DRY RUN: would close {sym} at {px:.3f} "
                 f"({pts:+.2f} pts)")
        return
    held = positions_of(sym)
    if held is None:
        log("  CANNOT READ POSITIONS while closing -- retrying")
        time.sleep(2)
        held = positions_of(sym)
    if not held:
        log("  nothing of ours open on this symbol -- nothing to close")
        return
    for p in held:
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": sym,
            "volume": p.volume,
            "type": mt5.ORDER_TYPE_SELL if p.type == 0 else mt5.ORDER_TYPE_BUY,
            "position": p.ticket,
            "price": px,
            "deviation": 20,
            "magic": MAGIC,
            "comment": "clock_scalp_exit",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        # A rejected close leaves a live position behind, so it is retried
        # and, if it still will not go, it is escalated rather than logged
        # once and forgotten.
        ok, res = False, None
        for attempt in range(1, 4):
            res = try_send(req)
            ok = res is not None and res.retcode == mt5.TRADE_RETCODE_DONE
            if ok:
                break
            log(f"  close attempt {attempt}/3 failed: "
                f"retcode={getattr(res, 'retcode', '?')} "
                f"{getattr(res, 'comment', '')}")
            time.sleep(2)
            t2 = mt5.symbol_info_tick(sym)
            if t2:
                req["price"] = t2.bid if direction > 0 else t2.ask
        log(f"  CLOSE ticket {p.ticket}: "
            f"{'done' if ok else 'STILL OPEN after 3 tries'}"
            f"  ({pts:+.2f} points)")
        telegram(f"clock_scalp: {'closed' if ok else 'FAILED TO CLOSE'} {sym} "
                 f"{pts:+.2f} pts"
                 + ("" if ok else " -- POSITION IS STILL OPEN, close it by hand"))


def entry_decision(elapsed, px, ref, gate, min_wait, max_wait):
    """The rule itself, with no MT5 in it so it can be tested directly.

    Watching starts the instant 19:30:00 arrives; min_wait is only the
    earliest moment an entry is PERMITTED, not a period of not looking.
    Returns +1/-1 to enter now, 0 to keep watching, None to give up.
    """
    if elapsed >= min_wait and px != ref and abs(px - ref) >= gate:
        return 1 if px > ref else -1
    if elapsed >= max_wait:
        return None
    return 0


def decide_all(syms, gates, target, a):
    """Poll every symbol from 19:30:00 and read each one's direction at
    +decide_after. One loop, because they all fire on the same second.

    `gates` must arrive here, not be attached afterwards -- an earlier
    version set them on the state dict after this returned, so the lookup
    below always saw its 0.0 default and the gate filtered nothing."""
    t0_ms = int(target.timestamp() * 1000)
    state = {s: {"ref": None, "last": None, "seen": 0, "done": None,
                 "moved": 0.0, "peak": 0.0, "next_log": 0.0,
                 "gate": float(gates[s])} for s in syms}
    deadline = time.time() + a.max_wait + 30
    while time.time() < deadline:
        pending = False
        for sym in syms:
            st = state[sym]
            if st["done"] is not None:
                continue
            pending = True
            tk = mt5.symbol_info_tick(sym)
            if tk is None:
                continue
            tms = int(tk.time_msc)
            if tms < t0_ms:
                continue
            px = (tk.bid + tk.ask) / 2.0 if tk.ask else tk.bid
            if st["ref"] is None:
                st["ref"] = px
                log(f"  [{sym}] 19:30:00 reference {px:.3f} "
                    f"(first tick +{(tms - t0_ms)/1000.0:.3f}s); "
                    f"needs {st['gate']:.3f} to fire")
            if px != st["last"]:
                st["seen"] += 1
            st["last"] = px
            elapsed = (tms - t0_ms) / 1000.0
            st["moved"] = abs(px - st["ref"])
            # Show the journey, not just the verdict: without this the log
            # says "entered" or "skipped" and there is no way to see how
            # close a skipped session came, or watch a live one build.
            if st["moved"] > st["peak"]:
                st["peak"] = st["moved"]
            if elapsed >= st["next_log"]:
                st["next_log"] = elapsed + (5.0 if elapsed < 60 else 30.0)
                pct = 100.0 * st["moved"] / st["gate"] if st["gate"] else 0.0
                log(f"  [{sym}] +{elapsed:6.1f}s  {px:.3f}  "
                    f"{px - st['ref']:+.3f}  {pct:3.0f}% of gate  "
                    f"(peak {st['peak']:.3f})")
            # decide-after is a MINIMUM wait, not a verdict -- watching
            # runs continuously from 19:30:00.000 and an entry may fire
            # on ANY tick from that moment onward. Reading direction at a
            # fixed +3s instead skipped 77-84% of the sessions that went
            # on to run, because a move covering 11-25 points over a
            # quarter hour rarely announces itself in three seconds.
            d = entry_decision(elapsed, px, st["ref"], st["gate"],
                               a.decide_after, a.max_wait)
            if d is None:
                st["done"] = (0, elapsed)
            elif d:
                st["done"] = (d, elapsed)
        if not pending:
            break
        time.sleep(POLL_SEC)
    for sym in syms:
        if state[sym]["done"] is None:
            state[sym]["done"] = (0, 0.0)
    return state


def selftest(a, syms: list) -> int:
    """Pre-flight. Touches nothing, sends nothing, waits for no bell.
    Answers the questions that decide whether a live start is safe, and
    the ones whose answers are usually assumed: is AutoTrading actually
    on, does the symbol resolve, is there money, what would one order
    cost, and would the broker accept it."""
    bad = 0
    print()
    log("=" * 68)
    log(" PRE-FLIGHT CHECK -- opens nothing, sends nothing")
    log("=" * 68)

    term = mt5.terminal_info()
    acct = mt5.account_info()
    if term is None or acct is None:
        log("  [FAIL] terminal/account unavailable -- MT5 IPC problem")
        return 1
    log(f"  terminal: {term.name}  connected={term.connected}  "
        f"trade_allowed={term.trade_allowed}")
    if not term.connected:
        log("  [FAIL] terminal is not connected to the broker"); bad += 1
    if not term.trade_allowed:
        log("  [FAIL] AutoTrading is OFF -- the button must be green, or "
            "every order comes back 10027")
        bad += 1
    else:
        log("  [OK]   AutoTrading is enabled")

    log(f"  account {acct.login} ({acct.server})  equity {acct.equity:,.2f} "
        f"{acct.currency}  free margin {acct.margin_free:,.2f}")
    if acct.equity <= 0:
        log("  [FAIL] equity is 0.00 -- nothing can be traded until funded")
        bad += 1
    else:
        log("  [OK]   the account has money")

    off = broker_offset_hours(syms[0])
    tgt = next_target(off)
    log(f"  broker clock UTC{off:+d}; next bell "
        f"{tgt:%Y-%m-%d %H:%M:%S} server = 19:30 Thai")

    for sym in syms:
        si = mt5.symbol_info(sym)
        tk = mt5.symbol_info_tick(sym)
        atr = atr_h1(sym)
        lot = a.lots.get(sym)
        log(f"  --- {sym} ---")
        if si is None or tk is None or not atr:
            log(f"  [FAIL] no quote/ATR for {sym}"); bad += 1; continue
        spread = si.spread * si.point
        gate = a.min_move_spread * spread
        gate_note = f"{a.min_move_spread}x spread"
        if a.gate_money > 0 and lot:
            pp = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, sym, lot,
                                       tk.ask, tk.ask + 1.0)
            if pp and float(pp) > 0:
                gate = a.gate_money / float(pp)
                gate_note = f"{a.gate_money:.2f} {acct.currency} at {lot} lot"
            else:
                log("  [FAIL] cannot price the money gate"); bad += 1
        log(f"  price {tk.bid:,.3f}/{tk.ask:,.3f}  spread {spread:.3f}  "
            f"ATR(H1) {atr:,.3f}")
        log(f"  gate {gate:.3f} pts ({gate_note} = {gate/spread:.2f}x spread)"
            f"   stop {a.sl_atr}xATR = {a.sl_atr*atr:,.3f}")
        if not lot:
            log(f"  [FAIL] no lot configured for {sym}"); bad += 1; continue
        if lot < si.volume_min or lot > si.volume_max:
            log(f"  [FAIL] lot {lot} outside broker range "
                f"{si.volume_min}-{si.volume_max}"); bad += 1
        else:
            log(f"  [OK]   lot {lot} is within {si.volume_min}-{si.volume_max}")
        entry = tk.ask
        sl = entry - a.sl_atr * atr
        margin = mt5.order_calc_margin(mt5.ORDER_TYPE_BUY, sym, lot, entry)
        loss = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, sym, lot, entry, sl)
        if margin is None or loss is None:
            log("  [FAIL] broker could not price this order"); bad += 1; continue
        risk = abs(float(loss))
        log(f"  a BUY {lot} now: margin {margin:,.2f}, "
            f"stop would cost {risk:,.2f} {acct.currency}")
        if acct.equity > 0:
            log(f"         = {risk/acct.equity*100:.1f}% of equity"
                + ("   [risk cap OFF by your instruction]"
                   if a.max_risk_pct <= 0 else ""))
            if risk > acct.equity:
                log(f"  [NOTE] the stop is bigger than the account -- the "
                    f"broker liquidates before it is reached")
        if margin > acct.margin_free:
            log(f"  [FAIL] margin {margin:,.2f} exceeds free "
                f"{acct.margin_free:,.2f} -- order would be rejected")
            bad += 1
        else:
            log(f"  [OK]   margin fits inside free margin")

    log("-" * 68)
    if os.path.exists(KILL_FILE):
        log(f"  [NOTE] kill switch {KILL_FILE} is present -- entries blocked")
    if bad:
        log(f"  RESULT: {bad} check(s) FAILED. Do not start live yet.")
    else:
        log("  RESULT: all checks passed. The bot is ready for a live start.")
        log("  Starting it is your decision and your command; this script")
        log("  has sent nothing and will not start itself.")
    return 1 if bad else 0


def run_once(a, syms: list) -> None:
    if os.path.exists(KILL_FILE):
        log(f"kill switch {KILL_FILE} present -- skipping today")
        return

    _acct = mt5.account_info()
    acct_ccy = _acct.currency if _acct else "?"
    off = broker_offset_hours(syms[0])
    target = next_target(off)
    log(f"broker clock = UTC{off:+d}; next 19:30:00 Thai is "
        f"{target:%Y-%m-%d %H:%M:%S} server time")
    now_srv = datetime.now(timezone.utc) + timedelta(hours=off)
    sleep_for = (target - now_srv).total_seconds() - ARM_LEAD
    if sleep_for > 0:
        log(f"sleeping {sleep_for/3600.0:.2f}h until {ARM_LEAD}s before the bell")
        time.sleep(sleep_for)

    # per-symbol context: each has its own ATR, spread and gate
    ctx = {}
    for sym in syms:
        atr = atr_h1(sym)
        si = mt5.symbol_info(sym)
        if not atr or si is None:
            log(f"  [{sym}] no ATR/info -- sitting this one out")
            continue
        # Gold is shut from Friday evening to Monday morning. Without this
        # the bot would poll a dead market for the full max-wait and then
        # log "never cleared the gate", which is true but names the wrong
        # reason -- a log that misreports WHY is worse than a quiet one.
        tk0 = mt5.symbol_info_tick(sym)
        age = (target.timestamp() - float(tk0.time)) if tk0 else 1e9
        if age > STALE_QUOTE_SEC:
            when = (datetime.fromtimestamp(float(tk0.time), timezone.utc)
                    if tk0 else None)
            log(f"  [{sym}] MARKET CLOSED -- last quote "
                + (f"{when:%a %d %b %H:%M} server, {age/3600.0:.1f}h before "
                   f"the bell" if when else "unavailable")
                + " -- no trade today")
            telegram(f"clock_scalp [{sym}]: market closed, no trade today")
            continue
        spread = si.spread * si.point
        lot = a.lots.get(sym, 0.0)
        gate = a.min_move_spread * spread
        gate_note = f"{a.min_move_spread}x spread"
        if a.gate_money > 0 and lot > 0:
            tk = mt5.symbol_info_tick(sym)
            per_pt = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, sym, lot,
                                           tk.ask, tk.ask + 1.0) if tk else None
            if per_pt and float(per_pt) > 0:
                gate = a.gate_money / float(per_pt)
                gate_note = (f"{a.gate_money:.2f} {acct_ccy} at {lot} lot")
            else:
                log(f"  [{sym}] cannot price a money gate -- falling back to "
                    f"{a.min_move_spread}x spread")
        ctx[sym] = {"atr": atr, "spread": spread, "gate": gate}
        log(f"armed [{sym}] ATR(H1) {atr:.3f}  spread {spread:.3f}  "
            f"gate {gate:.3f} pts ({gate_note} = {gate/spread:.2f}x spread)  "
            f"stop {a.sl_atr}xATR = {a.sl_atr*atr:.3f}")
    if not ctx:
        return

    state = decide_all(list(ctx), {k: v["gate"] for k, v in ctx.items()},
                       target, a)

    open_trades = []
    for sym, c in ctx.items():
        st = state[sym]
        d, waited = st["done"]
        moved = float(st.get("peak") or 0.0) if state[sym]["done"][0] == 0 \
            else float(st.get("moved") or 0.0)
        if d == 0:
            log(f"  [{sym}] never cleared {c['gate']:.3f} within "
                f"{a.max_wait:.0f}s -- closest it came was "
                f"{st.get('peak', 0.0):.3f} "
                f"({100.0*st.get('peak', 0.0)/c['gate']:.0f}% of the gate, "
                f"{st['seen']} price changes) -- skipped")
            telegram(f"clock_scalp [{sym}]: move {moved:.3f} < gate "
                     f"{c['gate']:.3f}, skipped")
            continue
        lot = a.lots.get(sym)
        if not lot:
            log(f"  [{sym}] no lot configured -- skipped")
            continue
        tk = mt5.symbol_info_tick(sym)
        entry_px = tk.ask if d > 0 else tk.bid
        sl_px = entry_px - d * a.sl_atr * c["atr"]
        log(f"  [{sym}] GATE CLEARED at +{waited:.3f}s: "
            f"{'BUY' if d > 0 else 'SELL'}  moved {moved:.3f} from the "
            f"reference = {moved/c['spread']:.1f}x spread "
            f"(needed {c['gate']:.3f})")

        otype = mt5.ORDER_TYPE_BUY if d > 0 else mt5.ORDER_TYPE_SELL
        acct = mt5.account_info()
        loss = mt5.order_calc_profit(otype, sym, lot, entry_px, sl_px)
        if loss is not None and acct is not None:
            risk = abs(float(loss)); eq = float(acct.equity)
            pct = (risk / eq * 100.0) if eq > 0 else float("inf")
            log(f"  [{sym}] stop {a.sl_atr}xATR = "
                f"{abs(entry_px-sl_px):.3f} = {risk:.2f} {acct.currency} "
                f"at {lot} lot"
                + (f"  ({pct:.1f}% of equity {eq:.2f})" if eq > 0
                   else "  (equity 0.00)"))
            if eq > 0 and risk > 0:
                pts_stop = abs(entry_px - sl_px)
                pts_bust = pts_stop * (eq / risk)
                if pts_bust < pts_stop:
                    log(f"  [{sym}] NOTE: equity runs out after "
                        f"~{pts_bust:.2f} but the stop is {pts_stop:.2f} "
                        f"-- the broker closes this before the stop")
            if a.live and eq > 0 and a.max_risk_pct > 0 and pct > a.max_risk_pct:
                log(f"  [{sym}] REFUSED: {pct:.1f}% over the "
                    f"{a.max_risk_pct}% limit")
                telegram(f"clock_scalp [{sym}]: refused, risk {pct:.1f}%")
                continue
        if a.live:
            need = mt5.order_calc_margin(otype, sym, lot, entry_px)
            if acct is None or need is None:
                log(f"  [{sym}] cannot price margin -- refusing"); continue
            if acct.margin_free <= 0 or need > acct.margin_free * (a.max_margin_pct / 100.0):
                log(f"  [{sym}] REFUSED: margin {need:.2f} exceeds "
                    f"{a.max_margin_pct:.0f}% of free {acct.margin_free:.2f}")
                continue

        res = send_order(sym, d, lot, sl_px, a.live)
        if a.live and res is None:
            continue
        if res is not None:
            entry_px = res.price
        telegram(f"clock_scalp {'LIVE' if a.live else 'DRY'} [{sym}]: "
                 f"{'BUY' if d > 0 else 'SELL'} {lot} @ {entry_px:.3f} "
                 f"SL {sl_px:.3f} (+{waited:.2f}s, moved {moved:.3f})")
        open_trades.append((sym, d, entry_px))

    for sym, d, entry_px in open_trades:
        manage_exit(sym, d, a.patience, a.max_minutes, a.live, entry_px,
                    a.exit_mode)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", default="XAUAUDm,BTCUSDm",
                   help="comma separated, e.g. XAUAUDm,BTCUSDm")
    p.add_argument("--lot", default="0.05",
                   help="0.05 for all, or XAUAUDm=0.05,BTCUSDm=0.01")
    p.add_argument("--decide-after", type=float, default=1.0,
                   help="seconds after 19:30:00 to read the direction (2-5)")
    p.add_argument("--max-wait", type=float, default=900.0,
                   help="seconds to keep watching before giving up on the "
                        "session (default 15 minutes)")
    p.add_argument("--sl-atr", type=float, default=3.0)
    p.add_argument("--max-risk-pct", type=float, default=0.0,
                   help="refuse to trade if the stop would cost more than "
                        "this percent of equity; 0 disables the check")
    p.add_argument("--max-margin-pct", type=float, default=95.0,
                   help="refuse only if the order needs more than this "
                        "percent of free margin, i.e. it would be rejected")
    p.add_argument("--patience", type=int, default=2)
    p.add_argument("--exit-mode", default="m15close",
                   help="m15close | fixed:MIN | stall:SEC | bars")
    p.add_argument("--gate-money", type=float, default=0.0,
                   help="gate expressed in account currency at the "
                        "configured lot; overrides --min-move-spread")
    p.add_argument("--min-move-spread", type=float, default=2.0,
                   help="skip the day unless the move is this many times the "
                        "spread; 0 takes every day")
    p.add_argument("--max-minutes", type=int, default=120)
    p.add_argument("--live", action="store_true",
                   help="actually send orders; without it, nothing is sent")
    p.add_argument("--once", action="store_true", help="one session then exit")
    p.add_argument("--selftest", action="store_true",
                   help="check everything and exit; sends nothing")
    a = p.parse_args()

    a.decide_after = max(0.0, a.decide_after)
    a.max_wait = max(a.decide_after + 1.0, a.max_wait)
    if a.sl_atr <= 0:
        print("[ERROR] --sl-atr must be positive; a stop is not optional here")
        return 2

    if not mt5.initialize():
        log(f"MT5 init failed: {mt5.last_error()}")
        return 2
    syms = []
    for want in [x.strip() for x in a.symbols.split(",") if x.strip()]:
        got = resolve_symbol(want)
        if got is None:
            log(f"symbol {want} not found on this broker -- skipped")
            continue
        mt5.symbol_select(got, True)
        if got != want:
            log(f"{want} -> {got}")
        syms.append(got)
    if not syms:
        log("no tradeable symbols"); return 2
    try:
        a.lots = parse_lots(a.lot, syms)
    except ValueError:
        log(f"cannot parse --lot {a.lot!r}"); return 2
    missing = [s for s in syms if s not in a.lots]
    if missing:
        log(f"no lot given for {missing} -- add it to --lot"); return 2

    acct = mt5.account_info()
    ccy = acct.currency if acct else ""
    gate_desc = (f"{a.gate_money:.2f} {ccy}/trade" if a.gate_money > 0
                 else f"{a.min_move_spread}x spread")
    log("=" * 68)
    log(f"clock_scalp_bot  {', '.join(f'{k} {v}' for k, v in a.lots.items())}"
        f"  decide +{a.decide_after}s  "
        f"SL {a.sl_atr}xATR  gate {gate_desc}  exit {a.exit_mode}  "
        f"risk cap {'OFF' if a.max_risk_pct <= 0 else str(a.max_risk_pct)+'%'}")
    log(f"MODE: {'LIVE -- REAL ORDERS' if a.live else 'DRY RUN -- sends nothing'}")
    if acct:
        log(f"account {acct.login} ({acct.server})  equity {acct.equity:.2f} "
            f"{acct.currency}")
        if a.live and acct.equity <= 0:
            log("equity is 0.00 -- nothing can be traded until the account "
                "is funded. Staying up in case it is topped up.")
    log("=" * 68)

    if a.selftest:
        rc = selftest(a, syms)
        mt5.shutdown()
        return rc

    try:
        while True:
            run_once(a, syms)
            if a.once:
                break
            time.sleep(60)
    except KeyboardInterrupt:
        log("stopped by operator")
    finally:
        mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
