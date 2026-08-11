#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 forex_executor.py  —  MetaTrader 5 (local terminal, attach mode)
================================================================================
รันบน Windows ที่มี MT5 terminal ติดตั้งและ login (Exness demo) อยู่แล้ว
Python เชื่อมต่อ terminal โดยตรงผ่าน MetaTrader5 package (IPC) — ไม่ผ่าน cloud bridge

  Python (mt5.initialize) ──IPC──▶ MT5 Terminal (login ค้างอยู่แล้ว) ──▶ Broker

สิ่งที่เหมือนเดิมทุกอย่าง (เทียบกับ MetaApi เวอร์ชันก่อน):
  - Symbol: XAUUSD, XAUUSDm ... (resolve_symbol หา suffix ของ broker เอง)
  - Order: market buy/sell + SL/TP ใน request เดียว
  - Position tracking: ticket (string) ใช้แทน positionId
  - Partial close (MT5 จัดการให้อัตโนมัติเมื่อ volume < position volume)
================================================================================
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Tuple

from forex_config import ForexConfig

try:
    import MetaTrader5 as mt5
    _HAS_MT5 = True
except ImportError:
    _HAS_MT5 = False


_TIMEFRAME_MAP = {
    "1m":  "TIMEFRAME_M1",
    "5m":  "TIMEFRAME_M5",
    "15m": "TIMEFRAME_M15",
    "30m": "TIMEFRAME_M30",
    "1h":  "TIMEFRAME_H1",
    "4h":  "TIMEFRAME_H4",
    "1d":  "TIMEFRAME_D1",
    "1w":  "TIMEFRAME_W1",
}

# Filling modes ที่จะลองตามลำดับ — บางโบรกเกอร์รับเฉพาะ IOC/FOK/RETURN
_FILLING_MODES = []
if _HAS_MT5:
    _FILLING_MODES = [
        mt5.ORDER_FILLING_IOC,
        mt5.ORDER_FILLING_FOK,
        mt5.ORDER_FILLING_RETURN,
    ]


