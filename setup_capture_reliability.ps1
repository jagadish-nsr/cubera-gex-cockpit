# Make the intraday capture reliable on a MODERN STANDBY (S0) machine.
#
# WHY: this PC reports "Standby (S0 Low Power Idle)" and NO S3 (powercfg /a). On S0
# machines the OS drops to HIBERNATE / Fast-Startup, and scheduled-task WAKE TIMERS DO
# NOT FIRE FROM HIBERNATE — so the 4:45am-UK (09:15 IST) capture silently misses whole
# mornings. There is no reliable "wake from sleep" here; the fix is to stop it sleeping/
# hibernating so that, as long as it is ON and plugged in, it stays awake through the
# IST session (04:45–11:00 UK) and the 5-min task fires.
#
# Run AS ADMINISTRATOR:
#   powershell -ExecutionPolicy Bypass -File setup_capture_reliability.ps1
#
# HONEST LIMIT: this keeps the machine awake when it is ON + on AC. It does NOT wake a
# machine that is SHUT DOWN or manually hibernated overnight. On trading nights either
# (a) leave it ON + plugged in, or (b) run capture on an always-on box (a cheap mini-PC/
# Pi, or a cloud VM in a region NSE accepts — the UK connection already works). See notes.

$ErrorActionPreference = 'Stop'
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "ADMIN REQUIRED. Right-click PowerShell -> 'Run as administrator', then re-run:" -ForegroundColor Yellow
    Write-Host "  & '$PSCommandPath'"
    exit 1
}

Write-Host "=== Sleep states on this system ===" -ForegroundColor Cyan
powercfg /a

# 1) Kill hibernate + Fast Startup (the states wake timers cannot escape on S0).
Write-Host "`nDisabling hibernate + Fast Startup..." -ForegroundColor Cyan
powercfg /h off
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Power" /v HiberbootEnabled /t REG_DWORD /d 0 /f | Out-Null

# 2) Never auto-sleep / auto-hibernate on AC (stay awake in S0 while plugged in).
Write-Host "Setting AC power plan: never sleep, never hibernate (plugged in)..." -ForegroundColor Cyan
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
# keep the disk/monitor free to idle; only STANDBY matters for the capture.

# 3) Enable wake timers (belt-and-suspenders; harmless, helps if S0 ever honours them).
$sleep = '238C9FA8-0AAD-41ED-83F4-97BE242C8F20'
$waketimers = 'BD3B718A-0680-4D9D-8AB2-E1D2B4AC806D'
powercfg /SETACVALUEINDEX SCHEME_CURRENT $sleep $waketimers 1
powercfg /SETDCVALUEINDEX SCHEME_CURRENT $sleep $waketimers 1
powercfg /SETACTIVE SCHEME_CURRENT

Write-Host "`nDONE. On AC power this machine will now stay awake (S0) instead of hibernating." -ForegroundColor Green
Write-Host ""
Write-Host "TO STOP LOSING SESSIONS, on trading nights (Mon-Fri) do ONE of:" -ForegroundColor Yellow
Write-Host "  * Leave the PC ON and plugged in (lid open, or set 'lid close = do nothing')."
Write-Host "  * OR move capture to an always-on device (mini-PC / Pi / UK cloud VM)."
Write-Host ""
Write-Host "VERIFY it worked: after the next weekday session, run"
Write-Host "  python scripts\log_futures_buildup.py ; python scripts\analyze_pinning.py"
Write-Host "and check data\journal\timeseries\NIFTY\<today>.csv spans ~09:15-15:30 IST."
Write-Host "If mornings still gap, the machine was OFF/asleep overnight -> use an always-on box."
Write-Host ""
Write-Host "Lid-close behaviour (optional, so closing the lid doesn't sleep it):"
Write-Host "  powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0 ; powercfg /setactive SCHEME_CURRENT"
