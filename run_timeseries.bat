@echo off
rem Launcher called by the scheduled task — appends ONE intraday Nifty GEX snapshot
rem to data\journal\timeseries\YYYY-MM-DD.csv. The script skips on weekends and
rem outside 09:15-15:30 IST, so off-window fires are safe no-ops.
rem Console output + any errors append to data\journal\timeseries_run.log.
cd /d "%~dp0"
if not exist "data\journal" mkdir "data\journal"
rem brief settle so Wi-Fi reconnects if we just woke from sleep (~5s; the NSE
rem fetch also retries / primes cookies).
ping -n 6 127.0.0.1 >nul 2>&1
python scripts\log_timeseries.py --once >> "data\journal\timeseries_run.log" 2>&1
