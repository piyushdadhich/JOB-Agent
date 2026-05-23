# Registers Windows Task Scheduler entry "JobAgentDailyEval".
#
# Trigger:  daily at 23:00 (11 PM) local time (Toronto when the
#           machine is in America/Toronto -- the trigger uses
#           local-time semantics, not a TZ-anchored absolute).
# Action:   the venv's python running scripts/run_daily.py against
#           the default profile via the cloud (two-stage) evaluator.
# Settings:
#   StartWhenAvailable        = $true   -- if the laptop was off at
#                                          11 PM, run as soon as
#                                          it boots/wakes.
#   AllowStartIfOnBatteries   = $false  -- only start when on AC.
#   DontStopIfGoingOnBatteries = $false -- if the user unplugs
#                                          mid-run, stop cleanly
#                                          (next pass picks up where
#                                          this one left off via
#                                          find_postings_needing_eval).
#   WakeToRun                 = $false  -- never wake the machine
#                                          to run this.
#
# Idempotent: if "JobAgentDailyEval" already exists it is removed
# first, then re-registered with the current settings.
#
# >>> RUN FROM AN ELEVATED PowerShell PROMPT <<<
# Right-click PowerShell -> "Run as administrator", then:
#     cd C:\job-agent
#     .\scripts\register_overnight_eval_task.ps1

$ErrorActionPreference = "Stop"

$TaskName    = "JobAgentDailyEval"
$ProjectRoot = "C:\job-agent"
$Python      = "$ProjectRoot\venv\Scripts\python.exe"
$Script      = "$ProjectRoot\scripts\run_daily.py"
$Args        = "--profile default --evaluator cloud"

if (-not (Test-Path $Python)) {
    throw "venv python not found at $Python; create the venv first"
}
if (-not (Test-Path $Script)) {
    throw "run_daily.py not found at $Script"
}

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Host "Removing existing task '$TaskName'..."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$Action = New-ScheduledTaskAction `
    -Execute $Python `
    -Argument "$Script $Args" `
    -WorkingDirectory $ProjectRoot

$Trigger = New-ScheduledTaskTrigger -Daily -At 23:00

$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries:$false `
    -AllowStartIfOnBatteries:$false `
    -WakeToRun:$false `
    -ExecutionTimeLimit (New-TimeSpan -Hours 4)

# Run as the current interactive user so Ollama (which the user
# launched in their session) is reachable on localhost.
$Principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Description "Job-agent: nightly cloud-first evaluator at 11 PM, AC only." | Out-Null

Write-Host "Registered '$TaskName' (daily 23:00, AC-only, no wake)."
