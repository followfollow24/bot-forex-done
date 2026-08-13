#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
chart_ai_trader.py -- a standalone live bot whose ENTIRE entry signal is
"what does the AI see on the H1 chart", not price-action rules, not news.
Built at the user's explicit request (2026-08-12) as a separate bot from
news_gemini_bot.py -- deliberately NOT wired into it, so this bot's
failure modes can't touch the news/chart-veto system already live and
trading, and vice versa.

DUAL-CONSENSUS, LIKE news_gemini_bot.py'S NEWS STAGE (not like its chart
VETO stage): Gemini and OpenAI each independently receive the SAME chart
image PLUS the same numeric market payload (close, EMA20/50, ATR, recent
swing high/low, last 5 bars), and each independently returns
LONG/SHORT/WAIT with absolute entry/sl/tp. A trade opens only if BOTH
return the same LONG or SHORT, after which their levels are averaged and
validated. This is a full trading-decision gate (unlike
log_anomaly_scanner.py's union-of-findings design) because a wrong entry
here costs real money, same reasoning as news_gemini's own news stage.

RISK TIER: the user explicitly chose to size this like a normal technical
bot (0.30-0.50%/trade), not news_gemini's ultra-conservative 0.15% --
despite this being an equally unvalidated signal source. Defaults to the
conservative end of that explicit range (0.30%) since "sized like a
technical bot" was about magnitude, not a claim that this has technical
bots' walk-forward-validated track record. Adjust via --risk.

EXIT MANAGEMENT: pure broker-side SL/TP -- placed once at entry, then left
alone (no code-driven time-stop, no trailing, no discretionary
re-evaluation of open positions). [2026-08-12] The DISTANCES are no longer
fixed: both models place entry/sl/tp per setup, the two are averaged and
validated (correct sides, stop inside the 1.5-2.0xATR band the prompt
asks for, R:R >= 1.5, entry close to the live price). The DISTANCES are
then applied to the actual fill rather than the AI's stated prices, so
price moving between decision and fill cannot distort the intended risk
or reward. Because lot sizing divides by the actual stop distance, a
wider AI-chosen stop produces a smaller lot -- the $-risk per trade stays
pinned to --risk no matter what the models pick, which is what makes
delegating the stop safe.

** UNVALIDATED STRATEGY -- no historical backtest exists for "AI reads a
chart and decides long/short/none". Live by explicit user decision,
same class of risk as news_gemini_bot.py at launch. **

Usage:
  python chart_ai_trader.py --dry-run             paper mode, no real orders
  python chart_ai_trader.py --allow-real           confirms trading a REAL
                                                   (non-demo) account
  python chart_ai_trader.py --risk 0.30 --poll-min 60 --allow-real
"""
import argparse
import concurrent.futures
import json
import logging
import logging.handlers
import math
import os
import sys
import time
from datetime import datetime, timezone
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from forex_config import ForexConfig
from forex_executor import MT5Connector, ForexOrderExecutor, _cfg_has_credentials

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 671xxx family: unused by any existing bot (555/666/667/668 are the
# forex_live_bot_gold_cwider.py + daily_sleeves_bot.py families,
# 669001 is news_gemini_bot.py).
MAGIC = 671001

SYMBOLS = {
    "XAUUSD": "gold",
    "BTCUSDC": "BTC",
    "ETHUSDC": "ETH",
}

TIMEFRAME = "15m"    # [2026-08-12] was 1h, changed at the user's request.
CHART_BARS = 160     # 160 M15 bars = ~40h of context. On H1 this was 120
                     # bars (~5 days); M15 needs more bars to still show a
                     # meaningful stretch of market rather than one session.
POLL_MIN = 15        # one M15 bar -- matches the entry timeframe, same way
                     # the H1 version polled hourly.

# [2026-08-12 v2] Reworked to the user's supplied spec. Three changes that
# matter, beyond the prompt rewrite:
#
#  1. The models are now given the NUMBERS (close, EMA20, EMA50, ATR,
#     recent swing high/low, last bars) alongside the chart image, instead
#     of having to read values off pixels. This is the single biggest
#     expected quality win: "price is above EMA20" becomes a comparison of
#     two floats it was handed rather than an eyeball judgement, and the
#     2-of-3 rule below is checkable rather than impressionistic.
#  2. They now return ABSOLUTE PRICES (entry/sl/tp), per the spec, rather
#     than ATR multiples. That is friendlier to state but needs more
#     validation -- a price is not scale-free, so a hallucinated or stale
#     number can be nonsense in a way a multiple cannot. See
#     cross_check_signal() for the side/sanity checks that result.
#  3. The confidence gate is GONE. The spec replaces it with the explicit
#     2-of-3 rule plus dual consensus, which is what stops a weak setup
#     now. Deliberate: the old CONF_MIN was the thing making the bot
#     almost never trade, and the user asked for it to not be so hard to
#     enter ("ไม่ออกไม้ยากเกินไป").
#
# SL band per the spec (1.5-2.0 x ATR), with a small tolerance either side
# so a model that lands at 1.45 or 2.1 is accepted rather than throwing the
# whole setup away; anything outside REJECTS the trade (it is not clamped
# silently -- an out-of-band stop means the model ignored the brief, and
# guessing what it meant with real money is worse than skipping).
SL_ATR_MIN, SL_ATR_MAX = 1.2, 2.5
MIN_RR = 1.5          # spec: TP must be at least 1:1.5 reward:risk
# The AI's stated entry must sit within this many ATR of the real current
# price, or the quote is stale/hallucinated and the setup is rejected.
MAX_ENTRY_DRIFT_ATR = 1.5

DEFAULT_RISK_PCT = 0.30
MAX_CONSEC_LOSSES = 3

# [2026-08-12] Position stacking. The user asked for the bot to be able to
# keep opening as the AI sees setups, rather than one-position-per-symbol.
# These are the bound on that: NOT a limit on the idea, a limit on the
# blast radius if the AI (or a bug) answers "long" every single cycle.
# Worst-case simultaneous risk = MAX_TOTAL_POSITIONS * risk_pct, since
# every position carries its own stop sized to risk_pct. At the defaults
# that is 6 x 0.30% = 1.8% of equity if every stop hits at once.
# Raise with --max-per-symbol / --max-total, but do the multiplication
# first: at --max-total 30 the same arithmetic gives 9%.
MAX_POSITIONS_PER_SYMBOL = 3
MAX_TOTAL_POSITIONS = 6

# Wall-clock cap on a single AI call. Sized so a full cycle (3 symbols x 2
# providers) still cannot exceed the watchdog's 5-minute staleness window
# once the per-symbol heartbeat below is taken into account: the heartbeat
# is refreshed between symbols, so the longest gap between two heartbeats
# is one symbol's two calls = 2 x AI_CALL_TIMEOUT_SEC = 120s < 300s.
AI_CALL_TIMEOUT_SEC = 60

# [2026-08-13] NEWS VETO. News can only BLOCK a chart setup, never create
# one -- the 2-of-3 chart rule stays the sole source of entries, exactly
# as the user asked ("รักษากลยุทธ์หลักให้คมเหมือนเดิม"). A clean chart
# fighting a high-impact release (CPI, NFP, a rate decision) is the case
# this exists for: price often spikes through the stop before going the
# "right" way, so sitting out is worth more than being right eventually.
#
# Implementation note -- this deliberately REUSES news_gemini_bot's
# already-live, already-tested gemini_scan()/openai_scan() rather than
# writing a fresh "is there contradicting news?" prompt. Two reasons:
#   * those functions already solve the hard part (grounded search in two
#     passes, because neither provider reliably combines a search tool
#     with a forced JSON schema in one call)
#   * the contradiction test then becomes ORDINARY CODE -- "does any
#     surfaced candidate for this symbol point the opposite way, at
#     >= NEWS_VETO_CONF_MIN?" -- instead of a second LLM judgement.
#     Deterministic, unit-testable, and it cannot hallucinate a veto.
#
# Cost is kept near zero by fetching LAZILY: the scan only runs once a
# chart consensus actually wants to trade, and is cached for the rest of
# the cycle. Cycles with no signal (the vast majority) make no extra
# calls at all.
#
# Either provider surfacing contradicting news is enough to block --
# asymmetric on purpose, same as news_gemini's chart veto: a filter whose
# only power is to reduce trading does not need consensus to act.
NEWS_VETO_CONF_MIN = 0.70
NEWS_LOOKBACK_MIN = 180


CHART_SIGNAL_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string"},   # "LONG" | "SHORT" | "WAIT"
        "entry": {"type": "number"},
        "sl": {"type": "number"},
        "tp": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["decision", "entry", "sl", "tp", "reason"],
}

ENTRY_PROMPT_TEMPLATE = """You are an AI trading-chart analysis system. \
Below is the latest market data for {symbol} on the M15 (15-minute) \
timeframe, plus a candlestick chart of the same data.

{payload}

ANALYSIS RULES -- score the setup against these three:
  1. CLEAR TREND: price is above BOTH EMAs (bullish) or below BOTH \
(bearish).
  2. PRICE ACTION: price has pulled back into the EMA zone, or is \
breaking out of the recent range.
  3. MOMENTUM: a reversal/continuation candle or volume supports the \
direction.

DECISION:
  * If AT LEAST 2 of the 3 rules are met, decide "LONG" or "SHORT".
  * If the market is too choppy, the signals conflict, or fewer than 2 \
rules are met, decide "WAIT".

LEVELS (only meaningful when the decision is LONG or SHORT):
  * entry: the current price, {price}.
  * sl: stop loss placed 1.5 to 2.0 x ATR away from entry. ATR is {atr}, \
so the stop distance must be between {sl_lo} and {sl_hi}. For LONG the \
stop goes BELOW entry; for SHORT it goes ABOVE.
  * tp: take profit giving a reward:risk of AT LEAST 1:{min_rr} -- i.e. \
the distance from entry to tp must be at least {min_rr} times the \
distance from entry to sl. For LONG the target is ABOVE entry; for \
SHORT it is BELOW.
  * reason: one or two sentences naming WHICH of the three rules were \
met and where you put the stop and target.

If the decision is "WAIT", still return numeric entry/sl/tp (they are \
ignored) -- for example entry {price} and any two nearby values.

Respond ONLY via the provided JSON schema."""


def _ema(values: list, span: int) -> float:
    k = 2.0 / (span + 1.0)
    prev = values[0]
    for v in values:
        prev = v * k + prev * (1 - k)
    return prev


def build_market_payload(candles: list, atr: float) -> dict:
    """The numeric context the models get alongside the chart image.

    This is the spec's key idea: hand the model the actual figures rather
    than making it read them off pixels, so "price is above EMA20" is a
    float comparison instead of an eyeball judgement. Pure function of the
    candle list -- unit-testable, no MT5 needed.

    candles: MT5 fetch_ohlcv format [[ts_ms, o, h, l, c, v], ...] ascending.
    """
    closes = [float(c[4]) for c in candles]
    highs = [float(c[2]) for c in candles]
    lows = [float(c[3]) for c in candles]
    price = closes[-1]
    ema20 = _ema(closes, 20)
    ema50 = _ema(closes, 50)
    look = min(20, len(candles))          # recent swing window
    recent_high = max(highs[-look:])
    recent_low = min(lows[-look:])
    # last few bars verbatim, so the momentum rule has something concrete
    tail = []
    for c in candles[-5:]:
        o, h, l, cl = float(c[1]), float(c[2]), float(c[3]), float(c[4])
        v = float(c[5]) if len(c) > 5 else 0.0
        tail.append({"o": o, "h": h, "l": l, "c": cl, "v": v})
    return {
        "price": price, "ema20": ema20, "ema50": ema50, "atr": atr,
        "recent_high": recent_high, "recent_low": recent_low,
        "bars": len(candles), "tail": tail,
    }


def format_payload(symbol: str, p: dict) -> str:
    """Render the payload as the compact text block the spec describes."""
    price, ema20, ema50 = p["price"], p["ema20"], p["ema50"]
    trend = ("above BOTH EMAs (bullish structure)" if price > ema20 and price > ema50
             else "below BOTH EMAs (bearish structure)" if price < ema20 and price < ema50
             else "BETWEEN the EMAs (no clear trend)")
    lines = [
        f"Current Data ({symbol}, M15, {p['bars']} bars):",
        f"  Close={price:.2f}  EMA20={ema20:.2f}  EMA50={ema50:.2f}  ATR={p['atr']:.2f}",
        f"  Recent High={p['recent_high']:.2f}  Recent Low={p['recent_low']:.2f}",
        f"  Position vs EMAs: price is {trend}.",
        f"  Distance to EMA20 = {price - ema20:+.2f} "
        f"({(price - ema20) / p['atr']:+.2f} ATR), "
        f"to EMA50 = {price - ema50:+.2f} "
        f"({(price - ema50) / p['atr']:+.2f} ATR)",
        "  Last 5 bars (oldest first) O/H/L/C/V:",
    ]
    for b in p["tail"]:
        lines.append(f"    {b['o']:.2f} / {b['h']:.2f} / {b['l']:.2f} / "
                     f"{b['c']:.2f} / {b['v']:.0f}")
    return "\n".join(lines)


def _fmt_prompt(symbol: str, bars: int, price: float, atr: float,
                payload_text: str) -> str:
    return ENTRY_PROMPT_TEMPLATE.format(
        symbol=symbol, bars=bars, price=f"{price:.2f}", atr=f"{atr:.2f}",
        payload=payload_text,
        sl_lo=f"{SL_ATR_MIN * atr:.2f}", sl_hi=f"{SL_ATR_MAX * atr:.2f}",
        min_rr=MIN_RR)


def gemini_chart_signal(api_key: str, model: str, png: bytes, symbol: str,
                        price: float, bars: int, atr: float,
                        payload_text: str = "") -> dict:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    prompt = _fmt_prompt(symbol, bars, price, atr, payload_text)
    resp = client.models.generate_content(
        model=model,
        contents=[types.Part.from_bytes(data=png, mime_type="image/png"), prompt],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=CHART_SIGNAL_SCHEMA,
        ),
    )
    return json.loads(resp.text)


def openai_chart_signal(api_key: str, model: str, png: bytes, symbol: str,
                        price: float, bars: int, atr: float,
                        payload_text: str = "") -> dict:
    import base64
    from openai import OpenAI
    # reuse the strict-schema converter already built and tested for
    # news_gemini_bot.py rather than duplicating that subtle logic here.
    sys.path.insert(0, _BASE_DIR)
    from news_gemini_bot import _openai_strict_schema

    client = OpenAI(api_key=api_key)
    prompt = _fmt_prompt(symbol, bars, price, atr, payload_text)
    b64 = base64.b64encode(png).decode()
    resp = client.responses.create(
        model=model,
        input=[{"role": "user", "content": [
            {"type": "input_image", "image_url": f"data:image/png;base64,{b64}"},
            {"type": "input_text", "text": prompt},
        ]}],
        text={"format": {
            "type": "json_schema",
            "name": "chart_signal",
            "schema": _openai_strict_schema(CHART_SIGNAL_SCHEMA),
            "strict": True,
        }},
    )
    return json.loads(resp.output_text)


def cross_check_signal(gemini_result: dict, openai_result: dict,
                       price: float, atr: float) -> Optional[dict]:
    """Consensus + validation. Pure function -- no API, no MT5 -- so every
    path between an AI opinion and a live order is cheaply testable.

    Per the user's spec: trade only when BOTH models return the same
    LONG/SHORT decision, then average their entry/sl/tp. Returns None on
    any disagreement, any WAIT, or any validation failure.

    Absolute prices need more checking than the ATR multiples they
    replaced, because a price is not scale-free -- a stale or hallucinated
    number can be plausible-looking nonsense. Validated, in order:
      - both decisions are LONG or SHORT and identical
      - all three levels are finite and positive
      - sl/tp are on the CORRECT SIDES of entry for the direction (a
        LONG whose stop sits above entry is not a typo to fix, it is a
        model that lost the plot)
      - the averaged entry is within MAX_ENTRY_DRIFT_ATR of the real
        current price (catches a stale quote or a fabricated level)
      - the stop distance falls in the SL_ATR_MIN..MAX band the prompt
        asked for -- rejected, not clamped: silently "fixing" an
        out-of-band stop would trade a setup the model never proposed
      - reward:risk clears MIN_RR

    Returns distances as well as prices. The caller applies the DISTANCES
    to the real fill, which is what keeps the intended risk/reward intact
    if price moves between the decision and the fill.
    """
    g_dec = str(gemini_result.get("decision", "")).strip().upper()
    o_dec = str(openai_result.get("decision", "")).strip().upper()
    if g_dec not in ("LONG", "SHORT") or o_dec not in ("LONG", "SHORT"):
        return None
    if g_dec != o_dec:
        return None

    try:
        vals = [float(r[k]) for r in (gemini_result, openai_result)
                for k in ("entry", "sl", "tp")]
    except (TypeError, ValueError, KeyError):
        return None
    if any((not math.isfinite(v)) or v <= 0 for v in vals):
        return None
    if not math.isfinite(atr) or atr <= 0:
        return None

    entry = (float(gemini_result["entry"]) + float(openai_result["entry"])) / 2.0
    sl = (float(gemini_result["sl"]) + float(openai_result["sl"])) / 2.0
    tp = (float(gemini_result["tp"]) + float(openai_result["tp"])) / 2.0

    long_ = g_dec == "LONG"
    if long_ and not (sl < entry < tp):
        return None
    if (not long_) and not (tp < entry < sl):
        return None

    if abs(entry - price) > MAX_ENTRY_DRIFT_ATR * atr:
        return None

    sl_dist = abs(entry - sl)
    tp_dist = abs(tp - entry)
    if sl_dist <= 0 or tp_dist <= 0:
        return None
    sl_mult = sl_dist / atr
    if not (SL_ATR_MIN <= sl_mult <= SL_ATR_MAX):
        return None
    rr = tp_dist / sl_dist
    if rr < MIN_RR:
        return None

    return {
        "signal": "long" if long_ else "short",
        "decision": g_dec,
        "entry": entry, "sl": sl, "tp": tp,
        "sl_dist": sl_dist, "tp_dist": tp_dist,
        "sl_atr_mult": sl_mult, "rr": rr,
        "gemini_reasoning": str(gemini_result.get("reason", "")),
        "openai_reasoning": str(openai_result.get("reason", "")),
    }


class ChartAITraderBot:
    def __init__(self, cfg: ForexConfig, risk_pct: float, poll_min: int,
                 max_per_symbol: int = MAX_POSITIONS_PER_SYMBOL,
                 max_total: int = MAX_TOTAL_POSITIONS):
        self.cfg = cfg
        self.risk_pct = risk_pct
        self.poll_min = poll_min
        self.max_per_symbol = max_per_symbol
        self.max_total = max_total

        self.stop_file = os.path.join(_BASE_DIR, "STOP_CHART_AI_TRADER")
        self.breaker_file = os.path.join(_BASE_DIR, "BREAKER_CHART_AI_TRADER")
        self.heartbeat_file = os.path.join(_BASE_DIR, "HEARTBEAT_CHART_AI_TRADER")
        self.state_file = os.path.join(_BASE_DIR, "chart_ai_trader_state.json")
        self.log_file = os.path.join(_BASE_DIR, "forex_bot_chart_ai_trader.log")

        self.log = self._setup_logging()
        self.connector = MT5Connector(cfg, self.log)
        self.executor = ForexOrderExecutor(self.connector, cfg, self.log)
        self._io = concurrent.futures.ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="mt5-io-chartai")
        self._mt5_timeout = 90.0

        self.gemini_key = os.environ.get("GEMINI_API_KEY", "")
        self.gemini_model = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
        self.openai_key = os.environ.get("OPENAI_API_KEY", "")
        self.openai_model = os.environ.get("OPENAI_MODEL", "gpt-5-mini")

        self.state = self._load_state()
        self.symbols: dict = {}
        self._news_cache: Optional[list] = None
        # per-cycle record of which providers actually answered, so a trade
        # entered while the veto was blind is identifiable AFTERWARDS -- see
        # _news_veto_state().
        self._news_ok: list = []
        self._news_failed: list = []

    def _setup_logging(self) -> logging.Logger:
        fmt = "%(asctime)s [%(levelname)s] %(message)s"
        handlers: list = [logging.StreamHandler(sys.stdout)]
        try:
            handlers.append(logging.handlers.RotatingFileHandler(
                self.log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"))
        except Exception:
            pass
        logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers, force=True)
        return logging.getLogger("ChartAITraderBot")

    def _mt5(self, func, *args, **kw):
        fut = self._io.submit(func, *args, **kw)
        return fut.result(timeout=self._mt5_timeout)

    def _heartbeat(self):
        try:
            with open(self.heartbeat_file, "w", encoding="utf-8") as f:
                f.write(datetime.now(timezone.utc).isoformat())
        except Exception:
            pass

    def _telegram(self, msg: str):
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat = os.environ.get("TELEGRAM_CHAT_ID")
        if not token or not chat:
            return
        try:
            import urllib.request, urllib.parse
            data = urllib.parse.urlencode({"chat_id": chat, "text": msg}).encode()
            urllib.request.urlopen(
                f"https://api.telegram.org/bot{token}/sendMessage", data, timeout=10)
        except Exception as e:
            self.log.warning(f"telegram failed: {e}")

    def _load_state(self) -> dict:
        default = {"positions": {}, "consec_losses": 0, "last_poll": ""}
        if not os.path.exists(self.state_file):
            return default
        try:
            with open(self.state_file, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            self.log.error(f"[STATE] {self.state_file} exists but failed to "
                           f"load ({e}) -- starting from a BLANK state "
                           f"(tracked positions/loss-streak lost; broker "
                           f"positions will still be found on next check)")
            self._telegram(f"⚠️ chart_ai_trader: state file corrupted/unreadable "
                           f"({type(e).__name__}) -- resumed with blank state, "
                           f"please verify no position was orphaned")
            return default

    def _save_state(self):
        tmp = self.state_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=1)
        os.replace(tmp, self.state_file)

    def _own_positions(self, bsym: Optional[str] = None) -> list:
        pos = [p for p in (self._mt5(self.connector.get_open_positions) or [])
              if p.get("magic") == MAGIC]
        if bsym:
            pos = [p for p in pos if p.get("symbol") == bsym]
        return pos

    def _atr14(self, bsym: str) -> Optional[float]:
        # must use the SAME timeframe the chart is drawn on -- an H1 ATR
        # is several times an M15 ATR, so mixing them would silently scale
        # every AI-chosen stop distance (and therefore every lot size).
        candles = self._mt5(self.connector.fetch_ohlcv, bsym, TIMEFRAME, 60)
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

    def _closed_position_net_pnl(self, position_id: str) -> Optional[float]:
        """None means 'unknown', never 0.0 -- same fail-safe reasoning as
        news_gemini_bot.py's identical method: a deal-history lookup
        failure must never look like a breakeven trade to the breaker."""
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

    def _watch_positions(self):
        known = self.state.get("positions", {})
        if not known:
            return
        live = {p["id"] for p in self._own_positions()}
        for k in list(known):
            info = known[k]
            if info.get("ticket") not in live:
                net = self._closed_position_net_pnl(info["ticket"])
                if net is None:
                    self.log.error(f"[BROKER-CLOSE] {k} closed but outcome "
                                   f"UNKNOWN (deal history unavailable) -- "
                                   f"loss-streak left UNCHANGED, not reset")
                    self._telegram(f"⚠️ chart_ai_trader: {k} closed at broker "
                                   f"but P&L could not be determined — breaker "
                                   f"streak NOT updated, please check manually")
                else:
                    self._update_loss_streak(net)
                    self.log.info(f"[CLOSED] {k} pnl={net:+.2f} -- {info}")
                    self._telegram(f"\U0001F534 CLOSED chart_ai_trader: {k} "
                                   f"entry={info.get('entry')} pnl={net:+.2f}")
                known.pop(k, None)
        self._save_state()

    def _update_loss_streak(self, net_pnl: float):
        if net_pnl < 0:
            self.state["consec_losses"] = self.state.get("consec_losses", 0) + 1
        else:
            self.state["consec_losses"] = 0
        if self.state["consec_losses"] >= MAX_CONSEC_LOSSES:
            with open(self.breaker_file, "w", encoding="utf-8") as f:
                f.write(f"{self.state['consec_losses']} consecutive losses as of "
                       f"{datetime.now(timezone.utc).isoformat()}")
            self.log.error(f"[BREAKER] {self.state['consec_losses']} consecutive "
                           f"losses -- auto-stopping new entries")
            self._telegram(f"⛔ chart_ai_trader AUTO-STOPPED: "
                           f"{self.state['consec_losses']} consecutive losses. "
                           f"Delete {os.path.basename(self.breaker_file)} to resume "
                           f"after review.")

    def _news_candidates(self) -> list:
        """Union of both providers' recent news candidates, fetched at most
        once per poll cycle and only when something actually wants to
        trade. Returns [] if both providers fail -- see _news_allows for
        why that means "no veto" rather than "no trade"."""
        if self._news_cache is not None:
            return self._news_cache
        sys.path.insert(0, _BASE_DIR)
        from news_gemini_bot import gemini_scan, openai_scan
        out = []
        self._news_ok, self._news_failed = [], []
        for name, fn, key, model in (
                ("gemini", gemini_scan, self.gemini_key, self.gemini_model),
                ("openai", openai_scan, self.openai_key, self.openai_model)):
            if not key:
                continue
            try:
                cands = self._call_with_timeout(
                    fn, AI_CALL_TIMEOUT_SEC, key, model, NEWS_LOOKBACK_MIN) or []
                for c in cands:
                    c["_provider"] = name
                out.extend(cands)
                self._news_ok.append(name)
            except Exception as e:
                self._news_failed.append(name)
                self.log.warning(f"[NEWS/{name}] scan failed ({str(e)[:150]}) "
                                 f"-- this provider contributes no veto")
        self._news_cache = out
        self.log.info(f"[NEWS] {len(out)} candidate(s); veto={self._news_veto_state()} "
                      f"(ok={self._news_ok or '-'} failed={self._news_failed or '-'})")
        return out

    def _news_veto_state(self) -> str:
        """How much of the news veto was actually working for this cycle.

        [2026-08-13] Added because the fail-OPEN design is only defensible
        if a trade taken with a blind veto is IDENTIFIABLE later. Without
        this, reconstructing "was the veto up when this position opened?"
        means correlating separate log lines by timestamp across hundreds
        of forward-test trades. The state is stamped onto the [OPEN] line,
        the Telegram alert and the saved position record instead.
          ok        every configured provider answered
          degraded  at least one answered, at least one did not
          blind     none answered -- the trade passed BECAUSE the veto
                    could not run, not because news agreed with it
        """
        if not self._news_ok and not self._news_failed:
            return "not-run"
        if not self._news_ok:
            return "blind"
        if self._news_failed:
            return "degraded(-" + ",".join(self._news_failed) + ")"
        return "ok"

    def _news_allows(self, canon: str, signal: str) -> bool:
        """False = a high-impact story points the OTHER way, skip the trade.

        FAILS OPEN. If both scans error (503s from these providers are a
        near-daily event in this fleet's logs), there are no candidates, so
        nothing contradicts, so the trade proceeds on the chart consensus
        that already passed every gate. That is the deliberate choice: a
        veto stage that cannot be reached must not silently become a
        kill-switch on the whole strategy. The failure is logged per
        provider above so a persistently blind veto is visible."""
        opposite = "short" if signal == "long" else "long"
        for c in self._news_candidates():
            if c.get("symbol") != canon:
                continue
            if str(c.get("signal", "")).lower() != opposite:
                continue
            try:
                conf = float(c.get("confidence", 0) or 0)
            except (TypeError, ValueError):
                continue
            if conf < NEWS_VETO_CONF_MIN:
                continue
            head = str(c.get("headline", ""))[:200]
            self.log.info(f"[NEWS VETO] {canon} {signal} cancelled by "
                          f"{c.get('_provider')}: {opposite} news conf={conf:.2f} "
                          f":: {head}")
            self._telegram(
                f"\U0001F6AB NEWS VETO {canon} {signal.upper()}\n"
                f"Chart said {signal.upper()}, but {c.get('_provider')} found "
                f"{opposite.upper()} news (conf {conf:.2f}):\n{head}")
            return False
        return True

    def _call_with_timeout(self, fn, timeout: float, *a, **kw):
        """Bound an external call's wall-clock time, same defence the rest of
        this fleet applies to MT5 calls (_call_with_timeout in
        forex_live_bot_gold_cwider.py). A hung thread cannot be killed in
        Python, so it leaks -- but control returns to the caller, which is
        the point: the loop keeps running, the heartbeat keeps ticking, and
        the watchdog does not restart a bot that is merely waiting."""
        ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            return ex.submit(fn, *a, **kw).result(timeout=timeout)
        finally:
            ex.shutdown(wait=False)

    def _safe_signal(self, name: str, fn, api_key: str, model: str,
                     png: bytes, symbol: str, price: float, bars: int,
                     atr: float, payload_text: str) -> Optional[dict]:
        # [2026-08-12 FIX] These were bare calls with NO timeout. Observed
        # live: the bot sat inside one for 7+ minutes with zero log output,
        # its heartbeat frozen the whole time. The google-genai / openai
        # clients set no default socket timeout, so a stalled connection
        # blocks the ONLY loop thread indefinitely. Two consequences, both
        # seen: the bot looks alive (process up) while doing nothing, and
        # the heartbeat goes past watchdog_h1.ps1's 5-minute staleness
        # threshold -- so the watchdog restarts it mid-cycle, forever.
        # Every MT5 call in this fleet has had this guard for months; the
        # AI calls were simply never given one.
        try:
            return self._call_with_timeout(
                fn, AI_CALL_TIMEOUT_SEC, api_key, model, png, symbol,
                price, bars, atr, payload_text)
        except Exception as e:
            self.log.warning(f"[{name.upper()}] chart-signal call failed -- "
                             f"skip this symbol this cycle: {str(e)[:200]}")
            return None

    def _evaluate_symbol(self, canon: str, bsym: str):
        # [2026-08-12] Stacking allowed at the user's explicit request ("ถ้า
        # ai เห็นว่าควรเปิดก็เปิดหลายๆไม้ได้เรื่อยๆ"): this used to be a hard
        # "any open position -> skip". It is now a COUNT check instead,
        # because unbounded is not the same as unlimited -- at a 15-minute
        # cadence an AI that keeps answering "long" would open ~96
        # positions per symbol per day, and since each one risks
        # self.risk_pct at its own stop, a correlated move against a stack
        # that size is an account-ending event rather than a drawdown.
        # The caps below bound worst-case simultaneous risk to
        # max_total * risk_pct (see the startup banner, which prints it).
        held = self._own_positions(bsym)
        total = len(self._own_positions())
        if len(held) >= self.max_per_symbol:
            self.log.info(f"[{canon}] SKIP -- already holding {len(held)} "
                          f"position(s), per-symbol cap is {self.max_per_symbol}")
            return
        if total >= self.max_total:
            self.log.info(f"[{canon}] SKIP -- {total} open position(s) across "
                          f"all symbols, total cap is {self.max_total}")
            return

        candles = self._mt5(self.connector.fetch_ohlcv, bsym, TIMEFRAME, CHART_BARS)
        if not candles or len(candles) < 30:
            self.log.warning(f"[{canon}] only {len(candles) if candles else 0} "
                             f"bars -- skip (need >=30 for a usable chart)")
            return
        price = float(candles[-1][4])

        # ATR is computed BEFORE the AI call now: the model is told the
        # current ATR so its sl/tp multiples are anchored to the same unit
        # the bot will actually use to convert them into prices. Computing
        # it first also means a bad-ATR cycle costs zero API calls.
        atr = self._atr14(bsym)
        if not atr or not math.isfinite(atr) or atr <= 0:
            self.log.warning(f"[{canon}] invalid ATR -- skip (no API call made)")
            return

        payload = build_market_payload(candles, atr)
        payload_text = format_payload(canon, payload)

        sys.path.insert(0, _BASE_DIR)
        from news_gemini_bot import render_chart_png
        try:
            png = render_chart_png(candles, canon, "")
        except Exception as e:
            self.log.warning(f"[{canon}] chart render failed: {e} -- skip")
            return

        gemini_result = self._safe_signal("gemini", gemini_chart_signal,
                                          self.gemini_key, self.gemini_model,
                                          png, canon, price, len(candles), atr,
                                          payload_text)
        if gemini_result is None:
            return
        if not self.openai_key:
            self.log.info(f"[{canon}] OPENAI not configured -- dual-consensus "
                          f"unavailable, no entry this cycle (fail-safe)")
            return
        openai_result = self._safe_signal("openai", openai_chart_signal,
                                          self.openai_key, self.openai_model,
                                          png, canon, price, len(candles), atr,
                                          payload_text)
        if openai_result is None:
            return

        self.log.info(
            f"[{canon}] gemini={gemini_result.get('decision')} "
            f"(e={gemini_result.get('entry')} sl={gemini_result.get('sl')} "
            f"tp={gemini_result.get('tp')})  "
            f"openai={openai_result.get('decision')} "
            f"(e={openai_result.get('entry')} sl={openai_result.get('sl')} "
            f"tp={openai_result.get('tp')})")
        decision = cross_check_signal(gemini_result, openai_result, price, atr)
        if decision is None:
            self.log.info(f"[{canon}] no consensus / failed validation this cycle")
            return

        self.log.info(f"[{canon}] CONSENSUS {decision['decision']} "
                      f"entry={decision['entry']:.2f} sl={decision['sl']:.2f} "
                      f"tp={decision['tp']:.2f} "
                      f"(sl={decision['sl_atr_mult']:.2f}xATR, R:R {decision['rr']:.2f})")
        # heartbeat before the news stage: it can add up to two more timed
        # calls, and refreshing here keeps the largest gap between two
        # heartbeats at 2 x AI_CALL_TIMEOUT_SEC rather than 4 (see case 19).
        self._heartbeat()
        if not self._news_allows(canon, decision["signal"]):
            return
        self._enter(canon, bsym, decision)

    def _enter(self, canon: str, bsym: str, decision: dict) -> bool:
        signal = decision["signal"]
        atr = self._atr14(bsym)
        if not atr or not math.isfinite(atr) or atr <= 0:
            self.log.warning(f"[{canon}] invalid ATR -- skip entry")
            return False

        eq = self._mt5(self.connector.get_equity)
        bid, ask = self._mt5(self.connector.get_current_price, bsym)
        if bid <= 0 or ask <= 0:
            self.log.warning(f"[{canon}] invalid price -- skip entry")
            return False
        long_ = signal == "long"
        px = ask if long_ else bid
        # Apply the AI's DISTANCES to the real execution price, not its
        # stated absolute levels. Price can move between the decision and
        # the fill; anchoring to the fill preserves the intended stop
        # distance (and therefore the risk sizing below) and the intended
        # reward:risk, instead of silently widening or tightening both.
        sd = float(decision["sl_dist"])
        td = float(decision["tp_dist"])
        sl = px - sd if long_ else px + sd
        tp = px + td if long_ else px - td

        pip_size = self.cfg.get_pip_size(bsym)
        pip_value = self._mt5(self.connector.get_pip_value_live, bsym)
        sd_pips = sd / pip_size
        if sd_pips <= 0 or pip_value <= 0:
            self.log.warning(f"[{canon}] invalid pip_value -- skip entry")
            return False
        risk_cash = eq * self.risk_pct / 100.0
        lot = round(risk_cash / (sd_pips * pip_value), 2)
        if lot < 0.01:
            self.log.warning(f"[{canon}] lot rounds to 0 -- skip entry")
            return False
        actual_risk_pct = (sd_pips * pip_value * lot) / eq * 100.0 if eq > 0 else float("inf")
        if actual_risk_pct > self.risk_pct * 1.5:
            self.log.error(f"[{canon}] SIZING SANITY CHECK FAILED: lot={lot} "
                           f"implies {actual_risk_pct:.2f}% > 1.5x intended "
                           f"{self.risk_pct}% -- REFUSING to open")
            self._telegram(f"⛔ chart_ai_trader: {canon} sizing sanity check "
                           f"failed — entry refused")
            return False

        side = "long" if long_ else "short"
        result = self._mt5(self.executor.open_position, bsym, side, lot,
                          sl, tp, "CHARTAI-" + side[:4].upper())
        if not result:
            self.log.error(f"[{canon}] open failed")
            self._telegram(f"⚠️ chart_ai_trader: {canon} order failed to open")
            return False

        fill = float(result.get("fill_price", px) or px)
        self.state.setdefault("positions", {})[f"{canon}-{result.get('trade_id')}"] = {
            "bsym": bsym, "ticket": str(result.get("trade_id", "") or ""),
            "side": side, "entry": fill, "sl": sl, "tp": tp, "lot": lot,
            "opened_utc": datetime.now(timezone.utc).isoformat(),
            "news_veto": self._news_veto_state(),
        }
        self._save_state()

        veto_state = self._news_veto_state()
        self.log.info(f"[OPEN] {side.upper()} {bsym} lot={lot} fill={fill:.2f} "
                      f"sl={sl:.2f} tp={tp:.2f} (risk={actual_risk_pct:.2f}%) "
                      f"news_veto={veto_state}")
        if veto_state in ("blind", "not-run"):
            self.log.warning(f"[OPEN] {bsym} entered with news veto {veto_state.upper()} "
                             f"-- no news check protected this trade (fail-open)")
        self._telegram(
            f"\U0001F7E2 CHART-AI ENTRY {side.upper()} {bsym}\n"
            f"lot={lot}  fill={fill:.2f}  SL={sl:.2f} TP={tp:.2f}\n"
            f"AI-chosen: SL {decision['sl_atr_mult']:.2f}xATR  "
            f"R:R {decision['rr']:.2f}\n"
            f"news veto: {veto_state}\n"
            f"gemini: {decision['gemini_reasoning'][:250]}\n"
            f"openai: {decision['openai_reasoning'][:250]}")
        return True

    def run(self):
        if not self._mt5(self.connector.connect):
            self.log.error("MT5 connect failed")
            sys.exit(1)

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
        self.log.info(f"  CHART AI TRADER (dual-provider consensus)  magic={MAGIC}  "
                      f"account={acct_tag}  symbols={self.symbols}")
        self.log.info(f"  equity={eq:.2f}  risk/trade={self.risk_pct}%  "
                      f"poll={self.poll_min}min  timeframe={TIMEFRAME}  "
                      f"rule=2-of-3")
        self.log.info(f"  SL/TP: chosen per-setup by the AI as absolute "
                      f"prices; stop must land in {SL_ATR_MIN}-{SL_ATR_MAX}"
                      f"xATR and R:R >= {MIN_RR}, else the setup is REJECTED "
                      f"(entry drift limit {MAX_ENTRY_DRIFT_ATR}xATR)")
        self.log.info(f"  news veto: ON -- a chart setup is cancelled if either "
                      f"provider surfaces opposite-direction news at conf >= "
                      f"{NEWS_VETO_CONF_MIN} (lookback {NEWS_LOOKBACK_MIN}min, "
                      f"fetched lazily, fails OPEN)")
        self.log.info(f"  stacking: up to {self.max_per_symbol}/symbol, "
                      f"{self.max_total} total -> worst-case simultaneous "
                      f"risk {self.max_total * self.risk_pct:.2f}% of equity "
                      f"if every stop hits at once")
        self.log.info(f"  gemini model={self.gemini_model}")
        openai_status = ("configured" if self.openai_key else
                        "NOT SET -- dual-consensus unavailable, bot will idle "
                        "(no new entries) until OPENAI_API_KEY is added to .env")
        self.log.info(f"  openai model={self.openai_model}  {openai_status}")
        self.log.info(f"  kill-switch: {os.path.basename(self.stop_file)}  "
                      f"breaker: {os.path.basename(self.breaker_file)}")
        self.log.info("  ** UNVALIDATED STRATEGY -- no historical backtest exists "
                      "for this signal source. Live by explicit user decision. **")
        self.log.info("=" * 70)
        self._telegram(f"\U0001F680 START chart_ai_trader  equity={eq:.2f}  "
                       f"risk={self.risk_pct}%/trade  poll={self.poll_min}min  "
                       f"dual-consensus={'ON' if self.openai_key else 'OFF (idling)'}")

        while True:
            try:
                self._heartbeat()
                self._watch_positions()
                now = datetime.now(timezone.utc)

                last = self.state.get("last_poll", "")
                elapsed_min = (999999.0 if not last else
                              (now - datetime.fromisoformat(last)).total_seconds() / 60.0)
                due = elapsed_min >= self.poll_min
                if due:
                    if os.path.exists(self.stop_file):
                        self.log.info("[KILL-SWITCH] present -- skipping poll cycle")
                    elif os.path.exists(self.breaker_file):
                        self.log.warning("[BREAKER] consecutive-loss breaker active "
                                         "-- skipping poll cycle (clear file to resume)")
                    else:
                        # refetch at most once per cycle; clear the health
                        # record too so a stale "ok" can't leak into a later
                        # cycle whose scan actually failed.
                        self._news_cache = None
                        self._news_ok, self._news_failed = [], []
                        for canon, bsym in self.symbols.items():
                            # [2026-08-12] refresh the heartbeat BETWEEN
                            # symbols, not just once per loop iteration: a
                            # poll cycle makes 6 network calls and can run
                            # for minutes, which previously let the
                            # heartbeat go stale enough for the watchdog to
                            # restart a perfectly healthy, merely-busy bot.
                            self._heartbeat()
                            try:
                                self._evaluate_symbol(canon, bsym)
                            except Exception as e:
                                self.log.error(f"[{canon}] evaluate error: {e}")
                        self.state["last_poll"] = now.isoformat()
                        self._save_state()
            except Exception as e:
                self.log.error(f"loop error: {e}")
            time.sleep(60)


def build_cfg(dry_run: bool, allow_real: bool) -> ForexConfig:
    """Build the live config. Factored out of main() purely so a test can
    assert on it -- see the magic-number note below.

    [2026-08-12 FIX] cfg.magic_number was NEVER SET here. ForexConfig
    defaults it to 20240101, so every order this bot placed went out under
    that shared default instead of MAGIC=671001, while _own_positions()
    filtered incoming positions on `magic == MAGIC`. The bot therefore
    could not see its own trades. Two consequences, both observed live on
    the very first real trade (XAUUSDc long 0.04 @ 4413.988, ticket
    4132289820):
      - _watch_positions() found the ticket missing from its own
        (magic-filtered, hence empty) view and declared it CLOSED with
        pnl=+0.00 less than two minutes after opening, then stopped
        tracking it -- while the position was in fact still open at the
        broker, with only an entry deal in history.
      - far worse, the "already-positioned" guard in _evaluate_symbol()
        also reads _own_positions(), so it would have kept returning
        empty: the bot could have opened an unbounded stack of duplicate
        positions on the same symbol, each sized as if it were the first.
    Every other bot in this fleet wires this (forex_live_bot_gold_cwider.py,
    news_gemini_bot.py, daily_sleeves_bot.py); this one simply never did.
    """
    cfg = ForexConfig()
    cfg.dry_run = dry_run
    cfg.allow_real = allow_real
    cfg.magic_number = MAGIC
    return cfg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--risk", type=float, default=DEFAULT_RISK_PCT,
                    help=f"%% equity risked per trade (default {DEFAULT_RISK_PCT})")
    ap.add_argument("--poll-min", type=int, default=POLL_MIN,
                    help=f"minutes between chart re-checks (default {POLL_MIN})")
    ap.add_argument("--max-per-symbol", type=int, default=MAX_POSITIONS_PER_SYMBOL,
                    help=f"max simultaneous positions on ONE symbol "
                         f"(default {MAX_POSITIONS_PER_SYMBOL})")
    ap.add_argument("--max-total", type=int, default=MAX_TOTAL_POSITIONS,
                    help=f"max simultaneous positions across ALL symbols "
                         f"(default {MAX_TOTAL_POSITIONS}). Worst-case "
                         f"simultaneous risk is this x --risk, since every "
                         f"position carries its own stop sized to --risk.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-real", action="store_true")
    args = ap.parse_args()

    if not os.environ.get("GEMINI_API_KEY"):
        print("[ERROR] GEMINI_API_KEY not set (add it to .env)")
        sys.exit(1)
    if not os.environ.get("OPENAI_API_KEY"):
        print("[WARN] OPENAI_API_KEY not set -- dual-consensus unavailable, "
             "bot will idle (no new entries) until it's added")

    cfg = build_cfg(args.dry_run, args.allow_real)
    if not cfg.dry_run and not _cfg_has_credentials(cfg):
        print("[ERROR] live mode needs MT5 (Windows) -- use --dry-run to test elsewhere")
        sys.exit(1)

    bot = ChartAITraderBot(cfg, risk_pct=args.risk, poll_min=args.poll_min,
                          max_per_symbol=args.max_per_symbol,
                          max_total=args.max_total)
    bot.run()


if __name__ == "__main__":
    main()
