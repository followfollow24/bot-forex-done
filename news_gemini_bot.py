#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
news_gemini_bot.py -- 2026-08-10/11. Live news-driven trading signal using
DUAL-PROVIDER CONSENSUS (Gemini + OpenAI, each with their own live web
search), trading XAUUSDc / BTCUSDc.  (ETHUSDc removed 2026-08-15.)

[!!] UNVALIDATED STRATEGY -- unlike every other bot in this repo, this one
has NO historical backtest (an LLM's news judgement today is not the same
as its judgement on 2020 data, so there is nothing meaningful to backtest).
The user was told this explicitly and chose to go live immediately anyway,
small size, with Telegram alerts on every decision. Every safety net below
exists BECAUSE of that -- read them before loosening any of them.

Design (all deliberate, not defaults):
  - DUAL-PROVIDER CONSENSUS (2026-08-11, added at user's request after the
    single-provider version hit Gemini's free-tier grounding quota): Gemini
    and OpenAI each run a FULLY INDEPENDENT news scan every cycle -- neither
    sees the other's output. A symbol only becomes tradeable if BOTH scans
    separately surfaced a candidate for it with the SAME direction, and each
    candidate independently passes its own confidence/tier-1-source gate.
    This is deliberately NOT "ask model B to confirm model A's claim" --
    that anchors B toward agreeing. Two blind scans that happen to agree is
    a much stronger signal than one model rubber-stamping another.
    OPENAI_API_KEY is OPTIONAL at the code level: if unset, the bot still
    runs (heartbeat, position watching, time-stops) but every cycle is
    logged as "dual-consensus unavailable" and no new entries are taken --
    same fail-safe philosophy as everything else here, not a crash.
  - CHART-VISION VETO (2026-08-11, user-requested "ดูกราฟร่วมด้วย"): after a
    candidate clears news consensus, an H1 candlestick chart is RENDERED
    from real MT5 bars and shown to BOTH models, which independently answer
    whether price action CONTRADICTS the news direction. EITHER model
    vetoing (conf >= CHART_VETO_CONF_MIN) cancels the trade. Note the
    asymmetry vs the news stage: news needs BOTH to agree to trade, the
    chart lets EITHER block -- the chart stage can only ever REDUCE
    trading, never create a trade, because there is no backtest for "LLM
    reads a chart image" and an unvalidated signal should not be given
    authority to open positions. Fails OPEN (render error / API error =>
    trade proceeds on news alone), so a broken veto stage cannot silently
    become a kill-switch. Verified live both ways: a synthetic downtrend
    vs a LONG idea is vetoed by both (conf 0.90-0.95), a matching uptrend
    is passed by both.
  - Poll cadence: once per NEWS_POLL_MIN minutes (default 45). News-driven
    setups do not need bar-close timing; a fixed wall-clock cadence keeps
    LLM spend and API quota bounded and predictable. BOOSTED to
    BOOST_POLL_MIN (default 5) during 19:00-20:00 Thai time (12:00-13:00
    UTC, user-requested -- catches major US economic data releases). The
    lookback each scan searches is the ACTUAL elapsed minutes since the
    last poll (capped at 4x the larger cadence), not a hardcoded constant,
    so a skipped/delayed cycle still searches the right window.
  - Source gating: each provider must return structured JSON (schema below)
    citing a source name/URL per candidate. The code -- NOT the model --
    checks the source domain against TIER1_DOMAINS. A model that just says
    "trust me" with no checkable source is treated as unsourced and skipped.
  - Confidence gate: candidates below CONF_MIN are skipped (checked per
    provider, before cross-checking).
  - Dedup: a story already acted on (by URL) blocks re-entry on the same
    symbol for DEDUP_HOURS, so one headline can't trigger repeated entries
    as it gets rephrased across poll cycles.
  - Sizing: EXACT same pip_size/get_pip_value_live() pattern as the proven
    H1 bots (forex_live_bot_gold_cwider.py _open_position()) and the
    2026-08-10-fixed daily sleeves -- never hand-roll lot math again, see
    feedback_cent_account_sizing memory. risk_pct defaults far below every
    other live bot (0.15%) because there is no historical edge estimate to
    size against.
  - Stop-loss: MANDATORY on every order, no exceptions. sl = 2.5xATR14(H1)
    from MT5 (real market volatility, not asked from the LLM). No take-
    profit; a TIME_STOP_HOURS flat close (default 18h) is the exit for a
    news-driven move that hasn't kept going, since these setups are not
    meant to be held like a trend position.
  - Consecutive-loss circuit breaker: MAX_CONSEC_LOSSES losses in a row
    (default 3) auto-writes a STOP file and alerts -- requires a human to
    clear it. There is no backtest to say "3 losses is normal variance",
    so the breaker is deliberately tight.
  - Fail-safe on EVERY external call (either provider's quota/error, MT5
    timeout, malformed JSON): skip this cycle, alert, never guess. A
    skipped cycle is always safe; a guessed one is not.

CLI:
  python news_gemini_bot.py --allow-real
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import logging
import logging.handlers
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from forex_executor import MT5Connector, ForexOrderExecutor, _cfg_has_credentials

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

MAGIC = 669001  # 669xxx family: unused by any existing bot (555/666/667/668)

SYMBOLS = {
    "XAUUSD": {"canon": "XAUUSD", "label": "gold", "ps": None, "pv": None},
    "BTCUSDC": {"canon": "BTCUSDC", "label": "BTC", "ps": 1.0, "pv": 0.01},
    # [2026-08-15] ETHUSDC removed on the user's explicit instruction:
    # never traded again by any bot. Removed from the SPEC, not just from
    # the traded list, so a stray symbol string cannot resolve to a
    # tradeable instrument anywhere downstream.
}

# Domains the CODE (not the model) accepts as tier-1. Deliberately narrow --
# widen only after reviewing what the model actually cites in practice.
TIER1_DOMAINS = {
    "reuters.com", "bloomberg.com", "apnews.com", "wsj.com", "ft.com",
    "cnbc.com", "federalreserve.gov", "ecb.europa.eu", "bls.gov",
    "home.treasury.gov", "coindesk.com", "theblock.co",
}

CONF_MIN = 0.70
DEDUP_HOURS = 12
NEWS_POLL_MIN = 45
TIME_STOP_HOURS = 18
MAX_CONSEC_LOSSES = 3
DEFAULT_RISK_PCT = 0.15
SL_ATR_MULT = 2.5

# [2026-08-11] Boosted polling window: 19:00-20:00 Thai time (UTC+7, no DST)
# = 12:00-13:00 UTC, requested by the user because this hour catches the
# major US economic data releases (CPI/NFP/etc typically print here).
# Fixed Thai-time window -> fixed UTC window; does NOT shift with US DST,
# which is what was actually asked for (a Thai wall-clock hour), not a
# "always aligned to the US data release" window (those drift by 1h across
# US DST changes -- if that drift ever matters, revisit this).
BOOST_START_UTC_HOUR = 12
BOOST_END_UTC_HOUR = 13
BOOST_POLL_MIN = 5

# [2026-08-11] Observed Gemini 503 "high demand" errors cluster inside the
# boost window itself (12:51 and 12:56 UTC same day) -- plausibly every
# other news-scanning bot on earth also hammers the API during major US
# data releases. A single skipped cycle used to burn the whole cycle; retry
# a couple of times with a short backoff before giving up, so a transient
# provider-side blip doesn't cost an entire poll interval during the exact
# window boosted polling exists for.
SCAN_MAX_RETRIES = 2
SCAN_RETRY_DELAY_SEC = 20

# [2026-08-12] Wall-clock caps on the AI calls. Added after the identical
# gap was found the hard way on chart_ai_trader.py: neither the
# google-genai nor the openai client sets a default socket timeout, so a
# stalled connection blocks the bot's ONLY loop thread indefinitely -- the
# process stays up, the log goes silent, and the heartbeat freezes until
# watchdog_h1.ps1's 5-minute staleness check restarts a bot that was never
# actually broken. Every MT5 call here has had this guard for months; the
# AI calls never did. This bot had not yet shown the symptom (it polls
# every 45min and makes far fewer calls than chart_ai_trader), but the
# exposure was the same.
# A scan is TWO chained API round-trips (grounded search, then schema
# pass), so it gets the larger budget; a chart veto is a single call.
SCAN_CALL_TIMEOUT_SEC = 120
CHART_CALL_TIMEOUT_SEC = 60
TRANSIENT_ERROR_MARKERS = ("503", "UNAVAILABLE", "overloaded", "high demand",
                           "timeout", "Timeout", "ConnectionError",
                           "connection reset", "temporarily unavailable",
                           # _safe_scan prefixes a recognised 429 rate limit
                           # with this so it reaches the retry path; billing
                           # exhaustion is classified separately and never
                           # gets here, because it does not recover.
                           "rate limit")

# How long an identical provider-outage alert is suppressed. Billing
# exhaustion persists for hours or days; re-sending the same line every poll
# cycle trains the reader to ignore the channel that also carries fills.
ALERT_REPEAT_SUPPRESS_SEC = 6 * 3600


def _is_transient_error(msg: str) -> bool:
    return any(m in msg for m in TRANSIENT_ERROR_MARKERS)

# Chart-vision veto stage (see _chart_allows / CHART_PROMPT_TEMPLATE).
# 120 H1 bars = ~5 days, enough context to see a trend and a recent move
# without shrinking each candle to an unreadable sliver at 1200px wide.
CHART_BARS = 120
CHART_VETO_CONF_MIN = 0.70


def _domain(url: str) -> str:
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def _normalize_url(url: str) -> str:
    """Strip query string / fragment / trailing slash so the same story
    re-cited with different tracking params (utm_*, ?ref=..., #anchor)
    still dedups against a URL already seen."""
    try:
        p = urllib.parse.urlparse(url)
        path = p.path.rstrip("/")
        return urllib.parse.urlunparse((p.scheme, p.netloc.lower(), path, "", "", ""))
    except Exception:
        return url


# Authored Gemini-style (no additionalProperties); _openai_strict_schema()
# converts it for OpenAI. Note every property ends up "required" on the
# OpenAI side, so the prompt tells the model to emit "" for a published_utc
# it doesn't know rather than omitting the key.
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "enum": ["XAUUSD", "BTCUSDC"]},
                    "signal": {"type": "string", "enum": ["long", "short", "none"]},
                    "confidence": {"type": "number"},
                    "headline": {"type": "string"},
                    "source_name": {"type": "string"},
                    "source_url": {"type": "string"},
                    "published_utc": {"type": "string"},
                    "reasoning": {"type": "string"},
                },
                "required": ["symbol", "signal", "confidence", "headline",
                            "source_name", "source_url", "reasoning"],
            },
        },
    },
    "required": ["candidates"],
}

