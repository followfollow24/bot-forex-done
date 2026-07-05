#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_telegram.py — Standalone test for Telegram alert setup.

Run this BEFORE deploying the live bot, to verify TELEGRAM_BOT_TOKEN and
TELEGRAM_CHAT_ID (in .env) are configured correctly, independent of the
bot's own MT5 connection or trading logic.

Usage (on the VPS, in the Desktop folder where .env lives):
    python test_telegram.py
"""
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("[WARN] python-dotenv not installed — reading OS environment "
          "variables directly instead (won't pick up .env file values)")

TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

print("=== Telegram Config Check ===")
print(f"TELEGRAM_BOT_TOKEN : {'set (' + TOKEN[:10] + '...)' if TOKEN else 'NOT SET'}")
print(f"TELEGRAM_CHAT_ID   : {CHAT_ID if CHAT_ID else 'NOT SET'}")
print()

if not TOKEN or not CHAT_ID:
    print("[FAIL] Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID.")
    print("       Add both to .env in this folder, then run this script again.")
    sys.exit(1)

message = (
    "✅ Test alert from forex bot VPS\n"
    "If you see this message, Telegram alerts are configured correctly.\n"
    "This did NOT come from the live trading bot — it's a standalone test."
)

try:
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": CHAT_ID, "text": message}).encode()
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = resp.read().decode()
    print("[OK] Message sent successfully. Check your Telegram now.")
    print(f"     Raw API response: {body[:200]}")
except urllib.error.HTTPError as exc:
    err_body = exc.read().decode(errors="replace")
    print(f"[FAIL] HTTP {exc.code} error from Telegram API: {err_body}")
    if exc.code == 401:
        print("       -> Likely cause: wrong TELEGRAM_BOT_TOKEN.")
    elif exc.code == 400:
        print("       -> Likely cause: wrong TELEGRAM_CHAT_ID, or you "
              "haven't messaged the bot yet (send it any message first).")
    sys.exit(1)
except Exception as exc:
    print(f"[FAIL] Could not reach Telegram API: {exc}")
    print("       -> Check VPS internet/firewall access to api.telegram.org")
    sys.exit(1)
