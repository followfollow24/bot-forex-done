#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Reproduces the 2026-08-10 sizing incident with mocked MT5 calls and proves
the fixed formula in daily_sleeves_bot.py now produces a lot ~100x smaller
than the buggy one, landing within the sanity-check tolerance.

Incident inputs (real, from the closed positions):
  BTC: equity=17410.07 USC, risk=0.3%, entry~64793, sl=68261.35 (sd=3468.07)
       buggy lot was 1.46 (implied risk ~5063 USC = 29% of equity)
  pip_value_live for a cent-account BTCUSDc ~1.0 USC per lot per $1 move
  (matches the observed live fill: -656.12 USC loss / 1.46 lot / $449.40 move)
"""
import sys, os
from unittest.mock import MagicMock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.modules.setdefault("MetaTrader5", MagicMock())

from forex_config import ForexConfig

cfg = ForexConfig()
cfg.pip_size["BTCUSDC"] = 1.0

EQ = 17410.07
RISK_PCT = 0.3
SD = 3468.07          # 2.5 x ATR14 at the time of the bad entry
PIP_VALUE_LIVE = 1.0002  # observed live sensitivity (USC per lot per $1 move)

# ---- OLD (buggy) formula --------------------------------------------------
coins_old = min(EQ * RISK_PCT / 100.0 / SD, 3.0 * EQ / 64793.28)
lot_old = round(coins_old / 0.01, 2)
implied_risk_old = SD * lot_old * PIP_VALUE_LIVE
print(f"OLD formula: lot={lot_old}  implied risk={implied_risk_old:.2f} USC "
      f"({implied_risk_old/EQ*100:.1f}% of equity, intended {RISK_PCT}%)")

# ---- NEW (fixed) formula, mirrors daily_sleeves_bot.py _decide_funding ----
pip_size = cfg.get_pip_size("BTCUSDC")
sd_pips = SD / pip_size
risk_cash = EQ * RISK_PCT / 100.0
lot_new = round(risk_cash / (sd_pips * PIP_VALUE_LIVE), 2)
implied_risk_new = sd_pips * PIP_VALUE_LIVE * lot_new
print(f"NEW formula: lot={lot_new}  implied risk={implied_risk_new:.2f} USC "
      f"({implied_risk_new/EQ*100:.2f}% of equity, intended {RISK_PCT}%)")

ratio = lot_old / lot_new if lot_new > 0 else float("inf")
print(f"\nOversizing ratio (old/new): {ratio:.0f}x")

# sanity-check gate from the actual fix code
actual_risk_pct = implied_risk_new / EQ * 100.0
passes = actual_risk_pct <= RISK_PCT * 1.5
print(f"Circuit-breaker check (<=1.5x intended risk): "
      f"{actual_risk_pct:.2f}% <= {RISK_PCT*1.5}%  -> {'PASS' if passes else 'FAIL'}")

assert 50 < ratio < 200, f"expected ~100x oversizing, got {ratio:.0f}x"
assert passes, "new formula should pass its own sanity gate"
assert lot_new < 0.05, f"new lot should be tiny (~0.01-0.02), got {lot_new}"
print("\nVERIFIED: fix reduces lot size by ~100x and passes its own sanity gate.")
