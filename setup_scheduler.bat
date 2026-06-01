@echo off
rem ONE-TIME setup: schedule the Nifty GEX "open" logger to run every weekday.
rem
rem Runs at 09:20 LOCAL time -- this assumes the PC's clock is set to IST (India),
rem so 09:20 = ~5 min after the 09:15 NSE open. If this PC is NOT on IST, change
rem the /ST value below to whatever local time equals ~09:20 IST.
rem
rem The task runs only while you are logged in (no admin/password needed). It opens
rem a brief console window each morning while it pulls data -- that's normal.
rem
rem To REMOVE the task later:  schtasks /Delete /TN "NiftyGEX-OpenLog" /F

setlocal
set "TASK=NiftyGEX-OpenLog"
set "DIR=%~dp0"

schtasks /Create /TN "%TASK%" /TR "\"%DIR%run_logger.bat\"" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 09:20 /F

if %ERRORLEVEL%==0 (
  echo.
  echo OK: scheduled "%TASK%" -- weekdays at 09:20 local time.
  echo It appends to data\journal\sessions.csv each morning.
  echo Remember to fill the outcome columns at the close ^(see VALIDATION.md^).
  echo To remove: schtasks /Delete /TN "%TASK%" /F
) else (
  echo.
  echo FAILED to create the task. Try right-click -^> Run as administrator.
)
pause
