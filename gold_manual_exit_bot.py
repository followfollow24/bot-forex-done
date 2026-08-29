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

    def on_poll(self):
        """[FIX 2026-08-05] The ATR-milestone check used to hang off
        process_bar(), which the main loop only calls when a new bar CLOSES.
        On the H1 bots that capped alerts at one check per hour (once a DAY on
        gold_daily_breakout), evaluated against the closed bar's close -- so a
        spike to +2xATR that retraced before the hour ended was never
        reported. Running it from on_poll() instead checks every
        poll_interval_sec (~30s) against the LIVE tick. The
        _alerted_atr_level_max/min ratchets still guarantee one message per
        whole-ATR level per trade, so the higher check rate cannot cause spam.

        [FIX 2026-08-05, part 2] MUST call super().on_poll() -- overriding
        without it was itself a bug: GoldCWiderBot.on_poll() now also runs
        _check_broker_close() (the CLOSE-alert check) every ~30s, and EVERY
        live bot runs through this subclass (all 9 use --manual-exit), so
        skipping the super() call would have silently kept close-detection
        latency at up to an hour for 100% of the fleet -- the exact bug this
        commit exists to fix, reintroduced by omission one class down. Caught
        in review before deploy, not from a live incident.
        """
        super().on_poll()
        self._check_atr_milestones()

    def _live_price(self, side: str):
        """Live tick price to value an open position against, or None.

        Uses the price the position would actually CLOSE at -- bid for a long,
        ask for a short -- so the reported P&L matches what the user would get
        by closing right now, rather than being optimistic by one spread.
        """
        try:
            if mt5 is None:
                return None
            tick = mt5.symbol_info_tick(self.bsym)
            if tick is None:
                return None
            px = tick.bid if side == "long" else tick.ask
            return float(px) if px and px > 0 else None
        except Exception:
            return None

    def _check_atr_milestones(self):
        if not self.positions:
            return
        d = self.buf.d
        if d is None:
            return
        bar_close = float(d["c"][-1])

        for pos in self.positions:
            if pos.entry_atr <= 0:
                continue
            direction = 1 if pos.side == "long" else -1
            # 1R is defined by the ACTUAL configured stop, not a constant: these
            # bots run --sl-atr 2.5 while the old alert text assumed 3.0.
            sl_atr = float(getattr(self.strategy, "sl_atr", 0.0) or 0.0)
            last_close = self._live_price(pos.side) or bar_close
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
                    # [2026-08-29] Report R FIRST, xATR second, and only invite a
                    # close at >= 1R.
                    #
                    # This alert was the mechanism behind the account's single
                    # largest measured leak. The stop is sl_atr x ATR (2.5 live),
                    # so 1R = 2.5xATR and the FIRST milestone (+1xATR) is only
                    # +0.40R. The old text announced it and said "close whenever
                    # you like", and the measured average hand-close on
                    # btc_h1_manual was +0.35R -- i.e. right at that first alert.
                    #
                    # A TP sweep over 591 BTC / 970 gold real filtered entries
                    # (13.4y gold, 8.9y BTC, real spread + swap) priced that exit
                    # policy: closing at +0.35R is EV -0.009R train / -0.032R test,
                    # versus +0.167 / +0.108R for letting the configured target
                    # run. The gap is 0.14-0.18R per trade with the SAME SIGN in
                    # all four train/test x market cells. On the 19 live BTC
                    # trades that is roughly -410 USC actual versus about +400 USC
                    # had they been left alone.
                    #
                    # Gold survives hand-closing because its human happens to
                    # close near +0.96R; BTC's closes at +0.35R and does not. So
                    # the guidance below is keyed to R, not to a feeling.
                    r_mult = profit_atr / sl_atr if sl_atr > 0 else 0.0
                    if r_mult >= 1.0:
                        advice = (f"ถึง +{r_mult:.2f}R แล้ว -- เกิน 1R ปิดได้ถ้าต้องการ "
                                  f"(ค่าเฉลี่ยที่ปิดมือแล้วยังกำไรคือ ~1R ขึ้นไป)")
                    else:
                        advice = (f"ยังได้แค่ +{r_mult:.2f}R -- อย่าเพิ่งปิด "
                                  f"ต้องถึง +1.00R ({sl_atr:.1f}xATR) เป็นอย่างน้อย "
                                  f"(วัดแล้ว: ปิดที่ ~0.35R ทำให้เสีย 0.14R/ไม้)")
                    self._send_telegram_alert(
                        f"[{tag}] {sym} {pos.side.upper()} magic={self.cfg.magic_number}\n"
                        f"ราคาปัจจุบัน: {last_close:.2f}  entry: {pos.entry:.2f}\n"
                        f"กำไรตอนนี้: +{r_mult:.2f}R  (+{profit_atr:.2f}xATR, ~${profit_usd:,.2f})\n"
                        f"{advice}\n"
                        f"(SL={sl_atr:.1f}xATR=1R, entry_atr={pos.entry_atr:.3f}, "
                        f"ts={datetime.now(timezone.utc).isoformat()})"
                    )
            else:
                prev_min = self._alerted_atr_level_min.get(pos.trade_id, 0)
                if level < prev_min:
                    self._alerted_atr_level_min[pos.trade_id] = level
                    # The old text hardcoded "SL 3.0xATR" while the live bots
                    # run --sl-atr 2.5, so it misreported how much room was left
                    # on every losing trade. Read the configured value instead.
                    r_mult = profit_atr / sl_atr if sl_atr > 0 else 0.0
                    self._send_telegram_alert(
                        f"[{tag}] {sym} {pos.side.upper()} magic={self.cfg.magic_number}\n"
                        f"ราคาปัจจุบัน: {last_close:.2f}  entry: {pos.entry:.2f}\n"
                        f"ขาดทุนตอนนี้: {r_mult:.2f}R  ({profit_atr:.2f}xATR, ~${profit_usd:,.2f})\n"
                        f"SL อยู่ที่ -1.00R ({sl_atr:.1f}xATR) -- ปล่อยให้ SL ทำงาน "
                        f"การปิดไม้แพ้เร็วไม่ได้ช่วย ถ้าไม้ชนะก็ถูกปิดเร็วเหมือนกัน\n"
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
