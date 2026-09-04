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

Usage on the VPS
----------------------------------------------------------------------
    python clock_scalp_bot.py                     # dry run, logs only
    python clock_scalp_bot.py --live              # sends real orders

    --decide-after 3.0   seconds after 19:30:00 to read the direction
                         (operator asked for 2-5; clamped to that range)
    --lot 0.03           fixed size
    --sl-atr 1.0         stop distance in ATR(H1) -- NOT optional
    --patience 2         M5 bars closing against you before exiting
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


def wait_for_direction(sym: str, target_srv: datetime, decide_after: float,
                       max_wait: float):
    """Poll ticks from 19:30:00 until `decide_after` seconds have passed on
    the BROKER's clock, then report which way it went.

    Returns (direction, ref_price, last_price, ticks_seen, waited_s) or
    (0, ...) when the price never moved inside the window."""
    t0_ms = int(target_srv.timestamp() * 1000)
    ref = None
    last = None
    seen = 0
    deadline = time.time() + max_wait + 15
    while time.time() < deadline:
        tk = mt5.symbol_info_tick(sym)
        if tk is None:
            time.sleep(POLL_SEC)
            continue
        tms = int(tk.time_msc)
        if tms < t0_ms:                       # still before the boundary
            time.sleep(POLL_SEC)
            continue
        px = (tk.bid + tk.ask) / 2.0 if tk.ask else tk.bid
        if ref is None:
            ref = px
            log(f"  19:30:00.000 reference {ref:.3f} "
                f"(first tick at +{(tms - t0_ms)/1000.0:.3f}s)")
        if px != last:
            seen += 1
        last = px
        elapsed = (tms - t0_ms) / 1000.0
        if elapsed >= decide_after:
            if last != ref:
                return (1 if last > ref else -1), ref, last, seen, elapsed
            if elapsed >= max_wait:           # flat through the whole window
                return 0, ref, last, seen, elapsed
        time.sleep(POLL_SEC)
    return 0, ref, last, seen, 0.0


def send_order(sym: str, direction: int, lot: float, sl_price: float,
               live: bool):
    tk = mt5.symbol_info_tick(sym)
    info = mt5.symbol_info(sym)
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
    res = mt5.order_send(req)
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


def manage_exit(sym: str, direction: int, patience: int, max_minutes: int,
                live: bool, entry_px: float):
    """Hold while it runs. Leave once `patience` M5 bars have closed
    against the position -- one pullback bar must not end the trade."""
    against = 0
    seen_bar = None
    deadline = time.time() + max_minutes * 60
    while time.time() < deadline:
        if live and not mt5.positions_get(symbol=sym, magic=MAGIC):
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
    px = tk.bid if direction > 0 else tk.ask
    pts = (px - entry_px) * direction
    if not live:
        log(f"  DRY RUN -- would close at {px:.3f}  ({pts:+.2f} points)")
        telegram(f"clock_scalp DRY RUN: would close {sym} at {px:.3f} "
                 f"({pts:+.2f} pts)")
        return
    for p in (mt5.positions_get(symbol=sym, magic=MAGIC) or []):
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
        res = mt5.order_send(req)
        ok = res is not None and res.retcode == mt5.TRADE_RETCODE_DONE
        log(f"  CLOSE ticket {p.ticket}: "
            f"{'done' if ok else 'FAILED ' + str(getattr(res, 'retcode', '?'))}"
            f"  ({pts:+.2f} points)")
        telegram(f"clock_scalp: closed {sym} {pts:+.2f} pts "
                 f"({'ok' if ok else 'FAILED'})")


