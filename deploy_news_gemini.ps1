# deploy_news_gemini.ps1 -- 2026-08-10. First deploy of news_gemini_bot.py.
#
# [!!] UNVALIDATED STRATEGY going live at user's explicit request after an
# explicit risk warning (no historical backtest exists for an LLM news
# signal). Small size (0.15% risk/trade, far below every other bot),
# mandatory SL on every order, consecutive-loss auto-breaker, Telegram
# alert on every decision cycle. See news_gemini_bot.py's module docstring
# for the full safety-net list before touching any of it.
#
# Pulls latest code, installs google-genai if missing, verifies GEMINI_API_KEY
# is present in .env, then starts the bot fresh (no existing process to stop
# on a first deploy).

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

Write-Host "=== 2. ensure google-genai is installed ===" -ForegroundColor Cyan
Set-Location $Desktop
python -c "import google.genai" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  installing google-genai..."
    pip install --quiet google-genai
}

Write-Host "=== 3. refuse to run if already running (avoid duplicate magic 669001) ===" -ForegroundColor Cyan
$existing = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
            Where-Object { $_.CommandLine -match "news_gemini_bot\.py" }
if ($existing) {
    Write-Host ("ABORT: news_gemini_bot.py already running (PID $($existing.ProcessId)). " +
               "Stop it first if you intend to restart.") -ForegroundColor Red
    exit 1
}

Write-Host "=== 4. start the bot ===" -ForegroundColor Cyan
Start-Process python -ArgumentList "news_gemini_bot.py --allow-real" -WorkingDirectory $Desktop
Start-Sleep -Seconds 8

Write-Host "=== 5. verify ===" -ForegroundColor Cyan
$live = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -match "news_gemini_bot\.py" }
if ($live) {
    Write-Host "  news_gemini_bot.py running, PID $($live.ProcessId)" -ForegroundColor Green
    Write-Host "  NEWS_GEMINI DEPLOYED - watch Telegram for the START message and the" -ForegroundColor Green
    Write-Host "  first poll cycle's decisions (no news = normal, most cycles find nothing)." -ForegroundColor Green
} else {
    Write-Host "  SOMETHING IS OFF - process not found after start, check the log:" -ForegroundColor Red
    Write-Host "  $Desktop\forex_bot_news_gemini.log"
}
