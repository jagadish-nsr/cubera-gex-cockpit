@echo off
rem ==============================================================================
rem Launch Cubera GEX Cockpit in PRODUCTION Mode
rem - Runs on Port 8640
rem - High-stability, zero-reload execution
rem ==============================================================================
cd /d "%~dp0"
if not exist "logs" mkdir "logs"

echo ======================================================================
echo   STARTING CUBERA GEX COCKPIT -- PRODUCTION MODE
echo   URL  : http://localhost:8640
echo   Mode : Stable Institutional Daemon
echo ======================================================================

python web_server.py --port 8640
if errorlevel 1 pause
