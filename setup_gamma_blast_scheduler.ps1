# DEPRECATED — you no longer need a separate gamma-blast task.
#
# The 1-min gamma-blast capture is now BUNDLED into the time-series task
# (setup_timeseries_scheduler.ps1 / .bat). On an expiry day, the same 15-min task
# automatically switches to 1-min per-strike capture through 13:00-15:30 IST, into
# data\journal\gamma_blast\<SYMBOL>\YYYY-MM-DD.csv. Expiry is detected live from the
# option-chain pull (nearest expiry == today) — nothing to schedule or maintain.
#
# This script intentionally does NOT register a task anymore, to avoid duplicate
# captures. For a one-off manual run on an expiry afternoon, use:
#     python scripts\log_gamma_blast.py --symbol NIFTY      (or BANKNIFTY)

Write-Host "DEPRECATED: gamma-blast capture is now bundled into the time-series task." -ForegroundColor Yellow
Write-Host "Nothing to register. Ensure the time-series tasks exist:" -ForegroundColor Yellow
Write-Host "    powershell -ExecutionPolicy Bypass -File setup_timeseries_scheduler.ps1"
Write-Host "Manual one-off run:  python scripts\log_gamma_blast.py --symbol NIFTY  (or BANKNIFTY)"
# If a legacy standalone task exists from an earlier version, offer to remove it.
foreach ($name in 'NiftyGEX-GammaBlast','BankNiftyGEX-GammaBlast') {
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        Write-Host "Found legacy task $name — remove it (it now duplicates the bundled capture) with:" -ForegroundColor Yellow
        Write-Host "    Unregister-ScheduledTask -TaskName $name -Confirm:`$false"
    }
}
