#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The bot and its checker must look in the same places.

The credentials existed on this VPS the whole time, on the Desktop next
to bot_repo. The bot looked in the repo and the home directory, missed
them, and telegram() swallowed the miss exactly as it is designed to --
so nineteen alert points were wired to nothing behind a log that looked
completely normal. A checker that searched a different list from the bot
would have reported it healthy, which is why these two lists are pinned
to each other here.
"""
import ast
import sys

ok = fail = 0


def check(cond, what):
    global ok, fail
    if cond:
        ok += 1
    else:
        fail += 1
        print(f"  FAILED: {what}")


def paths(fname):
    """The ENV_PATHS entries, as source text.

    Parsed rather than sliced: the entries contain their own brackets, so
    scanning for the closing one cuts the tuple in half after the first
    element -- which is what the first version of this test did, and it
    reported a correct three-entry list as having one.
    """
    tree = ast.parse(open(fname, encoding="utf-8").read())
    for node in tree.body:
        if (isinstance(node, ast.Assign)
                and any(getattr(t, "id", None) == "ENV_PATHS"
                        for t in node.targets)):
            return [ast.unparse(e) for e in node.value.elts]
    raise AssertionError(f"{fname} has no ENV_PATHS")


bot = paths("clock_scalp_bot.py")
chk = paths("_tg_check.py")

print("the bot and the checker read the same files")
check(bot == chk, f"lists differ:\n    bot {bot}\n    chk {chk}")
check(len(bot) == 3, f"three locations are searched, got {len(bot)}")

print("and the Desktop is one of them")
check(any("dirname(_HERE)" in p for p in bot),
      "the parent directory (the Desktop on this VPS) is searched -- this is "
      "where the shared .env actually lives")
check(bot[0] == "os.path.join(_HERE, '.env')",
      "the bot's own directory is searched first")
check(any("expanduser" in p for p in bot),
      "the home directory is still searched")

print("paths do not depend on the working directory")
check(all(("_HERE" in p) or ("expanduser" in p) for p in bot),
      "every entry is anchored to the file's own location or the home "
      "directory, so launching the bot from elsewhere cannot silently "
      "change where it looks")
check(not any(p.strip() in ("'.env'", '".env"') for p in bot),
      "no bare relative '.env' that would follow the working directory")

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
