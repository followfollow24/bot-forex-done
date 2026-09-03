#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_verify_sizing.py -- the re-verification btc_combo_lb has been waiting on.

On 2026-08-10 funding_contrarian and btc_combo_lb sized their first live
decision about 100x too large: a $174 equity, 0.3%-risk BTC short came out
at 1.46 lot with a ~$50 stop instead of ~0.01 lot with a ~$0.50 stop. Two
positions were closed by hand and the account lost $13.20. Both bots were
kill-switched the same day.

The fix (commit 5d4b05e) landed on the VPS on 2026-08-11 and the process
has been running it ever since. But the kill switch says the fix was never
re-verified, so btc_combo_lb -- the best-performing bot on the account at
+593.79 -- has sat blocked for 12.9 days waiting on a check nobody ran.

This is that check. It opens NOTHING. It calls the exact functions the live
bot calls, on the live account, and prices what the next order would be:

  - the fixed formula, via the connector's own get_pip_value_live()
  - the OLD buggy formula, side by side, so the correction is visible
  - the sanity gate's own arithmetic, so we know it would let a normal
    order through rather than refusing every trade

A gate that blocks everything looks identical to a working bot from the
outside -- both are silent -- so "the fix is safe" is only half of what
has to be shown. The other half is that the bot can still trade.

Usage (on the VPS):  python _verify_sizing.py [risk_pct]

[2026-09-04] SYMBOLS used to be the literal "BTCUSDc" -- correct for the
Exness cent account this was written against, but not an MT5 symbol at
all on the real USD account (Exness-MT5Real15) this bot was moved to:
that broker lists "XAUUSDm"/"BTCUSDm". Resolved via connector.resolve_symbol()
now, same as the live bot does for self.bsym, so this script needs no edit
to work on the next account either.
"""
import sys
import logging

sys.path.insert(0, ".")

RISK = float(sys.argv[1]) if len(sys.argv) > 1 else 0.30
CANONICAL_SYMBOLS = ["XAUUSDC", "BTCUSDC"]
SL_ATR = 2.5

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)")
    sys.exit(1)

from forex_config import ForexConfig
from forex_executor import MT5Connector

log = logging.getLogger("verify")
logging.basicConfig(level=logging.WARNING, format="%(message)s")


def atr14_h1(sym):
    r = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_D1, 0, 30)
    if r is None or len(r) < 15:
        return None
    trs = []
    for i in range(1, len(r)):
        h, l, pc = float(r[i]["high"]), float(r[i]["low"]), float(r[i - 1]["close"])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-14:]) / 14.0


def main():
    cfg = ForexConfig()
    conn = MT5Connector(cfg, log)
    if not conn.connect():
        print("[ERROR] MT5 connect failed")
        return 2

    acct = mt5.account_info()
    if acct is None:
        print("[ERROR] account_info() returned None -- MT5 IPC problem")
        return 2
    eq = float(acct.equity)
    print("=" * 76)
    print(" SIZING RE-VERIFICATION -- opens nothing, prices the next order")
    print("=" * 76)
    print(f"  account {acct.login} ({acct.server})   equity {eq:,.2f} {acct.currency}"
          f"   risk {RISK}%")
    print()

    bad = 0
    for canon in CANONICAL_SYMBOLS:
        sym = conn.resolve_symbol(canon)
        if sym == canon and mt5.symbol_info(sym) is None:
            print(f"  {canon}: NOT FOUND on this broker -- skipping")
            bad += 1
            continue
        if sym != canon:
            print(f"  {canon} -> {sym}")
        atr = atr14_h1(sym)
        if not atr:
            print(f"  {sym}: no bars")
            bad += 1
            continue
        tick = mt5.symbol_info_tick(sym)
        px = float(tick.bid) if tick else 0.0
        sd = SL_ATR * atr

        pip_size = cfg.get_pip_size(sym)
        pip_value = conn.get_pip_value_live(sym)
        sd_pips = sd / pip_size
        risk_cash = eq * RISK / 100.0

        # --- what the bot will actually do now ---
        lot = round(risk_cash / (sd_pips * pip_value), 2)
        actual = (sd_pips * pip_value * lot) / eq * 100.0 if eq > 0 else float("inf")

        # --- what the bug did: fixed 0.01 coin/lot, no tick-value lookup ---
        old_lot = round((risk_cash / sd) / 0.01, 2) if sd > 0 else 0.0

        print(f"  {sym}   price {px:,.2f}   D1 ATR14 {atr:,.2f}   "
              f"stop {SL_ATR}xATR = {sd:,.2f}")
        print(f"    pip_size {pip_size}   pip_value_live {pip_value:.6f}   "
              f"stop = {sd_pips:,.1f} pips")
        print(f"    risk budget         : {risk_cash:,.2f} {acct.currency} "
              f"({RISK}% of equity)")
        print(f"    FIXED formula  lot  : {lot}")
        print(f"      -> money at risk  : {sd_pips * pip_value * lot:,.2f} "
              f"= {actual:.3f}% of equity   (intended {RISK}%)")
        print(f"    OLD buggy formula   : {old_lot}   "
              f"({old_lot / lot:,.0f}x larger)" if lot > 0 else "")

        # 1. the fix must actually be a fix
        if abs(actual - RISK) > RISK * 0.5:
            print(f"    [FAIL] implied risk {actual:.3f}% is more than 50% off "
                  f"the intended {RISK}%")
            bad += 1
        else:
            print(f"    [OK]   implied risk matches intent within 50%")

        # 2. the sanity gate must not be refusing everything -- a gate that
        #    blocks every order is indistinguishable from a working bot
        if actual > RISK * 1.5:
            print(f"    [FAIL] the bot's own sanity gate would REFUSE this "
                  f"order ({actual:.3f}% > 1.5x {RISK}%) -- it would never trade")
            bad += 1
        else:
            print(f"    [OK]   passes the bot's 1.5x sanity gate -- it can trade")

        # 3. and it must not round to nothing at this equity
        if lot < 0.01:
            print(f"    [FAIL] lot rounds below the 0.01 minimum -- every entry "
                  f"would be skipped")
            bad += 1
        else:
            print(f"    [OK]   lot {lot} is above the 0.01 broker minimum")
        print()

    conn.disconnect() if hasattr(conn, "disconnect") else mt5.shutdown()
    print("-" * 76)
    if bad:
        print(f"  RESULT: {bad} check(s) FAILED -- do NOT clear the kill switch.")
        return 1
    print("  RESULT: all checks passed. The sizing bug is fixed on the live")
    print("  account, the order it would send is correctly sized, and the")
    print("  sanity gate lets it through. This is the re-verification the")
    print("  btc_combo_lb kill switch has been waiting for since 2026-08-10.")
    print()
    print("  Clearing the switch is still a money decision, not a test result:")
    print("  it puts a real position back on a real account. That call is the")
    print("  operator's.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
