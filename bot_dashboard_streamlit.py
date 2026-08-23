"""
bot_dashboard_streamlit.py — ARCADE DASH v4 (Flask API)
Pixel arcade style matching reference image.
"""
import requests
import streamlit as st
import streamlit.components.v1 as components
from datetime import datetime, timezone
import math

st.set_page_config(page_title="ARCADE DASH", layout="wide", initial_sidebar_state="collapsed")
st.markdown("""
<style>
#MainMenu,footer,header,[data-testid="stToolbar"],
[data-testid="stDecoration"],[data-testid="stStatusWidget"]{display:none!important}
.block-container{padding:0!important;max-width:100%!important;margin:0!important}
body,[data-testid="stAppViewContainer"],[data-testid="stApp"]{
  background:#000!important;padding:0!important;margin:0!important}
iframe{border:none!important}
</style>
""", unsafe_allow_html=True)

def fetch():
    try:
        r = requests.get("http://127.0.0.1:5000/status",
                         headers={"X-API-Key": "forex-bot-2026"}, timeout=3)
        return r.json()
    except Exception:
        return {}

data  = fetch()
xau   = data.get("XAUUSD",    {})
tp7   = data.get("XAUUSD_TP7", {})

cw_pnl    = float(xau.get("day_pnl",       0) or 0)
cw_pos    = int(  xau.get("open_positions", 0) or 0)
cw_trades = int(  xau.get("trades_today",   0) or 0)
cw_run    = bool( xau.get("running",    False))
cw_max    = int(  xau.get("max_positions",  3) or 3)

mx_pnl    = float(tp7.get("day_pnl",       0) or 0)
mx_pos    = int(  tp7.get("open_positions", 0) or 0)
mx_trades = int(  tp7.get("trades_today",   0) or 0)
mx_run    = bool( tp7.get("running",    False))

total_pnl    = cw_pnl + mx_pnl
total_pos    = cw_pos + mx_pos
total_trades = cw_trades + mx_trades
total_max    = cw_max
bot_pct      = (50 if cw_run else 0) + (50 if mx_run else 0)
now_utc      = datetime.now(timezone.utc).strftime("%H:%M:%S")

pnl_sign  = "+" if total_pnl >= 0 else ""
pnl_color = "#00FF95" if total_pnl >= 0 else "#FF2D78"
pnl_str   = f"{pnl_sign}{total_pnl:.2f}"
hs_str    = f"{pnl_sign}{total_pnl:.2f} USD"

# needle angle: 0%=-135°  100%=+135°
ndeg = -135 + (bot_pct / 100) * 270
nx = 60 + 50 * math.cos(math.radians(ndeg - 90))
ny = 58 + 50 * math.sin(math.radians(ndeg - 90))

