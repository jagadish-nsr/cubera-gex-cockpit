@echo off
setlocal

rem Change to project root
cd /d "%~dp0.."

echo Starting GEX Daemon (option chain & participant OI)...
start "GEX Daemon" /B python scripts\breeze_daemon.py
set DAEMON_PID=%!

echo Starting Indian GEX Dashboard (3-Tab)...
python -m streamlit run app\dashboard.py

rem Cleanup when streamlit exits
taskkill /F /IM python.exe /FI "WINDOWTITLE eq GEX Daemon" >nul 2>&1
