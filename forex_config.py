#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 forex_config.py  —  ForexBot Configuration
================================================================================
รองรับ: FX pairs (EURUSD, GBPUSD ...) + Commodities (XAUUSD / Gold)

ความแตกต่างสำคัญจาก LiveConfig (crypto):
  1. สกุลเงิน (pairs) + ทอง (XAUUSD) แทน coins
  2. Lot-based sizing แทน qty-based
  3. Spread + Swap (rollover) แทน Fee + Funding Rate
  4. Session filters + Weekend close logic
  5. MT5 connection แทน ccxt/Binance API
  6. ATR-based spread filter — instrument-agnostic (ใช้ได้กับทองด้วย)
  7. contract_sizes per-symbol (100,000 units สำหรับ FX, 100 oz สำหรับ XAUUSD)

ตัวอย่างรัน XAUUSD:
  python3 forex_live_bot.py --pairs XAUUSD --risk 0.3 --magic 555003
================================================================================
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple


@dataclass
class ForexConfig:
    # ── ทุน ───────────────────────────────────────────────────────────────────
    total_capital_usd: float = 10_000.0
    risk_per_trade_pct: float = 0.75       # % ของ equity ต่อไม้ (ปรับต่ำสำหรับทอง → 0.3)

    # ── เพดานความเสี่ยง 3 ชั้น ────────────────────────────────────────────────
    per_pair_cap_frac: float = 0.20
    max_concurrent: int = 4
    total_exposure_frac: float = 1.00
    min_lot: float = 0.01                  # micro lot = ขั้นต่ำ
    stop_equity_frac: float = 0.35

    # ── Lot / Risk cap (กัน lot หลุดขนาดเมื่อ min_lot ดันความเสี่ยงเกิน) ────────
    max_risk_per_trade_pct: float = 2.0    # ถ้าความเสี่ยงจริง > นี้ → ข้ามไม้นั้นไปเลย
    max_lot: float = 50.0                  # เพดาน backstop กว้างๆ (ไม่ใช่ตัวหลัก)

    # ── Fractional Kelly ─────────────────────────────────────────────────────
    kelly_fraction: float = 0.25
    kelly_min_trades: int = 20
    kelly_max_risk_pct: float = 2.0
    kelly_min_risk_pct: float = 0.1

    # ── Tiered Circuit Breaker (เข้มกว่า crypto) ─────────────────────────────
    cb_tier1_pct: float = 2.0
    cb_tier2_pct: float = 4.0
    cb_tier3_pct: float = 6.0

    # ── Partial Take Profit ──────────────────────────────────────────────────
    partial_tp_atr: float = 1.5
    partial_tp_frac: float = 0.5
    move_sl_to_breakeven: bool = True

    # ── ATR Trailing Stop ────────────────────────────────────────────────────
    trail_atr_mult: float = 2.0
    trail_activation_atr: float = 0.5

    # ── Spread filter (ATR-based — instrument-agnostic) ───────────────────────
    spread_filter_atr_ratio: float = 0.20

    # ── News Blackout ────────────────────────────────────────────────────────
    news_blackout: List[Tuple[str, str]] = field(default_factory=list)

    # ── Forex Cost Model ──────────────────────────────────────────────────────
    spread_pips: Dict[str, float] = field(default_factory=lambda: {
        "EURUSD": 0.5,  "GBPUSD": 0.7,  "USDJPY": 0.5,  "USDCHF": 0.9,
        "AUDUSD": 0.7,  "USDCAD": 0.9,  "NZDUSD": 1.0,
        "EURJPY": 0.8,  "GBPJPY": 1.2,  "EURGBP": 0.7,
        "XAUUSD": 50.0,
    })
    swap_long_pips_per_day: Dict[str, float] = field(default_factory=lambda: {
        "EURUSD": -0.30, "GBPUSD":  0.10, "USDJPY":  0.50, "USDCHF":  0.40,
        "AUDUSD":  0.20, "USDCAD": -0.40, "NZDUSD":  0.10,
        "EURJPY":  0.20, "GBPJPY":  0.60, "EURGBP": -0.20,
        "XAUUSD": -5.00,
    })
    swap_short_pips_per_day: Dict[str, float] = field(default_factory=lambda: {
        "EURUSD":  0.10, "GBPUSD": -0.40, "USDJPY": -0.70, "USDCHF": -0.60,
        "AUDUSD": -0.40, "USDCAD":  0.20, "NZDUSD": -0.30,
        "EURJPY": -0.50, "GBPJPY": -0.90, "EURGBP":  0.00,
        "XAUUSD":  1.00,
    })

    # ── Pip ──────────────────────────────────────────────────────────────────
    pip_size: Dict[str, float] = field(default_factory=lambda: {
        "EURUSD": 0.0001, "GBPUSD": 0.0001, "AUDUSD": 0.0001,
        "NZDUSD": 0.0001, "USDCAD": 0.0001, "USDCHF": 0.0001, "EURGBP": 0.0001,
        "USDJPY": 0.01,   "EURJPY": 0.01,   "GBPJPY": 0.01,
        "XAUUSD": 0.01,
    })
    # FALLBACK เท่านั้น (dry-run / ไม่มี MT5) — live mode ใช้ get_pip_value_live()
    # ที่ดึง trade_tick_value จาก MT5 โดยตรง ซึ่งถูกต้องไม่ว่า account currency
    # จะเป็น USD หรือ USC (cent) และไม่ว่า contract_size จริงจะเท่าไหร่ก็ตาม
    pip_value_usd_approx: Dict[str, float] = field(default_factory=lambda: {
        "EURUSD": 10.0,  "GBPUSD": 10.0,  "AUDUSD": 10.0,  "NZDUSD": 10.0,
        "USDCAD": 7.6,   "USDCHF": 11.0,  "EURGBP": 12.5,
        "USDJPY": 9.1,   "EURJPY": 9.1,   "GBPJPY": 9.1,
        "XAUUSD": 1.0,   # 100 oz × $0.01 = $1.00/pip/lot (fallback only)
    })

    # FALLBACK เท่านั้น — contract_size จริงต่างกันได้ต่อ broker/account type
    # (พิสูจน์แล้ว: XAUUSDc Real Cent = 1.0 oz ไม่ใช่ 100.0)
    # ห้ามใช้ค่าตรงนี้คำนวณ pip value จริงบน live mode อีกต่อไป
    contract_size: float = 100_000.0
    contract_sizes: Dict[str, float] = field(default_factory=lambda: {
        "EURUSD": 100_000.0, "GBPUSD": 100_000.0, "AUDUSD": 100_000.0,
        "NZDUSD": 100_000.0, "USDCAD": 100_000.0, "USDCHF": 100_000.0,
        "EURGBP": 100_000.0, "USDJPY": 100_000.0, "EURJPY": 100_000.0,
        "GBPJPY": 100_000.0,
        "XAUUSD": 100.0,     # fallback only — actual may differ per broker/account
    })

    # ── Session Filters ───────────────────────────────────────────────────────
    trade_london:    bool = True
    trade_newyork:   bool = True
    trade_tokyo:     bool = False
    trade_sydney:    bool = False
    require_active_session: bool = True
    close_before_weekend_mins: int = 60
    skip_rollover_window: bool = True
    near_close_size_mult: float = 0.5

    # ── การเทรด ──────────────────────────────────────────────────────────────
    timeframe: str = "15m"
    max_hold_bars: int = 64
    cooldown_bars: int = 20

    # ── คู่เงิน / Symbols ─────────────────────────────────────────────────────
    symbols: List[str] = field(default_factory=lambda: ["EURUSD"])
    history_bars: int = 900
    warm_bars: int = 150

    # ── ไฟล์ ─────────────────────────────────────────────────────────────────
    state_file: str = "forex_bot_state.json"
    log_file:   str = "forex_bot.log"
    trades_csv: str = "forex_trades.csv"
    db_file:    str = "forex_bot.db"

    # ── MT5 ──────────────────────────────────────────────────────────────────
    dry_run:              bool = True
    metaapi_token:        str  = ""
    metaapi_account_id:   str  = ""
    magic_number:         int  = 20240101
    leverage:             int  = 100
    poll_interval_sec:    int  = 30
    # ต้อง set True อย่างชัดเจนผ่าน --allow-real CLI flag เพื่อรันบนบัญชีที่
    # ไม่ใช่ DEMO — ค่า default False ป้องกันการเทรดเงินจริงโดยไม่ตั้งใจ
    allow_real:           bool = False

    # ── Regime Detection ─────────────────────────────────────────────────────
    hurst_window: int   = 200
    adx_period:   int   = 14
    vol_short:    int   = 8
    vol_long:     int   = 96
    h_trend:   float = 0.55
    h_mr:      float = 0.45
    adx_trend: float = 25.0
    adx_range: float = 20.0
    vol_expand: float = 1.8

    # ─────────────────────────────────────────────────────────────────────────

    @property
    def timeframe_ms(self) -> int:
        mul = {"m": 60_000, "h": 3_600_000, "d": 86_400_000}
        v, u = int(self.timeframe[:-1]), self.timeframe[-1]
        return v * mul.get(u, 60_000)

    @property
    def timeframe_minutes(self) -> int:
        mul = {"m": 1, "h": 60, "d": 1440}
        v, u = int(self.timeframe[:-1]), self.timeframe[-1]
        return v * mul.get(u, 1)

    def get_pip_size(self, symbol: str) -> float:
        return self.pip_size.get(symbol, 0.0001)

    def get_pip_value(self, symbol: str) -> float:
        """มูลค่า 1 pip ต่อ 1 lot (ค่าประมาณสำหรับ paper trading / fallback)."""
        return self.pip_value_usd_approx.get(symbol, 10.0)

    def get_contract_size(self, symbol: str) -> float:
        """Contract size ต่อ 1 standard lot — fallback เท่านั้น."""
        return self.contract_sizes.get(symbol, self.contract_size)

    def get_price_decimals(self, symbol: str) -> int:
        _dec = {
            "EURUSD": 5, "GBPUSD": 5, "AUDUSD": 5, "NZDUSD": 5,
            "USDCAD": 5, "USDCHF": 5, "EURGBP": 5,
            "USDJPY": 3, "EURJPY": 3, "GBPJPY": 3,
            "XAUUSD": 2,
            "BTCUSDC": 2, "ETHUSDC": 2,
        }
        if symbol in _dec:
            return _dec[symbol]
        u = symbol.upper()
        if "XAU" in u or "GOLD" in u:
            return 2
        if "BTC" in u or "ETH" in u:
            return 2
        if "JPY" in u:
            return 3
        return 5

    def add_symbol_alias(self, canonical: str, alias: str):
        """Copy pip_size/spread/swap config จาก canonical → broker alias.

        ปลอดภัยสำหรับ pip_size และ price_decimals (คงที่ตามรูปแบบราคา)
        แต่ห้ามใช้ค่า contract_size/pip_value ที่ copy มาตรงนี้คำนวณ pip value
        จริงบน live mode — ดึงจาก MT5 trade_tick_value โดยตรงแทนเสมอ
        """
        if canonical == alias:
            return
        for d in (self.pip_size, self.pip_value_usd_approx,
                  self.spread_pips, self.swap_long_pips_per_day,
                  self.swap_short_pips_per_day, self.contract_sizes):
            if canonical in d and alias not in d:
                d[alias] = d[canonical]

    def spread_cost_usd(self, symbol: str, lot: float) -> float:
        return self.spread_pips.get(symbol, 1.0) * self.get_pip_value(symbol) * lot
