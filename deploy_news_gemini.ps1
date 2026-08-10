# deploy_news_gemini.ps1 -- 2026-08-10/11. Deploy/redeploy news_gemini_bot.py.
#
# [!!] UNVALIDATED STRATEGY going live at user's explicit request after an
# explicit risk warning (no historical backtest exists for an LLM news
# signal). Small size (0.15% risk/trade, far below every other bot),
# mandatory SL on every order, consecutive-loss auto-breaker, Telegram
# alert on every decision cycle. See news_gemini_bot.py's module docstring
# for the full safety-net list before touching any of it.
#
# [2026-08-11] Now DUAL-PROVIDER CONSENSUS: Gemini AND OpenAI each run an
# independent news scan; a symbol only trades if both agree. OPENAI_API_KEY
# is optional at the code level -- if absent the bot still runs but takes
# no new entries (fail-safe idle), so this script does NOT abort if it's
# missing, only warns.
#
# Pulls latest code, installs google-genai/openai if missing, verifies
# GEMINI_API_KEY is present in .env, then (re)starts the bot -- stops any
# already-running instance first so this doubles as the update path.

$ErrorActionPreference = "Stop"
$Desktop = "$env:USERPROFILE\Desktop"
$Repo    = "$Desktop\bot_repo"

Write-Host "=== 1. pull latest code ===" -ForegroundColor Cyan
Set-Location $Repo
git pull origin main
Copy-Item news_gemini_bot.py, requirements.txt $Desktop -Force
if (-not (Test-Path "$Desktop\.env")) {
    Write-Host ("ABORT: $Desktop\.env not found -- create it with GEMINI_API_KEY, " +
               "TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID first.") -ForegroundColor Red
    exit 1
}
$envContent = Get-Content "$Desktop\.env" -Raw
if ($envContent -notmatch "GEMINI_API_KEY\s*=\s*\S+") {
    Write-Host "ABORT: GEMINI_API_KEY missing from $Desktop\.env" -ForegroundColor Red
    exit 1
}
Write-Host "  .env has GEMINI_API_KEY: OK" -ForegroundColor Green
if ($envContent -notmatch "OPENAI_API_KEY\s*=\s*\S+") {
    Write-Host ("  WARNING: OPENAI_API_KEY missing from .env -- dual-consensus " +
               "unavailable, bot will idle (no new entries) until it's added.") -ForegroundColor Yellow
} else {
    Write-Host "  .env has OPENAI_API_KEY: OK" -ForegroundColor Green
}

Write-Host "=== 2. ensure google-genai + openai are installed ===" -ForegroundColor Cyan
Set-Location $Desktop
# $ErrorActionPreference=Stop turns a native command's stderr output into a
# terminating error, which would abort here even on a normal "module not
# found" check -- relax it just for these probes, then restore it.
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"
python -c "import google.genai" 2>$null
$hasGenai = ($LASTEXITCODE -eq 0)
python -c "import openai" 2>$null
$hasOpenai = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = $prevEAP
if (-not $hasGenai) {
    Write-Host "  installing google-genai..."
    pip install --quiet google-genai
}
if (-not $hasOpenai) {
    Write-Host "  installing openai..."
    pip install --quiet openai
}

Write-Host "=== 3. stop any existing instance (avoid duplicate magic 669001) ===" -ForegroundColor Cyan
$existing = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
            Where-Object { $_.CommandLine -match "news_gemini_bot\.py" }
if ($existing) {
    foreach ($p in $existing) {
        Write-Host "  stopping existing PID $($p.ProcessId)"
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 3
} else {
    Write-Host "  no existing instance running"
}

Write-Host "=== 4. start the bot ===" -ForegroundColor Cyan
Start-Process python -ArgumentList "news_gemini_bot.py --allow-real" -WorkingDirectory $Desktop
Start-Sleep -Seconds 8

Write-Host "=== 5. verify ===" -ForegroundColor Cyan
$live = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -match "news_gemini_bot\.py" }
if ($live) {
    Write-Host "  news_gemini_bot.py running, PID $($live.ProcessId)" -ForegroundColor Green
    Write-Host "  NEWS BOT DEPLOYED - watch Telegram for the START message." -ForegroundColor Green
    Write-Host "  Check the START message for 'dual-consensus=ON' vs 'OFF'." -ForegroundColor Green
} else {
    Write-Host "  SOMETHING IS OFF - process not found after start, check the log:" -ForegroundColor Red
    Write-Host "  $Desktop\forex_bot_news_gemini.log"
}
