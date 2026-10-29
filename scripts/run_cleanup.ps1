# 清理过期会话与失效登录计数，并把结果写入 logs/cleanup.log。
# 供计划任务调用；退出码沿用脚本自身（0 = 成功）。
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$script = Join-Path $root "scripts\cleanup_sessions.py"
$logDir = Join-Path $root "logs"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$output = & $python $script --json 2>&1
$exited = $LASTEXITCODE
Add-Content -Path (Join-Path $logDir "cleanup.log") `
  -Value ("[" + $stamp + "] exit=" + $exited + " " + ($output -join " ")) -Encoding UTF8
exit $exited
