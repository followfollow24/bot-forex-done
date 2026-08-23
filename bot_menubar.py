"""
bot_menubar.py — macOS Menu Bar App
ไอคอนใน menu bar บน Mac กดดูสถานะและควบคุมบอทได้เลย

ติดตั้ง (ครั้งเดียว):
  pip3 install rumps requests

แก้ค่าตรง CONFIG ด้านล่างก่อนรัน:
  VPS_IP  = IP ของ VPS (144.172.88.48)
  API_KEY = ค่าเดียวกับใน vps_bot_api.py

รัน:
  python3 bot_menubar.py

จะเห็นไอคอน 📊 ที่ menu bar ด้านบนขวาของ Mac
"""

import rumps
import requests
import threading
import webbrowser
import os

# ── ตั้งค่า ──────────────────────────────────────────────────────────────────
VPS_IP  = "144.172.88.48"
API_KEY = "forex-bot-2026"
REFRESH_SEC = 30   # รีเฟรชทุก 30 วินาที
DASH_PORT = 8501   # Streamlit dashboard port
# ─────────────────────────────────────────────────────────────────────────────

API     = f"http://{VPS_IP}:5000"
DASH_URL = f"http://{VPS_IP}:{DASH_PORT}"
HEADERS = {"X-API-Key": API_KEY}

SYMBOLS = ["XAUUSD", "EURUSD", "GBPUSD"]
LABELS  = {
    "XAUUSD": "GOLD",
    "EURUSD": "EUR/USD",
    "GBPUSD": "GBP/USD",
}


def api(method: str, path: str) -> dict | None:
    try:
        r = getattr(requests, method)(f"{API}{path}", headers=HEADERS, timeout=6)
        return r.json() if r.ok else None
    except Exception:
        return None


