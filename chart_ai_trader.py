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
VETO stage): Gemini and OpenAI each independently look at the SAME chart
image and each independently answer long/short/none + confidence. A trade
only opens if BOTH agree on the same non-none direction with confidence
>= CONF_MIN. This is a full trading-decision gate (unlike
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
fixed: both models choose sl_atr_mult/tp_atr_mult per setup, the two are
averaged, clamped to a sane band, and rejected unless the resulting
reward:risk clears MIN_RR. Because lot sizing divides by the actual stop
distance, a wider AI-chosen stop produces a smaller lot -- the $-risk per
trade stays pinned to --risk no matter what the models pick, which is what
makes delegating the stop safe.

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

# [2026-08-12] Lowered 0.70 -> 0.60 at the user's request. Observed live:
# both models called XAUUSD "long" at 0.62/0.60 on the first real cycle and
# no trade fired, i.e. 0.70 was binding hard enough that this bot might
# almost never trade. 0.60 is deliberately paired with the R:R floor below
# so the loosened confidence gate isn't the ONLY thing standing between a
# mediocre setup and a live order.
CONF_MIN = 0.60

TIMEFRAME = "15m"    # [2026-08-12] was 1h, changed at the user's request.
CHART_BARS = 160     # 160 M15 bars = ~40h of context. On H1 this was 120
                     # bars (~5 days); M15 needs more bars to still show a
                     # meaningful stretch of market rather than one session.
POLL_MIN = 15        # one M15 bar -- matches the entry timeframe, same way
                     # the H1 version polled hourly.

# [2026-08-12] SL/TP are now chosen by the AI per-setup rather than fixed
# at 2.5/15 xATR, at the user's request ("ให้ ai อิสระไม่ตายตัว").
# Expressed as ATR MULTIPLES, not absolute prices: scale-free, so the same
# numbers mean the same thing on gold, BTC and ETH, and the model cannot
# accidentally return a price on the wrong side of the market.
#
# Guardrails, because "free" must not mean "unbounded" with real money:
#   - every multiple is CLAMPED into a sane band (a model returning 0.01 or
#     500 must not become a live order)
#   - a minimum reward:risk is enforced; a setup the models themselves
#     score as worse than MIN_RR is not worth taking
#   - $-risk per trade is UNAFFECTED by any of this: lot sizing divides by
#     the actual SL distance, so a wider AI-chosen stop just means a
#     smaller lot, never a bigger loss. That property is what makes
#     handing SL choice to the model safe at all.
SL_ATR_MIN, SL_ATR_MAX = 0.5, 6.0
TP_ATR_MIN, TP_ATR_MAX = 1.0, 40.0
MIN_RR = 1.2

DEFAULT_RISK_PCT = 0.30
MAX_CONSEC_LOSSES = 3

# Wall-clock cap on a single AI call. Sized so a full cycle (3 symbols x 2
# providers) still cannot exceed the watchdog's 5-minute staleness window
# once the per-symbol heartbeat below is taken into account: the heartbeat
# is refreshed between symbols, so the longest gap between two heartbeats
# is one symbol's two calls = 2 x AI_CALL_TIMEOUT_SEC = 120s < 300s.
AI_CALL_TIMEOUT_SEC = 60


CHART_SIGNAL_SCHEMA = {
    "type": "object",
    "properties": {
        "signal": {"type": "string"},   # "long" | "short" | "none"
        "confidence": {"type": "number"},
        "sl_atr_mult": {"type": "number"},
        "tp_atr_mult": {"type": "number"},
        "reasoning": {"type": "string"},
    },
    "required": ["signal", "confidence", "sl_atr_mult", "tp_atr_mult", "reasoning"],
}

ENTRY_PROMPT_TEMPLATE = """You are a discretionary technical trader looking \
at an M15 (15-minute) candlestick chart of {symbol}, covering roughly the \
last {bars} bars. Current price: {price}. The current ATR(14) on this \
timeframe is {atr} -- use it as your unit of "normal move size". EMA20 \
(blue) and EMA50 (orange) are shown when there is enough history.

Decide: is there a clear, high-quality LONG or SHORT setup visible RIGHT \
NOW on this chart -- or is there nothing worth trading?

Most cycles should be "none". Only call a direction when the setup is \
genuinely clean: a well-defined trend with a good entry point (e.g. a \
pullback to a moving average in an established trend, a clear breakout \
with follow-through, a clean reversal pattern at an obvious level) -- not \
just any price movement. Do not force a call because you were asked a \
question; a real discretionary trader passes on most setups. Being \
undecided means "none".

If (and only if) you do call a direction, also choose where the stop-loss \
and take-profit belong FOR THIS SPECIFIC SETUP, expressed as multiples of \
ATR:

sl_atr_mult: distance from current price to your stop, in ATRs. Put it \
where the setup would be genuinely invalidated -- beyond the swing \
high/low or structure level that would prove you wrong -- not at an \
arbitrary round number. Typical range {sl_min}-{sl_max}.
tp_atr_mult: distance from current price to your target, in ATRs. Put it \
at the next real structure/resistance/support the move can realistically \
reach. Typical range {tp_min}-{tp_max}.

Your target must be worth the risk: tp_atr_mult should be at least \
{min_rr}x sl_atr_mult. If the only sensible stop for this setup is so wide \
that the realistic target is not worth it, the honest answer is "none".

If signal is "none", still return numbers for sl_atr_mult and tp_atr_mult \
(they will be ignored) -- for example 1 and 2.

signal: "long", "short", or "none".
confidence: 0.0-1.0, your genuine calibrated confidence in this specific \
setup -- most real setups should score well under 0.8; reserve high \
confidence for a genuinely clean, textbook setup.
reasoning: one to two sentences on what you see, and where you placed the \
stop and target and why.

Respond ONLY via the provided JSON schema."""


def _fmt_prompt(symbol: str, bars: int, price: float, atr: float) -> str:
    return ENTRY_PROMPT_TEMPLATE.format(
        symbol=symbol, bars=bars, price=f"{price:.2f}", atr=f"{atr:.2f}",
        sl_min=SL_ATR_MIN, sl_max=SL_ATR_MAX,
        tp_min=TP_ATR_MIN, tp_max=TP_ATR_MAX, min_rr=MIN_RR)


def gemini_chart_signal(api_key: str, model: str, png: bytes, symbol: str,
                        price: float, bars: int, atr: float) -> dict:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    prompt = _fmt_prompt(symbol, bars, price, atr)
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
                        price: float, bars: int, atr: float) -> dict:
    import base64
    from openai import OpenAI
    # reuse the strict-schema converter already built and tested for
    # news_gemini_bot.py rather than duplicating that subtle logic here.
    sys.path.insert(0, _BASE_DIR)
    from news_gemini_bot import _openai_strict_schema

    client = OpenAI(api_key=api_key)
    prompt = _fmt_prompt(symbol, bars, price, atr)
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


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def cross_check_signal(gemini_result: dict, openai_result: dict) -> Optional[dict]:
    """Returns a merged decision dict only if both providers independently
    agree on the same non-none direction, each individually clearing
    CONF_MIN -- pure function, testable without any API calls. Returns
    None on any disagreement, any "none", or either confidence too low.

    [2026-08-12] Also merges the two models' AI-chosen SL/TP multiples:
    each is averaged (both models agreed on direction, so averaging their
    structure read is the natural consensus), then CLAMPED into the
    configured band, then the resulting reward:risk is checked against
    MIN_RR. A non-finite or non-positive multiple from either model is
    treated as a malformed response and rejects the trade rather than
    being silently coerced -- a bad stop distance is the one input that
    directly scales a live loss."""
    g_sig = gemini_result.get("signal", "none")
    o_sig = openai_result.get("signal", "none")
    g_conf = float(gemini_result.get("confidence", 0) or 0)
    o_conf = float(openai_result.get("confidence", 0) or 0)
    if g_sig not in ("long", "short") or o_sig not in ("long", "short"):
        return None
    if g_sig != o_sig:
        return None
    if g_conf < CONF_MIN or o_conf < CONF_MIN:
        return None

    try:
        g_sl = float(gemini_result.get("sl_atr_mult"))
        o_sl = float(openai_result.get("sl_atr_mult"))
        g_tp = float(gemini_result.get("tp_atr_mult"))
        o_tp = float(openai_result.get("tp_atr_mult"))
    except (TypeError, ValueError):
        return None
    vals = (g_sl, o_sl, g_tp, o_tp)
    if any((not math.isfinite(v)) or v <= 0 for v in vals):
        return None

    sl_mult = _clamp((g_sl + o_sl) / 2.0, SL_ATR_MIN, SL_ATR_MAX)
    tp_mult = _clamp((g_tp + o_tp) / 2.0, TP_ATR_MIN, TP_ATR_MAX)
    rr = tp_mult / sl_mult
    if rr < MIN_RR:
        return None

    return {
        "signal": g_sig,
        "confidence": min(g_conf, o_conf),
        "sl_atr_mult": sl_mult,
        "tp_atr_mult": tp_mult,
        "rr": rr,
        "gemini_reasoning": gemini_result.get("reasoning", ""),
        "openai_reasoning": openai_result.get("reasoning", ""),
    }


