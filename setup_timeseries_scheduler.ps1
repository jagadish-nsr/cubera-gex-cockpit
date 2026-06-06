# RECOMMENDED setup: capture an intraday Nifty GEX snapshot every 15 min through
# the NSE session, each WEEKDAY, even when NOT logged in, WAKING the PC from sleep.
# This is the reliable unattended path (the dashboard's auto-refresh needs an open
# browser; see CLAUDE.md / the time-series notes).
#
# Run it:  right-click this file -> "Run with PowerShell"   (or:
#          powershell -ExecutionPolicy Bypass -File setup_timeseries_scheduler.ps1)
# Run-if-not-logged-in (S4U) + wake timers BOTH require Administrator.
#
# Times are LOCAL (UK). NSE = 09:15-15:30 IST (6h15m).
#   Summer/BST (UTC+1): start 04:45  (IST +4:30)
#   Winter/GMT (UTC+0): start 03:45  (IST +5:30)  -> set $StartTime below and re-run.
#
# HONEST LIMITS (see also VALIDATION.md):
#  - Works from normal SLEEP (S3). Will NOT wake from hibernate or power-off.
#  - "Modern Standby (S0)" machines have flaky wake timers - test it; if unreliable,
#    keep the PC awake in the window or run on an always-on machine.
#  - Weekday-only here; it does NOT know NSE holidays (a holiday fire captures stale
#    data, flagged by the OI staleness check). The script also skips off-window.

$ErrorActionPreference = 'Stop'

# --- EDIT for winter (GMT): change to '3:45am' and re-run -------------------
$StartTime   = '4:45am'                          # 09:15 IST in BST
$WindowHours = 6; $WindowMinutes = 15            # NSE session length (09:15-15:30)
$EveryMins   = 15                                # capture cadence
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

$bat  = Join-Path $PSScriptRoot 'run_timeseries.bat'
$days = 'Monday','Tuesday','Wednesday','Thursday','Friday'

$action = New-ScheduledTaskAction -Execute $bat
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RunOnlyIfNetworkAvailable `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
# S4U: run whether logged on or not, no stored password, keeps network access.
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERNAME" -LogonType S4U -RunLevel Limited

# Weekly weekday trigger, with a 15-min repetition grafted on so it fires through
# the whole session window then stops until the next weekday (the documented idiom).
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At $StartTime
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At $StartTime `
    -RepetitionInterval (New-TimeSpan -Minutes $EveryMins) `
    -RepetitionDuration (New-TimeSpan -Hours $WindowHours -Minutes $WindowMinutes)).Repetition

try {
    Register-ScheduledTask -TaskName 'NiftyGEX-TimeSeries' -Action $action -Trigger $trigger `
        -Settings $settings -Principal $principal -Force | Out-Null
    Write-Host "OK: registered NiftyGEX-TimeSeries - every $EveryMins min from $StartTime for ${WindowHours}h${WindowMinutes}m (= 09:15-15:30 IST), weekdays, wake-to-run + run-if-not-logged-in." -ForegroundColor Green
} catch {
    Write-Host "FAILED to register task: $($_.Exception.Message)" -ForegroundColor Red
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
Write-Host 'Snapshots -> data\journal\timeseries\YYYY-MM-DD.csv ; run log -> data\journal\timeseries_run.log'
Write-Host 'Test now with:  Start-ScheduledTask -TaskName NiftyGEX-TimeSeries'
Write-Host 'Remove with:    Unregister-ScheduledTask -TaskName NiftyGEX-TimeSeries -Confirm:$false'