PROMPT_TEMPLATE = """You are a market-moving-news scanner for a live trading system. \
Use Google Search to find REAL, VERIFIED news published in the last {lookback_min} \
minutes (current UTC time: {now_utc}) that could move XAUUSD (gold) or BTCUSDC (Bitcoin) \
prices. Focus on:
  - Macro: Fed/central bank rate decisions or statements, CPI/inflation prints, \
NFP/jobs data, GDP, major fiscal policy news.
  - Crypto-specific: ETF flows/approvals, exchange incidents, regulatory actions, \
major protocol or whale events.
  - Geopolitical: war, sanctions, major diplomatic events that shift safe-haven demand.

Only report a candidate if you found a SPECIFIC, checkable news article with a real \
URL and a real publish time -- never a general market commentary or your own \
speculation dressed up as news. If you find nothing meeting this bar, return an \
empty candidates list -- that is a normal and expected result most cycles.

For each candidate, give a directional call: "long" (price should rise), "short" \
(price should fall), or "none" if the news is notable but not clearly directional. \
confidence is 0.0-1.0, your genuine calibrated confidence this specific news moves \
this specific symbol in this direction within the next {time_stop_h} hours -- do \
not inflate it, most real news should score well under 0.7. Set published_utc to \
the article's publish time if you know it, or an empty string if you do not.

Respond ONLY via the provided JSON schema."""


# ── chart-confirmation stage (2026-08-11) ────────────────────────────────
# Runs ONLY on candidates that already passed news consensus. Both models
# are shown the SAME rendered H1 chart image and asked, independently,
# whether price action CONTRADICTS the news direction. Deliberately framed
# as a VETO, not a second opinion: a chart can cancel a news trade, but a
# bullish-looking chart alone can never create one. Rationale -- there is
# no backtest for "LLM reads a chart image", so it only gets authority to
# REDUCE trading, never to increase it.
# The two providers need DIFFERENT schema dialects (verified live, both
# directions):
#   OpenAI strict json_schema: REQUIRES additionalProperties:false on every
#     object level, and every property must be listed in "required".
#   Gemini response_schema: REJECTS additionalProperties outright
#     ("Unknown name additional_properties ... Cannot find field").
# So schemas are authored Gemini-style and converted for OpenAI by
# _openai_strict_schema() below. Do not "simplify" these into one dict.
CHART_VETO_SCHEMA = {
    "type": "object",
    "properties": {
        "contradicts": {"type": "boolean"},
        "confidence": {"type": "number"},
        "observation": {"type": "string"},
    },
    "required": ["contradicts", "confidence", "observation"],
}


def _openai_strict_schema(schema: dict) -> dict:
    """Deep-copy a Gemini-style schema into OpenAI strict-mode form:
    additionalProperties:false on every object, and every property forced
    into "required" (strict mode forbids optional properties)."""
    if not isinstance(schema, dict):
        return schema
    out = {k: v for k, v in schema.items()}
    if out.get("type") == "object":
        props = out.get("properties", {})
        out["properties"] = {k: _openai_strict_schema(v) for k, v in props.items()}
        out["required"] = list(props.keys())
        out["additionalProperties"] = False
    elif out.get("type") == "array" and "items" in out:
        out["items"] = _openai_strict_schema(out["items"])
    return out

CHART_PROMPT_TEMPLATE = """You are shown an H1 (1-hour) candlestick chart of \
{symbol} covering roughly the last {bars} hours. Current price: {price}.

A news-driven trading system wants to open a {signal_upper} position based on \
this headline: "{headline}"

Question: does the PRICE ACTION on this chart CONTRADICT that {signal_upper} \
idea badly enough that the trade should be skipped?

Answer contradicts=true ONLY for a clear, strong conflict -- for example a \
{opposite_upper} trade idea would be obvious from this chart, price just made \
a decisive move the other way, or the move implied by the news appears to have \
already fully played out (so entering now is chasing an exhausted move).

Answer contradicts=false if the chart is merely neutral, choppy, unclear, or \
mildly unsupportive. Being undecided means false. Do NOT try to be clever or \
find reasons to veto -- the default is false, and a veto needs a strong, \
specific, visible reason you can name.

confidence is 0.0-1.0 in your contradicts answer. observation: one sentence on \
what you actually see in the price action.

Respond ONLY via the provided JSON schema."""