HTML = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Press+Start+2P&display=swap" rel="stylesheet">
<style>
html,body{{margin:0;padding:6px;background:#000;display:flex;align-items:flex-start;justify-content:center;min-height:100vh;font-family:'Press Start 2P',monospace}}
.cab{{position:relative;width:min(960px,100%);background:linear-gradient(160deg,#2A1A35 0%,#170D24 50%,#1C1030 100%);border-radius:20px;border:4px solid #3A2050;overflow:hidden;padding:14px 0 16px}}
.neon-l,.neon-r{{position:absolute;top:0;bottom:0;width:26px;z-index:10;display:flex;align-items:center;justify-content:center;overflow:hidden}}
.neon-l{{left:0;background:#EE00BB;box-shadow:4px 0 20px #FF00CC,8px 0 40px rgba(255,0,204,.5);border-radius:17px 0 0 17px}}
.neon-r{{right:0;background:#EE00BB;box-shadow:-4px 0 20px #FF00CC,-8px 0 40px rgba(255,0,204,.5);border-radius:0 17px 17px 0}}
.stxt{{font-size:5px;color:rgba(255,160,255,.75);white-space:nowrap;letter-spacing:.5px;writing-mode:vertical-rl;overflow:hidden;max-height:90%;user-select:none}}
.stxt.l{{transform:rotate(180deg)}}
.stxt.r{{transform:rotate(0deg)}}
.bezel{{margin:10px 28px;background:#03000E;border-radius:14px;border:4px solid #160830;padding:12px 14px 14px;position:relative;box-shadow:inset 0 0 35px rgba(100,0,200,.18)}}
.bezel::before{{content:'';position:absolute;inset:0;border-radius:10px;pointer-events:none;z-index:50;background:repeating-linear-gradient(0deg,rgba(0,0,0,.09) 0,rgba(0,0,0,.09) 1px,transparent 1px,transparent 3px)}}
.hdr{{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;position:relative;z-index:1}}
.logo{{background:#02000C;border:3px solid #FF0088;border-radius:8px;padding:10px 20px;text-align:center;box-shadow:0 0 16px #FF0088,0 0 32px rgba(255,0,136,.3)}}
.la{{display:block;color:#FFE000;font-size:22px;text-shadow:0 0 14px #FFE000,2px 2px 0 #AA8000;margin-bottom:5px}}
.lb{{display:block;color:#00FFCC;font-size:22px;text-shadow:0 0 14px #00FFCC,2px 2px 0 #007755}}
.sc{{text-align:right}}
.scl{{display:block;color:#00FF44;font-size:13px;text-shadow:0 0 10px #00FF44;margin-bottom:8px}}
.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;position:relative;z-index:1}}
.cw{{border-radius:11px;padding:2.5px}}
.c1w{{background:linear-gradient(160deg,#00EEFF,#0044FF,#FF00FF,#FF2D78)}}
.c2w{{background:linear-gradient(160deg,#00FFAA,#00CC44,#00FFFF,#0088FF)}}
.c3w{{background:linear-gradient(160deg,#FFEE00,#FF9900,#FF4400,#FFCC00)}}
.c4w{{background:linear-gradient(160deg,#FF00FF,#AA00FF,#FF0088,#8800FF)}}
.card{{background:#040118;border-radius:9px;padding:14px 10px 12px;text-align:center;display:flex;flex-direction:column;align-items:center;min-height:270px}}
.ic{{width:62px;height:62px;margin-bottom:8px;flex-shrink:0}}
.lb2{{color:#998BAA;font-size:6px;letter-spacing:.4px;margin-bottom:9px;flex-shrink:0;line-height:1.9}}
.bars{{display:flex;align-items:flex-end;justify-content:center;gap:4px;height:62px;width:100%;margin-bottom:8px;flex-shrink:0}}
.b{{flex:1;border-radius:2px 2px 0 0;max-width:14px}}
.val{{font-size:20px;margin-bottom:5px}}
.sub{{font-size:5px;color:#3A2A58;line-height:1.6}}
.ctrl{{display:flex;align-items:center;gap:14px;margin-top:12px;position:relative;z-index:1}}
.coin{{width:82px;height:82px;border-radius:50%;flex-shrink:0;background:radial-gradient(circle at 32% 24%,#FF9977,#CC1100 50%,#770000);border:4px solid #FFB800;box-shadow:0 7px 0 #550000,0 0 20px rgba(255,34,0,.6);display:flex;align-items:center;justify-content:center;cursor:pointer}}
.coint{{color:#FFE600;font-size:6px;text-align:center;line-height:1.9;text-shadow:1px 1px 0 #771100}}
.bcl{{display:flex;flex-direction:column;align-items:center;gap:6px}}
.bp{{display:flex;gap:10px}}
.bst{{width:46px;height:46px;border-radius:50%;background:radial-gradient(circle at 32% 24%,#FFEE66,#CC9900 52%,#775500);border:3px solid #FFCC00;box-shadow:0 5px 0 #443300;display:flex;align-items:center;justify-content:center;color:#332200;font-size:5px;cursor:pointer;font-family:inherit}}
.brs{{width:46px;height:46px;border-radius:50%;background:radial-gradient(circle at 32% 24%,#AABBCC,#334466 52%,#111E2E);border:3px solid #5577AA;box-shadow:0 5px 0 #0A1020;display:flex;align-items:center;justify-content:center;color:#99AABB;font-size:5px;cursor:pointer;font-family:inherit}}
.bml{{font-size:5.5px;color:#2E1E46}}
.joy{{width:68px;height:68px;background:#007A8A;border-radius:11px;box-shadow:0 5px 0 #004455;display:flex;align-items:center;justify-content:center;flex-shrink:0;position:relative}}
.jball{{width:36px;height:36px;border-radius:50%;background:radial-gradient(circle at 32% 26%,#FF99CC,#EE1177 52%,#990044);box-shadow:0 0 12px rgba(238,17,119,.6)}}
.sp{{flex:1}}
.live{{text-align:right}}
.dot{{display:inline-block;width:10px;height:10px;background:#00FF44;border-radius:50%;margin-right:6px;animation:blink .8s step-start infinite;vertical-align:middle}}
.ltx{{font-size:9px;color:#00FF44;text-shadow:0 0 10px #00FF44}}
.lsub{{font-size:10px;color:#00CC44;margin-top:6px;text-shadow:0 0 6px #00AA33;letter-spacing:.3px}}
.foot{{font-size:4.5px;color:#160E28;text-align:center;margin-top:8px;letter-spacing:.6px}}
.dia{{position:absolute;bottom:11px;right:11px;color:#5533AA;font-size:10px;letter-spacing:2px;z-index:15}}
@keyframes blink{{50%{{opacity:0}}}}
</style>
</head>
<body>
<div class="cab">
  <div class="neon-l"><span class="stxt l">&#169; 1987 ARCADE DASH CORP.&nbsp;&nbsp;&nbsp;8-BIT CLASSIC</span></div>
  <div class="neon-r"><span class="stxt r">PRESS P TO PAUSE&nbsp;&nbsp;&nbsp;&#169; 1987</span></div>
  <div class="bezel">
    <div class="hdr">
      <div class="logo"><span class="la">ARCADE</span><span class="lb">DASH</span></div>
      <div class="sc">
        <span class="scl">HIGH SCORE: {hs_str}</span>
        <span class="scl">TIME: {now_utc} UTC</span>
      </div>
    </div>
    <div class="grid">

      <!-- CARD 1: HEART / POSITIONS -->
      <div class="cw c1w"><div class="card">
        <svg class="ic" viewBox="0 0 62 56" xmlns="http://www.w3.org/2000/svg">
          <defs>
            <radialGradient id="hg" cx="36%" cy="26%" r="68%">
              <stop offset="0%" stop-color="#FF99CC"/>
              <stop offset="45%" stop-color="#FF2D78"/>
              <stop offset="100%" stop-color="#AA0044"/>
            </radialGradient>
            <filter id="hf"><feGaussianBlur stdDeviation="2.5" result="b"/>
              <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
          </defs>
          <path filter="url(#hf)" d="M31,53 C31,53 3,35 3,17 C3,8 10,2 18,2 C23,2 27.5,4.5 31,9 C34.5,4.5 39,2 44,2 C52,2 59,8 59,17 C59,35 31,53 31,53Z" fill="url(#hg)"/>
          <ellipse cx="20" cy="13" rx="6" ry="4.5" fill="white" opacity=".3"/>
        </svg>
        <div class="lb2">ACTIVE<br>USERS</div>
        <div class="bars">
          <div class="b" style="height:32%;background:#FF2D78"></div>
          <div class="b" style="height:58%;background:#FF5500"></div>
          <div class="b" style="height:44%;background:#FFAA00"></div>
          <div class="b" style="height:82%;background:#FFE600"></div>
          <div class="b" style="height:65%;background:#00FF88"></div>
          <div class="b" style="height:94%;background:#00DDFF"></div>
          <div class="b" style="height:50%;background:#8844FF"></div>
        </div>
        <div class="val" style="color:#00DDFF;text-shadow:0 0 10px #00DDFF">{total_pos}/{total_max}</div>
        <div class="sub">(STAGES CLEARED)</div>
      </div></div>

      <!-- CARD 2: RADAR / BOT STATUS -->
      <div class="cw c2w"><div class="card">
        <svg class="ic" viewBox="0 0 62 62" xmlns="http://www.w3.org/2000/svg">
          <defs><filter id="rf"><feGaussianBlur stdDeviation="1.8" result="b"/>
            <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>
          <circle cx="31" cy="31" r="28" fill="none" stroke="#00FF77" stroke-width="2.5" filter="url(#rf)"/>
          <circle cx="31" cy="31" r="20" fill="none" stroke="#00FF77" stroke-width="1.5" opacity=".6"/>
          <circle cx="31" cy="31" r="11" fill="none" stroke="#00FF77" stroke-width="1.5" opacity=".4"/>
          <line x1="31" y1="3" x2="31" y2="59" stroke="#00FF77" stroke-width="1" opacity=".3"/>
          <line x1="3" y1="31" x2="59" y2="31" stroke="#00FF77" stroke-width="1" opacity=".3"/>
          <circle cx="31" cy="31" r="4.5" fill="#00FF77" filter="url(#rf)"/>
          <line x1="31" y1="31" x2="53" y2="11" stroke="#00FF77" stroke-width="3" filter="url(#rf)"/>
          <circle cx="50" cy="14" r="4.5" fill="#00FF77" filter="url(#rf)"/>
          <circle cx="18" cy="20" r="2.5" fill="#00FF77" opacity=".45"/>
        </svg>
        <div class="lb2">NETWORK<br>STATUS</div>
        <svg width="120" height="68" viewBox="0 0 120 68" style="margin-bottom:6px;flex-shrink:0">
          <defs><filter id="gf"><feGaussianBlur stdDeviation="1.5" result="b"/>
            <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>
          <path d="M10,62 A50,50 0 0,1 110,62" fill="none" stroke="#0A1A0A" stroke-width="14" stroke-linecap="round"/>
          <path d="M10,62 A50,50 0 0,1 31,26" fill="none" stroke="#FF2200" stroke-width="12" stroke-linecap="butt"/>
          <path d="M31,26 A50,50 0 0,1 60,14" fill="none" stroke="#FFAA00" stroke-width="12" stroke-linecap="butt"/>
          <path d="M60,14 A50,50 0 0,1 110,62" fill="none" stroke="#00FF77" stroke-width="12" stroke-linecap="butt"/>
          <line x1="60" y1="58" x2="{nx:.1f}" y2="{ny:.1f}" stroke="#FF2200" stroke-width="3.5" stroke-linecap="round" filter="url(#gf)"/>
          <circle cx="60" cy="58" r="8" fill="#00FF77" filter="url(#gf)"/>
          <circle cx="60" cy="58" r="4" fill="#FF2200"/>
        </svg>
        <div class="val" style="color:#00FF77;text-shadow:0 0 10px #00FF77">{bot_pct}%</div>
        <div class="sub">(SERVER ONLINE)</div>
      </div></div>

      <!-- CARD 3: COIN / P&L -->
      <div class="cw c3w"><div class="card">
        <svg class="ic" viewBox="0 0 62 62" xmlns="http://www.w3.org/2000/svg">
          <defs>
            <radialGradient id="cg" cx="35%" cy="27%" r="65%">
              <stop offset="0%" stop-color="#FFEE88"/>
              <stop offset="42%" stop-color="#FFD700"/>
              <stop offset="100%" stop-color="#AA7700"/>
            </radialGradient>
            <filter id="cf"><feGaussianBlur stdDeviation="2" result="b"/>
              <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
          </defs>
          <circle cx="31" cy="31" r="29" fill="#775500" filter="url(#cf)"/>
          <circle cx="31" cy="31" r="27" fill="#BB7700"/>
          <circle cx="31" cy="31" r="23" fill="url(#cg)"/>
          <circle cx="31" cy="31" r="17" fill="none" stroke="#BB7700" stroke-width="2"/>
          <path d="M24,22 Q20,22 20,31 Q20,40 24,40 L38,40 L38,36 L26,36 Q24,36 24,31 Q24,26 26,26 L38,26 L38,22 Z" fill="#AA7700"/>
          <ellipse cx="22" cy="22" rx="6.5" ry="5" fill="#FFEE88" opacity=".5"/>
        </svg>
        <div class="lb2">REVENUE</div>
        <svg width="120" height="68" viewBox="0 0 120 68" style="margin-bottom:6px;flex-shrink:0">
          <defs>
            <linearGradient id="lg" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stop-color="#FF9900" stop-opacity=".5"/>
              <stop offset="100%" stop-color="#FF9900" stop-opacity="0"/>
            </linearGradient>
            <filter id="lf"><feGaussianBlur stdDeviation="1" result="b"/>
              <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
          </defs>
          <line x1="0" y1="58" x2="120" y2="58" stroke="#1E1400" stroke-width="1"/>
          <line x1="0" y1="40" x2="120" y2="40" stroke="#1E1400" stroke-width="1"/>
          <line x1="0" y1="22" x2="120" y2="22" stroke="#1E1400" stroke-width="1"/>
          <polygon points="5,62 18,57 28,59 39,48 49,52 60,38 68,44 78,30 88,34 98,20 108,24 115,12 115,66 5,66" fill="url(#lg)"/>
          <polyline points="5,62 18,57 28,59 39,48 49,52 60,38 68,44 78,30 88,34 98,20 108,24 115,12"
            fill="none" stroke="#FF9900" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round" filter="url(#lf)"/>
          <circle cx="115" cy="12" r="3.5" fill="#FF9900"/>
        </svg>
        <div class="val" style="color:{pnl_color};text-shadow:0 0 10px {pnl_color}">{pnl_str}</div>
        <div class="sub">(QUARTERS COLLECTED)</div>
      </div></div>

      <!-- CARD 4: TROPHY / TRADES -->
      <div class="cw c4w"><div class="card">
        <svg class="ic" viewBox="0 0 62 62" xmlns="http://www.w3.org/2000/svg">
          <defs>
            <linearGradient id="tg" x1="0.2" y1="0" x2="0.8" y2="1">
              <stop offset="0%" stop-color="#FFEE88"/>
              <stop offset="50%" stop-color="#FFD700"/>
              <stop offset="100%" stop-color="#CC8800"/>
            </linearGradient>
            <filter id="tf"><feGaussianBlur stdDeviation="2" result="b"/>
              <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
          </defs>
          <path filter="url(#tf)" d="M14,8 L48,8 L44,35 Q41,44 31,46 Q21,44 18,35 Z" fill="url(#tg)"/>
          <path d="M14,8 L48,8 L44,35 Q41,44 31,46 Q21,44 18,35 Z" fill="url(#tg)"/>
          <path d="M14,8 L14,14 Q6,16 6,23 Q6,30 14,32 L14,38 Q4,35 4,23 Q4,10 14,8Z" fill="#FFD700"/>
          <path d="M48,8 L48,14 Q56,16 56,23 Q56,30 48,32 L48,38 Q58,35 58,23 Q58,10 48,8Z" fill="#FFD700"/>
          <rect x="24" y="46" width="14" height="7" fill="#FFD700"/>
          <rect x="17" y="53" width="28" height="5" fill="#FFD700"/>
          <rect x="13" y="57" width="36" height="4" fill="#CC8800"/>
          <path d="M19,10 L25,10 L23,28 L18,25Z" fill="#FFEE88" opacity=".6"/>
        </svg>
        <div class="lb2">ACHIEVEMENTS</div>
        <div class="bars">
          <div class="b" style="height:28%;background:#FF2D78"></div>
          <div class="b" style="height:62%;background:#FF5500"></div>
          <div class="b" style="height:40%;background:#FFAA00"></div>
          <div class="b" style="height:86%;background:#FFE600"></div>
          <div class="b" style="height:70%;background:#00FF88"></div>
          <div class="b" style="height:96%;background:#00DDFF"></div>
          <div class="b" style="height:54%;background:#CC33FF"></div>
        </div>
        <div class="val" style="color:#CC33FF;text-shadow:0 0 10px #CC33FF">{total_trades}</div>
        <div class="sub">(AWARDS UNLOCKED)</div>
      </div></div>

    </div>

    <div class="ctrl">
      <div class="coin"><div class="coint">INSERT<br>COIN</div></div>
      <div class="bcl">
        <div class="bp"><div class="bst">START</div><div class="brs">RESET</div></div>
        <div class="bml">MENU</div>
      </div>
      <div class="joy"><div class="jball"></div></div>
      <div class="sp"></div>
      <div class="live">
        <div style="display:flex;align-items:center;justify-content:flex-end;margin-bottom:5px">
          <span class="dot"></span><span class="ltx">LIVE</span>
        </div>
        <div class="lsub">C_WIDER: {cw_pnl:+.2f} USD</div>
        <div class="lsub">MIX_A&nbsp;&nbsp;: {mx_pnl:+.2f} USD</div>
      </div>
    </div>
  </div>
  <div class="foot">Exness-MT5Trial6 &nbsp;&#183;&nbsp; Account 413879493 &nbsp;&#183;&nbsp; RISK 0.30%/trade &nbsp;&#183;&nbsp; &#169; ARCADE DASH 2026</div>
  <div class="dia">&#9670;&#9670;</div>
</div>
</body>
</html>"""

components.html(HTML, height=820, scrolling=False)
