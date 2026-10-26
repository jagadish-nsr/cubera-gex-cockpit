@echo off
setlocal

rem Change to project root
cd /d "%~dp0.."

echo Starting GEX Daemon (option chain & participant OI)...
start "GEX Daemon" /B python scripts\gex_daemon.py
set DAEMON_PID=%!

echo Starting the web cockpit...
python web_server.py --port 8640

rem Cleanup when the server exits
taskkill /F /IM python.exe /FI "WINDOWTITLE eq GEX Daemon" >nul 2>&1
