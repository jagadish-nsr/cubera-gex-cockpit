@echo off
rem Launcher called by the scheduled task — logs today's Nifty GEX open snapshot.
rem Output (and any errors) are appended to data\journal\logger_run.log.
cd /d "%~dp0"
if not exist "data\journal" mkdir "data\journal"
python scripts\log_session.py >> "data\journal\logger_run.log" 2>&1
