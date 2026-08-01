# Register a Windows scheduled task that captures the gamma-blast anchor strikes on expiry days.
#
# The driver (run_blast_ticks.py --market-hours) is IST-aware: it WAITS for the 13:45 IST freeze, then
# resolves the ATM call/put and streams their ticks until 15:30 IST. It SELF-GATES to expiry days (skips
# a symbol whose nearest expiry isn't today), so we can schedule it EVERY weekday and it no-ops on the
# non-expiry ones. This machine runs UK time (GMT/BST); 07:00 local is before 13:45 IST (08:15 UTC) in
# both DST regimes, and the driver waits out the slack. Ticks land under data/journal/blast_ticks/.
#
# NOTE: needs a VALID Dhan token (data/.dhan_token, 24h-capped) at run time — paste a fresh one before
# 13:45 IST on expiry day; the driver retries the WS until a token is present.
#
#   powershell -ExecutionPolicy Bypass -File scripts\install_blast_schedule.ps1
#   powershell -File scripts\install_blast_schedule.ps1 -Remove
param(
    [string]$TaskName = "GEX-BlastTicks",
    [string]$TriggerLocal = "07:00",
    [string]$Symbols = "NIFTY",
    [string]$Python = "C:\Python313\python.exe",
    [switch]$Headless,          # run whether logged on or not (S4U, passwordless); needs admin to register
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$script = Join-Path $repo "scripts\run_blast_ticks.py"

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed scheduled task $TaskName"
    return
}

if (-not (Test-Path $Python)) { throw "Python not found at $Python. Pass -Python with the full path." }
if (-not (Test-Path $script)) { throw "Driver not found at $script." }

$action = New-ScheduledTaskAction -Execute $Python `
    -Argument "`"$script`" --market-hours --symbols $Symbols" -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At $TriggerLocal

$settingArgs = @{
    AllowStartIfOnBatteries    = $true
    DontStopIfGoingOnBatteries = $true
    StartWhenAvailable         = $true
    ExecutionTimeLimit         = (New-TimeSpan -Hours 8)
    RestartCount               = 3
    RestartInterval            = (New-TimeSpan -Minutes 5)
}
if ($Headless) { $settingArgs.WakeToRun = $true }
$settings = New-ScheduledTaskSettingsSet @settingArgs

$user = "$env:USERDOMAIN\$env:USERNAME"
$logon = if ($Headless) { 'S4U' } else { 'Interactive' }
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType $logon -RunLevel Limited

$desc = "Gamma-blast anchor-strike tick capture ($Symbols). Waits 13:45 IST, self-gates to expiry days, stops 15:30 IST."

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description $desc -Force | Out-Null

$mode = if ($Headless) { "whether logged on or not (S4U, passwordless)" } else { "only when you are logged on" }
Write-Host "Registered $TaskName : weekdays at $TriggerLocal local. Driver waits for 13:45 IST, captures only on expiry days, stops 15:30 IST."
Write-Host "Runs $mode. Paste a fresh Dhan token before 13:45 IST on expiry day."
Write-Host "Ticks under data/journal/blast_ticks/, logs under data/journal/blast_ticks_logs/. Remove with -Remove."
