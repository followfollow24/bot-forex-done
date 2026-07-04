#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
report_open_positions.py — Detailed per-position report from MT5 (live, real-time)

Run this on the VPS where MT5 terminal is already logged in:
    cd C:\\Users\\Administrator\\Desktop
    python report_open_positions.py

Shows every open position across all magic numbers used by the 3 bots
(adx20tp7 / m5tp7 / adx18tp7), with entry price, SL, TP, current bid/ask,
floating P&L in USD, and how far price has moved toward TP/SL as a %.
"""
import MetaTrader5 as mt5
from datetime import datetime, timezone

# Mirrors SYMBOL_MAGIC + VARIANT_MAGIC_OFFSET from forex_live_bot_gold_cwider.py
SYMBOL_MAGIC = {"XAUUSD": 555003, "EURUSD": 555001, "GBPUSD": 555002}
VARIANT_MAGIC_OFFSET = {
    "cwider": 0, "tp7": 10, "tp8": 20, "mix_a": 30, "mix_b": 40,
    "adx20tp7": 50, "m5tp7": 60, "m5adx18": 70, "adx18tp7": 80,
}
MAGIC_TO_VARIANT = {}
for sym, base in SYMBOL_MAGIC.items():
    for variant, off in VARIANT_MAGIC_OFFSET.items():
        MAGIC_TO_VARIANT[base + off] = f"{sym}/{variant}"


def fmt_usd(x: float) -> str:
    sign = "+" if x >= 0 else ""
    return f"{sign}{x:,.2f}"


def main():
    if not mt5.initialize():
        print(f"[ERROR] mt5.initialize() failed: {mt5.last_error()}")
        return

    acct = mt5.account_info()
    if acct is None:
        print(f"[ERROR] mt5.account_info() returned None: {mt5.last_error()}")
        mt5.shutdown()
        return

    positions = mt5.positions_get()
    positions = list(positions) if positions else []

    print("=" * 100)
    print(f" ACCOUNT  login={acct.login}  balance={acct.balance:,.2f}  "
          f"equity={acct.equity:,.2f}  {acct.currency}")
    print(f" Report time (UTC): {datetime.now(timezone.utc).isoformat()}")
    print(f" Open positions: {len(positions)}")
    print("=" * 100)

    if not positions:
        print("\n  (no open positions right now)")
        mt5.shutdown()
        return

    total_floating = 0.0

    # sort by variant name for readability
    positions.sort(key=lambda p: (MAGIC_TO_VARIANT.get(p.magic, f"UNKNOWN({p.magic})"), p.time))

    for p in positions:
        variant = MAGIC_TO_VARIANT.get(p.magic, f"UNKNOWN(magic={p.magic})")
        side = "LONG" if p.type == mt5.POSITION_TYPE_BUY else "SHORT"

        tick = mt5.symbol_info_tick(p.symbol)
        bid, ask = (tick.bid, tick.ask) if tick else (0.0, 0.0)
        current = bid if side == "LONG" else ask  # price you'd close at right now

        entry = p.price_open
        sl = p.sl
        tp = p.tp
        floating_pnl = p.profit + p.swap
        total_floating += floating_pnl

        # progress toward TP / SL as % of the distance from entry
        direction = 1 if side == "LONG" else -1
        moved = (current - entry) * direction
        dist_to_tp = abs(tp - entry) if tp else 0.0
        dist_to_sl = abs(entry - sl) if sl else 0.0
        pct_to_tp = (moved / dist_to_tp * 100.0) if dist_to_tp > 0 else float("nan")
        pct_to_sl = (-moved / dist_to_sl * 100.0) if dist_to_sl > 0 and moved < 0 else 0.0

        opened_at = datetime.fromtimestamp(p.time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        held_hours = (datetime.now(timezone.utc) - datetime.fromtimestamp(p.time, tz=timezone.utc)).total_seconds() / 3600.0

        print(f"\n  [{variant}]  ticket={p.ticket}  {side}  {p.symbol}  lot={p.volume}")
        print(f"    Opened     : {opened_at}  ({held_hours:.1f}h ago)")
        print(f"    Entry      : {entry:.2f}")
        print(f"    Current    : {current:.2f}  (bid={bid:.2f} / ask={ask:.2f})")
        print(f"    SL         : {sl:.2f}" + (f"   ({pct_to_sl:.0f}% of the way to SL)" if pct_to_sl > 0 else ""))
        print(f"    TP         : {tp:.2f}" + (f"   ({pct_to_tp:.0f}% of the way to TP)" if not (pct_to_tp != pct_to_tp) else ""))
        print(f"    Floating PnL: {fmt_usd(floating_pnl)} USD  (swap={p.swap:+.2f})")
        print(f"    Comment    : {p.comment}")

    print("\n" + "=" * 100)
    print(f" TOTAL FLOATING P&L (all open positions): {fmt_usd(total_floating)} USD")
    print("=" * 100)

    mt5.shutdown()


if __name__ == "__main__":
    main()
