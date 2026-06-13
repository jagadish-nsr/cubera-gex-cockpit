@echo off
rem ONE-TIME setup: capture an intraday Nifty GEX snapshot EVERY 15 MIN during the
rem NSE session. Simple tier - runs ONLY while you are logged in (no admin). For
rem unattended / wake-from-sleep capture use setup_timeseries_scheduler.ps1 instead.
rem
rem Times below are LOCAL (this PC is on UK time). NSE = 09:15-15:30 IST (6h15m).
rem   Summer (BST = UTC+1): IST +4:30  -> start 04:45 BST  (= 09:15 IST)
rem   Winter (GMT = UTC+0): IST +5:30  -> start 03:45 GMT  (= 09:15 IST)
rem Re-run this file with the GMT start time after the UK clocks change.
rem
rem /SC DAILY = recur EVERY day; /RI 15 = repeat every 15 min within /DU 0006:15
rem (the 6h15m session window). The task may also fire outside the window / on
rem weekends - that is harmless: the script skips outside 09:15-15:30 IST (you'll
rem just see "outside NSE session - skipped" lines in the run log).
rem DO NOT use /SC MINUTE - that creates a ONE-TIME task that only fires on its
rem start date and never recurs (Next Run Time: N/A). DAILY + /RI is what recurs.
rem Each fire opens a brief console window.
rem Remove with:  schtasks /Delete /TN "NiftyGEX-TimeSeries" /F

setlocal
set "DIR=%~dp0"

schtasks /Create /TN "NiftyGEX-TimeSeries" /TR "\"%DIR%run_timeseries.bat\"" /SC DAILY /ST 04:45 /RI 15 /DU 0006:15 /F

echo.
echo Created task NiftyGEX-TimeSeries (LOCAL/BST): every 15 min from 04:45 for 6h15m
echo   = 09:15-15:30 IST. Snapshots -> data\journal\timeseries\YYYY-MM-DD.csv
echo   Run log -> data\journal\timeseries_run.log
echo Winter (GMT): re-run this file after editing /ST to 03:45.
echo Test now:  schtasks /Run /TN "NiftyGEX-TimeSeries"
pause
