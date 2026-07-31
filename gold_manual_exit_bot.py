#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gold_manual_exit_bot.py -- generic manual-exit wrapper, originally built for
variant "adx20_manual" (gold, H1 trend + M15 pullback, ADX>=20) and reused
2026-07-29 for the H1 cost/ATR fix across btc_h1_manual / eth_h1_manual /
gold_h1_manual. Nothing in this class is gold- or symbol-specific -- it only
touches self.positions / self.bsym / self.cfg / self.variant_tag, all of which
GoldCWiderBot already sets generically per --symbol/--variant-tag. TP is
effectively disabled (set to a very large ATR multiple via CLI --tp-atr, e.g.
999) so the position is never auto-closed at a target -- the user decides when
to close manually via MT5.

In place of an automatic TP, this sends a Telegram alert every time
unrealized P&L crosses a new whole-ATR milestone in either direction
(+1xATR, +2xATR, ... on profit; -1xATR, -2xATR, ... on drawdown), so the
user has the information to make that manual call. Each milestone alerts
once per position -- only when a *new* extreme (further from zero than
any level already alerted, in that direction) is reached, so recovering
back toward zero and dipping again to an already-seen level does not
re-spam.

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
        # [FIX 2026-07-31] "Manual exit" means no automatic profit-taking at
        # all -- not just no fixed TP. HybridTrendPullback already defaults
        # trail_atr_mult/trail_activation_atr to 999 (off), so this was a
        # no-op for btc_h1_manual/eth_h1_manual/gold_h1_manual. But
        # GoldDailyDonchianBreakout defaults trailing ON (3.0xATR, activates
        # at 1.0xATR) since that's how it was validated (PF 2.44/DD 3.1%) --
        # left as-is, this class's trailing stop would still auto-close
        # positions, defeating the entire point of routing through
        # GoldManualExitBot. Force it off here so ANY strategy run through
        # this wrapper is unambiguously SL-only + manual close, matching
        # what "--manual-exit" actually promises regardless of strategy_cls.
        self.strategy.trail_atr_mult = 999.0
        self.strategy.trail_activation_atr = 999.0
        self._alerted_atr_level_max = {}  # trade_id -> highest whole-ATR profit milestone already alerted
        self._alerted_atr_level_min = {}  # trade_id -> lowest (most negative) whole-ATR drawdown milestone already alerted

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
            level = int(profit_atr)  # floor toward zero: +1.9 -> 1, -0.5 -> 0, -1.9 -> -1
            if level == 0:
                continue

            profit_usd = profit_price * pos.lot * self._pip_value_per_lot_approx()

            tag = self.variant_tag.upper()
            sym = self.bsym
            if level > 0:
                prev_max = self._alerted_atr_level_max.get(pos.trade_id, 0)
                if level > prev_max:
                    self._alerted_atr_level_max[pos.trade_id] = level
                    self._send_telegram_alert(
                        f"[{tag}] {sym} {pos.side.upper()} magic={self.cfg.magic_number}\n"
                        f"ราคาปัจจุบัน: {last_close:.2f}  entry: {pos.entry:.2f}\n"
                        f"กำไรตอนนี้: +{profit_atr:.2f}xATR (~${profit_usd:,.2f})\n"
                        f"ผ่านจุด +{level}xATR ใหม่ -- ไม่มี TP อัตโนมัติ ตัดสินใจปิดเองได้เลยถ้าต้องการ\n"
                        f"(entry_atr={pos.entry_atr:.3f}, ts={datetime.now(timezone.utc).isoformat()})"
                    )
            else:
                prev_min = self._alerted_atr_level_min.get(pos.trade_id, 0)
                if level < prev_min:
                    self._alerted_atr_level_min[pos.trade_id] = level
                    self._send_telegram_alert(
                        f"[{tag}] {sym} {pos.side.upper()} magic={self.cfg.magic_number}\n"
                        f"ราคาปัจจุบัน: {last_close:.2f}  entry: {pos.entry:.2f}\n"
                        f"ขาดทุนตอนนี้: {profit_atr:.2f}xATR (~${profit_usd:,.2f})\n"
                        f"ผ่านจุด {level}xATR ใหม่ (ขาดทุนเพิ่ม) -- SL อยู่ที่ 3.0xATR, ตัดสินใจปิดเองได้เลยถ้าต้องการ\n"
                        f"(entry_atr={pos.entry_atr:.3f}, ts={datetime.now(timezone.utc).isoformat()})"
                    )

    def _pip_value_per_lot_approx(self):
        # Reads the real tick_value/tick_size ratio from MT5 for whichever
        # symbol this instance trades (self.bsym) -- works unchanged for gold,
        # BTC, or ETH. Best-effort approximation for the alert text only -- not
        # used for any order sizing or risk calculation, purely informational.
        try:
            if mt5 is not None:
                info = mt5.symbol_info(self.bsym)
                if info and info.trade_tick_size > 0:
                    return info.trade_tick_value / info.trade_tick_size
        except Exception:
            pass
        return 1.0
