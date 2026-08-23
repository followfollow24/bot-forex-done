"""
vps_bot_api.py — รันบน Windows VPS (Desktop)
ให้ Mac app เรียกดูสถานะและควบคุมบอทได้ผ่าน HTTP

วิธีรัน:
  pip install flask
  python vps_bot_api.py

เปิด Windows Firewall port 5000:
  New-NetFirewallRule -DisplayName "Bot API" -Direction Inbound -Protocol TCP -LocalPort 5000 -Action Allow
"""

from flask import Flask, jsonify, request, abort
import os, json, subprocess

app = Flask(__name__)

# ── ตั้งค่า ──────────────────────────────────────────────────────────────────
API_KEY  = "forex-bot-2026"          # เปลี่ยนเป็นค่าที่ต้องการ (ใช้คู่กับ Mac app)
DESKTOP  = r"C:\Users\Administrator\Desktop"
SYMBOLS  = ["XAUUSD", "XAUUSD_TP7", "EURUSD", "GBPUSD"]
MAX_POS  = {"XAUUSD": 2, "XAUUSD_TP7": 1, "EURUSD": 1, "GBPUSD": 1}

BOT_CMD  = {
    "XAUUSD":     "python forex_live_bot_gold_cwider.py --max-positions 2",
    "XAUUSD_TP7": "python forex_live_bot_gold_cwider.py --symbol XAUUSD --sl-atr 2.5 --tp-atr 7.0 --variant-tag tp7 --max-positions 1",
    "EURUSD":     "python forex_live_bot_gold_cwider.py --symbol EURUSD --max-positions 1",
    "GBPUSD":     "python forex_live_bot_gold_cwider.py --symbol GBPUSD --max-positions 1",
}
# ─────────────────────────────────────────────────────────────────────────────


def auth():
    if request.headers.get("X-API-Key") != API_KEY:
        abort(401, "Unauthorized")


def _process_running(symbol: str) -> bool:
    """เช็คว่ามี python process ที่มี --symbol SYMBOL หรือ default XAUUSD รันอยู่ไหม"""
    try:
        if symbol == "XAUUSD_TP7":
            cmd = (
                "Get-WmiObject Win32_Process -Filter \"Name='python.exe'\" | "
                "Where-Object {$_.CommandLine -like '*tp7*'} | "
                "Measure-Object | Select-Object -ExpandProperty Count"
            )
        elif symbol == "XAUUSD":
            # C_wider instance: has *cwider* but NOT EURUSD/GBPUSD/tp7
            cmd = (
                "Get-WmiObject Win32_Process -Filter \"Name='python.exe'\" | "
                "Where-Object {$_.CommandLine -like '*cwider*' -and "
                "$_.CommandLine -notlike '*EURUSD*' -and "
                "$_.CommandLine -notlike '*GBPUSD*' -and "
                "$_.CommandLine -notlike '*tp7*'} | "
                "Measure-Object | Select-Object -ExpandProperty Count"
            )
        else:
            cmd = (
                f"Get-WmiObject Win32_Process -Filter \"Name='python.exe'\" | "
                f"Where-Object {{$_.CommandLine -like '*{symbol}*'}} | "
                f"Measure-Object | Select-Object -ExpandProperty Count"
            )
        r = subprocess.run(
            ["powershell", "-Command", cmd],
            capture_output=True, text=True, timeout=5
        )
        return r.stdout.strip() not in ("", "0")
    except Exception:
        return False


def _state_fname(symbol: str) -> str:
    if symbol == "XAUUSD_TP7":
        return "xauusd_tp7_state.json"
    return f"{symbol.lower()}_cwider_state.json"


def _fills_fname(symbol: str) -> str:
    if symbol == "XAUUSD_TP7":
        return "fills_log_xauusd_tp7.csv"
    return f"fills_log_{symbol.lower()}_cwider.csv"


def _stop_fname(symbol: str) -> str:
    if symbol == "XAUUSD_TP7":
        return "STOP_XAUUSD_TP7"
    return f"STOP_{symbol}"


def _read_state(symbol: str) -> dict:
    path = os.path.join(DESKTOP, _state_fname(symbol))
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _count_fills(symbol: str) -> int:
    path = os.path.join(DESKTOP, _fills_fname(symbol))
    try:
        with open(path, encoding="utf-8") as f:
            return max(0, sum(1 for _ in f) - 1)  # minus header
    except Exception:
        return 0


@app.route("/status")
def status():
    auth()
    result = {}
    for sym in SYMBOLS:
        state = _read_state(sym)
        ds    = state.get("day_state", {})
        positions = state.get("positions", [])
        stop_file = os.path.join(DESKTOP, _stop_fname(sym))

        result[sym] = {
            "running":        _process_running(sym),
            "killed":         os.path.exists(stop_file),
            "day_pnl":        ds.get("day_realized_pnl", 0.0),
            "day_blocked":    ds.get("day_blocked", False),
            "trades_today":   ds.get("day_trade_count", 0),
            "open_positions": len(positions),
            "max_positions":  MAX_POS.get(sym, 1),
            "fills_total":    _count_fills(sym),
        }
    return jsonify(result)