def run_once(a, sym: str) -> None:
    if os.path.exists(KILL_FILE):
        log(f"kill switch {KILL_FILE} present -- skipping today")
        return

    off = broker_offset_hours(sym)
    target = next_target(off)
    log(f"broker clock = UTC{off:+d}; next 19:30:00 Thai is "
        f"{target:%Y-%m-%d %H:%M:%S} server time")

    # server "now" is UTC plus the broker's own offset; compare like with like
    now_srv = datetime.now(timezone.utc) + timedelta(hours=off)
    sleep_for = (target - now_srv).total_seconds() - ARM_LEAD
    if sleep_for > 0:
        log(f"sleeping {sleep_for/3600.0:.2f}h until {ARM_LEAD}s before the bell")
        time.sleep(sleep_for)

    atr = atr_h1(sym)
    if not atr:
        log("no ATR available -- skipping today")
        return
    log(f"armed. ATR(H1) {atr:.2f}  stop {a.sl_atr} xATR = {a.sl_atr*atr:.2f} pts")

    d, ref, last, seen, waited = wait_for_direction(
        sym, target, a.decide_after, a.max_wait)
    if d == 0:
        log(f"  no movement inside {a.max_wait}s ({seen} price changes) "
            f"-- no trade today")
        telegram("clock_scalp: price flat at 19:30, no trade")
        return

    spread = mt5.symbol_info(sym).spread * mt5.symbol_info(sym).point
    move = abs(last - ref)
    log(f"  decided after {waited:.3f}s: "
        f"{'BUY' if d > 0 else 'SELL'}  moved {move:.3f} pts "
        f"on {seen} price changes  (spread {spread:.2f} = "
        f"{spread/move if move else float('inf'):.1f}x the move)")

    tk = mt5.symbol_info_tick(sym)
    entry_px = tk.ask if d > 0 else tk.bid
    sl_px = entry_px - d * a.sl_atr * atr

    if a.live:
        acct = mt5.account_info()
        need = mt5.order_calc_margin(
            mt5.ORDER_TYPE_BUY if d > 0 else mt5.ORDER_TYPE_SELL,
            sym, a.lot, entry_px)
        if acct is None or need is None:
            log("  cannot price margin -- refusing to send")
            return
        if acct.margin_free <= 0 or need > acct.margin_free * 0.30:
            log(f"  REFUSED: margin {need:.2f} is more than 30% of free "
                f"margin {acct.margin_free:.2f}")
            telegram(f"clock_scalp: refused, margin {need:.2f} vs free "
                     f"{acct.margin_free:.2f}")
            return

    res = send_order(sym, d, a.lot, sl_px, a.live)
    if a.live and res is None:
        return
    if res is not None:
        entry_px = res.price
    telegram(f"clock_scalp {'LIVE' if a.live else 'DRY'}: "
             f"{'BUY' if d > 0 else 'SELL'} {a.lot} {sym} @ {entry_px:.3f} "
             f"SL {sl_px:.3f} (decided +{waited:.2f}s, moved {move:.3f})")
    manage_exit(sym, d, a.patience, a.max_minutes, a.live, entry_px)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="XAUAUDm")
    p.add_argument("--lot", type=float, default=0.03)
    p.add_argument("--decide-after", type=float, default=3.0,
                   help="seconds after 19:30:00 to read the direction (2-5)")
    p.add_argument("--max-wait", type=float, default=5.0,
                   help="give up on a flat market after this many seconds")
    p.add_argument("--sl-atr", type=float, default=1.0)
    p.add_argument("--patience", type=int, default=2)
    p.add_argument("--max-minutes", type=int, default=120)
    p.add_argument("--live", action="store_true",
                   help="actually send orders; without it, nothing is sent")
    p.add_argument("--once", action="store_true", help="one session then exit")
    a = p.parse_args()

    a.decide_after = min(5.0, max(2.0, a.decide_after))
    a.max_wait = max(a.decide_after, min(5.0, a.max_wait))
    if a.sl_atr <= 0:
        print("[ERROR] --sl-atr must be positive; a stop is not optional here")
        return 2

    if not mt5.initialize():
        log(f"MT5 init failed: {mt5.last_error()}")
        return 2
    sym = resolve_symbol(a.symbol)
    if sym is None:
        log(f"symbol {a.symbol} not found on this broker")
        return 2
    mt5.symbol_select(sym, True)

    acct = mt5.account_info()
    log("=" * 68)
    log(f"clock_scalp_bot  {sym}  lot {a.lot}  decide +{a.decide_after}s  "
        f"SL {a.sl_atr}xATR  patience {a.patience}")
    log(f"MODE: {'LIVE -- REAL ORDERS' if a.live else 'DRY RUN -- sends nothing'}")
    if acct:
        log(f"account {acct.login} ({acct.server})  equity {acct.equity:.2f} "
            f"{acct.currency}")
        if a.live and acct.equity <= 0:
            log("equity is 0.00 -- nothing can be traded until the account "
                "is funded. Staying up in case it is topped up.")
    log("=" * 68)

    try:
        while True:
            run_once(a, sym)
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
