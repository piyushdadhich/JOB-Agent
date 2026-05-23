# Registers Windows Task Scheduler entry "JobAgentDashboard".
#
# Trigger:  at user logon (the current interactive user).
# Action:   the venv's python running scripts/dashboard.py with
#           --no-browser --port 8000. The user opens the dashboard
#           by visiting http://127.0.0.1:8000 from a browser
#           shortcut; the auto-start just keeps the FastAPI server
#           running so the page is always there.
# Settings:
#   StartWhenAvailable        = $true   -- if the trigger missed
#                                          (e.g. logon during boot
#                                          while other tasks were
#                                          waiting), start as soon
#                                          as the user is in.
#   AllowStartIfOnBatteries   = $true   -- dashboard is lightweight;
#                                          fine on battery.
#   DontStopIfGoingOnBatteries = $true  -- stay up across power
#                                          transitions.
#   WakeToRun                 = $false  -- never wake the machine.
#
# Idempotent: if "JobAgentDashboard" already exists it is removed
# first, then re-registered with the current settings.
#
# >>> RUN FROM AN ELEVATED PowerShell PROMPT <<<
# Right-click PowerShell -> "Run as administrator", then:
#     cd C:\job-agent
#     .\scripts\register_dashboard_autostart_task.ps1

$ErrorActionPreference = "Stop"

$TaskName    = "JobAgentDashboard"
$ProjectRoot = "C:\job-agent"
$Python      = "$ProjectRoot\venv\Scripts\python.exe"
$Script      = "$ProjectRoot\scripts\dashboard.py"
$Args        = "--no-browser --port 8000"

if (-not (Test-Path $Python)) {
    throw "venv python not found at $Python; create the venv first"
}
if (-not (Test-Path $Script)) {
    throw "dashboard.py not found at $Script"
}

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Host "Removing existing task '$TaskName'..."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$Action = New-ScheduledTaskAction `
    -Execute $Python `
    -Argument "$Script $Args" `
    -WorkingDirectory $ProjectRoot

# AtLogOn for the current user. Without -User this triggers on ANY
# user's logon; pinning to the current user keeps it scoped.
$Trigger = New-ScheduledTaskTrigger `
    -AtLogOn `
    -User "$env:USERDOMAIN\$env:USERNAME"

$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries:$true `
    -AllowStartIfOnBatteries:$true `
    -WakeToRun:$false `
    -ExecutionTimeLimit (New-TimeSpan -Days 0)  # 0 = no limit

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
    -Description "Job-agent: keep the localhost FastAPI dashboard running." | Out-Null

Write-Host "Registered '$TaskName' (at user logon, port 8000)."
