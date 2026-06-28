@echo off
rem ONE-TIME setup: capture a 1-MIN per-strike gamma-blast snapshot during the
rem expiry-day blast window (13:00-15:30 IST). Simple tier - runs ONLY while you
rem are logged in (no admin). For unattended / wake-from-sleep capture use
rem setup_gamma_blast_scheduler.ps1 instead.
rem
rem This COMPLEMENTS setup_timeseries_scheduler.bat (the 15-min all-session
rem capture); run BOTH. The launcher self-gates to EXPIRY DAYS + the window, so
rem firing every minute on a non-expiry day is a harmless no-op (you will see
rem "not an expiry day - skipped" / "outside window - skipped" in the run log).
rem
rem Times below are LOCAL (this PC is on UK time). Blast window = 13:00-15:30 IST.
rem   Summer (BST = UTC+1): IST -4:30  -> start 08:30 BST  (= 13:00 IST)
rem   Winter (GMT = UTC+0): IST -5:30  -> start 07:30 GMT  (= 13:00 IST)
rem Re-run this file with the GMT start time after the UK clocks change.
rem
rem /SC DAILY = recur EVERY day; /RI 1 = repeat every 1 min within /DU 0002:30
rem (the 2h30m blast window). DAILY + /RI is what recurs (do NOT use /SC MINUTE).
rem Each fire opens a brief console window.
rem Remove with:  schtasks /Delete /TN "NiftyGEX-GammaBlast" /F

setlocal
set "DIR=%~dp0"

schtasks /Create /TN "NiftyGEX-GammaBlast" /TR "\"%DIR%run_gamma_blast.bat\"" /SC DAILY /ST 08:30 /RI 1 /DU 0002:30 /F

echo.
echo Created task NiftyGEX-GammaBlast (LOCAL/BST): every 1 min from 08:30 for 2h30m
echo   = 13:00-15:30 IST, expiry days only. Per-strike 1-min capture ->
echo   data\journal\gamma_blast\YYYY-MM-DD.csv
echo   Run log -> data\journal\gamma_blast_run.log
echo Winter (GMT): re-run this file after editing /ST to 07:30.
echo Test now:  schtasks /Run /TN "NiftyGEX-GammaBlast"
pause