class BotApp(rumps.App):
    def __init__(self):
        super().__init__("📊", quit_button=None)
        self._data: dict = {}
        self._lock = threading.Lock()

        # ── menu items ────────────────────────────────────────────────────────
        self._rows: dict[str, rumps.MenuItem] = {}
        self._kill_btns: dict[str, rumps.MenuItem] = {}
        self._stop_btns: dict[str, rumps.MenuItem] = {}
        self._start_btns: dict[str, rumps.MenuItem] = {}

        menu_items = []
        for sym in SYMBOLS:
            row    = rumps.MenuItem(f"  {LABELS[sym]}")
            kill   = rumps.MenuItem(f"  🔒 Kill-switch {sym}",
                                    callback=lambda s, sym=sym: self._toggle_kill(sym))
            stop   = rumps.MenuItem(f"  ■ Stop {sym}",
                                    callback=lambda s, sym=sym: self._stop(sym))
            start  = rumps.MenuItem(f"  ▶ Start {sym}",
                                    callback=lambda s, sym=sym: self._start(sym))

            self._rows[sym]      = row
            self._kill_btns[sym] = kill
            self._stop_btns[sym] = stop
            self._start_btns[sym] = start

            menu_items += [row, kill, stop, start, None]

        menu_items += [
            rumps.MenuItem("─────────────────"),
            rumps.MenuItem("🌐 Open Dashboard", callback=self._open_dashboard),
            None,
            rumps.MenuItem("🔄 รีเฟรช", callback=self._manual_refresh),
            rumps.MenuItem("🚫 Kill ALL",  callback=self._kill_all),
            rumps.MenuItem("■  Stop ALL",  callback=self._stop_all),
            None,
            rumps.MenuItem("ออก", callback=rumps.quit_application),
        ]

        self.menu = menu_items
        self._refresh()   # initial load

    # ─────────────────────────────────────────────────────────────────────────
    # Refresh
    # ─────────────────────────────────────────────────────────────────────────

    @rumps.timer(REFRESH_SEC)
    def _auto_refresh(self, _):
        threading.Thread(target=self._refresh, daemon=True).start()

    def _manual_refresh(self, _):
        threading.Thread(target=self._refresh, daemon=True).start()

    def _open_dashboard(self, _):
        webbrowser.open(DASH_URL)

    def _refresh(self):
        data = api("get", "/status") or {}
        with self._lock:
            self._data = data
        rumps.App.menu.fget(self)  # ensure menu is accessible
        self._update_ui()

    # ─────────────────────────────────────────────────────────────────────────
    # UI update
    # ─────────────────────────────────────────────────────────────────────────

    def _update_ui(self):
        if not self._data:
            self.title = "📊❓"
            return

        statuses = []
        for sym in SYMBOLS:
            d = self._data.get(sym, {})
            running = d.get("running", False)
            killed  = d.get("killed", False)
            pnl     = d.get("day_pnl", 0.0)
            pos     = d.get("open_positions", 0)
            max_pos = d.get("max_positions", 1)
            trades  = d.get("trades_today", 0)

            # Status dot
            if not running:
                dot = "🔴"
            elif killed:
                dot = "🟡"
            else:
                dot = "🟢"

            # P&L color (rumps ไม่รองรับ color โดยตรง ใช้ +/- แทน)
            pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
            kill_str = " 🔒" if killed else ""

            row_title = (
                f"{dot}{kill_str}  {LABELS[sym]:<8}"
                f"  P&L {pnl_str:<10}"
                f"  Pos {pos}/{max_pos}"
                f"  {trades} trades"
            )
            self._rows[sym].title = row_title

            # Kill-switch button label
            self._kill_btns[sym].title = (
                f"  {'🔓 ปลด' if killed else '🔒 เปิด'} Kill-switch {sym}"
            )

            statuses.append((running, killed))

        # Update menubar icon
        all_run  = all(r for r, _ in statuses)
        some_run = any(r for r, _ in statuses)
        any_kill = any(k for _, k in statuses)

        if not some_run:
            self.title = "📊🔴"
        elif any_kill:
            self.title = "📊🟡"
        elif all_run:
            self.title = "📊🟢"
        else:
            self.title = "📊🟠"

    # ─────────────────────────────────────────────────────────────────────────
    # Actions
    # ─────────────────────────────────────────────────────────────────────────

    def _toggle_kill(self, symbol: str):
        with self._lock:
            killed = self._data.get(symbol, {}).get("killed", False)
        endpoint = f"/{'unkill' if killed else 'kill'}/{symbol}"
        threading.Thread(target=lambda: (api("post", endpoint), self._refresh()),
                         daemon=True).start()

    def _stop(self, symbol: str):
        result = rumps.alert(
            title=f"หยุด {symbol} bot?",
            message="Process จะถูก force-stop ทันที\nแนะนำกด Kill-switch ก่อนเสมอ",
            ok="หยุดเลย", cancel="ยกเลิก"
        )
        if result:
            threading.Thread(
                target=lambda: (api("post", f"/stop/{symbol}"), self._refresh()),
                daemon=True
            ).start()

    def _start(self, symbol: str):
        result = rumps.alert(
            title=f"เริ่ม {symbol} bot?",
            message=f"จะเปิด instance ใหม่ของ {symbol} บน VPS",
            ok="เริ่มเลย", cancel="ยกเลิก"
        )
        if result:
            threading.Thread(
                target=lambda: (api("post", f"/start/{symbol}"), self._refresh()),
                daemon=True
            ).start()

    def _kill_all(self, _):
        result = rumps.alert(
            title="Kill-switch ทั้งหมด?",
            message="บอททุกตัวจะไม่รับสัญญาณใหม่\n(ไม้ที่เปิดอยู่ยังรันต่อ)",
            ok="เปิด Kill-switch ทั้งหมด", cancel="ยกเลิก"
        )
        if result:
            for sym in SYMBOLS:
                api("post", f"/kill/{sym}")
            self._refresh()

    def _stop_all(self, _):
        result = rumps.alert(
            title="⚠️ หยุดทุก bot?",
            message="Process ทั้ง 3 จะถูก force-stop\nทำแน่ใจว่าไม่มี position เปิดอยู่",
            ok="หยุดทั้งหมด", cancel="ยกเลิก"
        )
        if result:
            for sym in SYMBOLS:
                api("post", f"/stop/{sym}")
            self._refresh()


if __name__ == "__main__":
    BotApp().run()
