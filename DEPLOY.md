# Deploying gex-indian to another machine

How to ship the app + the intraday time-series scheduler to a second Windows
laptop. Same disclaimer as everywhere: decision-support only, paper trading,
dealer-sign **UNVALIDATED** — see `CLAUDE.md` and `VALIDATION.md`.

> These machines are on **UK time**. The scheduler files default to **BST**
> (04:45 = 09:15 IST). India has no DST, so if you ever deploy to an **IST**
> laptop instead, set the start time to `9:15am` and skip the DST step below.

## 1. Build a clean zip (on the source machine)
`git archive` ships only committed files — so it automatically excludes your
personal captures (`data/journal/` is gitignored), `.venv/`, `__pycache__/`, and
`.git/`:

```
git archive --format=zip --prefix=gex-indian/ -o ../gex-indian-deploy.zip HEAD
```

Extracts to a `gex-indian/` folder. (If you copy the folder by hand instead,
delete `data/journal/` first — that's personal data, and the target should start
with an empty time-series.)

## 2. Install Python + dependencies (on the target machine)
- Install **Python 3.11+** from python.org — **tick "Add Python to PATH"**.
  (Required: the scheduler launcher calls a bare `python`.)
- In the extracted folder:
  ```
  pip install -r requirements.txt
  python -m pytest -q
  ```
  All tests passing confirms a sound offline install.

> **venv gotcha:** `run_timeseries.bat` calls bare `python`. Install into the
> default user Python (no virtualenv) so the scheduled task finds the deps — or
> edit `run_timeseries.bat` to point at the venv's `python.exe`.

## 3. Verify a live NSE pull works on this machine/connection
```
python scripts/log_timeseries.py --once --ignore-session
```
This is the main risk on a new machine: **NSE blocks bots / datacenter / many
non-Indian IPs**. A normal Indian home connection should just work and print a
snapshot line. If you get "NSE fetch produced no data", the connection is gated —
fix that before bothering with the scheduler (it can't fix a blocked pull).

## 4. Register the scheduler (auto-capture every 15 min, 09:15–15:30 IST)
- **Recommended (unattended + wakes from sleep, weekdays):** right-click
  `setup_timeseries_scheduler.ps1` → **Run with PowerShell** as Administrator.
- **Simple (only while logged in, no admin):** double-click
  `setup_timeseries_scheduler.bat`.

Off-window / weekend / holiday fires are harmless — `--once` skips outside
09:15–15:30 IST.

## 5. Confirm it's live
```
Start-ScheduledTask -TaskName NiftyGEX-TimeSeries
```
Check `data\journal\timeseries_run.log` for a success line and that a
`data\journal\timeseries\YYYY-MM-DD.csv` was created. Opening the dashboard
(`run_app.bat`) and refreshing will show the intraday chart fill in.

## Recurring maintenance
- **DST (twice a year):** the scheduler defaults to **BST (04:45)**. When UK
  clocks change to GMT in late October, edit the start time to **03:45** and
  re-run the setup file; switch back to **04:45** in spring. (Same as the
  existing `log_session` scheduler.)
- **NSE holidays:** there's no holiday calendar yet — a holiday fire records
  stale data, flagged by the OI staleness check. Ignore those rows.
- **Lot size:** `config.NIFTY_LOT_SIZE` (currently 75) is shared across machines
  and still flagged to verify against the current NSE circular. It scales GEX
  magnitudes, not the wall/flip levels.

## Removing the scheduler
```
schtasks /Delete /TN "NiftyGEX-TimeSeries" /F
# or, if registered via the .ps1:
Unregister-ScheduledTask -TaskName NiftyGEX-TimeSeries -Confirm:$false
```
