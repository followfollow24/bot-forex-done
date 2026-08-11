#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
log_anomaly_scanner.py -- periodic AI review of the fleet's log files,
looking for anomalies a human operator should know about that a plain
regex (grep for ERROR/CRITICAL/Exception, as done manually earlier this
session) would miss: a warning repeating in a way that suggests something
is stuck, a value that doesn't look right, internally inconsistent state
-- as opposed to routine operation, including a fail-safe skip working
exactly as designed (those are NOT anomalies; they're the safety net
doing its job).

[2026-08-12] Built at the user's explicit request, item 1 of two AI ideas
offered: automate the log-sweep this session did by hand every time asked
"เช็คบัคทั้งหมด". Read-only and advisory -- this script never touches a
live bot, a position, or any file other than its own state/report/
heartbeat files.

DUAL-PROVIDER DESIGN CHOICE, DELIBERATELY DIFFERENT FROM news_gemini_bot.py:
news_gemini_bot.py requires Gemini AND OpenAI to AGREE before trading,
because a wrong trade costs real money -- consensus reduces false
positives at the cost of coverage. Here a false positive just wastes a
few seconds reading a Telegram message, while a false NEGATIVE (a real
bug missed) is the expensive outcome. So this scans with BOTH providers
independently and takes the UNION of their findings (each tagged with
which provider found it), not an intersection -- more eyes, not stricter
agreement. Missing one provider for a cycle (unset key, or a transient
API error) still gets you the other provider's coverage, unlike
news_gemini's fail-CLOSED design.

STATE / OFFSET TRACKING: each bot's log file is read incrementally (byte
offset since the last successful scan, not the whole file every time) so
token cost stays bounded regardless of how large these logs grow. Offsets
only advance after AT LEAST ONE provider successfully reviewed that
content -- if every configured provider errors, the same content is
retried next cycle rather than silently skipped (same fail-safe
philosophy as _safe_scan() in news_gemini_bot.py).

Usage:
  python log_anomaly_scanner.py                 single scan, print + exit
  python log_anomaly_scanner.py --daemon         recompute every
                                                  --interval-hours (24h
                                                  default), heartbeat +
                                                  kill-switch like every
                                                  other daemon in this fleet
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

DESKTOP_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(DESKTOP_DIR, "log_anomaly_scanner_state.json")
REPORT_LOG = os.path.join(DESKTOP_DIR, "log_anomaly_scanner_report.log")
HEARTBEAT_FILE = os.path.join(DESKTOP_DIR, "HEARTBEAT_LOG_ANOMALY_SCANNER")
STOP_FILE = os.path.join(DESKTOP_DIR, "STOP_LOG_ANOMALY_SCANNER")
HEARTBEAT_TICK_SEC = 30

# Log filenames captured verbatim from the real files on the VPS Desktop
# (confirmed working via direct grep/tail this session) -- keep this in
# sync by hand if a bot's variant tag or log-naming convention ever
# changes, same caveat as CURRENT_RISK_PCT in portfolio_allocator.py.
# portfolio_allocator.py itself is deliberately excluded: it's not a
# trading bot, and its own stdout isn't captured to a log file the way
# these are (it prints to its own console window).
LOG_FILES = {
    "gold_h1_manual": "forex_xauusdc_gold_h1_manual.log",
    "gold_daily_breakout": "forex_xauusdc_gold_daily_breakout.log",
    "gold_momentum_rsi": "forex_xauusdc_gold_momentum_rsi.log",
    "btc_h1_manual": "forex_btcusdc_btc_h1_manual.log",
    "btc_h1_breakout": "forex_btcusdc_btc_h1_breakout.log",
    "btc_amd": "forex_btcusdc_btc_amd.log",
    "btc_lqsweep": "forex_btcusdc_btc_lqsweep.log",
    "btc_tpo": "forex_btcusdc_btc_tpo.log",
    "eth_h1_manual": "forex_ethusdc_eth_h1_manual.log",
    "funding_contrarian": "forex_bot_crypto_funding_contrarian.log",
    "btc_combo_lb": "forex_bot_btcusdc_btc_combo_lb.log",
    "news_gemini": "forex_bot_news_gemini.log",
}

MAX_CHARS_PER_BOT = 6000   # ~1500 tokens/bot cap -- bounds prompt size
                          # regardless of how chatty a bot's log gets
                          # between scans; keeps the NEWEST content if
                          # a bot logged more than this since last scan.

FINDING_SCHEMA_ITEM = {
    "type": "object",
    "properties": {
        "bot": {"type": "string"},
        "severity": {"type": "string"},   # "low" | "medium" | "high"
        "summary": {"type": "string"},
        "evidence": {"type": "string"},
    },
    "required": ["bot", "severity", "summary", "evidence"],
}
FINDINGS_SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {"type": "array", "items": FINDING_SCHEMA_ITEM},
    },
    "required": ["findings"],
}

