#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Is Telegram actually wired up, and does the token work?

telegram() in the bot swallows every error by design -- a notification
failure must never interrupt a trade being managed. The cost of that is
silence: a missing token and a working one look identical from the log.
So this checks it explicitly.

It verifies WITHOUT sending anything to the chat. getMe proves the token
is valid and getChat proves the chat id is reachable; neither puts a
message in front of anyone. --send is a separate, deliberate flag.

Secrets are never printed -- only whether a value was found and how long
it is. A token pasted into a terminal is a token in the scrollback.
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

KEYS = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")


def read_env():
    """Same search order the bot uses, so this tests what it will read."""
    found, where = {}, {}
    for path in (".env", os.path.expanduser("~/.env")):
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                if "=" not in raw or raw.strip().startswith("#"):
                    continue
                k, v = raw.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k in KEYS and v:
                    found[k], where[k] = v, path
    return found, where


def api(token, method, **params):
    url = f"https://api.telegram.org/bot{token}/{method}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.load(r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true",
                    help="actually put a test message in the chat")
    a = ap.parse_args()

    found, where = read_env()
    for k in KEYS:
        if k in found:
            print(f"  {k:<20} found in {where[k]}  ({len(found[k])} chars)")
        else:
            print(f"  {k:<20} MISSING -- every notification will be dropped "
                  f"silently")
    if len(found) < len(KEYS):
        print("\n  The bot will keep trading; it just will not tell you "
              "anything.")
        return 1

    token, chat = found["TELEGRAM_BOT_TOKEN"], found["TELEGRAM_CHAT_ID"]
    try:
        me = api(token, "getMe")
    except Exception as exc:
        print(f"  TOKEN                REJECTED or unreachable: {exc!r}")
        return 1
    if not me.get("ok"):
        print(f"  TOKEN                REJECTED by Telegram: {me}")
        return 1
    print(f"  TOKEN                valid -- bot is "
          f"@{me['result'].get('username', '?')}")

    try:
        ch = api(token, "getChat", chat_id=chat)
    except Exception as exc:
        print(f"  CHAT                 unreachable: {exc!r}")
        print("  A chat id is only reachable after you have sent that bot a "
              "message at least once.")
        return 1
    if not ch.get("ok"):
        print(f"  CHAT                 Telegram refused it: {ch}")
        return 1
    r = ch["result"]
    who = r.get("title") or r.get("username") or r.get("first_name") or "?"
    print(f"  CHAT                 reachable -- {r.get('type')} \"{who}\"")

    if not a.send:
        print("\n  Verified without sending anything. Add --send to put an "
              "actual test message in the chat.")
        return 0
    try:
        res = api(token, "sendMessage", chat_id=chat,
                  text="clock_scalp: test message. If you can read this, "
                       "alerts from the 19:30 bot will reach you.")
        print("\n  TEST MESSAGE         sent" if res.get("ok")
              else f"\n  TEST MESSAGE         refused: {res}")
    except Exception as exc:
        print(f"\n  TEST MESSAGE         failed: {exc!r}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
