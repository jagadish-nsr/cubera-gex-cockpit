@echo off
rem ==============================================================================
rem Launch Cubera GEX Cockpit in DEVELOPMENT Mode
rem - Runs on Port 8650 (isolated from Production on 8640)
rem - Enables Auto-Reload (--reload) for real-time development
rem ==============================================================================
cd /d "%~dp0"

echo ======================================================================
echo   STARTING CUBERA GEX COCKPIT -- DEVELOPMENT MODE
echo   URL  : http://localhost:8650
echo   Mode : Hot Reload Active
echo ======================================================================

python web_server.py --port 8650 --reload --dev
if errorlevel 1 pause
