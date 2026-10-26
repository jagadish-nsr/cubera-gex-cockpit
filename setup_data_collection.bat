@echo off
rem ==============================================================================
rem One-Shot Installer for ALL Automatic Data Collection (Windows Task Scheduler)
rem Registers:
rem   1. Intraday 5-min GEX capture (with auto 1-min gamma-blast on expiry days)
rem   2. Daily EOD Nifty index-futures OI build-up
rem   3. Daily EOD Participant OI (Client / DII / FII / Pro)
rem ==============================================================================
cd /d "%~dp0"

net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [!] Administrator permissions required. Requesting elevation...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process cmd -ArgumentList '/c \"\"%~f0\"\"' -Verb RunAs"
    exit /b
)

echo ======================================================================
echo   INSTALLING AUTOMATIC DATA COLLECTION SCHEDULED TASKS
echo ======================================================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_data_collection.ps1"
echo.
pause