# =============================================================================
# MT5 CONNECTOR  (local terminal, attach mode)
# =============================================================================
class MT5Connector:
    """เชื่อมต่อ MT5 terminal ที่รันอยู่บนเครื่องเดียวกัน (login ไว้แล้ว) ผ่าน
    MetaTrader5 python package (IPC ตรงกับ terminal — ไม่ใช่ cloud bridge)."""

    def __init__(self, cfg: ForexConfig, log: logging.Logger):
        self.cfg = cfg
        self.log = log
        self._account_info: dict = {}

    # ── connect ──────────────────────────────────────────────────────────────

    def connect(self) -> bool:
        if not _HAS_MT5:
            if self.cfg.dry_run:
                self.log.warning(
                    "MetaTrader5 package ใช้ได้บน Windows เท่านั้น — "
                    "dry_run ใช้ข้อมูลจำลอง")
                return True
            self.log.error(
                "กรุณาติดตั้ง: pip install MetaTrader5 (รันบน Windows ที่มี "
                "MT5 terminal login ไว้แล้ว)")
            return False

        try:
            path = os.environ.get("MT5_TERMINAL_PATH") or None
            ok = mt5.initialize(path=path) if path else mt5.initialize()

            # Fallback: ถ้า attach ไม่สำเร็จ และมี MT5_LOGIN/MT5_PASSWORD/MT5_SERVER
            # ใน env — ลอง initialize แบบ login ตรง
            if not ok:
                login    = os.environ.get("MT5_LOGIN")
                password = os.environ.get("MT5_PASSWORD")
                server   = os.environ.get("MT5_SERVER")
                if login and password and server:
                    self.log.info(
                        "MT5: attach ไม่สำเร็จ — ลอง initialize() ด้วย "
                        "MT5_LOGIN/MT5_PASSWORD/MT5_SERVER")
                    kwargs = dict(login=int(login), password=password, server=server)
                    if path:
                        kwargs["path"] = path
                    ok = mt5.initialize(**kwargs)

            if not ok:
                self.log.error(f"mt5.initialize() failed: {mt5.last_error()}")
                return False

            info = mt5.account_info()
            if info is None:
                self.log.error(f"mt5.account_info() คืนค่า None: {mt5.last_error()}")
                mt5.shutdown()
                return False

            self._account_info = self._account_info_to_dict(info)
            mode = "DEMO" if self._account_info["type"] == "ACCOUNT_TRADE_MODE_DEMO" else "LIVE"
            self.log.info(
                f"MT5 connected [{mode}]  login={info.login}  server={info.server}  "
                f"balance={info.balance:.2f}  equity={info.equity:.2f}  "
                f"currency={info.currency}")
            return True
        except Exception as exc:
            self.log.error(f"MT5 connect failed: {exc}")
            return False

    @staticmethod
    def _account_info_to_dict(info) -> dict:
        type_map = {
            getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", 0):    "ACCOUNT_TRADE_MODE_DEMO",
            getattr(mt5, "ACCOUNT_TRADE_MODE_CONTEST", 1): "ACCOUNT_TRADE_MODE_CONTEST",
            getattr(mt5, "ACCOUNT_TRADE_MODE_REAL", 2):    "ACCOUNT_TRADE_MODE_REAL",
        }
        return {
            "type":     type_map.get(info.trade_mode, "UNKNOWN"),
            "broker":   info.company,
            "server":   info.server,
            "login":    info.login,
            "name":     info.name,
            "balance":  float(info.balance),
            "equity":   float(info.equity),
            "currency": info.currency,
        }

    # ── account ───────────────────────────────────────────────────────────────

    def get_account_info(self, refresh: bool = False) -> dict:
        """คืน account info dict (cached จาก connect(), หรือ refresh ใหม่).

        Keys ที่สำคัญ: type ("ACCOUNT_TRADE_MODE_DEMO"/"ACCOUNT_TRADE_MODE_REAL"),
        broker, balance, equity, currency, login, server, name.
        """
        if not _HAS_MT5:
            return self._account_info
        if refresh or not self._account_info:
            info = mt5.account_info()
            if info is not None:
                self._account_info = self._account_info_to_dict(info)
        return self._account_info

    def is_demo(self) -> bool:
        """True ถ้า account type คือ DEMO."""
        return self.get_account_info().get("type", "").upper() == "ACCOUNT_TRADE_MODE_DEMO"

    def get_balance(self) -> float:
        if not _HAS_MT5:
            return self.cfg.total_capital_usd
        info = mt5.account_info()
        if info is None:
            # [FIX C1-1] Do NOT silently return stale cfg.total_capital_usd —
            # that default can be wildly wrong (e.g. 10,000 USD default vs a
            # small Real Cent account). Raise so the main-loop reconnect path
            # (consecutive_errors → reconnect) handles this correctly.
            raise RuntimeError(f"mt5.account_info() returned None: {mt5.last_error()}")
        return float(info.balance)

    def get_equity(self) -> float:
        if not _HAS_MT5:
            return self.cfg.total_capital_usd
        info = mt5.account_info()
        if info is None:
            # [FIX C1-2] Same reasoning as get_balance(). Equity feeds both
            # position-sizing (risk_cash = equity * risk_pct) and the equity-stop
            # circuit breaker — a silently wrong fallback could mis-size a position
            # or fail to trigger equity-stop during a real drawdown.
            raise RuntimeError(f"mt5.account_info() returned None: {mt5.last_error()}")
        return float(info.equity)

    # ── price ─────────────────────────────────────────────────────────────────

    def get_current_price(self, symbol: str) -> Tuple[float, float]:
        """คืน (bid, ask)."""
        if not _HAS_MT5:
            return (0.0, 0.0)
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return (0.0, 0.0)
        return (float(tick.bid), float(tick.ask))

    def get_pip_value_live(self, symbol: str) -> float:
        """คำนวณ pip value ต่อ lot จาก MT5 trade_tick_value โดยตรง.

        สูตร: pip_value = trade_tick_value × (pip_size / trade_tick_size)
        trade_tick_value จาก MT5 คืนค่าใน account currency อยู่แล้ว
        ถูกต้องไม่ว่า account จะเป็น USD หรือ USC (cent) และไม่ว่า
        contract_size จริงจะเท่าไหร่ (XAUUSDm=100 oz, XAUUSDc=1 oz etc.)
        """
        if not _HAS_MT5:
            return self.cfg.get_pip_value(symbol)
        info = mt5.symbol_info(symbol)
        if info is None or not info.trade_tick_size:
            self.log.debug(
                f"get_pip_value_live: symbol_info('{symbol}') unavailable or "
                f"trade_tick_size=0 — falling back to cfg.get_pip_value()")
            return self.cfg.get_pip_value(symbol)
        pip_size = self.cfg.get_pip_size(symbol)
        return info.trade_tick_value * (pip_size / info.trade_tick_size)

    # ── OHLCV ─────────────────────────────────────────────────────────────────

    def fetch_ohlcv(self, symbol: str, timeframe: str,
                    count: int = 10) -> list:
        """ดึง OHLCV จาก MT5 terminal — คืน [[ts_ms, o, h, l, c, v], ...]
        เรียงจากเก่า -> ใหม่ (ascending by timestamp)."""
        if not _HAS_MT5:
            return []

        tf_name = _TIMEFRAME_MAP.get(timeframe)
        if tf_name is None:
            self.log.debug(f"fetch_ohlcv: unknown timeframe '{timeframe}'")
            return []
        tf = getattr(mt5, tf_name)

        try:
            rates = mt5.copy_rates_from_pos(symbol, tf, 0, min(count, 1000))
            if rates is None:
                return []
            result = [
                [int(r["time"]) * 1000, float(r["open"]), float(r["high"]),
                 float(r["low"]), float(r["close"]), float(r["tick_volume"])]
                for r in rates
            ]
            result.sort(key=lambda r: r[0])   # ascending — ที่ CandleBuffer ต้องการ
            return result
        except Exception as exc:
            self.log.debug(f"fetch_ohlcv {symbol}: {exc}")
            return []

    def fetch_ohlcv_paginated(self, symbol: str, timeframe: str,
                               count: int) -> list:
        """ดึง OHLCV มากกว่า 999 แท่ง โดย paginate ผ่าน start_pos
        (MT5/broker จำกัด ~999 แท่งต่อ copy_rates_from_pos() หนึ่งครั้ง)
        คืน [[ts_ms, o, h, l, c, v], ...] เรียงจากเก่า -> ใหม่."""
        if not _HAS_MT5:
            return []

        tf_name = _TIMEFRAME_MAP.get(timeframe)
        if tf_name is None:
            self.log.debug(f"fetch_ohlcv_paginated: unknown timeframe '{timeframe}'")
            return []
        tf = getattr(mt5, tf_name)

        batch_size = 999
        all_rates = []
        start_pos = 0
        try:
            while len(all_rates) < count:
                n = min(batch_size, count - len(all_rates))
                rates = mt5.copy_rates_from_pos(symbol, tf, start_pos, n)
                if rates is None or len(rates) == 0:
                    break
                all_rates = list(rates) + all_rates
                start_pos += len(rates)
                if len(rates) < n:
                    break  # ชนจุดที่ broker history หมดแล้ว
        except Exception as exc:
            self.log.debug(f"fetch_ohlcv_paginated {symbol}: {exc}")

        if not all_rates:
            return []

        result = [
            [int(r["time"]) * 1000, float(r["open"]), float(r["high"]),
             float(r["low"]), float(r["close"]), float(r["tick_volume"])]
            for r in all_rates
        ]
        result.sort(key=lambda r: r[0])
        # dedupe เผื่อ batch ซ้อนทับ
        seen = set()
        deduped = []
        for c in result:
            if c[0] not in seen:
                seen.add(c[0])
                deduped.append(c)
        return deduped

    # ── open positions (สำหรับ sync) ─────────────────────────────────────────

    def get_open_positions(self) -> list:
        if not _HAS_MT5:
            return []
        positions = mt5.positions_get()
        if not positions:
            return []
        return [{
            "id":         str(p.ticket),
            "symbol":     p.symbol,
            "magic":      p.magic,
            "type":       "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL",
            "openPrice":  float(p.price_open),
            "stopLoss":   float(p.sl),
            "takeProfit": float(p.tp),
            "volume":     float(p.volume),
            "profit":     float(p.profit),
        } for p in positions]

    # ── deal history (best-effort — สำหรับ commission/fill audit) ─────────────

    def get_position_deals(self, position_id: str, lookback_minutes: int = 30) -> list:
        """คืน deals (entries/exits) ของ position นี้ จาก deal history.

        ใช้สำหรับดึง commission จริง/fill price จริงของแต่ละ leg (open/close)
        และ 'reason' (FIX) ที่ _log_broker_close() ใน live bot เช็คว่า broker
        ปิดเพราะ SL หรือ TP โดยหา "SL"/"TP" เป็น substring ใน reason string.

        ก่อนแก้: ฟังก์ชันนี้ไม่เคยคืน 'reason' เลย → _log_broker_close() ได้
        ค่าว่างเปล่า → fallback ไป "SL/TP (broker)" ทุกครั้ง → fills_log แยก
        SL/TP ไม่ได้เลย

        แก้: เพิ่ม reason_map จาก mt5.DEAL_REASON_* → string มีคำว่า "SL"/"TP"
        ไม่ต้องแก้โค้ด live bot เพราะ logic _log_broker_close() ถูกต้องอยู่แล้ว
        """
        if not _HAS_MT5 or not position_id:
            return []
        try:
            deals = mt5.history_deals_get(position=int(position_id))
            if not deals:
                return []
            entry_map = {
                getattr(mt5, "DEAL_ENTRY_IN", 0):     "DEAL_ENTRY_IN",
                getattr(mt5, "DEAL_ENTRY_OUT", 1):    "DEAL_ENTRY_OUT",
                getattr(mt5, "DEAL_ENTRY_INOUT", 2):  "DEAL_ENTRY_INOUT",
                getattr(mt5, "DEAL_ENTRY_OUT_BY", 3): "DEAL_ENTRY_OUT_BY",
            }
            # FIX: map MT5's DEAL_REASON_* enum → string containing "SL"/"TP"
            # so the substring checks in _log_broker_close() actually work.
            # DEAL_REASON_SO (stop out from margin call) grouped with SL side.
            reason_map = {
                getattr(mt5, "DEAL_REASON_CLIENT", 0):   "DEAL_REASON_CLIENT",
                getattr(mt5, "DEAL_REASON_MOBILE", 1):   "DEAL_REASON_MOBILE",
                getattr(mt5, "DEAL_REASON_WEB", 2):      "DEAL_REASON_WEB",
                getattr(mt5, "DEAL_REASON_EXPERT", 3):   "DEAL_REASON_EXPERT",
                getattr(mt5, "DEAL_REASON_SL", 4):       "DEAL_REASON_SL",
                getattr(mt5, "DEAL_REASON_TP", 5):       "DEAL_REASON_TP",
                getattr(mt5, "DEAL_REASON_SO", 6):       "DEAL_REASON_SL_STOPOUT",
                getattr(mt5, "DEAL_REASON_ROLLOVER", 7): "DEAL_REASON_ROLLOVER",
                getattr(mt5, "DEAL_REASON_VMARGIN", 8):  "DEAL_REASON_VMARGIN",
                getattr(mt5, "DEAL_REASON_SPLIT", 9):    "DEAL_REASON_SPLIT",
            }
            return [{
                "positionId": str(d.position_id),
                "entryType":  entry_map.get(d.entry, "UNKNOWN"),
                "price":      float(d.price),
                "commission": float(d.commission),
                "profit":     float(d.profit),
                "swap":       float(d.swap),
                "reason":     reason_map.get(d.reason, "UNKNOWN"),
            } for d in deals]
        except Exception as exc:
            self.log.debug(f"get_position_deals({position_id}): {exc}")
            return []

    # ── symbol resolver (สำหรับ gold / commodities) ───────────────────────────

    def list_broker_symbols(self) -> list:
        """ดึงรายชื่อ symbols ทั้งหมดที่โบรกเกอร์นี้รองรับ."""
        if not _HAS_MT5:
            return []
        try:
            syms = mt5.symbols_get()
            return [s.name for s in syms] if syms else []
        except Exception as exc:
            self.log.debug(f"list_broker_symbols: {exc}")
            return []

    def resolve_symbol(self, configured: str) -> str:
        """แปลงชื่อ symbol ที่กำหนดไว้ → ชื่อจริงที่โบรกเกอร์ใช้.

        ปัญหาทอง: XAUUSD อาจถูกตั้งชื่อว่า XAUUSDm (Exness ใช้ suffix 'm')
        ลำดับการค้นหา:
          1. Exact match
          2. configured + 'm' (เช่น XAUUSD → XAUUSDm)
          3. Prefix match — ต้องขึ้นต้นด้วย configured ทั้งคำ (กัน XAUEURm หลุดมา)
          4. XAU/GOLD keyword ที่เป็น USD-quoted เท่านั้น (กัน BTCXAUm หลุดมา)
          5. ถ้าไม่เจอ: คืน configured เดิม + warn
        """
        if not _HAS_MT5:
            return configured

        broker_syms = self.list_broker_symbols()
        if not broker_syms:
            return configured

        upper_cfg     = configured.upper()
        sym_by_upper  = {s.upper(): s for s in broker_syms}
        resolved      = configured

        if configured in broker_syms:
            resolved = configured
        elif upper_cfg + "M" in sym_by_upper:
            resolved = sym_by_upper[upper_cfg + "M"]
        else:
            prefixes = [s for s in broker_syms if s.upper().startswith(upper_cfg)]
            if prefixes:
                resolved = sorted(prefixes, key=len)[0]  # ชื่อสั้นสุด = ใกล้ exact ที่สุด
                self.log.info(
                    f"[SymbolResolver] {configured} -> {resolved}"
                    f"  (prefix match, candidates: {prefixes[:5]})")
            elif "XAU" in upper_cfg or upper_cfg == "GOLD":
                gold_matches = [
                    s for s in broker_syms
                    if ("XAU" in s.upper() or "GOLD" in s.upper()) and "USD" in s.upper()
                ]
                if gold_matches:
                    resolved = sorted(gold_matches, key=len)[0]
                    self.log.info(
                        f"[SymbolResolver] {configured} -> {resolved}"
                        f"  (gold keyword match, USD-quoted; all: {gold_matches[:8]})")
                else:
                    self.log.warning(
                        f"[SymbolResolver] '{configured}' ไม่พบในรายการ broker!"
                        f"  ตัวอย่างที่มี: {broker_syms[:10]}")
            else:
                self.log.warning(
                    f"[SymbolResolver] '{configured}' ไม่พบในรายการ broker!"
                    f"  ตัวอย่างที่มี: {broker_syms[:10]}")

        if resolved != configured:
            self.log.info(f"[SymbolResolver] {configured} -> {resolved}")

        mt5.symbol_select(resolved, True)
        return resolved

    def disconnect(self):
        if _HAS_MT5:
            mt5.shutdown()