@app.route("/kill/<symbol>", methods=["POST"])
def kill(symbol):
    auth()
    symbol = symbol.upper()
    if symbol not in SYMBOLS:
        abort(404)
    path = os.path.join(DESKTOP, _stop_fname(symbol))
    with open(path, "w") as f:
        f.write("")
    return jsonify({"ok": True, "msg": f"Kill-switch ON: {symbol}"})


@app.route("/unkill/<symbol>", methods=["POST"])
def unkill(symbol):
    auth()
    symbol = symbol.upper()
    if symbol not in SYMBOLS:
        abort(404)
    path = os.path.join(DESKTOP, _stop_fname(symbol))
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    return jsonify({"ok": True, "msg": f"Kill-switch OFF: {symbol}"})


@app.route("/stop/<symbol>", methods=["POST"])
def stop(symbol):
    """Force-stop bot process (ไม่มี graceful shutdown — ใช้ kill-switch ก่อนถ้าทำได้)"""
    auth()
    symbol = symbol.upper()
    if symbol not in SYMBOLS:
        abort(404)
    if symbol == "XAUUSD_TP7":
        filter_clause = (
            "Get-WmiObject Win32_Process -Filter \"Name='python.exe'\" | "
            "Where-Object {$_.CommandLine -like '*tp7*'} | "
            "ForEach-Object {$_.Terminate()}"
        )
    elif symbol == "XAUUSD":
        filter_clause = (
            "Get-WmiObject Win32_Process -Filter \"Name='python.exe'\" | "
            "Where-Object {$_.CommandLine -like '*cwider*' -and "
            "$_.CommandLine -notlike '*EURUSD*' -and "
            "$_.CommandLine -notlike '*GBPUSD*' -and "
            "$_.CommandLine -notlike '*tp7*'} | "
            "ForEach-Object {$_.Terminate()}"
        )
    else:
        filter_clause = (
            f"Get-WmiObject Win32_Process -Filter \"Name='python.exe'\" | "
            f"Where-Object {{$_.CommandLine -like '*{symbol}*'}} | "
            f"ForEach-Object {{$_.Terminate()}}"
        )
    try:
        subprocess.run(["powershell", "-Command", filter_clause],
                       capture_output=True, timeout=10)
        return jsonify({"ok": True, "msg": f"Stopped: {symbol}"})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


@app.route("/start/<symbol>", methods=["POST"])
def start(symbol):
    """Start bot instance ใหม่ใน background"""
    auth()
    symbol = symbol.upper()
    if symbol not in SYMBOLS:
        abort(404)
    cmd = BOT_CMD.get(symbol, "")
    if not cmd:
        abort(400)
    try:
        subprocess.Popen(
            f"start cmd /k {cmd}",
            shell=True, cwd=DESKTOP
        )
        return jsonify({"ok": True, "msg": f"Starting: {symbol}"})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


@app.route("/fills/<symbol>")
def fills(symbol):
    """Return parsed fills log as JSON (for spread/slippage analysis)."""
    auth()
    symbol = symbol.upper()
    if symbol not in SYMBOLS:
        abort(404)
    path = os.path.join(DESKTOP, _fills_fname(symbol))
    try:
        import csv as _csv
        rows = []
        with open(path, encoding="utf-8", newline="") as f:
            reader = _csv.DictReader(f)
            for row in reader:
                rows.append(row)
        # Compute spread stats
        spreads = []
        slippages = []
        for r in rows:
            try:
                spreads.append(float(r.get("spread_at_fill", 0) or 0))
                slippages.append(float(r.get("slippage", 0) or 0))
            except (ValueError, TypeError):
                pass
        stats = {
            "count": len(rows),
            "spread_avg":  round(sum(spreads) / len(spreads), 5) if spreads else None,
            "spread_max":  round(max(spreads), 5) if spreads else None,
            "spread_min":  round(min(spreads), 5) if spreads else None,
            "slippage_avg": round(sum(slippages) / len(slippages), 5) if slippages else None,
            "slippage_max": round(max(slippages), 5) if slippages else None,
            "slippage_min": round(min(slippages), 5) if slippages else None,
        }
        return jsonify({"stats": stats, "rows": rows})
    except FileNotFoundError:
        return jsonify({"stats": None, "rows": [], "error": "fills log not found"})
    except Exception as e:
        return jsonify({"stats": None, "rows": [], "error": str(e)}), 500


if __name__ == "__main__":
    print(f"Bot API running — API key: {API_KEY}")
    print("เปิดใน Mac app ได้ที่ http://VPS_IP:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)
