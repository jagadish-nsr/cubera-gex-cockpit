@echo off
rem ONE-TIME setup: schedule the Nifty GEX logger TWICE each weekday.
rem
rem Times below are LOCAL. This PC is on UK time. NSE runs in IST (UTC+5:30).
rem   Summer (BST = UTC+1): IST is 4h30 ahead, so
rem      OPEN  ~09:20 IST = 04:50 BST     AFTERNOON 15:00 IST = 10:30 BST
rem
rem NOTE (DST): when UK clocks go to GMT (UTC+0, late Oct), IST is 5h30 ahead.
rem   Re-run this file after changing the times to: OPEN 03:50, AFTERNOON 09:30.
rem
rem Tasks run only while you are logged in (no admin/password). Each opens a brief
rem console window while it pulls data. Remove with:
rem   schtasks /Delete /TN "NiftyGEX-OpenLog" /F
rem   schtasks /Delete /TN "NiftyGEX-AfternoonLog" /F

setlocal
set "DIR=%~dp0"

schtasks /Create /TN "NiftyGEX-OpenLog"      /TR "\"%DIR%run_logger.bat\"" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 04:50 /F
schtasks /Create /TN "NiftyGEX-AfternoonLog" /TR "\"%DIR%run_logger.bat\"" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 10:30 /F

echo.
echo Created two tasks (LOCAL/BST times):
echo   NiftyGEX-OpenLog       04:50  (= 09:20 IST, just after the open)
echo   NiftyGEX-AfternoonLog  10:30  (= 15:00 IST, near the close)
echo Logs append to data\journal\sessions.csv.
echo After ~10 sessions run:  python scripts\score_sessions.py
pause
