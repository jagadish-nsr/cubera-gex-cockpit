@echo off
rem DEPRECATED — you no longer need a separate gamma-blast task.
rem
rem The 1-min gamma-blast capture is now BUNDLED into the time-series task
rem (setup_timeseries_scheduler.bat / .ps1). On an expiry day, the same 15-min task
rem automatically switches to 1-min per-strike capture through 13:00-15:30 IST, into
rem data\journal\gamma_blast\<SYMBOL>\YYYY-MM-DD.csv. Expiry is detected live from the
rem option-chain pull (nearest expiry == today) — nothing to schedule.
rem
rem This script no longer registers a task (that would duplicate captures).
echo DEPRECATED: gamma-blast capture is now bundled into the time-series task.
echo Nothing to register. Ensure the time-series tasks exist:
echo     setup_timeseries_scheduler.bat   (or the .ps1 for wake-from-sleep)
echo Manual one-off run:  python scripts\log_gamma_blast.py --symbol NIFTY  (or BANKNIFTY)
echo If a legacy NiftyGEX-GammaBlast / BankNiftyGEX-GammaBlast task exists, remove it:
echo     schtasks /Delete /TN "NiftyGEX-GammaBlast" /F
echo     schtasks /Delete /TN "BankNiftyGEX-GammaBlast" /F
pause
