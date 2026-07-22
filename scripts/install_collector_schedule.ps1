# Register a Windows scheduled task that runs the big-money futures collector on weekdays.
#
# The collector is IST-aware: it WAITS for the 09:15 IST market open and STOPS at 15:30 IST, so the
# trigger only needs to fire sometime before open. This machine runs on UK time (BST/GMT, which shifts
# with DST), so we trigger EARLY (03:15 local). That maps to roughly 07:45-08:45 IST year-round, always
# before open, and the collector waits out the slack. It saves raw ticks + big-money events under
# data/journal/ and logs to data/journal/big_money_logs/.
#
# NOTE: needs a VALID Dhan token (data/.dhan_token, 24h-capped) at run time. Paste a fresh one each
# morning before open; the collector retries until a token is present, then connects.
#
#   powershell -ExecutionPolicy Bypass -File scripts\install_collector_schedule.ps1
#   powershell -File scripts\install_collector_schedule.ps1 -Remove
param(
    [string]$TaskName = "GEX-BigMoneyCollector",
    [string]$TriggerLocal = "03:15",
    [string]$Python = "C:\Python313\python.exe",
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$script = Join-Path $repo "scripts\run_big_money_collector.py"

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed scheduled task $TaskName"
    return
}

if (-not (Test-Path $Python)) { throw "Python not found at $Python. Pass -Python with the full path." }
if (-not (Test-Path $script)) { throw "Collector not found at $script." }

$action = New-ScheduledTaskAction -Execute $Python -Argument "`"$script`" --market-hours" -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At $TriggerLocal
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 9) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)
$desc = "Big-money futures tape collector (NIFTY BankNifty SENSEX). Waits for 09:15 IST, stops 15:30 IST. W2"

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description $desc -Force | Out-Null

Write-Host "Registered $TaskName : weekdays at $TriggerLocal local. Collector waits for 09:15 IST, stops 15:30 IST."
Write-Host "Runs only when you are logged on. Paste a fresh Dhan token each morning before open."
Write-Host "Logs under data/journal/big_money_logs/. Remove with the -Remove switch."
