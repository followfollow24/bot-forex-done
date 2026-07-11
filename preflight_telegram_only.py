#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
preflight_telegram_only.py -- isolated re-check of Step 3 item 5 (Telegram
fires, labeled per-variant) after preflight_btc_bots.py's first run showed
"not configured" -- which was a bug in that script (missing load_dotenv()),
not an actual config problem, since gold bots already send Telegram alerts
successfully using the same .env. No real orders placed here.
ASCII-only.
"""
import os
import urllib.parse
import urllib.request

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("[WARN] python-dotenv not installed -- relying on already-set env vars")

token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
print(f"TELEGRAM_BOT_TOKEN: {'set (' + token[:10] + '...)' if token else 'NOT SET'}")
print(f"TELEGRAM_CHAT_ID  : {chat_id or 'NOT SET'}")

if not token or not chat_id:
    print("\n[FAIL] still not configured after load_dotenv() -- real problem, investigate .env location")
    raise SystemExit(1)

for name, magic in [("btc_cons", 666000), ("btc_aggr", 666010)]:
    msg = (f"[PREFLIGHT] {name} (BTCUSDc, magic={magic}) Telegram check\n"
           f"This confirms alerts are correctly labeled per-variant before Step 4 deploy.")
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": msg}).encode()
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
        print(f"[PASS] {name}: Telegram sent OK")
    except Exception as exc:
        print(f"[FAIL] {name}: {exc}")
