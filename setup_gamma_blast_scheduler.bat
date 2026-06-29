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

rem One task per index — the launcher self-gates to each index's expiry day (Nifty:
rem weekly Tuesday; Bank Nifty: monthly last Tuesday), so daily fires are no-ops
rem otherwise. Per-index journal subfolder (data\journal\gamma_blast\<SYMBOL>\...).
schtasks /Create /TN "NiftyGEX-GammaBlast" /TR "\"%DIR%run_gamma_blast.bat\" NIFTY" /SC DAILY /ST 08:30 /RI 1 /DU 0002:30 /F
schtasks /Create /TN "BankNiftyGEX-GammaBlast" /TR "\"%DIR%run_gamma_blast.bat\" BANKNIFTY" /SC DAILY /ST 08:30 /RI 1 /DU 0002:30 /F

echo.
echo Created tasks NiftyGEX-GammaBlast + BankNiftyGEX-GammaBlast (LOCAL/BST):
echo   every 1 min from 08:30 for 2h30m = 13:00-15:30 IST, expiry days only.
echo   Per-strike 1-min capture -> data\journal\gamma_blast\<SYMBOL>\YYYY-MM-DD.csv
echo   Run logs -> data\journal\gamma_blast_run_<SYMBOL>.log
echo Winter (GMT): re-run this file after editing both /ST to 07:30.
echo Test now:  schtasks /Run /TN "NiftyGEX-GammaBlast"   (or BankNiftyGEX-GammaBlast)
pause
