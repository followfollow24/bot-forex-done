# =============================================================================
# watchdog.ps1 — Defense layer #2 (external) สำหรับ forex_live_bot_gold_cwider.py
# =============================================================================
# ตรวจ STATUS line ล่าสุดของ 3 บอท ถ้าค้างเกิน threshold ต่อ variant → kill process
# นั้น (เฉพาะตัวที่ค้าง — ไม่แตะตัวอื่น) แล้ว start ใหม่ด้วยคำสั่งเดียวกับ deploy.ps1
#
# ทำไมต้องมีชั้นนี้เพิ่ม (แม้จะแก้ timeout ในตัว bot แล้ว):
#   Layer 1 (_call_with_timeout ใน .py) แปลง MT5-IPC-hang ให้กลายเป็น
#   TimeoutError เพื่อ trigger reconnect ได้ — แต่ thread ที่ค้างจริงจะไม่ถูก
#   ฆ่า (Python ทำไม่ได้กับ blocking C-call) ถ้า IPC ตายลึกจริงๆ reconnect
#   อาจไม่ช่วย เพราะ handle เดิมยังค้างอยู่ในโปรเซส วิธีที่รับประกันแก้ได้จริง
#   คือฆ่า process ทั้งตัวแล้วให้ Windows คืน resource ให้ — นั่นคืองานของสคริปต์นี้
#
# วิธีใช้:
#   ตั้งรันผ่าน Task Scheduler ทุก 5-10 นาที (ดู setup_watchdog_task.ps1)
#   ทดสอบมือ:  cd C:\Users\Administrator\Desktop; .\watchdog.ps1
#
# ข้อควรระวัง:
#   - อย่ารันถี่กว่า 5 นาที (กัน race กับ deploy.ps1 ที่ Stop-Process ทุก python.exe)
#   - ถ้ากำลัง deploy.ps1 อยู่พอดี watchdog อาจเห็น log ค้างชั่วคราวระหว่าง
#     restart แล้ว restart ซ้ำ — ผลกระทบต่ำ (แค่ restart ซ้ำ ไม่เสียหาย) แต่ถ้า
#     กังวลให้ปิด Task ชั่วคราวก่อนรัน deploy.ps1 ด้วยมือ
# =============================================================================

$DESKTOP = "$env:USERPROFILE\Desktop"
$SYMBOL  = "xauusd"   # ใช้ตรงกับ slug ที่ _make_paths() สร้างไฟล์ (symbol.lower())

# ── นิยามบอท 3 ตัว — args ต้องตรงกับ deploy.ps1 เป๊ะๆ ──────────────────────────
$bots = @(
    @{
        Variant      = "adx20tp7"
        StaleMinutes = 30    # M15 → STATUS ทุก ~15 นาที, buffer 2x
        Args         = "forex_live_bot_gold_cwider.py --variant-tag adx20tp7 --sl-atr 3.0 --tp-atr 7.0 --adx-min 20 --timeframe 15m --max-positions 3 --risk 0.30"
    },
    @{
        Variant      = "m5tp7"
        StaleMinutes = 15    # M5 → STATUS ทุก ~5 นาที, buffer 3x (poll ถี่กว่า เผื่อ noise มากกว่า)
        Args         = "forex_live_bot_gold_cwider.py --variant-tag m5tp7 --sl-atr 3.0 --tp-atr 7.0 --adx-min 20 --timeframe 5m --max-positions 3 --risk 0.30"
    },
    @{
        Variant      = "adx18tp7"
        StaleMinutes = 30
        Args         = "forex_live_bot_gold_cwider.py --variant-tag adx18tp7 --sl-atr 3.0 --tp-atr 7.0 --adx-min 18 --timeframe 15m --max-positions 3 --risk 0.30"
    }
)

# ── watchdog เขียน log ของตัวเองแยกจาก bot logs (แยกวันละไฟล์) ─────────────────
$wlogFile = "$DESKTOP\watchdog_$(Get-Date -Format 'yyyy-MM-dd').log"
function WLog($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $msg"
    Write-Host $line
    Add-Content -Path $wlogFile -Value $line
}

WLog "=== watchdog run start ==="

foreach ($bot in $bots) {
    $variant = $bot.Variant
    $logFile = "$DESKTOP\forex_${SYMBOL}_$variant.log"

    if (-not (Test-Path $logFile)) {
        WLog "[$variant] log ไม่พบ ($logFile) — ข้าม (อาจยังไม่เคย start)"
        continue
    }

    # หา STATUS line ล่าสุดใน 300 บรรทัดท้าย — ตั้งใจไม่ใช้ (Get-Item).LastWriteTime
    # เฉยๆ เพราะ error-loop (log.error ซ้ำๆ ตอน reconnect ล้มเหลว) ก็ทำให้ไฟล์ดู
    # "active" ทั้งที่ main loop จริงๆ ไม่ได้ประมวลผลอะไรเลย ต้องเช็ค STATUS line
    # โดยเฉพาะ เพราะมันแปลว่า loop วิ่งจบรอบจริง
    $statusLine = Get-Content $logFile -Tail 300 -ErrorAction SilentlyContinue |
                  Select-String "== STATUS ==" | Select-Object -Last 1

    if (-not $statusLine) {
        WLog "[$variant] ไม่พบ STATUS line ใน log ล่าสุด 300 บรรทัด — ถือว่าค้าง"
        $staleMin = 9999
    } else {
        # รูปแบบ timestamp: "2026-07-03 01:26:00,123 [INFO] == STATUS == ..."
        $tsStr = ($statusLine.Line -split '\[')[0].Trim()
        try {
            $tsClean = $tsStr.Substring(0, 19)   # "yyyy-MM-dd HH:mm:ss"
            $ts = [datetime]::ParseExact($tsClean, "yyyy-MM-dd HH:mm:ss", $null)
            $staleMin = (New-TimeSpan -Start $ts -End (Get-Date)).TotalMinutes
        } catch {
            WLog "[$variant] parse timestamp ไม่ได้จาก '$tsStr' — ถือว่าค้าง (fail-safe)"
            $staleMin = 9999
        }
    }

    if ($staleMin -le $bot.StaleMinutes) {
        WLog "[$variant] OK — STATUS ล่าสุด $([math]::Round($staleMin,1)) นาทีก่อน (threshold=$($bot.StaleMinutes))"
        continue
    }

    WLog "[$variant] STALE! $([math]::Round($staleMin,1)) นาที > threshold $($bot.StaleMinutes) นาที — restarting"

    # ── หา process ที่ command line มี --variant-tag ตัวนี้อยู่ (แม่นกว่าฆ่า python.exe ทุกตัว) ──
    $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
             Where-Object { $_.CommandLine -like "*--variant-tag $variant*" }

    if ($procs) {
        foreach ($p in $procs) {
            WLog "[$variant] Stop-Process -Id $($p.ProcessId) -Force"
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Seconds 5
    } else {
        WLog "[$variant] ไม่พบ process รันอยู่ (ตายไปเงียบๆ แล้ว) — start ใหม่เลยโดยไม่ต้อง kill"
    }

    Start-Process python -ArgumentList $bot.Args -WorkingDirectory $DESKTOP -WindowStyle Normal
    WLog "[$variant] restarted: python $($bot.Args)"
    Start-Sleep -Seconds 3
}

$runningCount = (Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
                  Where-Object { $_.CommandLine -like "*forex_live_bot_gold_cwider.py*" }).Count
WLog "=== watchdog run complete — python bots running: $runningCount / 3 ==="
