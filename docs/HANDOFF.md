# Nifty GEX — Setup & Daily-Use Guide

A plain-language handoff for running the **Simple dashboard** and turning on the
**automatic data collection** it reads. Decision-support only — paper trading; you
confirm every trade. No coding needed; every step is a copy-paste command or a
double-click.

Project folder (everything below assumes this path):

```
C:\Users\navee\Python\gex-indian
```

---

## 0. One-time: install Python (once per PC)

1. Install **Python 3.11+** from https://www.python.org/downloads/ — on the first
   installer screen **tick "Add python.exe to PATH"**, then "Install Now".
2. That's it. The dashboard installs its own extra bits automatically on first run.

*(No Dhan token or login is needed for daily use — the dashboard and the daily
capture use public NSE data.)*

---

## 1. Open the dashboard (daily)

**Windows:** double-click `run_simple.bat` in the project folder. A black window opens and
the dashboard appears in your browser. The first launch takes ~1 minute (it installs
dependencies once); after that it's instant. **Close the black window to stop it.**

**macOS / Linux:** double-click `run_simple.command` (first time only: right-click → **Open** to
clear Gatekeeper, or run `chmod +x run_simple.command` once in Terminal). Same behaviour — a window
opens and the dashboard appears in your browser; close the window / press Ctrl-C to stop it.

Either way the dashboard is served at **http://localhost:8610**.

Command-line equivalent (if you prefer typing) — forward slashes work on every OS:

```
cd <the project folder>
python -m streamlit run app/simple_dashboard.py --server.port 8610
```
(On macOS use `python3` if `python` isn't found.)

On the page: use the sidebar **🔄 Refresh** (or tick **⏱️ Auto-refresh**). On expiry
day the gamma-blast panel lights up automatically. Tick **🔔 Sound alert on ignition**
and **click the page once** to "arm" the sound (browsers block audio until you click).

---

## 2. Turn on automatic data collection (one-time, as Administrator)

**Why:** the dashboard's *today's range*, *positioning footprint*, *OI build-up
windows*, and the *gamma-blast timing/alert* all read snapshots saved during the day.
This step schedules that capture to run by itself in the background — even when you're
not logged in — so the data is there when you open the dashboard.

1. **Start → type `PowerShell` → right-click "Windows PowerShell" → "Run as
   administrator".**
2. Paste these two lines (one command sets up all three captures):

```powershell
cd C:\Users\navee\Python\gex-indian
powershell -ExecutionPolicy Bypass -File setup_data_collection.ps1
```

That registers three scheduled tasks:

| Task | What it captures | When |
|---|---|---|
| **NiftyGEX-TimeSeries** + **BankNiftyGEX-TimeSeries** | intraday GEX + per-strike OI every 5 min; **auto 1-min gamma-blast on expiry days** | every weekday, 09:15–15:30 IST |
| **NiftyGEX-FuturesBuildup** | daily futures OI build-up (EOD bhavcopy) | weekday evening |
| **NiftyGEX-ParticipantOI** | Client/DII/FII/Pro positioning (EOD) | weekday evening |

*(Prefer to run them one at a time instead of the one-shot? Same PowerShell,
as admin:)*

```powershell
powershell -ExecutionPolicy Bypass -File setup_timeseries_scheduler.ps1
powershell -ExecutionPolicy Bypass -File setup_futures_buildup_scheduler.ps1
powershell -ExecutionPolicy Bypass -File setup_participant_oi_scheduler.ps1
```

### ⏰ Summer vs winter time (do this at each clock change)

The start times are **UK local** and default to **summer (BST)**. NSE runs
09:15–15:30 IST. In **winter (GMT)**, open each `setup_*_scheduler.ps1`, change the
`$StartTime` near the top to the **winter value written in that file's header**
(e.g. intraday `4:45am` → `3:45am`), and re-run the command for it. Each file's
header spells out both values.

### 💻 If it's a "Modern Standby" laptop that misses whole mornings

Some laptops can't wake from sleep for a scheduled task. Fix (run **as
administrator**), then keep the PC **on and plugged in** through the IST session:

```powershell
powershell -ExecutionPolicy Bypass -File setup_capture_reliability.ps1
```

---

## 3. Expiry day — nothing extra to do

On an expiry day the **same** intraday task automatically switches to **1-minute**
gamma-blast capture from 13:00–15:30 IST (into `data\journal\gamma_blast\`). Just
keep the PC **on**. Open the dashboard to watch the blast panel, the ignition-timing
readout, and the 🔔 alert.

---

## 4. Check it's actually working

**Data files** appear under `data\journal\` (created automatically):

```
data\journal\timeseries\NIFTY\YYYY-MM-DD.csv       (5-min GEX snapshots)
data\journal\oi_strikes\NIFTY\YYYY-MM-DD.csv       (per-strike OI)
data\journal\gamma_blast\NIFTY\YYYY-MM-DD.csv       (expiry-day 1-min)
data\journal\futures\NIFTY\YYYY-MM-DD.csv           (daily futures)
data\journal\participant_oi\YYYY-MM-DD.csv          (daily participant OI)
```

**Run logs** (what each task did / any errors):

```
data\journal\timeseries_run_NIFTY.log
data\journal\futures_buildup_run.log
data\journal\participant_oi_run.log
```

**PowerShell checks** (normal window is fine):

```powershell
cd C:\Users\navee\Python\gex-indian
Get-ScheduledTask | Where-Object TaskName -like '*GEX*'      # list the tasks
Start-ScheduledTask -TaskName NiftyGEX-TimeSeries            # fire one now (writes if in-session)
```

---

## 5. Manual one-off capture (optional)

Double-click any of these to grab one snapshot right now (safe no-op outside market
hours):

```
run_timeseries.bat        (intraday GEX + per-strike OI, one snapshot)
run_gamma_blast.bat       (expiry-day 1-min, one snapshot)
run_futures_buildup.bat   (daily futures OI)
run_participant_oi.bat    (daily participant OI)
```

---

## 6. Remove / stop the automatic tasks (if ever needed)

```powershell
Unregister-ScheduledTask -TaskName NiftyGEX-TimeSeries -Confirm:$false
Unregister-ScheduledTask -TaskName BankNiftyGEX-TimeSeries -Confirm:$false
Unregister-ScheduledTask -TaskName NiftyGEX-FuturesBuildup -Confirm:$false
Unregister-ScheduledTask -TaskName NiftyGEX-ParticipantOI -Confirm:$false
```

---

## Quick reference — all commands in one place

```powershell
# --- open the dashboard (daily) ---
cd C:\Users\navee\Python\gex-indian
python -m streamlit run app\simple_dashboard.py       # (or double-click run_simple.bat)

# --- turn on all data collection (ONCE, as Administrator) ---
powershell -ExecutionPolicy Bypass -File setup_data_collection.ps1

# --- laptop that misses mornings (ONCE, as Administrator) ---
powershell -ExecutionPolicy Bypass -File setup_capture_reliability.ps1

# --- verify ---
Get-ScheduledTask | Where-Object TaskName -like '*GEX*'
Start-ScheduledTask -TaskName NiftyGEX-TimeSeries
```

### Good to know
- **No Dhan token** is needed for the dashboard or daily capture (that's only for
  historical research backfills).
- Tasks are **weekday-only and holiday-unaware**, but self-heal: off-market fires are
  safe no-ops, and the evening EOD captures walk back to the last trading day.
- Everything is **paper / decision-support** — the tool never places an order; you
  confirm every decision. See `docs/simple-dashboard.md` for what each panel means.
