@echo off
rem Launcher for the EXPIRY-DAY 1-min gamma-blast capture. Appends ONE per-strike
rem snapshot (OI/dOI + nearest-expiry premiums + gamma exposure) to
rem data\journal\gamma_blast\<SYMBOL>\YYYY-MM-DD.csv. Self-gates: it skips on
rem weekends, outside 13:00-15:30 IST, and on non-expiry days, so a 1-min fire is a
rem safe no-op. (Bank Nifty's only expiry is the monthly last Tuesday.)
rem
rem   %1 = index symbol (NIFTY or BANKNIFTY). Defaults to NIFTY when omitted.
rem Console output + errors append to data\journal\gamma_blast_run_<SYMBOL>.log.
rem
rem For unattended use, register a Task Scheduler task firing every 1 min from
rem the BST/GMT equivalent of 13:00 IST for 2h30m (mirror setup_timeseries_*),
rem pointing at this .bat. Or just run it once in a console on expiry afternoons:
rem   python scripts\log_gamma_blast.py --symbol NIFTY
cd /d "%~dp0"
set "SYMBOL=%~1"
if "%SYMBOL%"=="" set "SYMBOL=NIFTY"
if not exist "data\journal" mkdir "data\journal"
python scripts\log_gamma_blast.py --once --symbol %SYMBOL% >> "data\journal\gamma_blast_run_%SYMBOL%.log" 2>&1
