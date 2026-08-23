#!/usr/bin/env python3
"""
menubar_bot_monitor.py — XAUUSD Bot Menu Bar Monitor (macOS)

Install (once):
  pip3 install rumps requests Pillow

Run:
  python3 menubar_bot_monitor.py
"""
import io
import json
import webbrowser
from datetime import datetime

import rumps
import requests
from PIL import Image, ImageDraw

# ── Config ────────────────────────────────────────────────────────────────────
FLASK_URL = "http://144.172.88.48:5000/status"
FLASK_KEY = "forex-bot-2026"
DASH_URL  = "http://144.172.88.48:8501/"
POLL_SEC  = 30

# ── Icon generator (pixel robot, 36×36 for Retina) ───────────────────────────
def make_icon(gold_on=True, cyan_on=True, warn=False) -> str:
    """Draw a tiny pixel robot. Returns path to temp PNG."""
    import tempfile, os
    SIZE = 36
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d   = ImageDraw.Draw(img)

    GOLD = (255, 215,   0, 255) if gold_on else (80, 60, 0, 255)
    CYAN = (  0, 229, 255, 255) if cyan_on else (0,  60, 80, 255)
    WARN = (255, 170,   0, 255)
    BG   = (  8,  16,  30, 255)
    DARK = (  2,   6,  12, 255)

    # Antenna
    d.rectangle([16, 0, 19, 3],  fill=GOLD if gold_on else WARN if warn else DARK)
    d.rectangle([17, 3, 18, 7],  fill=(50, 50, 80, 255))

    # Head
    d.rectangle([ 6,  7, 29, 19], fill=BG)
    d.rectangle([ 6,  7, 29,  7], fill=GOLD)   # top border
    d.rectangle([ 6,  7,  6, 19], fill=GOLD)   # left
    d.rectangle([29,  7, 29, 19], fill=GOLD)   # right
    d.rectangle([ 6, 19, 29, 19], fill=GOLD)   # bottom

    # Eyes
    d.rectangle([10, 10, 14, 14], fill=DARK)
    d.rectangle([11, 11, 13, 13], fill=GOLD)
    d.rectangle([21, 10, 25, 14], fill=DARK)
    d.rectangle([22, 11, 24, 13], fill=GOLD)

    # Mouth dots
    for x in [12, 16, 20, 24]:
        d.rectangle([x, 17, x+1, 17], fill=GOLD)

    # Body
    d.rectangle([ 4, 21, 31, 31], fill=BG)
    d.rectangle([ 4, 21, 31, 21], fill=GOLD)
    d.rectangle([ 4, 21,  4, 31], fill=GOLD)
    d.rectangle([31, 21, 31, 31], fill=GOLD)
    d.rectangle([ 4, 31, 31, 31], fill=GOLD)

    # Body: GOLD chip (cwider), CYAN chip (mix_a)
    d.rectangle([ 8, 24, 13, 28], fill=GOLD)
    d.rectangle([16, 24, 21, 28], fill=CYAN)

    # Arms
    d.rectangle([ 0, 22,  3, 28], fill=(50, 50, 80, 255))
    d.rectangle([32, 22, 35, 28], fill=(50, 50, 80, 255))

    # Legs
    d.rectangle([ 8, 32, 13, 35], fill=BG)
    d.rectangle([ 8, 32, 13, 32], fill=GOLD)
    d.rectangle([22, 32, 27, 35], fill=BG)
    d.rectangle([22, 32, 27, 32], fill=GOLD)

    # Warning overlay
    if warn:
        d.rectangle([0, 0, SIZE-1, SIZE-1], outline=(255, 80, 0, 200), width=2)

    path = os.path.join(tempfile.gettempdir(), "botmon_icon.png")
    img.save(path)
    return path


# ── State ─────────────────────────────────────────────────────────────────────
_state = {
    "cwider": {"pnl": 0.0, "pos": 0, "max_pos": 2, "blocked": False,
               "running": None, "killed": False, "trades": 0},
    "mix_a":  {"pnl": 0.0, "pos": 0, "max_pos": 1, "blocked": False,
               "running": None, "killed": False, "trades": 0},
    "error":  None,
    "updated": None,
}


