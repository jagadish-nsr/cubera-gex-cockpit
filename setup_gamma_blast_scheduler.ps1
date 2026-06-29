# RECOMMENDED setup: capture a 1-MIN per-strike gamma-blast snapshot through the
# expiry-day blast window (13:00-15:30 IST), each WEEKDAY, even when NOT logged in,
# WAKING the PC from sleep. The launcher self-gates to EXPIRY DAYS only (and to the
# window), so weekday non-expiry fires are safe no-ops — no need for an expiry
# calendar here.
#
# This COMPLEMENTS setup_timeseries_scheduler.ps1 (the 15-min all-session capture):
# this one adds fine-grained 1-min data ONLY in the blast window, into a separate
# folder (data\journal\gamma_blast\). Run BOTH.
#
# Run it:  right-click this file -> "Run with PowerShell"   (or:
#          powershell -ExecutionPolicy Bypass -File setup_gamma_blast_scheduler.ps1)
# Run-if-not-logged-in (S4U) + wake timers BOTH require Administrator.
#
# Times are LOCAL (UK). Blast window = 13:00-15:30 IST (2h30m).
#   Summer/BST (UTC+1): start 08:30  (IST -4:30)
#   Winter/GMT (UTC+0): start 07:30  (IST -5:30)  -> set $StartTime below and re-run.
#
# HONEST LIMITS (same as the 15-min task):
#  - Works from normal SLEEP (S3). Will NOT wake from hibernate or power-off.
#  - "Modern Standby (S0)" wake timers can be flaky - test it; if unreliable, keep
#    the PC awake in the window or run on an always-on machine.
#  - Weekday-only; expiry/window gating is done by the launcher, not the trigger.

$ErrorActionPreference = 'Stop'

# --- EDIT for winter (GMT): change to '7:30am' and re-run --------------------
$StartTime   = '8:30am'                          # 13:00 IST in BST
$WindowHours = 2; $WindowMinutes = 30            # blast window length (13:00-15:30)
$EveryMins   = 1                                 # capture cadence (1 min)
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

$bat  = Join-Path $PSScriptRoot 'run_gamma_blast.bat'
$days = 'Monday','Tuesday','Wednesday','Thursday','Friday'

# One task per index — the launcher self-gates to each index's expiry day, so a
# daily 1-min fire is a no-op except on that index's expiry (Nifty: weekly Tuesday;
# Bank Nifty: monthly last Tuesday).
$tasks = [ordered]@{ 'NiftyGEX-GammaBlast' = 'NIFTY'; 'BankNiftyGEX-GammaBlast' = 'BANKNIFTY' }

# 1-min cadence: cap each run to 2 min and never stack instances.
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RunOnlyIfNetworkAvailable `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 2)
# S4U: run whether logged on or not, no stored password, keeps network access.
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERNAME" -LogonType S4U -RunLevel Limited

# Weekly weekday trigger, with a 1-min repetition grafted on so it fires through
# the whole blast window then stops until the next weekday (the documented idiom).
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
        Write-Host "OK: registered $name ($sym) - every $EveryMins min from $StartTime for ${WindowHours}h${WindowMinutes}m (= 13:00-15:30 IST), weekdays, expiry-day-gated, wake-to-run + run-if-not-logged-in." -ForegroundColor Green
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
Write-Host 'Per-strike 1-min capture -> data\journal\gamma_blast\<SYMBOL>\YYYY-MM-DD.csv (expiry days only)'
Write-Host 'Run logs -> data\journal\gamma_blast_run_<SYMBOL>.log'
Write-Host 'Test now with:  Start-ScheduledTask -TaskName NiftyGEX-GammaBlast  (or BankNiftyGEX-GammaBlast)'
Write-Host 'Remove with:    Unregister-ScheduledTask -TaskName NiftyGEX-GammaBlast -Confirm:$false'
Write-Host '                Unregister-ScheduledTask -TaskName BankNiftyGEX-GammaBlast -Confirm:$false'