PROMPT_TEMPLATE = """You are reviewing raw log output from live automated \
trading bots, looking for ANOMALIES a human operator should know about -- \
not routine operation.

DO NOT FLAG (these are normal, expected, and mean the system is working \
correctly):
  - heartbeat/poll messages, "no signal", "flat", "no candidates found"
  - a position closing at its own stop-loss or take-profit
  - a fail-safe skip that is working AS DESIGNED, e.g. "skip cycle: API \
error", "SIZING SANITY CHECK FAILED -- REFUSING to open", "quota \
exceeded -- skip cycle" -- these mean a safety net caught something \
correctly, which is the opposite of a bug
  - normal startup/shutdown banners, routine info-level status lines

DO FLAG:
  - the SAME warning/error repeating many times in a way suggesting \
something is stuck, not just occasionally transient
  - any text suggesting a value doesn't look right: unexpected \
magnitude, an unexplained sign flip, NaN, None where a number was \
expected, a lot size or risk% that looks wildly off
  - state that looks internally inconsistent, e.g. "position tracked \
locally but broker shows none" persisting across multiple lines
  - anything that reads as a genuine CODE DEFECT rather than an \
external condition (API, broker, network) being handled correctly

severity: "high" = likely real money impact or a stuck process; \
"medium" = probably worth a human look, not urgent; "low" = minor / \
low-confidence, include only if genuinely notable.

For each bot section below, review the log excerpt. If a bot's log has \
nothing worth flagging, do not include it in findings at all -- an \
empty findings list is a normal and GOOD result.

{bot_sections}

Respond ONLY via the provided JSON schema."""


def gemini_log_scan(api_key: str, model: str, bot_sections: str) -> list:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    prompt = PROMPT_TEMPLATE.format(bot_sections=bot_sections)
    resp = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=FINDINGS_SCHEMA,
        ),
    )
    return json.loads(resp.text).get("findings", [])


def openai_log_scan(api_key: str, model: str, bot_sections: str) -> list:
    from openai import OpenAI
    # reuse the strict-schema converter already built and tested for
    # news_gemini_bot.py rather than duplicating that subtle logic here.
    sys.path.insert(0, DESKTOP_DIR)
    from news_gemini_bot import _openai_strict_schema

    client = OpenAI(api_key=api_key)
    prompt = PROMPT_TEMPLATE.format(bot_sections=bot_sections)
    resp = client.responses.create(
        model=model,
        input=[{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
        text={"format": {
            "type": "json_schema",
            "name": "log_findings",
            "schema": _openai_strict_schema(FINDINGS_SCHEMA),
            "strict": True,
        }},
    )
    return json.loads(resp.output_text).get("findings", [])


def _load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}   # corrupt state -- restart offsets from a safe tail,
                    # not from 0 (see _read_new_lines' MAX_CHARS_PER_BOT cap)


def _save_state(state: dict):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)


def _read_new_lines(path: str, state: dict) -> tuple:
    """Returns (new_text, new_offset). Byte-offset based (binary-mode
    seek, decode after) -- text-mode seek positions are only valid when
    they come from that same file handle's tell(), not an arbitrary
    stored integer, so this deliberately avoids that trap."""
    key = os.path.basename(path)
    last_offset = state.get(key, 0)
    if not os.path.exists(path):
        return "", last_offset
    size = os.path.getsize(path)
    if size < last_offset:
        # rotated or truncated since last scan -- don't error, just
        # resume from a bounded tail instead of re-reading from 0
        last_offset = max(0, size - MAX_CHARS_PER_BOT)
    with open(path, "rb") as f:
        f.seek(last_offset)
        raw = f.read()
    new_offset = last_offset + len(raw)
    text = raw.decode("utf-8", errors="replace")
    if len(text) > MAX_CHARS_PER_BOT:
        text = text[-MAX_CHARS_PER_BOT:]
    return text, new_offset


def _telegram(msg: str):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        import urllib.request, urllib.parse
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": msg}).encode()
        urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=10)
    except Exception as e:
        print(f"[WARN] telegram send failed: {e}")


