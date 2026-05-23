# Removes the "JobAgentDailyEval" scheduled task. No-op if absent.
#
# >>> RUN FROM AN ELEVATED PowerShell PROMPT <<<
#     cd C:\job-agent
#     .\scripts\unregister_overnight_eval_task.ps1

$ErrorActionPreference = "Stop"
$TaskName = "JobAgentDailyEval"

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed '$TaskName'."
} else {
    Write-Host "'$TaskName' not registered; nothing to do."
}
