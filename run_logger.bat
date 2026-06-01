@echo off
rem Launcher called by the scheduled task — logs today's Nifty GEX open snapshot.
rem Output (and any errors) are appended to data\journal\logger_run.log.
cd /d "%~dp0"
if not exist "data\journal" mkdir "data\journal"
rem brief settle so Wi-Fi reconnects if we just woke from sleep (ping = headless-safe
rem wait; the NSE fetch also retries). ~15s.
ping -n 16 127.0.0.1 >nul 2>&1
python scripts\log_session.py >> "data\journal\logger_run.log" 2>&1
