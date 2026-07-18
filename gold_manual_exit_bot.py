#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gold_manual_exit_bot.py -- variant "adx20_manual": same entry signal as
adx20tp7 (H1 trend + M15 pullback, ADX>=20), SL=3.0xATR unchanged, but
TP is effectively disabled (set to a very large ATR multiple via CLI
--tp-atr, e.g. 999) so the position is never auto-closed at a target --
the user decides when to close manually via MT5.

In place of an automatic TP, this sends a Telegram alert every time
unrealized profit crosses a new whole-ATR milestone (+1xATR, +2xATR, ...),
so the user has the information to make that manual call. Each milestone
alerts once per position (no repeat spam).

No backtest exists for this variant -- manual-close outcomes cannot be
simulated. Entry/SL logic is the already-validated adx20tp7 signal;
only the exit decision differs. Live-only forward test.
"""
from datetime import datetime, timezone

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

from forex_live_bot_gold_cwider import GoldCWiderBot


class GoldManualExitBot(GoldCWiderBot):
    """adx20tp7 entry/SL, no auto-TP, ATR-milestone Telegram alerts."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._alerted_atr_level = {}  # trade_id -> highest whole-ATR milestone already alerted

    def process_bar(self):
        super().process_bar()
        self._check_atr_milestones()

    def _check_atr_milestones(self):
        if not self.positions:
            return
        d = self.buf.d
        if d is None:
            return
        last_close = float(d["c"][-1])

        for pos in self.positions:
            if pos.entry_atr <= 0:
                continue
            direction = 1 if pos.side == "long" else -1
            profit_price = (last_close - pos.entry) * direction
            profit_atr = profit_price / pos.entry_atr
            level = int(profit_atr)  # floor toward zero: +1.9 -> 1, -0.5 -> 0
            if level < 1:
                continue  # only alert on profit milestones, not drawdown

            prev = self._alerted_atr_level.get(pos.trade_id, 0)
            if level > prev:
                self._alerted_atr_level[pos.trade_id] = level
                profit_usd = profit_price * pos.lot * self._pip_value_per_lot_approx()
                self._send_telegram_alert(
                    f"[ADX20-MANUAL] {pos.side.upper()} magic={self.cfg.magic_number}\n"
                    f"ราคาปัจจุบัน: {last_close:.2f}  entry: {pos.entry:.2f}\n"
                    f"กำไรตอนนี้: +{profit_atr:.2f}xATR (~${profit_usd:,.2f})\n"
                    f"ผ่านจุด +{level}xATR ใหม่ -- ไม่มี TP อัตโนมัติ ตัดสินใจปิดเองได้เลยถ้าต้องการ\n"
                    f"(entry_atr={pos.entry_atr:.3f}, ts={datetime.now(timezone.utc).isoformat()})"
                )

    def _pip_value_per_lot_approx(self):
        # XAUUSDc: 1 lot = pip_value ~ contract_size (already established this
        # session's convention for gold: price move x lot roughly = USD, since
        # contract_size=100oz standard but live formula reads real tick_value.
        # Best-effort approximation for the alert text only -- not used for
        # any order sizing or risk calculation, purely informational.
        try:
            if mt5 is not None:
                info = mt5.symbol_info(self.bsym)
                if info and info.trade_tick_size > 0:
                    return info.trade_tick_value / info.trade_tick_size
        except Exception:
            pass
        return 1.0
