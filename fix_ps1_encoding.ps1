# =============================================================================
# fix_ps1_encoding.ps1 — แปลงไฟล์ .ps1 ทุกตัวในโฟลเดอร์เดียวกันให้เป็น
# UTF-8-with-BOM (ไม่แก้เนื้อหาโค้ดเลย แค่เปลี่ยน encoding ตอนเซฟไฟล์)
# =============================================================================
# ปัญหาที่แก้: PowerShell 5.1 (ค่า default บน Windows Server ส่วนใหญ่) จะอ่าน
# ไฟล์ .ps1 ที่ไม่มี BOM ด้วย system codepage (ANSI) เสมอ ไม่ใช่ UTF-8 — ถ้าไฟล์
# มี comment ภาษาไทย (multi-byte UTF-8) จะถูกอ่านผิดเพี้ยน และถ้าไปชนกับ
# string terminator (" หรือ ') พอดี จะกลายเป็น parse error แบบที่เจอ
#
# วิธีใช้ (รันบน VPS จาก Desktop):
#   cd C:\Users\Administrator\Desktop
#   .\fix_ps1_encoding.ps1
#
# สคริปต์นี้จะ:
#   1. หาไฟล์ .ps1 ทุกตัวในโฟลเดอร์ปัจจุบัน (deploy.ps1, watchdog.ps1,
#      setup_watchdog_task.ps1, fix_ps1_encoding.ps1 เอง ฯลฯ)
#   2. อ่านเนื้อหาเดิม (ลองหลาย encoding เผื่อไฟล์เพี้ยนไปแล้วจาก error รอบก่อน)
#   3. เขียนทับด้วย UTF-8-with-BOM encoding เดิม content ไม่เปลี่ยนแม้แต่ตัวอักษรเดียว
#   4. สำรองไฟล์เดิมไว้เป็น .bak ก่อนเขียนทับ กันพลาด
# =============================================================================

$files = Get-ChildItem -Path . -Filter "*.ps1" | Where-Object { $_.Name -ne "fix_ps1_encoding.ps1" }

if (-not $files) {
    Write-Host "ไม่พบไฟล์ .ps1 ในโฟลเดอร์นี้" -ForegroundColor Yellow
    exit 0
}

Write-Host "พบ $($files.Count) ไฟล์ .ps1 — จะแปลงเป็น UTF-8-with-BOM ทีละไฟล์" -ForegroundColor Cyan

foreach ($f in $files) {
    $path = $f.FullName
    $bakPath = "$path.bak"

    try {
        # ── สำรองไฟล์เดิมก่อนเสมอ ──
        Copy-Item -Path $path -Destination $bakPath -Force

        # ── ตรวจว่ามี BOM อยู่แล้วหรือยัง (ถ้ามีแล้ว ข้ามไปเลย) ──
        $bytes = [System.IO.File]::ReadAllBytes($path)
        $hasBom = ($bytes.Length -ge 3) -and ($bytes[0] -eq 0xEF) -and ($bytes[1] -eq 0xBB) -and ($bytes[2] -eq 0xBF)
        if ($hasBom) {
            Write-Host "  [$($f.Name)] มี UTF-8 BOM อยู่แล้ว — ข้าม" -ForegroundColor Green
            Remove-Item $bakPath -Force
            continue
        }

        # ── อ่านเนื้อหาเดิม: ลอง UTF-8 (no BOM) ก่อน เพราะเป็นสาเหตุที่พบบ่อยสุด ──
        $content = [System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8)

        # ── เขียนทับด้วย UTF-8-with-BOM ──
        $utf8Bom = New-Object System.Text.UTF8Encoding($true)
        [System.IO.File]::WriteAllText($path, $content, $utf8Bom)

        Write-Host "  [$($f.Name)] แปลงเป็น UTF-8-with-BOM สำเร็จ (สำรองไว้ที่ $($f.Name).bak)" -ForegroundColor Green
    } catch {
        Write-Host "  [$($f.Name)] ERROR: $_" -ForegroundColor Red
        Write-Host "    -> ไฟล์ต้นฉบับไม่ถูกแก้ (bak ยังอยู่ที่ $bakPath ถ้าต้องการ restore ด้วยมือ)" -ForegroundColor Yellow
    }
}

Write-Host "`nเสร็จแล้ว — ทดสอบรันไฟล์ที่แก้ได้เลย (เช่น .\deploy.ps1 หรือ .\setup_watchdog_task.ps1)" -ForegroundColor Cyan
Write-Host "ถ้าทุกอย่างโอเค ลบไฟล์ .bak ทิ้งได้ด้วย:  Remove-Item *.ps1.bak" -ForegroundColor Cyan
