# Removes the "JobAgentDashboard" scheduled task. No-op if absent.
#
# >>> RUN FROM AN ELEVATED PowerShell PROMPT <<<
#     cd C:\job-agent
#     .\scripts\unregister_dashboard_autostart_task.ps1

$ErrorActionPreference = "Stop"
$TaskName = "JobAgentDashboard"

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed '$TaskName'."
} else {
    Write-Host "'$TaskName' not registered; nothing to do."
}