def render_chart_png(candles: list, symbol: str, signal: str) -> bytes:
    """Render H1 candles to a PNG for the vision models.

    candles: MT5 fetch_ohlcv format [[ts_ms, o, h, l, c, v], ...] ascending.
    Deliberately plain: candles + EMA20/50 + a "now" marker, no annotations
    hinting at the desired answer (an arrow saying "we want to go LONG"
    would bias the very judgement being asked for).
    """
    import matplotlib
    matplotlib.use("Agg")            # headless: no display on the VPS
    import matplotlib.pyplot as plt
    import io

    o = [float(c[1]) for c in candles]
    h = [float(c[2]) for c in candles]
    l = [float(c[3]) for c in candles]
    cl = [float(c[4]) for c in candles]
    n = len(cl)
    x = list(range(n))

    def ema(vals, span):
        k = 2.0 / (span + 1.0)
        out, prev = [], vals[0]
        for v in vals:
            prev = v * k + prev * (1 - k)
            out.append(prev)
        return out

    fig, ax = plt.subplots(figsize=(12, 6), dpi=100)
    for i in range(n):
        up = cl[i] >= o[i]
        color = "#26a69a" if up else "#ef5350"
        ax.plot([i, i], [l[i], h[i]], color=color, linewidth=0.8, zorder=2)
        ax.plot([i, i], [o[i], cl[i]], color=color, linewidth=3.0,
                solid_capstyle="butt", zorder=3)
    if n >= 50:
        ax.plot(x, ema(cl, 20), color="#2196f3", linewidth=1.2, label="EMA20", zorder=4)
        ax.plot(x, ema(cl, 50), color="#ff9800", linewidth=1.2, label="EMA50", zorder=4)
        ax.legend(loc="upper left", fontsize=9)
    ax.set_title(f"{symbol}  H1  (most recent {n} bars, newest at right)", fontsize=11)
    ax.grid(alpha=0.25, zorder=1)
    ax.set_xlim(-1, n)
    ax.margins(y=0.06)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


def gemini_chart_veto(api_key: str, model: str, png: bytes, symbol: str,
                      signal: str, headline: str, price: float, bars: int) -> dict:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    prompt = CHART_PROMPT_TEMPLATE.format(
        symbol=symbol, bars=bars, price=f"{price:.2f}", signal_upper=signal.upper(),
        opposite_upper=("SHORT" if signal == "long" else "LONG"), headline=headline)
    resp = client.models.generate_content(
        model=model,
        contents=[types.Part.from_bytes(data=png, mime_type="image/png"), prompt],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=CHART_VETO_SCHEMA,
        ),
    )
    return json.loads(resp.text)


def openai_chart_veto(api_key: str, model: str, png: bytes, symbol: str,
                      signal: str, headline: str, price: float, bars: int) -> dict:
    import base64
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    prompt = CHART_PROMPT_TEMPLATE.format(
        symbol=symbol, bars=bars, price=f"{price:.2f}", signal_upper=signal.upper(),
        opposite_upper=("SHORT" if signal == "long" else "LONG"), headline=headline)
    b64 = base64.b64encode(png).decode()
    resp = client.responses.create(
        model=model,
        input=[{"role": "user", "content": [
            {"type": "input_image", "image_url": f"data:image/png;base64,{b64}"},
            {"type": "input_text", "text": prompt},
        ]}],
        text={"format": {
            "type": "json_schema",
            "name": "chart_veto",
            "schema": _openai_strict_schema(CHART_VETO_SCHEMA),
            "strict": True,
        }},
    )
    return json.loads(resp.output_text)


