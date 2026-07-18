#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
preflight_gold_manual.py -- Step 3 MANDATORY pre-flight for the new gold
manual-exit live bot (variant adx20_manual, magic 555113) before it receives
its first real signal. Same discipline as preflight_btc_bots.py: min-lot
order round-trip on the real magic number, lot-sizing sanity at the intended
risk%, Telegram check, heartbeat/lock/state file uniqueness vs all 5 other
live variants (adx20tp7, adx18tp7, regime22, btc_cons, btc_aggr).

SAFETY: min lot (0.01) XAUUSDc, immediate close, real cost = one round-trip
spread (~$0.25-0.35 on XAUUSDc) -- negligible in USC terms.
Run on VPS (real MT5 terminal connected).
ASCII-only.
"""
import os
import sys
import time
import urllib.parse
import urllib.request

import MetaTrader5 as mt5

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_live_bot_gold_cwider import _make_paths, SYMBOL_MAGIC, VARIANT_MAGIC_OFFSET

EXPECTED_LOGIN = 160075275
SYMBOL = "XAUUSDc"
CANONICAL_SYMBOL = "XAUUSD"
VARIANT = "adx20_manual"
RISK_PCT = 0.30

OTHER_VARIANTS = [
    ("gold_adx20tp7", "XAUUSD", "adx20tp7"),
    ("gold_adx18tp7", "XAUUSD", "adx18tp7"),
    ("gold_regime22", "XAUUSD", "regime22"),
    ("btc_cons", "BTCUSDC", "btc_cons"),
    ("btc_aggr", "BTCUSDC", "btc_aggr"),
]

results = {}


def send_telegram(msg):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        print("  [TELEGRAM] not configured -- SKIPPED")
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": msg}).encode()
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
        print("  [TELEGRAM] sent OK")
        return True
    except Exception as exc:
        print(f"  [TELEGRAM] FAILED: {exc}")
        return False


def main():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    print("=" * 88)
    print(" GOLD MANUAL-EXIT LIVE BOT PRE-FLIGHT (Step 3, mandatory) -- adx20_manual")
    print("=" * 88)

    if not mt5.initialize():
        print(f"[FAIL] mt5.initialize(): {mt5.last_error()}"); sys.exit(1)

    acc = mt5.account_info()
    if acc is None or acc.login != EXPECTED_LOGIN:
        print(f"[FAIL] login={acc.login if acc else None} != expected {EXPECTED_LOGIN} -- ABORT")
        mt5.shutdown(); sys.exit(1)

    print(f"\nAccount confirmed: login={acc.login}  equity={acc.equity:,.2f} USC "
          f"(${acc.equity/100:,.2f})  balance={acc.balance:,.2f} USC")

    # ── check 1: magic number resolves correctly for adx20_manual ──
    magic = SYMBOL_MAGIC[CANONICAL_SYMBOL] + VARIANT_MAGIC_OFFSET[VARIANT]
    print(f"\nCHECK 1 [magic number]: adx20_manual -> {magic}  "
          f"(expect 555113 = 555003 base + 110 offset)")
    if magic != 555113:
        print(f"  [FAIL] magic mismatch! got {magic}, expected 555113"); mt5.shutdown(); sys.exit(1)
    print("  [PASS]")

    # ── check 2: symbol resolves ──
    info = mt5.symbol_info(SYMBOL)
    if info is None:
        print(f"[FAIL] symbol_info('{SYMBOL}') is None"); mt5.shutdown(); sys.exit(1)
    mt5.symbol_select(SYMBOL, True)
    tick = mt5.symbol_info_tick(SYMBOL)
    print(f"CHECK 2 [symbol resolves]: PASS  ({SYMBOL}, tick bid={tick.bid} ask={tick.ask})")

    # ── check 3: lot sizing sanity at risk 0.30% ──
    equity_usc = acc.equity
    pip_value = info.trade_tick_value * (1.0 / info.trade_tick_size)  # live formula, per $1/oz move per lot approx
    print(f"\nCHECK 3 [lot sizing @ risk={RISK_PCT}%, live equity={equity_usc:,.2f} USC]:")
    approx_atr = 8.0  # $ , rough recent XAUUSD M15 ATR estimate, sanity bound only
    sl_dist = approx_atr * 3.0  # SL = 3.0xATR
    risk_cash = equity_usc * RISK_PCT / 100.0
    raw_lot = risk_cash / (sl_dist * pip_value)
    actual_lot = max(info.volume_min, round(raw_lot, 2))
    floored = actual_lot <= info.volume_min and raw_lot < info.volume_min * 1.5
    print(f"  SL=3.0xATR (approx SLdist=${sl_dist:.1f})  raw_lot={raw_lot:.4f}  "
          f"actual_lot={actual_lot:.2f}  {'!! FLOOR-BOUND' if floored else 'OK, not floor-bound'}")

    # ── check 4: order round-trip on the new magic number ──
    print(f"\nCHECK 4 [order open/close] magic={magic}:")
    lot = info.volume_min
    tick = mt5.symbol_info_tick(SYMBOL)
    req = {
        "action": mt5.TRADE_ACTION_DEAL, "symbol": SYMBOL, "volume": lot,
        "type": mt5.ORDER_TYPE_BUY, "price": tick.ask, "magic": magic,
        "comment": "PREFLIGHT-ADX20MAN"[:16],
        "type_time": mt5.ORDER_TIME_GTC, "type_filling": mt5.ORDER_FILLING_IOC,
    }
    r = mt5.order_send(req)
    if r is None or r.retcode != mt5.TRADE_RETCODE_DONE:
        rc = r.retcode if r else mt5.last_error()
        print(f"  [FAIL] OPEN failed retcode={rc}"); results["order"] = "FAIL-open"
    else:
        ticket = r.order; fill_px = r.price
        print(f"  [PASS] OPEN ok  ticket={ticket}  fill={fill_px}")
        time.sleep(1)
        tick2 = mt5.symbol_info_tick(SYMBOL)
        close_req = {
            "action": mt5.TRADE_ACTION_DEAL, "symbol": SYMBOL, "volume": lot,
            "type": mt5.ORDER_TYPE_SELL, "position": ticket,
            "price": tick2.bid if tick2 else fill_px, "magic": magic,
            "comment": "PREFLIGHT-ADX20MAN-CLOSE"[:24],
            "type_time": mt5.ORDER_TIME_GTC, "type_filling": mt5.ORDER_FILLING_IOC,
        }
        cr = mt5.order_send(close_req)
        if cr is None or cr.retcode != mt5.TRADE_RETCODE_DONE:
            rc = cr.retcode if cr else mt5.last_error()
            print(f"  [WARN] CLOSE failed retcode={rc} -- MANUALLY CLOSE ticket={ticket} NOW")
            results["order"] = "FAIL-close"
        else:
            diff = cr.price - fill_px
            print(f"  [PASS] CLOSE ok  fill={cr.price}  diff={diff:+.2f}")
            results["order"] = "PASS"
            send_telegram(
                f"[PREFLIGHT TEST] adx20_manual (XAUUSDc, magic={magic})\n"
                f"Open {fill_px} -> Close {cr.price} (min-lot roundtrip)\n"
                f"This is a pre-flight check, not a real signal.")

    # ── check 5: file path uniqueness vs all other live variants ──
    print(f"\nCHECK 5 [heartbeat/lock/state file uniqueness vs all other live variants]:")
    all_paths = {"adx20_manual": _make_paths(CANONICAL_SYMBOL, VARIANT)}
    for label, sym, var in OTHER_VARIANTS:
        all_paths[label] = _make_paths(sym, var)
    labels = ["STOP", "STATE", "LOG", "FILLS", "LOCK", "MAGIC", "EQUITY_STOP", "HEARTBEAT"]
    seen = {}
    collision = False
    for who, paths in all_paths.items():
        for lbl, p in zip(labels, paths):
            if lbl == "MAGIC":
                continue
            key = (lbl, p)
            if key in seen and seen[key] != who:
                print(f"  [FAIL] COLLISION: {lbl}={p} used by both {seen[key]} and {who}")
                collision = True
            seen[key] = who
    if not collision:
        print("  [PASS] no filename collisions across all 5 live variants")
    for who, paths in all_paths.items():
        print(f"    {who:<16} magic={paths[5]}  heartbeat={os.path.basename(paths[7])}  "
              f"lock={os.path.basename(paths[4])}")

    print("\n" + "=" * 88)
    print(" SUMMARY")
    print("=" * 88)
    print(f"  order round-trip: {results.get('order', 'NOT RUN')}")
    all_pass = results.get("order") == "PASS" and not collision
    print(f"\n  OVERALL: {'ALL CHECKS PASSED -- safe to proceed to Step 4' if all_pass else 'FAILURES PRESENT -- DO NOT PROCEED to Step 4'}")
    print("=" * 88)

    mt5.shutdown()


if __name__ == "__main__":
    main()
