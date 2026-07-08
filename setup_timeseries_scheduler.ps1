# RECOMMENDED setup: capture an intraday Nifty GEX snapshot every 5 min through
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
$EveryMins   = 5                                 # capture cadence (5-min; was 15)
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

# One task per index — each passes its symbol to the launcher, which writes to the
# per-index journal subfolder. Bank Nifty is monthly-only, but the 5-min session
# capture still applies (it records the monthly chain every session day).
$tasks = [ordered]@{ 'NiftyGEX-TimeSeries' = 'NIFTY'; 'BankNiftyGEX-TimeSeries' = 'BANKNIFTY' }

# ExecutionTimeLimit spans the whole session: on an EXPIRY day the 5-min fire
# that first lands in the blast window (13:00 IST) stays resident at 1-min cadence
# until 15:30 IST (bundled gamma-blast — no separate task). MultipleInstances
# IgnoreNew stops the later 5-min fires from stacking on top of that loop.
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RunOnlyIfNetworkAvailable `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 8)
# S4U: run whether logged on or not, no stored password, keeps network access.
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERNAME" -LogonType S4U -RunLevel Limited

# Weekly weekday trigger, with a 5-min repetition grafted on so it fires through
# the whole session window then stops until the next weekday (the documented idiom).
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At $StartTime
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At $StartTime `
    -RepetitionInterval (New-TimeSpan -Minutes $EveryMins) `
    -RepetitionDuration (New-TimeSpan -Hours $WindowHours -Minutes $WindowMinutes)).Repetition

foreach ($name in $tasks.Keys) {
    $sym = $tasks[$name]
    $action = New-ScheduledTaskAction -Execute $bat -Argument $sym
    try {
        Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger `
            -Settings $settings -Principal $principal -Force | Out-Null
        Write-Host "OK: registered $name ($sym) - every $EveryMins min from $StartTime for ${WindowHours}h${WindowMinutes}m (= 09:15-15:30 IST), weekdays, wake-to-run + run-if-not-logged-in." -ForegroundColor Green
    } catch {
        Write-Host "FAILED to register ${name}: $($_.Exception.Message)" -ForegroundColor Red
        exit 1
    }
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
Write-Host 'Snapshots -> data\journal\timeseries\<SYMBOL>\YYYY-MM-DD.csv (1 row/snapshot)'
Write-Host 'Per-strike OI/dOI -> data\journal\oi_strikes\<SYMBOL>\YYYY-MM-DD.csv (1 row/strike)'
Write-Host 'EXPIRY DAYS: this same task auto-captures 1-min gamma-blast 13:00-15:30 IST'
Write-Host '   -> data\journal\gamma_blast\<SYMBOL>\YYYY-MM-DD.csv  (no separate task needed)'
Write-Host 'Run logs -> data\journal\timeseries_run_<SYMBOL>.log'
Write-Host 'Test now with:  Start-ScheduledTask -TaskName NiftyGEX-TimeSeries  (or BankNiftyGEX-TimeSeries)'
Write-Host 'Remove with:    Unregister-ScheduledTask -TaskName NiftyGEX-TimeSeries -Confirm:$false'
Write-Host '                Unregister-ScheduledTask -TaskName BankNiftyGEX-TimeSeries -Confirm:$false'
