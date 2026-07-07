# Daily post-close capture of the NIFTY index-futures OI build-up from the NSE F&O
# bhavcopy (price+OI matrix, sign-free — see docs / app/futures_buildup.py).
# Runs ONCE per weekday evening, even when NOT logged in, WAKING the PC from sleep.
#
# Run it:  right-click this file -> "Run with PowerShell"   (or:
#          powershell -ExecutionPolicy Bypass -File setup_futures_buildup_scheduler.ps1)
# Run-if-not-logged-in (S4U) + wake timers BOTH require Administrator.
#
# WHY THIS TIME: NSE publishes the F&O bhavcopy EOD, in the evening (after the 15:30
# IST close). We target ~20:00 IST to be safely past publication. The capture is
# best-effort + idempotent and walks back a few days, so a slightly-early or missed
# fire self-heals on the next run — timing is not critical.
#
# NOTE: the INTRADAY futures snapshots are already covered — they piggyback the
# NiftyGEX-TimeSeries task (scripts/log_timeseries.py). This task is ONLY the daily
# EOD bhavcopy build-up; no separate intraday task is needed.
#
# Times below are LOCAL (UK). 20:00 IST = IST - 4:30 (BST) / - 5:30 (GMT):
#   Summer/BST (UTC+1): 15:30
#   Winter/GMT (UTC+0): 14:30  -> set $StartTime below and re-run.
#
# HONEST LIMITS (same as the other EOD tasks): wakes from normal SLEEP (S3) only,
# not hibernate/power-off; weekday-only, NSE-holiday-unaware (the walk-back covers a
# holiday by re-capturing the last trading day).

$ErrorActionPreference = 'Stop'

# --- EDIT for winter (GMT): change to '2:30pm' and re-run -------------------
$StartTime = '3:30pm'                              # 20:00 IST in BST
# ---------------------------------------------------------------------------

# S4U ("run whether logged on or not") + wake timers BOTH require Administrator.
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "ADMIN REQUIRED: this sets a wake-from-sleep / run-if-not-logged-in task," -ForegroundColor Yellow
    Write-Host "which needs Administrator. Right-click PowerShell -> 'Run as administrator',"
    Write-Host "then re-run:  & '$PSCommandPath'"
    exit 1
}

$bat  = Join-Path $PSScriptRoot 'run_futures_buildup.bat'
$days = 'Monday','Tuesday','Wednesday','Thursday','Friday'
$name = 'GEX-FuturesBuildup'

# Per-index daily file, but NIFTY only here (the verified/in-use symbol).
# StartWhenAvailable catches up if the PC was off at fire time; capture is idempotent.
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RunOnlyIfNetworkAvailable `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
# S4U: run whether logged on or not, no stored password, keeps network access.
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERNAME" -LogonType S4U -RunLevel Limited
$trigger   = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At $StartTime
$action    = New-ScheduledTaskAction -Execute $bat

try {
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger `
        -Settings $settings -Principal $principal -Force | Out-Null
    Write-Host "OK: registered $name - once at $StartTime UK (= ~20:00 IST), weekdays, wake-to-run + run-if-not-logged-in." -ForegroundColor Green
} catch {
    Write-Host "FAILED to register ${name}: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

# Enable "Allow wake timers" in the active power plan.
$sleep = '238C9FA8-0AAD-41ED-83F4-97BE242C8F20'       # SUB_SLEEP
$waketimers = 'BD3B718A-0680-4D9D-8AB2-E1D2B4AC806D'  # Allow wake timers
powercfg /SETACVALUEINDEX SCHEME_CURRENT $sleep $waketimers 1 *> $null
powercfg /SETDCVALUEINDEX SCHEME_CURRENT $sleep $waketimers 1 *> $null
powercfg /SETACTIVE SCHEME_CURRENT *> $null
if ($LASTEXITCODE -eq 0) {
    Write-Host 'Wake timers enabled in the active power plan.' -ForegroundColor Green
} else {
    Write-Host 'WARN: could not enable wake timers; set Power Options -> Sleep -> "Allow wake timers" = Enable manually.' -ForegroundColor Yellow
}

Write-Host ''
Write-Host 'Futures build-up -> data\journal\futures\NIFTY\YYYY-MM-DD.csv (1 row/day, daily EOD)'
Write-Host 'Run log          -> data\journal\futures_buildup_run.log'
Write-Host 'Test now with: Start-ScheduledTask -TaskName GEX-FuturesBuildup'
Write-Host 'Remove with:   Unregister-ScheduledTask -TaskName GEX-FuturesBuildup -Confirm:$false'
