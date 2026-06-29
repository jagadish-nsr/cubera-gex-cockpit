@echo off
rem Launcher called by the scheduled task — appends ONE intraday GEX snapshot for
rem the given index to data\journal\timeseries\<SYMBOL>\YYYY-MM-DD.csv, plus the
rem per-strike OI/dOI rows to data\journal\oi_strikes\<SYMBOL>\YYYY-MM-DD.csv (same
rem fetch). The script skips on weekends and outside 09:15-15:30 IST, so off-window
rem fires are safe no-ops.
rem
rem   %1 = index symbol (NIFTY or BANKNIFTY). Defaults to NIFTY when omitted.
rem Console output + any errors append to data\journal\timeseries_run_<SYMBOL>.log.
cd /d "%~dp0"
set "SYMBOL=%~1"
if "%SYMBOL%"=="" set "SYMBOL=NIFTY"
if not exist "data\journal" mkdir "data\journal"
rem brief settle so Wi-Fi reconnects if we just woke from sleep (~5s; the NSE
rem fetch also retries / primes cookies).
ping -n 6 127.0.0.1 >nul 2>&1
python scripts\log_timeseries.py --once --symbol %SYMBOL% >> "data\journal\timeseries_run_%SYMBOL%.log" 2>&1