def gemini_scan(api_key: str, model: str, lookback_min: int) -> list:
    """Returns a list of raw candidate dicts, or raises on any failure --
    caller must treat ANY exception as 'skip this cycle', never partial-trust
    a malformed response."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    prompt = PROMPT_TEMPLATE.format(
        lookback_min=lookback_min,
        now_utc=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        time_stop_h=TIME_STOP_HOURS)

    # Pass 1: grounded search (must be free-form text -- the API does not
    # allow combining a search tool with a forced JSON response schema).
    resp = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
        ),
    )
    grounded_text = resp.text or ""

    # Pass 2: force the grounded findings into the strict schema. No new
    # search here -- this pass only structures what pass 1 already found,
    # so it can't invent sources pass 1 didn't cite.
    resp2 = client.models.generate_content(
        model=model,
        contents=(
            "Convert the following news findings into the required JSON "
            "schema. Do not add any candidate not already present in the "
            "findings text; if the findings text has no concrete news "
            "meeting the bar, return an empty candidates list.\n\n"
            f"FINDINGS:\n{grounded_text}"
        ),
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=RESPONSE_SCHEMA,
        ),
    )
    data = json.loads(resp2.text)
    return data.get("candidates", [])


def openai_scan(api_key: str, model: str, lookback_min: int) -> list:
    """OpenAI equivalent of gemini_scan() -- SAME prompt template, its OWN
    independent web search, same 2-pass search-then-structure pattern (the
    Responses API does not reliably combine a tool call with a forced
    strict json_schema output in one turn either). Raises on any failure --
    caller must treat ANY exception as 'skip this cycle'."""
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    prompt = PROMPT_TEMPLATE.format(
        lookback_min=lookback_min,
        now_utc=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        time_stop_h=TIME_STOP_HOURS)

    # Pass 1: web-search-grounded free text.
    resp = client.responses.create(
        model=model,
        input=prompt,
        tools=[{"type": "web_search"}],
    )
    grounded_text = resp.output_text or ""

    # Pass 2: structure pass 1's findings into the strict schema (no new
    # search -- can't invent sources pass 1 didn't cite).
    resp2 = client.responses.create(
        model=model,
        input=(
            "Convert the following news findings into the required JSON "
            "schema. Do not add any candidate not already present in the "
            "findings text; if the findings text has no concrete news "
            "meeting the bar, return an empty candidates list.\n\n"
            f"FINDINGS:\n{grounded_text}"
        ),
        text={"format": {
            "type": "json_schema",
            "name": "news_candidates",
            "schema": _openai_strict_schema(RESPONSE_SCHEMA),
            "strict": True,
        }},
    )
    data = json.loads(resp2.output_text)
    return data.get("candidates", [])


def cross_check_consensus(gemini_candidates: list, openai_candidates: list) -> list:
    """DUAL-PROVIDER CONSENSUS: a symbol is only tradeable if BOTH providers'
    independent scans separately produced a directional candidate for it,
    the directions agree, and each side individually clears CONF_MIN and a
    tier-1 source domain (checked here, not just trusted from either model).
    Returns merged candidates carrying both sides' headline/source/reasoning
    for a fully auditable Telegram alert.
    """
    def eligible(c):
        return (c.get("signal") in ("long", "short")
               and float(c.get("confidence", 0) or 0) >= CONF_MIN
               and _domain(c.get("source_url", "")) in TIER1_DOMAINS)

    by_symbol_g = {}
    for c in gemini_candidates:
        if eligible(c):
            by_symbol_g.setdefault(c["symbol"], []).append(c)
    by_symbol_o = {}
    for c in openai_candidates:
        if eligible(c):
            by_symbol_o.setdefault(c["symbol"], []).append(c)

    confirmed = []
    for symbol in set(by_symbol_g) & set(by_symbol_o):
        for g in by_symbol_g[symbol]:
            for o in by_symbol_o[symbol]:
                if g["signal"] != o["signal"]:
                    continue
                confirmed.append({
                    "symbol": symbol,
                    "signal": g["signal"],
                    "confidence": min(float(g["confidence"]), float(o["confidence"])),
                    "headline": f"[Gemini] {g.get('headline','')}  |  "
                               f"[OpenAI] {o.get('headline','')}",
                    "source_name": f"{g.get('source_name','')} + {o.get('source_name','')}",
                    "source_url": g.get("source_url", ""),  # primary citation for dedup
                    "reasoning": f"GEMINI: {g.get('reasoning','')}\n\n"
                                f"OPENAI: {o.get('reasoning','')}",
                })
    return confirmed


class NewsGeminiBot:
    def __init__(self, cfg: ForexConfig, risk_pct: float, poll_min: int,
                boost_poll_min: int = BOOST_POLL_MIN,
                boost_start_hour: int = BOOST_START_UTC_HOUR,
                boost_end_hour: int = BOOST_END_UTC_HOUR):
        self.cfg = cfg
        self.risk_pct = risk_pct
        self.poll_min = poll_min
        self.boost_poll_min = boost_poll_min
        self.boost_start_hour = boost_start_hour
        self.boost_end_hour = boost_end_hour

        self.stop_file = os.path.join(_BASE_DIR, "STOP_NEWS_GEMINI")
        self.breaker_file = os.path.join(_BASE_DIR, "BREAKER_NEWS_GEMINI")
        self.heartbeat_file = os.path.join(_BASE_DIR, "HEARTBEAT_NEWS_GEMINI")
        self.state_file = os.path.join(_BASE_DIR, "news_gemini_state.json")
        self.log_file = os.path.join(_BASE_DIR, "forex_bot_news_gemini.log")

        self.log = self._setup_logging()
        self.connector = MT5Connector(cfg, self.log)
        self.executor = ForexOrderExecutor(self.connector, cfg, self.log)
        self._io = concurrent.futures.ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="mt5-io-news")
        self._mt5_timeout = 90.0

        self.gemini_key = os.environ.get("GEMINI_API_KEY", "")
        # [2026-08-15] Model CHAIN, not a single id -- chart_ai_trader has had
        # one for a while and this bot did not, which is why it kept giving up
        # on 503s. "This model is currently experiencing high demand" is
        # per-model capacity on Google's side, so retrying the SAME id after
        # 20s is close to the least useful response available; a different id
        # is usually served fine at that moment. Deduped, because the VPS .env
        # pins GEMINI_MODEL and a chain of [x, x] would burn a retry attempt
        # on the identical overloaded model.
        _gm = [os.environ.get("GEMINI_MODEL", "gemini-flash-lite-latest"),
               os.environ.get("GEMINI_MODEL_FALLBACK", "gemini-flash-latest")]
        self.gemini_models = list(dict.fromkeys([m for m in _gm if m]))
        # kept for the chart-veto call site, which takes a single id
        self.gemini_model = self.gemini_models[0]
        self.openai_key = os.environ.get("OPENAI_API_KEY", "")
        self.openai_model = os.environ.get("OPENAI_MODEL", "gpt-5-mini")

        # in-memory only: a restart SHOULD re-alert, since the operator may
        # not have seen the pre-restart message
        self._alert_last: dict = {}

        self.state = self._load_state()
        self.symbols: dict = {}

    # ── infra (same pattern as daily_sleeves_bot.py) ────────────────────
    def _setup_logging(self) -> logging.Logger:
        fmt = "%(asctime)s [%(levelname)s] %(message)s"
        handlers: list = [logging.StreamHandler(sys.stdout)]
        try:
            handlers.append(logging.handlers.RotatingFileHandler(
                self.log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"))
        except Exception:
            pass
        logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers, force=True)
        return logging.getLogger("NewsGeminiBot")

    def _mt5(self, func, *args, **kw):
        fut = self._io.submit(func, *args, **kw)
        return fut.result(timeout=self._mt5_timeout)

    def _call_with_timeout(self, fn, timeout: float, *a, **kw):
        """Bound an AI call's wall-clock time. Uses its own short-lived
        executor rather than self._io so a stalled AI request can never
        starve the MT5 pool (which has only 2 workers and is on the
        position-management path). A hung thread still leaks -- Python
        cannot kill one -- but control returns to the loop, which is the
        whole point: the heartbeat keeps ticking and the watchdog does not
        restart a bot that is merely waiting on a slow provider."""
        ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            return ex.submit(fn, *a, **kw).result(timeout=timeout)
        finally:
            ex.shutdown(wait=False)

    def _heartbeat(self):
        try:
            with open(self.heartbeat_file, "w") as f:
                f.write(datetime.now(timezone.utc).isoformat())
        except Exception:
            pass

    def _telegram(self, msg: str):
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat = os.environ.get("TELEGRAM_CHAT_ID")
        if not token or not chat:
            return
        try:
            data = urllib.parse.urlencode({"chat_id": chat, "text": msg}).encode()
            urllib.request.urlopen(
                f"https://api.telegram.org/bot{token}/sendMessage", data, timeout=10)
        except Exception as e:
            self.log.warning(f"telegram failed: {e}")

    def _alert_once(self, key: str, msg: str):
        """Telegram, but at most once per ALERT_REPEAT_SUPPRESS_SEC per key.

        Provider outages last hours. The unthrottled version sent the same
        line every poll cycle -- every 5 minutes inside the boost window --
        which is how a channel that also carries fill and close notices
        becomes one the reader scrolls past. Suppression is per key, so a
        different failure still gets through immediately, and the log always
        records every occurrence regardless.
        """
        now = time.time()
        last = self._alert_last.get(key, 0.0)
        if now - last < ALERT_REPEAT_SUPPRESS_SEC:
            self.log.info(f"[ALERT] suppressed repeat of '{key}' "
                          f"({(now - last) / 60:.0f}min since last)")
            return
        self._alert_last[key] = now
        self._telegram(msg)

    def _load_state(self) -> dict:
        default = {"seen_urls": {}, "positions": {}, "consec_losses": 0,
                  "last_poll": ""}
        if not os.path.exists(self.state_file):
            return default   # normal on first run -- no alert needed
        # [2026-08-11 FIX] a CORRUPT state file (bad JSON, encoding, a
        # permissions blip) used to be indistinguishable from "no file yet"
        # -- both silently returned a blank default, wiping any tracked
        # open positions, the loss-streak count, and the dedup URL history
        # with no log line and no alert. A wiped `positions` dict is
        # recoverable (the broker is still queried directly before any new
        # entry), but it happens invisibly, which is the actual problem:
        # an operator has no way to know it occurred. Now logged and
        # alerted loudly, distinctly from the normal first-run case.
        try:
            with open(self.state_file) as f:
                return json.load(f)
        except Exception as e:
            self.log.error(f"[STATE] {self.state_file} exists but failed to "
                           f"load ({e}) -- starting from a BLANK state "
                           f"(tracked positions/loss-streak/dedup history "
                           f"lost; broker positions will still be found on "
                           f"next new-entry check via a live query)")
            self._telegram(f"⚠️ news_gemini: state file corrupted/unreadable "
                           f"({type(e).__name__}) -- resumed with blank "
                           f"state, please verify no position was orphaned")
            return default

    def _prune_seen_urls(self, seen: dict):
        """seen_urls grows one entry per qualified+traded headline forever
        with nothing ever removed. Drop anything older than DEDUP_HOURS --
        it can no longer affect a dedup decision anyway."""
        now = datetime.now(timezone.utc)
        stale = []
        for u, ts in seen.items():
            try:
                age_h = (now - datetime.fromisoformat(ts)).total_seconds() / 3600
                if age_h >= DEDUP_HOURS:
                    stale.append(u)
            except Exception:
                stale.append(u)   # unparsable timestamp -- drop it too
        for u in stale:
            del seen[u]

    def _save_state(self):
        tmp = self.state_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.state, f, indent=1)
        os.replace(tmp, self.state_file)

    def _own_positions(self, bsym: Optional[str] = None) -> list:
        pos = [p for p in (self._mt5(self.connector.get_open_positions) or [])
               if int(p.get("magic", 0)) == MAGIC]
        if bsym:
            pos = [p for p in pos if p.get("symbol") == bsym]
        return pos

    # ── ATR14 H1, computed from real MT5 bars (never asked from the LLM) ──
    def _atr14(self, bsym: str) -> Optional[float]:
        candles = self._mt5(self.connector.fetch_ohlcv, bsym, "1h", 60)
        if not candles or len(candles) < 20:
            return None
        trs = []
        prev_close = None
        for c in candles:
            h, l, cl = float(c[2]), float(c[3]), float(c[4])
            tr = (h - l) if prev_close is None else max(
                h - l, abs(h - prev_close), abs(l - prev_close))
            trs.append(tr)
            prev_close = cl
        trs = trs[-14:]
        if len(trs) < 14 or any(not math.isfinite(t) for t in trs):
            return None
        return sum(trs) / len(trs)

    # ── main loop ────────────────────────────────────────────────────────
    def run(self):
        if not self._mt5(self.connector.connect):
            self.log.error("MT5 connect failed")
            sys.exit(1)

        # [2026-08-11 FIX] every other bot in this repo refuses to place real
        # orders on a non-demo account unless --allow-real was explicitly
        # passed (see daily_sleeves_bot.py's identical gate, itself copied
        # from forex_live_bot_gold_cwider.py's _print_banner_and_verify_demo).
        # This file set cfg.allow_real from the CLI flag but never actually
        # READ it anywhere -- the flag was pure decoration and this bot would
        # trade real money on ANY connected account regardless of the flag
        # or the account's actual demo/real status. If MT5 credentials ever
        # pointed at the wrong account during a redeploy, every other bot
        # would loudly refuse to start; this one would not have.
        info = self.connector.get_account_info()
        is_demo = self.connector.is_demo()
        if not self.cfg.dry_run and not is_demo and not self.cfg.allow_real:
            self.log.error(
                "REFUSING TO START: account is NOT confirmed DEMO "
                f"(type={info.get('type', 'UNKNOWN')}) and --allow-real was "
                "not passed. Use --dry-run to test, or --allow-real to "
                "confirm you intend to trade this real account.")
            sys.exit(1)
        acct_tag = ("DEMO" if is_demo else
                   ("LIVE (--allow-real)" if self.cfg.allow_real else "UNKNOWN"))

        for canon in SYMBOLS:
            self.symbols[canon] = self.connector.resolve_symbol(canon)
        eq = self._mt5(self.connector.get_equity)
        self.log.info("=" * 70)
        self.log.info(f"  NEWS BOT (dual-provider consensus)  magic={MAGIC}  "
                      f"account={acct_tag}  symbols={self.symbols}")
        self.log.info(f"  equity={eq:.2f}  risk/trade={self.risk_pct}%  "
                      f"poll={self.poll_min}min (boosted to {self.boost_poll_min}min "
                      f"during {self.boost_start_hour:02d}:00-{self.boost_end_hour:02d}:00 UTC "
                      f"= 19:00-20:00 Thai time)")
        self.log.info(f"  gemini models={self.gemini_models} "
                      f"(primary first, next on a 503 rather than a re-ask)")
        openai_status = ("configured" if self.openai_key else
                        "NOT SET -- dual-consensus unavailable, bot will idle "
                        "(no new entries) until OPENAI_API_KEY is added to .env")
        self.log.info(f"  openai model={self.openai_model}  {openai_status}")
        self.log.info(f"  kill-switch: {os.path.basename(self.stop_file)}  "
                      f"breaker: {os.path.basename(self.breaker_file)}")
        self.log.info("  ** UNVALIDATED STRATEGY -- no historical backtest exists "
                      "for this signal source. Live by explicit user decision. **")
        self.log.info("=" * 70)
        self._telegram(f"\U0001F680 START news_gemini  equity={eq:.2f}  "
                       f"risk={self.risk_pct}%/trade  poll={self.poll_min}min  "
                       f"dual-consensus={'ON' if self.openai_key else 'OFF (Gemini only, no OpenAI key -- idling)'}")

        in_boost_last = False
        while True:
            try:
                self._heartbeat()
                self._watch_positions()
                self._check_time_stops()
                now = datetime.now(timezone.utc)

                in_boost = self.boost_start_hour <= now.hour < self.boost_end_hour
                if in_boost != in_boost_last:
                    self.log.info(f"[BOOST] {'entering' if in_boost else 'leaving'} "
                                  f"high-frequency window (poll -> "
                                  f"{self.boost_poll_min if in_boost else self.poll_min}min)")
                    in_boost_last = in_boost
                interval_min = self.boost_poll_min if in_boost else self.poll_min

                last = self.state.get("last_poll", "")
                elapsed_min = (999999.0 if not last else
                              (now - datetime.fromisoformat(last)).total_seconds() / 60.0)
                due = elapsed_min >= interval_min
                if due:
                    if os.path.exists(self.stop_file):
                        self.log.info("[KILL-SWITCH] present -- skipping poll cycle")
                        # [2026-08-11 FIX] last_poll used to advance even on
                        # this skip, so a multi-hour/day kill-switch stop
                        # left the lookback (elapsed-since-last-poll) window
                        # covering only ~poll_min once resumed -- news from
                        # the entire stopped period would never be searched
                        # for. Leaving last_poll untouched means the NEXT
                        # cycle after resuming naturally has a large elapsed
                        # window (capped at 4x the cadence, same as any long
                        # outage) instead of a blind spot.
                    elif os.path.exists(self.breaker_file):
                        self.log.warning("[BREAKER] consecutive-loss breaker active -- "
                                         "skipping poll cycle (clear file to resume)")
                        # same reasoning as the kill-switch branch above.
                    else:
                        # cap the lookback at NEWS_POLL_MIN even if elapsed_min is huge
                        # (first run, or a long outage) -- searching for "news in the
                        # last 3 days" is a different, noisier task than the intended
                        # "news since last check", so bound it to a sane ceiling.
                        lookback = min(round(elapsed_min), max(self.poll_min, self.boost_poll_min) * 4)
                        self._poll_cycle(lookback)
                        self.state["last_poll"] = now.isoformat()
                        self._save_state()
            except Exception as e:
                self.log.error(f"loop error: {e}")
            time.sleep(60)

    def _watch_positions(self):
        known = self.state.get("positions", {})
        if not known:
            return
        live = {p["id"] for p in self._own_positions()}
        for k in list(known):
            info = known[k]
            if info.get("ticket") not in live:
                # closed at broker (SL or manual) -- look up the real outcome
                # from deal history so the loss-streak breaker actually sees
                # SL hits, not just time-stops.
                net = self._closed_position_net_pnl(info["ticket"])
                # [2026-08-11 FIX] net used to be a plain float that defaulted
                # to 0.0 whenever the deal-history lookup failed. 0.0 is not
                # neutral to _update_loss_streak() -- it's >= 0, which RESETS
                # consec_losses to 0. So a deal-history hiccup (same 90s MT5
                # timeout class as everything else) didn't just fail to count
                # a loss, it actively ERASED the breaker's memory, for
                # exactly the scenario (a broker-side SL hit) the breaker
                # most needs to catch. Now None means "couldn't determine" --
                # the streak is left untouched and this is loudly alerted
                # instead of silently treated as a win.
                if net is None:
                    self.log.error(f"[BROKER-CLOSE] {k} closed but outcome "
                                   f"UNKNOWN (deal history unavailable) -- "
                                   f"loss-streak left UNCHANGED, not reset")
                    self._telegram(f"⚠️ news_gemini: {k} closed at broker but "
                                   f"P&L could not be determined — breaker "
                                   f"streak NOT updated, please check MT5 "
                                   f"history manually")
                else:
                    self._update_loss_streak(net)
                    self.log.info(f"[BROKER-CLOSE] {k} position closed, "
                                  f"pnl={net:+.2f} -- {info}")
                    self._telegram(f"\U0001F534 CLOSED (broker) news_gemini: {k} "
                                   f"entry={info.get('entry')} pnl={net:+.2f}")
                known.pop(k, None)
        self._save_state()

    def _closed_position_net_pnl(self, position_id: str) -> Optional[float]:
        """Sum profit+swap+commission across a closed position's deals.
        Returns None -- NOT 0.0 -- if the outcome can't be determined (deal
        history unavailable or empty). Callers must treat None as 'unknown',
        never as a win/breakeven; see the 2026-08-11 fix note at the call
        site in _watch_positions for why this distinction is safety-critical
        (0.0 was silently resetting the consecutive-loss breaker)."""
        try:
            deals = self._mt5(self.connector.get_position_deals, position_id, 1440)
        except Exception as e:
            self.log.warning(f"deal history lookup failed for {position_id}: {e}")
            return None
        if not deals:
            self.log.warning(f"deal history EMPTY for {position_id} -- "
                             f"cannot determine outcome")
            return None
        return sum(d.get("profit", 0.0) + d.get("swap", 0.0) + d.get("commission", 0.0)
                  for d in deals)

    def _check_time_stops(self):
        known = self.state.get("positions", {})
        now = datetime.now(timezone.utc)
        for k, info in list(known.items()):
            opened = datetime.fromisoformat(info["opened_utc"])
            if (now - opened).total_seconds() < TIME_STOP_HOURS * 3600:
                continue
            pos = [p for p in self._own_positions(info["bsym"])
                   if p["id"] == info["ticket"]]
            if not pos:
                known.pop(k, None)
                continue
            p = pos[0]
            side = "long" if p["type"] == "BUY" else "short"
            r = self._mt5(self.executor.close_position_market, info["bsym"], side,
                          float(p["volume"]), str(p["id"]), "news-timestop")
            # [2026-08-11 FIX] close_position_market() returns {} (falsy) on a
            # rejected/failed order specifically so the caller can detect it
            # (forex_executor.py's own comment; forex_live_bot_gold_cwider.py
            # _close_timeout() does check it, this function used to not).
            # Without this check, a requote/IPC hiccup would leave the
            # position live at the broker while the bot marked it closed,
            # updated the loss streak from a stale pre-close snapshot, told
            # Telegram it closed, and stopped watching it entirely -- no
            # further time-stop enforcement, ever, for that position.
            if not r:
                self.log.error(f"[TIME-STOP] close FAILED for {k} -- position "
                               f"still open at broker, NOT untracking. Will "
                               f"retry next loop (~60s).")
                self._telegram(f"⚠️ news_gemini: {k} time-stop close order "
                               f"FAILED — position still open, retrying")
                continue
            net = float(p.get("profit", 0.0))
            self._update_loss_streak(net)
            self.log.info(f"[TIME-STOP] closed {k} after {TIME_STOP_HOURS}h, pnl={net:+.2f}")
            self._telegram(f"⏱ TIME-STOP news_gemini: {k} closed after "
                           f"{TIME_STOP_HOURS}h, pnl={net:+.2f}")
            known.pop(k, None)
        self._save_state()

    def _update_loss_streak(self, net_pnl: float):
        if net_pnl < 0:
            self.state["consec_losses"] = self.state.get("consec_losses", 0) + 1
        else:
            self.state["consec_losses"] = 0
        if self.state["consec_losses"] >= MAX_CONSEC_LOSSES:
            with open(self.breaker_file, "w") as f:
                f.write(f"{self.state['consec_losses']} consecutive losses as of "
                       f"{datetime.now(timezone.utc).isoformat()}")
            self.log.error(f"[BREAKER] {self.state['consec_losses']} consecutive "
                           f"losses -- auto-stopping new entries")
            self._telegram(f"⛔ news_gemini AUTO-STOPPED: "
                           f"{self.state['consec_losses']} consecutive losses. "
                           f"Delete {os.path.basename(self.breaker_file)} to resume "
                           f"after review.")

    # ── decision cycle ───────────────────────────────────────────────────
    def _safe_scan(self, name: str, fn, api_key: str, model: str,
                   lookback_min: int) -> Optional[list]:
        """Returns the candidate list, or None on ANY failure (quota, error,
        malformed JSON) -- None means 'skip this cycle', never partial-trust.

        Transient provider-side errors (503/overloaded/timeout) get a couple
        of short retries first -- see SCAN_MAX_RETRIES. Quota exhaustion and
        anything else non-transient (bad schema, auth) skip immediately;
        retrying those wastes the retry budget on something that can't
        recover within one poll cycle anyway.

        `model` accepts a single id or a CHAIN. On a transient failure the
        chain advances to the next id BEFORE sleeping, because a 503 reading
        "this model is currently experiencing high demand" is a statement
        about that model's capacity -- another id is usually served fine
        straight away, so waiting 20s to ask the identical overloaded model
        again is the weakest available response. Only once the distinct ids
        are exhausted does the delay apply. Taking a list or a string keeps
        the signature and both call sites unchanged: adding a parameter here
        is exactly the arity change that has silently disabled a bot in this
        repo twice."""
        models = [model] if isinstance(model, str) else [m for m in model if m]
        if not models:
            self.log.error(f"[{name.upper()}] no model configured -- skip cycle")
            return None
        attempt, idx = 0, 0
        while True:
            active = models[min(idx, len(models) - 1)]
            try:
                return self._call_with_timeout(
                    fn, SCAN_CALL_TIMEOUT_SEC, api_key, active, lookback_min)
            except Exception as e:
                # str(TimeoutError()) is '' -- fall back to the class name so
                # the log/Telegram line says something instead of nothing.
                msg = str(e) or f"{type(e).__name__} (no message)"
                low = msg.lower()
                # A 429 means two very different things and the old branch
                # treated them identically, which was wrong in both
                # directions. Billing exhaustion does not recover on its own
                # -- retrying wastes calls and re-alerts forever. A rate
                # limit recovers in seconds -- and it was being handled by
                # NOT retrying, which is the one response guaranteed not to
                # work. Match the specific wording; a bare 429 with no
                # further detail is treated as a rate limit, because one
                # wasted retry is cheaper than silently benching the bot.
                credit_dead = ("insufficient_quota" in low
                               or "exceeded your current quota" in low
                               or "billing" in low
                               or "RESOURCE_EXHAUSTED" in msg)
                rate_limited = ("rate_limit" in low or "rate limit" in low
                                or "too many requests" in low
                                or ("429" in msg and not credit_dead))
                if credit_dead and not rate_limited:
                    self.log.warning(f"[{name.upper()}] provider quota/billing "
                                     f"exhausted -- skip cycle: {msg[:300]}")
                    # Throttled: this state persists for hours or days, and
                    # an identical alert every poll cycle trains the reader
                    # to ignore the channel that also carries fill notices.
                    self._alert_once(f"quota:{name}",
                                     f"⚠️ news_gemini: {name} quota/billing "
                                     f"exhausted — no trades until it is "
                                     f"topped up. This will NOT self-recover."
                                     f"\n{msg[:300]}")
                    return None
                if rate_limited and attempt >= SCAN_MAX_RETRIES:
                    self.log.warning(f"[{name.upper()}] rate limited, retries "
                                     f"spent -- skip cycle: {msg[:300]}")
                    self._alert_once(f"ratelimit:{name}",
                                     f"⚠️ news_gemini: {name} rate limited "
                                     f"after {attempt} retries — skipped this "
                                     f"cycle (recovers on its own)\n{msg[:200]}")
                    return None
                if rate_limited:
                    # fall through to the transient retry path below
                    msg = f"rate limit: {msg}"
                # A timeout from _call_with_timeout is transient by
                # definition, but must be matched by TYPE, not message:
                # str(TimeoutError()) is the empty string, so the marker
                # check below would silently classify a timed-out scan as
                # permanent and skip the retry it most deserves.
                timed_out = isinstance(e, (TimeoutError,
                                          concurrent.futures.TimeoutError))
                if (timed_out or _is_transient_error(msg)) and attempt < SCAN_MAX_RETRIES:
                    attempt += 1
                    had_spare = idx < len(models) - 1
                    idx += 1
                    nxt = models[min(idx, len(models) - 1)]
                    if had_spare:
                        # different model available: go immediately, the wait
                        # buys nothing when the congestion is per-model
                        self.log.warning(
                            f"[{name.upper()}] transient on {active}, retry "
                            f"{attempt}/{SCAN_MAX_RETRIES} NOW on {nxt}: "
                            f"{msg[:200]}")
                    else:
                        self.log.warning(
                            f"[{name.upper()}] transient on {active}, no "
                            f"further models -- retry {attempt}/"
                            f"{SCAN_MAX_RETRIES} in {SCAN_RETRY_DELAY_SEC}s: "
                            f"{msg[:200]}")
                        time.sleep(SCAN_RETRY_DELAY_SEC)
                    continue
                suffix = (f" (after {attempt} retr{'y' if attempt == 1 else 'ies'})"
                         if attempt else "")
                self.log.error(f"[{name.upper()}] scan failed -- skip cycle{suffix}: {msg[:300]}")
                self._telegram(f"⚠️ news_gemini: {name} scan failed — skipped "
                               f"this cycle{suffix}: {msg[:150]}")
                return None

    def _poll_cycle(self, lookback_min: int):
        self.log.info(f"── news poll cycle (dual-provider consensus, "
                      f"lookback={lookback_min}min) ──")

        # [2026-08-12] Heartbeat between each long-running stage, not just
        # once per loop iteration in run(). A cycle can span two scans
        # (each up to SCAN_CALL_TIMEOUT_SEC, and each possibly retried)
        # plus per-candidate chart vetoes; refreshing only at the top let a
        # legitimately slow-but-healthy cycle drift past watchdog_h1.ps1's
        # 5-minute staleness threshold and get restarted mid-decision.
        self._heartbeat()
        gemini_candidates = self._safe_scan("gemini", gemini_scan,
                                            self.gemini_key, self.gemini_models,
                                            lookback_min)
        self._heartbeat()
        if gemini_candidates is None:
            return

        if not self.openai_key:
            self.log.info("[OPENAI] not configured -- dual-consensus unavailable, "
                          "no new entries this cycle (fail-safe)")
            return

        openai_candidates = self._safe_scan("openai", openai_scan,
                                            self.openai_key, self.openai_model,
                                            lookback_min)
        self._heartbeat()
        if openai_candidates is None:
            return

        self.log.info(f"[SCAN] gemini={len(gemini_candidates)} candidate(s), "
                      f"openai={len(openai_candidates)} candidate(s)")

        confirmed = cross_check_consensus(gemini_candidates, openai_candidates)
        if not confirmed:
            self.log.info("[CONSENSUS] no symbol confirmed by both providers this cycle")
            return

        for c in confirmed:
            self._heartbeat()   # each candidate runs 2 more chart-veto calls
            self.log.info(f"[CONSENSUS] both providers agree: {c['symbol']} "
                          f"{c['signal']} conf={c['confidence']:.2f}")
            self._evaluate_candidate(c)

    def _evaluate_candidate(self, c: dict):
        symbol = c.get("symbol", "")
        signal = c.get("signal", "none")
        conf = float(c.get("confidence", 0) or 0)
        url = c.get("source_url", "")
        src = c.get("source_name", "")
        headline = c.get("headline", "")
        reasoning = c.get("reasoning", "")
        dom = _domain(url)

        tag = f"{symbol} {signal} conf={conf:.2f} src={src}({dom}) :: {headline[:80]}"

        if signal == "none":
            self.log.info(f"[SKIP no-direction] {tag}")
            return
        if symbol not in SYMBOLS:
            self.log.warning(f"[SKIP unknown-symbol] {tag}")
            return
        if conf < CONF_MIN:
            self.log.info(f"[SKIP low-confidence] {tag}")
            self._telegram(f"ℹ️ news_gemini SKIP (confidence {conf:.2f} < "
                           f"{CONF_MIN}): {headline[:150]}")
            return
        if dom not in TIER1_DOMAINS:
            self.log.info(f"[SKIP untrusted-source] {tag}")
            self._telegram(f"ℹ️ news_gemini SKIP (source '{dom}' not tier-1): "
                           f"{headline[:150]}")
            return

        norm_url = _normalize_url(url)
        seen = self.state.setdefault("seen_urls", {})
        last_seen = seen.get(norm_url)
        if last_seen:
            age_h = (datetime.now(timezone.utc)
                     - datetime.fromisoformat(last_seen)).total_seconds() / 3600
            if age_h < DEDUP_HOURS:
                self.log.info(f"[SKIP dedup {age_h:.1f}h ago] {tag}")
                return

        bsym = self.symbols[symbol]
        if self._own_positions(bsym):
            self.log.info(f"[SKIP already-positioned] {tag}")
            self._telegram(f"ℹ️ news_gemini SKIP (already have a {symbol} "
                           f"position open): {headline[:150]}")
            return

        self.log.info(f"[QUALIFIED] {tag}")
        if not self._chart_allows(symbol, bsym, signal, headline):
            return
        # mark dedup only on a CONFIRMED trade -- a fail-safe skip inside
        # _enter() (bad ATR/price/sizing/order-reject) must not burn the
        # DEDUP_HOURS window for a story that never actually got traded.
        if self._enter(symbol, bsym, signal, headline, src, url, reasoning, conf):
            seen[norm_url] = datetime.now(timezone.utc).isoformat()
            self._prune_seen_urls(seen)
            self._save_state()

    def _chart_allows(self, symbol: str, bsym: str, signal: str,
                     headline: str) -> bool:
        """Chart-vision VETO gate. Returns False to cancel an otherwise
        news-qualified entry.

        Both providers see the SAME chart image and answer independently;
        EITHER one vetoing (with confidence >= CHART_VETO_CONF_MIN) cancels
        the trade. That asymmetry is deliberate: unlike the news stage
        (where both must AGREE to trade), here either can BLOCK -- the
        chart's only job is to reduce trades, never to create them.

        Fail-OPEN on any error: if charts can't be rendered or the models
        can't be reached, the trade proceeds on news consensus alone (which
        is what it did before this stage existed). A broken veto stage must
        not become a silent kill-switch."""
        try:
            candles = self._mt5(self.connector.fetch_ohlcv, bsym, "1h", CHART_BARS)
            if not candles or len(candles) < 30:
                self.log.warning(f"[CHART] {symbol}: only "
                                 f"{len(candles) if candles else 0} bars -- "
                                 f"skipping veto stage (fail-open)")
                return True
            png = render_chart_png(candles, symbol, signal)
            price = float(candles[-1][4])
        except Exception as e:
            self.log.warning(f"[CHART] {symbol}: render failed ({e}) -- fail-open")
            return True

        verdicts = {}
        for name, fn, key, model in (
                ("gemini", gemini_chart_veto, self.gemini_key, self.gemini_model),
                ("openai", openai_chart_veto, self.openai_key, self.openai_model)):
            if not key:
                continue
            try:
                v = self._call_with_timeout(
                    fn, CHART_CALL_TIMEOUT_SEC, key, model, png, symbol,
                    signal, headline, price, len(candles))
                verdicts[name] = v
                self.log.info(f"[CHART/{name}] {symbol} contradicts="
                              f"{v.get('contradicts')} conf={v.get('confidence')} "
                              f":: {str(v.get('observation'))[:150]}")
            except Exception as e:
                self.log.warning(f"[CHART/{name}] {symbol} failed ({e}) -- "
                                 f"ignoring this provider's vote (fail-open)")

        for name, v in verdicts.items():
            if (v.get("contradicts") is True
                    and float(v.get("confidence", 0) or 0) >= CHART_VETO_CONF_MIN):
                obs = str(v.get("observation", ""))[:300]
                self.log.info(f"[CHART VETO] {symbol} {signal} cancelled by {name}: {obs}")
                self._telegram(
                    f"🚫 CHART VETO {symbol} {signal.upper()}\n"
                    f"News passed both models, but {name} sees the chart "
                    f"contradicting it (conf {v.get('confidence')}):\n{obs}\n\n"
                    f"headline: {headline[:200]}")
                return False
        return True

    def _enter(self, symbol: str, bsym: str, signal: str, headline: str,
              src: str, url: str, reasoning: str, conf: float) -> bool:
        atr = self._atr14(bsym)
        if not atr or not math.isfinite(atr) or atr <= 0:
            self.log.warning(f"[{symbol}] invalid ATR -- skip entry")
            self._telegram(f"⚠️ news_gemini: {symbol} ATR unavailable — "
                           f"entry skipped (fail-safe)")
            return False

        eq = self._mt5(self.connector.get_equity)
        bid, ask = self._mt5(self.connector.get_current_price, bsym)
        if bid <= 0 or ask <= 0:
            self.log.warning(f"[{symbol}] invalid price -- skip entry")
            return False
        long_ = signal == "long"
        px = ask if long_ else bid
        sd = SL_ATR_MULT * atr
        sl = px - sd if long_ else px + sd

        # sizing: proven pip_size/get_pip_value_live pattern (2026-08-10
        # cent-account sizing fix -- see feedback_cent_account_sizing memory)
        pip_size = self.cfg.get_pip_size(bsym)
        pip_value = self._mt5(self.connector.get_pip_value_live, bsym)
        sd_pips = sd / pip_size
        if sd_pips <= 0 or pip_value <= 0:
            self.log.warning(f"[{symbol}] invalid pip_value -- skip entry")
            return False
        risk_cash = eq * self.risk_pct / 100.0
        lot = round(risk_cash / (sd_pips * pip_value), 2)
        if lot < 0.01:
            self.log.warning(f"[{symbol}] lot rounds to 0 -- skip entry")
            return False
        actual_risk_pct = (sd_pips * pip_value * lot) / eq * 100.0 if eq > 0 else float("inf")
        if actual_risk_pct > self.risk_pct * 1.5:
            self.log.error(f"[{symbol}] SIZING SANITY CHECK FAILED: lot={lot} implies "
                           f"{actual_risk_pct:.2f}% > 1.5x intended {self.risk_pct}% "
                           f"-- REFUSING to open")
            self._telegram(f"⛔ news_gemini: {symbol} sizing sanity check failed "
                           f"— entry refused")
            return False

        side = "long" if long_ else "short"
        result = self._mt5(self.executor.open_position, bsym, side, lot,
                          sl, 0.0, "NEWS-" + side[:4].upper())
        if not result:
            self.log.error(f"[{symbol}] open failed")
            self._telegram(f"⚠️ news_gemini: {symbol} order failed to open")
            return False

        fill = float(result.get("fill_price", px) or px)
        self.state.setdefault("positions", {})[f"{symbol}-{result.get('trade_id')}"] = {
            "bsym": bsym, "ticket": str(result.get("trade_id", "") or ""),
            "side": side, "entry": fill, "sl": sl, "lot": lot,
            "opened_utc": datetime.now(timezone.utc).isoformat(),
            "headline": headline, "source": src,
        }
        self._save_state()

        self.log.info(f"[OPEN] {side.upper()} {bsym} lot={lot} fill={fill:.2f} "
                      f"sl={sl:.2f} (risk={actual_risk_pct:.2f}%)")
        self._telegram(
            f"\U0001F7E2 NEWS ENTRY {side.upper()} {bsym}\n"
            f"lot={lot}  fill={fill:.2f}  SL={sl:.2f} ({SL_ATR_MULT}xATR)\n"
            f"conf={conf:.2f}  time-stop={TIME_STOP_HOURS}h\n"
            f"headline: {headline}\n"
            f"source: {src} ({url})\n"
            f"reasoning: {reasoning[:400]}")
        return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--risk", type=float, default=DEFAULT_RISK_PCT,
                    help=f"%% equity risked per trade (default {DEFAULT_RISK_PCT} -- "
                         f"deliberately far below every other live bot; there is no "
                         f"historical edge estimate to size against)")
    ap.add_argument("--poll-min", type=int, default=NEWS_POLL_MIN,
                    help=f"minutes between news scans outside the boost window "
                         f"(default {NEWS_POLL_MIN})")
    ap.add_argument("--boost-poll-min", type=int, default=BOOST_POLL_MIN,
                    help=f"minutes between scans DURING the boost window "
                         f"(default {BOOST_POLL_MIN})")
    ap.add_argument("--boost-start-utc-hour", type=int, default=BOOST_START_UTC_HOUR,
                    help=f"boost window start, UTC hour 0-23 (default "
                         f"{BOOST_START_UTC_HOUR} = 19:00 Thai time)")
    ap.add_argument("--boost-end-utc-hour", type=int, default=BOOST_END_UTC_HOUR,
                    help=f"boost window end, UTC hour 0-23, exclusive (default "
                         f"{BOOST_END_UTC_HOUR} = 20:00 Thai time)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-real", action="store_true")
    args = ap.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    if not os.environ.get("GEMINI_API_KEY"):
        print("[ERROR] GEMINI_API_KEY not set (add it to .env)")
        sys.exit(1)
    if not os.environ.get("OPENAI_API_KEY"):
        print("[WARN] OPENAI_API_KEY not set -- dual-provider consensus is "
              "unavailable, the bot will run but skip every cycle (no new "
              "entries) until it's added to .env. Not fatal, continuing.")

    cfg = ForexConfig()
    cfg.symbols = ["XAUUSD", "BTCUSDC"]
    cfg.magic_number = MAGIC
    cfg.dry_run = args.dry_run
    cfg.allow_real = args.allow_real
    for s in ("BTCUSDC",):
        cfg.pip_size[s] = 1.0
        cfg.pip_value_usd_approx[s] = 0.01

    if not cfg.dry_run and not _cfg_has_credentials(cfg):
        print("[ERROR] MT5 unavailable -- use --dry-run off-Windows")
        sys.exit(1)

    NewsGeminiBot(cfg, args.risk, args.poll_min,
                 args.boost_poll_min, args.boost_start_utc_hour,
                 args.boost_end_utc_hour).run()


if __name__ == "__main__":
    main()
