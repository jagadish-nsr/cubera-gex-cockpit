@echo off
rem Launcher called by the scheduled task — fetches NSE's F&O bhavcopy (EOD, per
rem contract) and caches the NIFTY index-futures OI build-up to
rem data\journal\futures\NIFTY\YYYY-MM-DD.csv (one row/day; per-index).
rem
rem Best-effort + idempotent: if the bhavcopy isn't published yet it walks back a few
rem days; if today is already captured it's a no-op. Runs once per weekday evening
rem (the bhavcopy is EOD, published after the 15:30 IST close).
rem Console output + any errors append to data\journal\futures_buildup_run.log.
cd /d "%~dp0"
if not exist "data\journal" mkdir "data\journal"
rem brief settle so Wi-Fi reconnects if we just woke from sleep (~5s).
ping -n 6 127.0.0.1 >nul 2>&1
python scripts\log_futures_buildup.py >> "data\journal\futures_buildup_run.log" 2>&1
