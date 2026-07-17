# ONE-SHOT installer for ALL automatic data collection (Windows Task Scheduler).
# Registers, in one go, the three background captures the dashboard reads:
#   1. Intraday 5-min GEX capture  (Nifty + Bank Nifty; auto-switches to 1-min
#      gamma-blast capture on EXPIRY days, 13:00-15:30 IST — no separate task)
#   2. Daily EOD Nifty index-FUTURES OI build-up   (~20:00 IST)
#   3. Daily EOD PARTICIPANT OI  (Client/DII/FII/Pro)  (~20:00 IST)
#
# RUN AS ADMINISTRATOR (wake-from-sleep + run-if-not-logged-in need admin):
#   Start -> type "PowerShell" -> right-click -> "Run as administrator", then:
#     cd C:\Users\navee\Python\gex-indian
#     powershell -ExecutionPolicy Bypass -File setup_data_collection.ps1
#
# NO Dhan token is needed for daily collection — these use public NSE endpoints.
#
# SEASON (UK clocks): the intraday start time defaults to BST (summer). In WINTER
# (GMT) edit $StartTime near the top of setup_timeseries_scheduler.ps1 (and the two
# EOD scripts) to the winter value in each script's header, then re-run this. NSE
# session = 09:15-15:30 IST.

$ErrorActionPreference = 'Stop'
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "ADMIN REQUIRED. Right-click PowerShell -> 'Run as administrator', then re-run:" -ForegroundColor Yellow
    Write-Host "  powershell -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    exit 1
}

$scripts = @(
    'setup_timeseries_scheduler.ps1',        # intraday 5-min + bundled 1-min gamma-blast
    'setup_futures_buildup_scheduler.ps1',   # daily EOD futures OI
    'setup_participant_oi_scheduler.ps1'     # daily EOD participant OI
)
foreach ($s in $scripts) {
    $p = Join-Path $PSScriptRoot $s
    if (-not (Test-Path $p)) { Write-Host "MISSING: $s (skipped)" -ForegroundColor Red; continue }
    Write-Host "`n=== Running $s ===" -ForegroundColor Cyan
    # each in its own process so its internal exit-code can't abort this installer
    & powershell -NoProfile -ExecutionPolicy Bypass -File $p
}

Write-Host "`nDone. Verify the scheduled tasks with:" -ForegroundColor Green
Write-Host "  Get-ScheduledTask | Where-Object TaskName -like '*GEX*'"
Write-Host "Test the intraday one now (writes a snapshot if in-session):" -ForegroundColor Green
Write-Host "  Start-ScheduledTask -TaskName NiftyGEX-TimeSeries"
Write-Host "`nModern-Standby laptop that misses whole mornings? Also run (as admin):" -ForegroundColor Yellow
Write-Host "  powershell -ExecutionPolicy Bypass -File setup_capture_reliability.ps1"
Write-Host "…and keep the PC ON + plugged in through the 09:15-15:30 IST session." -ForegroundColor Yellow