# =============================================================================
# ORDER EXECUTOR
# =============================================================================
class ForexOrderExecutor:
    """ส่งคำสั่งซื้อขายผ่าน MetaTrader5 ไปยัง MT5 terminal โดยตรง."""

    def __init__(self, connector: MT5Connector, cfg: ForexConfig,
                 log: logging.Logger):
        self.conn = connector
        self.cfg  = cfg
        self.log  = log
        self._order_keys: set = set()

    def _normalize_lot(self, lot: float) -> float:
        return max(self.cfg.min_lot, round(lot, 2))

    # ── low-level order_send (ลอง filling mode หลายแบบ) ───────────────────────

    def _send_market_order(self, symbol: str, order_type: int, lot: float,
                            sl: float, tp: float, comment: str,
                            position: int | None = None):
        tick = mt5.symbol_info_tick(symbol)
        # [FIX C1-3] symbol_info_tick() returns None when symbol is not subscribed
        # (e.g. at reconnect before symbol_select is called) — guard before deref.
        if tick is None:
            self.log.error(
                f"  [ORDER ERROR] symbol_info_tick('{symbol}') returned None "
                f"— cannot determine price, aborting order")
            return None
        price = tick.ask if order_type == mt5.ORDER_TYPE_BUY else tick.bid

        base_request = {
            "action":    mt5.TRADE_ACTION_DEAL,
            "symbol":    symbol,
            "volume":    lot,
            "type":      order_type,
            "price":     price,
            "magic":     self.cfg.magic_number,
            "comment":   (comment or "ForexBot")[:31],
            "type_time": mt5.ORDER_TIME_GTC,
        }
        if sl:
            base_request["sl"] = sl
        if tp:
            base_request["tp"] = tp
        if position is not None:
            base_request["position"] = position

        last_result = None
        for filling in _FILLING_MODES:
            request = dict(base_request, type_filling=filling)
            result = mt5.order_send(request)
            last_result = result
            if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
                return result
            if result is not None and result.retcode != mt5.TRADE_RETCODE_INVALID_FILL:
                return result
        return last_result

    # ── open ─────────────────────────────────────────────────────────────────

    def open_position(self, symbol: str, side: str, lot: float,
                      sl: float, tp: float, comment: str = "") -> dict:
        lot = self._normalize_lot(lot)

        # Duplicate guard — key เพิ่มก่อนส่ง, ลบออกถ้า order ล้มเหลว
        okey = f"{side}-{symbol}-{lot:.2f}"
        if okey in self._order_keys:
            self.log.warning(f"  [DUP] ป้องกัน order ซ้ำ: {okey}")
            return {}
        self._order_keys.add(okey)

        # ── Paper trade ──
        if self.cfg.dry_run or not _HAS_MT5:
            bid, ask = self.conn.get_current_price(symbol)
            fill = ask if side == "long" else bid
            self.log.info(
                f"  [PAPER] OPEN {side.upper():5} {symbol:8} "
                f"lot={lot:.2f}  SL={sl:.5f}  TP={tp:.5f}")
            return {"trade_id": "paper", "fill_price": fill or 0.0, "lot": lot,
                    "commission": 0.0}

        # ── Live: ส่ง MT5 order ──
        dec = self.cfg.get_price_decimals(symbol)
        order_type = mt5.ORDER_TYPE_BUY if side == "long" else mt5.ORDER_TYPE_SELL

        try:
            result = self._send_market_order(
                symbol, order_type, lot, round(sl, dec), round(tp, dec), comment)

            if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                err = result.retcode if result is not None else mt5.last_error()
                self.log.error(f"  [ORDER ERROR] open {symbol}: retcode={err}")
                self._order_keys.discard(okey)   # ลบ key ถ้า order ล้มเหลว → retry ได้
                return {}

            pos_id  = str(result.order)
            fill_px = float(result.price)
            self.log.info(
                f"  [MT5] OPEN {side.upper():5} {symbol:8} "
                f"lot={lot:.2f}  fill={fill_px:.{dec}f}  id={pos_id}")

            # best-effort: ดึง commission ของ leg เปิด จาก deal history
            commission = 0.0
            try:
                deals = self.conn.get_position_deals(pos_id)
                open_deals = [d for d in deals
                               if str(d.get("entryType", "")).endswith("IN")]
                if open_deals:
                    commission = sum(float(d.get("commission", 0) or 0) for d in open_deals)
                    last = open_deals[-1]
                    if last.get("price"):
                        fill_px = float(last["price"])
            except Exception as exc:
                self.log.debug(f"  open_position deal-lookup {symbol}: {exc}")

            return {"trade_id": pos_id, "fill_price": fill_px, "lot": lot,
                    "commission": commission}
        except Exception as exc:
            self.log.error(f"  [ORDER ERROR] open {symbol}: {exc}")
            self._order_keys.discard(okey)   # ลบ key ถ้า exception → retry ได้
            return {}

    def release_order_key(self, symbol: str, side: str, lot: float):
        """ลบ DUP guard key เมื่อ position ปิดแล้ว — อนุญาตให้เปิดใหม่ได้."""
        okey = f"{side}-{symbol}-{self._normalize_lot(lot):.2f}"
        self._order_keys.discard(okey)

    # ── close ────────────────────────────────────────────────────────────────

    def close_position(self, symbol: str, side: str, lot: float,
                       trade_id: str = "") -> bool:
        lot = self._normalize_lot(lot)

        if self.cfg.dry_run or not _HAS_MT5:
            self.log.info(
                f"  [PAPER] CLOSE {side.upper():5} {symbol:8} lot={lot:.2f}")
            return True

        if not trade_id or trade_id == "paper":
            return True

        try:
            # ปิด long → ขาย, ปิด short → ซื้อคืน — MT5 จัดการ partial close
            # อัตโนมัติเมื่อ volume < position volume
            order_type = mt5.ORDER_TYPE_SELL if side == "long" else mt5.ORDER_TYPE_BUY
            result = self._send_market_order(
                symbol, order_type, lot, 0, 0, "ForexBot-close",
                position=int(trade_id))

            if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                err = result.retcode if result is not None else mt5.last_error()
                self.log.error(f"  [ORDER ERROR] close {symbol}: retcode={err}")
                return False

            self.log.info(
                f"  [MT5] CLOSE {side.upper():5} {symbol:8} "
                f"lot={lot:.2f}  id={trade_id}")
            return True
        except Exception as exc:
            self.log.error(f"  [ORDER ERROR] close {symbol}: {exc}")
            return False

    # ── close (market, return fill/commission สำหรับ logging) ─────────────────

    def close_position_market(self, symbol: str, side: str, lot: float,
                               trade_id: str = "", comment: str = "") -> dict:
        """ปิด position แบบ market order แล้วคืน {"fill_price":..., "commission":...}
        สำหรับใช้บันทึก fills_log.csv (execution-cost measurement).

        - dry_run/paper: คืน fill_price = current bid/ask, commission=0.0
        - live: ส่ง close order ไปที่ MT5 แล้วพยายาม (best-effort) อ่าน
          fill price / commission จริงจาก deal history.
          ถ้าอ่านไม่ได้ → fallback เป็น order_send result.price และ commission=0.0
        """
        lot = self._normalize_lot(lot)
        bid, ask = self.conn.get_current_price(symbol)
        # ปิด long → ขายที่ bid, ปิด short → ซื้อคืนที่ ask
        fallback_fill = (bid if side == "long" else ask) or 0.0

        if self.cfg.dry_run or not _HAS_MT5:
            self.log.info(
                f"  [PAPER] CLOSE {side.upper():5} {symbol:8} lot={lot:.2f}")
            return {"fill_price": fallback_fill, "commission": 0.0}

        if not trade_id or trade_id == "paper":
            return {"fill_price": fallback_fill, "commission": 0.0}

        try:
            order_type = mt5.ORDER_TYPE_SELL if side == "long" else mt5.ORDER_TYPE_BUY
            result = self._send_market_order(
                symbol, order_type, lot, 0, 0,
                (comment or "ForexBot-close")[:31], position=int(trade_id))

            if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                err = result.retcode if result is not None else mt5.last_error()
                self.log.error(f"  [ORDER ERROR] close {symbol}: retcode={err}")
                # [FIX C1-7] Return falsy {} so caller's `if not result:` fires
                # and the position is NOT removed from tracking — it remains in
                # self.positions and will be retried on the next poll cycle.
                return {}

            self.log.info(
                f"  [MT5] CLOSE {side.upper():5} {symbol:8} "
                f"lot={lot:.2f}  id={trade_id}")

            fill_px    = float(result.price)
            commission = 0.0
            try:
                deals = self.conn.get_position_deals(trade_id)
                # deal ล่าสุดที่เป็นการปิด position
                close_deals = [d for d in deals
                               if str(d.get("entryType", "")).endswith("OUT")]
                if close_deals:
                    last = close_deals[-1]
                    fill_px    = float(last.get("price", fill_px) or fill_px)
                    commission = sum(float(d.get("commission", 0) or 0) for d in close_deals)
            except Exception as exc:
                self.log.debug(f"  close_position_market deal-lookup {symbol}: {exc}")

            return {"fill_price": fill_px, "commission": commission}
        except Exception as exc:
            self.log.error(f"  [ORDER ERROR] close {symbol}: {exc}")
            return {"fill_price": fallback_fill, "commission": 0.0}

    # ── modify SL (trailing stop) ─────────────────────────────────────────────

    def modify_sl(self, symbol: str, trade_id: str, new_sl: float) -> bool:
        if self.cfg.dry_run or not _HAS_MT5:
            return True
        if not trade_id or trade_id == "paper":
            return True
        try:
            dec = self.cfg.get_price_decimals(symbol)
            positions = mt5.positions_get(ticket=int(trade_id))
            if not positions:
                return False
            p = positions[0]
            request = {
                "action":   mt5.TRADE_ACTION_SLTP,
                "symbol":   symbol,
                "position": int(trade_id),
                "sl":       round(new_sl, dec),
                "tp":       p.tp,
            }
            result = mt5.order_send(request)
            return result is not None and result.retcode == mt5.TRADE_RETCODE_DONE
        except Exception as exc:
            self.log.debug(f"  modify_sl {symbol}: {exc}")
            return False


# =============================================================================
# HELPERS
# =============================================================================
def _cfg_has_credentials(cfg: ForexConfig) -> bool:
    """MT5 attach mode ใช้ terminal ที่ login ไว้แล้ว — ไม่ต้องมี credentials ใน cfg.
    เงื่อนไขเดียวสำหรับ live mode คือ MetaTrader5 package ต้องใช้งานได้ (Windows)."""
    return _HAS_MT5
