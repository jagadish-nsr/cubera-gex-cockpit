@echo off
rem ==============================================================================
rem Setup: Daily post-close capture of NSE participant-wise OI (Client/DII/FII/Pro)
rem Runs setup_participant_oi_scheduler.ps1 with Administrator privileges
rem ==============================================================================
cd /d "%~dp0"

net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [!] Administrator permissions required for wake-from-sleep scheduled task.
    echo Requesting elevation...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process cmd -ArgumentList '/c \"\"%~f0\"\"' -Verb RunAs"
    exit /b
)

echo Registering GEX-ParticipantOI task...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_participant_oi_scheduler.ps1"
echo.
pause