def run_once(log_dir: str = DESKTOP_DIR, log_files: dict = None,
            gemini_key: str = None, gemini_model: str = "gemini-flash-latest",
            openai_key: str = None, openai_model: str = "gpt-5-mini",
            scan_fns: dict = None) -> dict:
    """Pure-ish core (log_dir/log_files/scan_fns injectable) so this is
    unit-testable without real API keys or the real Desktop layout.
    scan_fns defaults to {'gemini': gemini_log_scan, 'openai': openai_log_scan}.
    Returns a summary dict: {scanned, findings, providers_used, providers_failed}."""
    log_files = log_files if log_files is not None else LOG_FILES
    scan_fns = scan_fns or {"gemini": gemini_log_scan, "openai": openai_log_scan}
    state = _load_state()

    sections = []
    new_offsets = {}
    for tag, fname in log_files.items():
        path = os.path.join(log_dir, fname)
        text, new_offset = _read_new_lines(path, state)
        new_offsets[fname] = new_offset
        if text.strip():
            sections.append(f"=== {tag} ===\n{text}")

    if not sections:
        print("[SCAN] no new log content since last scan -- nothing to review")
        return {"scanned": 0, "findings": [], "providers_used": [], "providers_failed": []}

    combined = "\n\n".join(sections)
    findings = []
    used, failed = [], []
    for name, key, model in (("gemini", gemini_key, gemini_model),
                             ("openai", openai_key, openai_model)):
        if not key:
            continue
        try:
            fs = scan_fns[name](key, model, combined)
            for f in fs:
                f["source"] = name
            findings.extend(fs)
            used.append(name)
        except Exception as e:
            print(f"[WARN] {name} log-scan failed: {e}")
            failed.append(name)

    if not used:
        print("[SCAN] every configured provider failed this cycle -- NOT "
             "advancing offsets, same content will be retried next cycle")
        return {"scanned": len(sections), "findings": [], "providers_used": [],
                "providers_failed": failed}

    # at least one provider succeeded -- that content has real coverage now
    for fname, off in new_offsets.items():
        state[fname] = off
    _save_state(state)

    ts = datetime.now().isoformat()
    with open(REPORT_LOG, "a") as f:
        f.write(f"\n=== scan {ts} -- providers={used} bots_with_new_content="
               f"{len(sections)} findings={len(findings)} ===\n")
        for finding in findings:
            f.write(f"  [{finding.get('severity','?')}/{finding.get('source','?')}] "
                   f"{finding.get('bot','?')}: {finding.get('summary','')}\n"
                   f"    evidence: {finding.get('evidence','')[:300]}\n")

    alertable = [f for f in findings if f.get("severity") in ("medium", "high")]
    if alertable:
        lines = [f"\U0001F50D Log anomaly scan found {len(alertable)} finding(s):"]
        for f in sorted(alertable, key=lambda x: x.get("severity") != "high"):
            lines.append(f"[{f.get('severity')}/{f.get('source')}] {f.get('bot')}: "
                        f"{f.get('summary')}")
        _telegram("\n".join(lines))
    else:
        print(f"[SCAN] clean ({len(findings)} low-severity or 0 findings, "
             f"nothing telegrammed) -- providers used: {used}")

    return {"scanned": len(sections), "findings": findings,
            "providers_used": used, "providers_failed": failed}


def _write_heartbeat():
    tmp = HEARTBEAT_FILE + ".tmp"
    with open(tmp, "w") as f:
        f.write(datetime.now().isoformat())
    os.replace(tmp, HEARTBEAT_FILE)


def _wait_with_heartbeat(total_seconds: float) -> bool:
    """Same pattern as portfolio_allocator.py's _wait_with_heartbeat --
    ticks every HEARTBEAT_TICK_SEC so a long recompute interval doesn't
    look stuck to watchdog_h1.ps1's 5min staleness check, and re-checks
    the kill-switch every tick instead of only at the end of the wait."""
    elapsed = 0.0
    while elapsed < total_seconds:
        if os.path.exists(STOP_FILE):
            return False
        _write_heartbeat()
        step = min(HEARTBEAT_TICK_SEC, total_seconds - elapsed)
        time.sleep(step)
        elapsed += step
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--daemon", action="store_true",
                    help="keep running, rescanning every --interval-hours "
                         "(default: single scan and exit)")
    ap.add_argument("--interval-hours", type=float, default=24.0,
                    help="daemon rescan interval (default 24h)")
    args = ap.parse_args()

    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if not gemini_key and not openai_key:
        print("[ERROR] neither GEMINI_API_KEY nor OPENAI_API_KEY set -- "
             "this scanner needs at least one")
        sys.exit(1)

    def _cycle():
        result = run_once(gemini_key=gemini_key, openai_key=openai_key)
        print(f"[CYCLE] scanned={result['scanned']} findings={len(result['findings'])} "
             f"providers_used={result['providers_used']} "
             f"providers_failed={result['providers_failed']}")

    if not args.daemon:
        _cycle()
        return

    print(f"[DAEMON] log_anomaly_scanner running, rescan every "
         f"{args.interval_hours}h. kill-switch: {STOP_FILE}")
    while True:
        if os.path.exists(STOP_FILE):
            print(f"[DAEMON] kill-switch present ({STOP_FILE}) -- exiting")
            return
        _write_heartbeat()
        _cycle()
        if not _wait_with_heartbeat(args.interval_hours * 3600):
            print(f"[DAEMON] kill-switch present ({STOP_FILE}) -- exiting")
            return


if __name__ == "__main__":
    main()