def fetch():
    """Poll Flask API → update _state."""
    try:
        r = requests.get(FLASK_URL, headers={"X-API-Key": FLASK_KEY}, timeout=5)
        data = r.json()
        xau = data.get("XAUUSD", {})
        _state["cwider"].update({
            "pnl":     float(xau.get("day_pnl",       0) or 0),
            "pos":     int(  xau.get("open_pos",       0) or 0),
            "blocked": bool( xau.get("day_blocked", False)),
            "running": bool( xau.get("running",     False)),
            "killed":  bool( xau.get("kill_switch", False)),
            "trades":  int(  xau.get("day_trades",    0) or 0),
        })
        _state["error"]   = None
        _state["updated"] = datetime.now()
    except Exception as e:
        _state["error"] = str(e)

    # Mix_A not in Flask API — mark unknown
    _state["mix_a"]["running"] = None


def bar_title() -> str:
    if _state["error"]:
        return "🤖⚠️"
    c   = _state["cwider"]
    pnl = c["pnl"]
    sgn = "+" if pnl >= 0 else ""
    blk = " 🔒" if c["blocked"] else ""
    dot = "●" if c["running"] else "○"
    return f"🤖{dot} {sgn}${pnl:.2f}{blk}"


# ── App ───────────────────────────────────────────────────────────────────────
class BotMonitorApp(rumps.App):
    def __init__(self):
        icon_path = make_icon()
        super().__init__(
            name="BotMonitor",
            title=None,
            icon=icon_path,
            template=False,          # colour icon (not monochrome template)
            quit_button="Quit BotMonitor",
        )

        self._mi_open    = rumps.MenuItem("📊 Open Dashboard", callback=self.open_dash)
        self._mi_cwider  = rumps.MenuItem("C_WIDER: loading…")
        self._mi_mixa    = rumps.MenuItem("MIX_A:  checking…")
        self._mi_refresh = rumps.MenuItem("↻  Refresh now",   callback=self.force_refresh)
        self._mi_ts      = rumps.MenuItem("Last update: —")
        self._mi_sep1    = None   # separator
        self._mi_sep2    = None

        self.menu = [
            self._mi_open,
            None,
            "── VARIANTS ──────────────",
            self._mi_cwider,
            self._mi_mixa,
            None,
            self._mi_refresh,
            self._mi_ts,
        ]

        self._timer = rumps.Timer(self._tick, POLL_SEC)
        self._timer.start()
        self._tick(None)

    # ── poll & refresh ────────────────────────────────────────────────────────
    def _tick(self, _):
        fetch()
        self._update_ui()

    def force_refresh(self, _):
        self._tick(None)

    def _update_ui(self):
        c = _state["cwider"]
        m = _state["mix_a"]

        # Menu bar title
        self.title = bar_title()

        # Update icon (colour reflects status)
        gold_on = c["running"] is True and not c["killed"]
        cyan_on = m["running"] is not False  # unknown → keep on
        warn    = bool(_state["error"]) or c["blocked"]
        self.icon = make_icon(gold_on=gold_on, cyan_on=cyan_on, warn=warn)

        # C_WIDER row
        run_sym = "🟢" if c["running"] else ("🔴" if c["running"] is False else "⚪")
        flags   = ("  🔒BLK" if c["blocked"] else "") + ("  ⚔KILL" if c["killed"] else "")
        self._mi_cwider.title = (
            f"{run_sym} C_WIDER  "
            f"{'+' if c['pnl']>=0 else ''}"
            f"${c['pnl']:.2f}  "
            f"pos {c['pos']}/{c['max_pos']}"
            f"{flags}"
        )

        # MIX_A row (not in Flask API → check via RDP)
        self._mi_mixa.title = (
            f"⚪ MIX_A   "
            f"${m['pnl']:.2f}  "
            f"pos {m['pos']}/{m['max_pos']}  "
            f"[see :8501]"
        )

        # Timestamp
        if _state["error"]:
            self._mi_ts.title = f"⚠️  {_state['error'][:50]}"
        elif _state["updated"]:
            self._mi_ts.title = "Updated: " + _state["updated"].strftime("%H:%M:%S")

    # ── actions ───────────────────────────────────────────────────────────────
    def open_dash(self, _):
        webbrowser.open(DASH_URL)


# ── entry ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    BotMonitorApp().run()
