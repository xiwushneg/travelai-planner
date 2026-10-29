# 注册 TravelAI 清理任务（幂等：重复执行会覆盖同名任务）。
# 用法：powershell -ExecutionPolicy Bypass -File scripts\register_cleanup_task.ps1 [-At 03:30]
param(
    [string]$TaskName = "TravelAI-CleanupSessions",
    [string]$At = "03:30"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$wrapper = Join-Path $root "scripts\run_cleanup.ps1"

if (-not (Test-Path $wrapper)) {
    throw "找不到清理脚本：$wrapper"
}

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$wrapper`"" `
    -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew

try {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Force -ErrorAction Stop | Out-Null
} catch {
    Write-Output ("注册失败（需要管理员权限）：" + $_.Exception.Message)
    exit 1
}

$task = Get-ScheduledTask -TaskName $TaskName
Write-Output ("已注册计划任务：" + $task.TaskName + "，状态=" + $task.State + "，每日 " + $At + " 执行")
Write-Output ("日志：" + (Join-Path $root "logs\cleanup.log"))
