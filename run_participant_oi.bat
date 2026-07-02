@echo off
rem Launcher called by the scheduled task — fetches NSE's participant-wise OI EOD
rem file (Client/DII/FII/Pro index F&O) and caches it to
rem data\journal\participant_oi\YYYY-MM-DD.csv (flat, market-wide; not per-index).
rem
rem Best-effort + idempotent: if the file isn't published yet it walks back a few
rem days; if today is already captured it's a no-op. Runs once per weekday evening
rem (participant OI is EOD, published after the 15:30 IST close).
rem Console output + any errors append to data\journal\participant_oi_run.log.
cd /d "%~dp0"
if not exist "data\journal" mkdir "data\journal"
rem brief settle so Wi-Fi reconnects if we just woke from sleep (~5s).
ping -n 6 127.0.0.1 >nul 2>&1
python scripts\log_participant_oi.py >> "data\journal\participant_oi_run.log" 2>&1
