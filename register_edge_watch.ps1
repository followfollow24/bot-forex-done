# register_edge_watch.ps1 -- weekly early warning that the invert premise
# still holds. ASCII-only: PowerShell 5.1 fails to parse a no-BOM .ps1 with
# non-ASCII bytes.
#
# chart_ai_trader is profitable only while Gemini and OpenAI keep being
# systematically WRONG about BTC direction. That is a property of models we
# do not control, reached through floating aliases, and it can vanish with a
# provider-side swap: no error, no log line, no alert -- just losses that
# take weeks to read. This job re-measures the edge every week and alerts
# only when it has moved.
#
# COST: ~80 API calls per run (40 samples x 2 providers), once a week.
# Stated here on purpose -- an unattended job spending API budget is exactly
# what silently stopped both AI bots on 2026-08-15.
#
# Runs Monday 09:00 VPS time: after the weekend lull, before the week's
# trading matters. Alerts via Telegram ONLY on DRIFT/FLIP, so a quiet inbox
# means the premise is intact.

$ErrorActionPreference = "Stop"
$Desktop  = "$env:USERPROFILE\Desktop"
$TaskName = "ChartAiEdgeWatch"
$Log      = "$Desktop\edge_watch.log"

$cmd = "cd `"$Desktop`"; python `"$Desktop\_edge_watch.py`" 40 *>> `"$Log`""
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -Command `"$cmd`""
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At 9:00AM
# Interactive Administrator, not SYSTEM: SYSTEM has no USERPROFILE and its
# restarts died in session 0 once already in this repo.
$principal = New-ScheduledTaskPrincipal -UserId "Administrator" `
    -LogonType S4U -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings | Out-Null

$t = Get-ScheduledTask -TaskName $TaskName
"REGISTERED: $($t.TaskName)  state=$($t.State)"
"NEXT RUN  : $((Get-ScheduledTaskInfo -TaskName $TaskName).NextRunTime)"
"LOG       : $Log"
"COST      : ~80 API calls per weekly run"
