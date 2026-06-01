# Advanced scheduler setup: run the Nifty GEX logger even when NOT logged in, and
# WAKE the PC from sleep to run it. Twice each weekday (open + ~3PM IST).
#
# Run it:  right-click this file -> "Run with PowerShell"   (or:
#          powershell -ExecutionPolicy Bypass -File setup_scheduler_wake.ps1)
# Enabling wake timers needs Administrator; the task registration does not.
#
# Times are LOCAL (UK). Summer/BST: 04:50 = 09:20 IST, 10:30 = 15:00 IST.
# Winter/GMT: edit the two -At times to 03:50 and 09:30 and re-run.
#
# HONEST LIMITS (see also VALIDATION.md):
#  - Works from normal SLEEP (S3). Will NOT wake from hibernate or power-off.
#  - Many recent laptops use "Modern Standby (S0)" where wake timers are flaky
#    and can drain battery — test it; if unreliable, keep the PC awake in the
#    window or run on an always-on machine.
#  - After running, Windows returns to sleep on the "unattended sleep timeout".

$ErrorActionPreference = 'Stop'
$bat = Join-Path $PSScriptRoot 'run_logger.bat'
$days = 'Monday','Tuesday','Wednesday','Thursday','Friday'

$action = New-ScheduledTaskAction -Execute $bat
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RunOnlyIfNetworkAvailable `
    -MultipleInstances IgnoreNew
# S4U: run whether logged on or not, no stored password, keeps network access.
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERNAME" -LogonType S4U -RunLevel Limited

$tOpen = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At 4:50am
$tAft  = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At 10:30am

Register-ScheduledTask -TaskName 'NiftyGEX-OpenLog'      -Action $action -Trigger $tOpen -Settings $settings -Principal $principal -Force | Out-Null
Register-ScheduledTask -TaskName 'NiftyGEX-AfternoonLog' -Action $action -Trigger $tAft  -Settings $settings -Principal $principal -Force | Out-Null
Write-Host 'Registered NiftyGEX-OpenLog (04:50) and NiftyGEX-AfternoonLog (10:30): wake-to-run + run-if-not-logged-in.'

# Enable "Allow wake timers" in the active power plan (needs admin).
try {
    $sleep = '238C9FA8-0AAD-41ED-83F4-97BE242C8F20'   # SUB_SLEEP
    $waketimers = 'BD3B718A-0680-4D9D-8AB2-E1D2B4AC806D'  # Allow wake timers
    powercfg /SETACVALUEINDEX SCHEME_CURRENT $sleep $waketimers 1 | Out-Null
    powercfg /SETDCVALUEINDEX SCHEME_CURRENT $sleep $waketimers 1 | Out-Null
    powercfg /SETACTIVE SCHEME_CURRENT | Out-Null
    Write-Host 'Wake timers enabled in the active power plan.'
} catch {
    Write-Host 'Could NOT enable wake timers (need Administrator). Re-run this script as admin,'
    Write-Host 'or set Power Options -> Sleep -> "Allow wake timers" = Enable manually.'
}

Write-Host ''
Write-Host 'Verify in Task Scheduler, or test now with:  Start-ScheduledTask -TaskName NiftyGEX-OpenLog'
Write-Host 'Remove with:  Unregister-ScheduledTask -TaskName NiftyGEX-OpenLog,NiftyGEX-AfternoonLog -Confirm:$false'