class ChartAITraderBot:
    def __init__(self, cfg: ForexConfig, risk_pct: float, poll_min: int):
        self.cfg = cfg
        self.risk_pct = risk_pct
        self.poll_min = poll_min

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
                     atr: float) -> Optional[dict]:
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
                price, bars, atr)
        except Exception as e:
            self.log.warning(f"[{name.upper()}] chart-signal call failed -- "
                             f"skip this symbol this cycle: {str(e)[:200]}")
            return None

    def _evaluate_symbol(self, canon: str, bsym: str):
        if self._own_positions(bsym):
            self.log.info(f"[{canon}] SKIP already-positioned")
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

        sys.path.insert(0, _BASE_DIR)
        from news_gemini_bot import render_chart_png
        try:
            png = render_chart_png(candles, canon, "")
        except Exception as e:
            self.log.warning(f"[{canon}] chart render failed: {e} -- skip")
            return

        gemini_result = self._safe_signal("gemini", gemini_chart_signal,
                                          self.gemini_key, self.gemini_model,
                                          png, canon, price, len(candles), atr)
        if gemini_result is None:
            return
        if not self.openai_key:
            self.log.info(f"[{canon}] OPENAI not configured -- dual-consensus "
                          f"unavailable, no entry this cycle (fail-safe)")
            return
        openai_result = self._safe_signal("openai", openai_chart_signal,
                                          self.openai_key, self.openai_model,
                                          png, canon, price, len(candles), atr)
        if openai_result is None:
            return

        self.log.info(f"[{canon}] gemini={gemini_result.get('signal')}/"
                      f"{gemini_result.get('confidence')}  "
                      f"openai={openai_result.get('signal')}/"
                      f"{openai_result.get('confidence')}")
        decision = cross_check_signal(gemini_result, openai_result)
        if decision is None:
            self.log.info(f"[{canon}] no consensus this cycle")
            return

        self.log.info(f"[{canon}] CONSENSUS {decision['signal']} "
                      f"conf={decision['confidence']:.2f}")
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
        # AI-chosen distances (already averaged, clamped and RR-checked in
        # cross_check_signal). Fall back to the old fixed shape only if a
        # caller somehow passes a decision without them.
        sl_mult = float(decision.get("sl_atr_mult") or 2.5)
        tp_mult = float(decision.get("tp_atr_mult") or 15.0)
        sd = sl_mult * atr
        sl = px - sd if long_ else px + sd
        td = tp_mult * atr
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
        }
        self._save_state()

        self.log.info(f"[OPEN] {side.upper()} {bsym} lot={lot} fill={fill:.2f} "
                      f"sl={sl:.2f} tp={tp:.2f} (risk={actual_risk_pct:.2f}%)")
        self._telegram(
            f"\U0001F7E2 CHART-AI ENTRY {side.upper()} {bsym}\n"
            f"lot={lot}  fill={fill:.2f}  SL={sl:.2f} TP={tp:.2f}\n"
            f"AI-chosen: SL {sl_mult:.2f}xATR / TP {tp_mult:.2f}xATR "
            f"(R:R {decision.get('rr', tp_mult / sl_mult):.2f})\n"
            f"conf={decision['confidence']:.2f}\n"
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
                      f"conf_min={CONF_MIN}")
        self.log.info(f"  SL/TP: chosen per-setup by the AI, clamped to "
                      f"SL {SL_ATR_MIN}-{SL_ATR_MAX}xATR / TP {TP_ATR_MIN}-"
                      f"{TP_ATR_MAX}xATR, minimum R:R {MIN_RR}")
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--risk", type=float, default=DEFAULT_RISK_PCT,
                    help=f"%% equity risked per trade (default {DEFAULT_RISK_PCT})")
    ap.add_argument("--poll-min", type=int, default=POLL_MIN,
                    help=f"minutes between chart re-checks (default {POLL_MIN})")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-real", action="store_true")
    args = ap.parse_args()

    if not os.environ.get("GEMINI_API_KEY"):
        print("[ERROR] GEMINI_API_KEY not set (add it to .env)")
        sys.exit(1)
    if not os.environ.get("OPENAI_API_KEY"):
        print("[WARN] OPENAI_API_KEY not set -- dual-consensus unavailable, "
             "bot will idle (no new entries) until it's added")

    cfg = ForexConfig()
    cfg.dry_run = args.dry_run
    cfg.allow_real = args.allow_real
    if not cfg.dry_run and not _cfg_has_credentials(cfg):
        print("[ERROR] live mode needs MT5 (Windows) -- use --dry-run to test elsewhere")
        sys.exit(1)

    bot = ChartAITraderBot(cfg, risk_pct=args.risk, poll_min=args.poll_min)
    bot.run()


if __name__ == "__main__":
    main()
