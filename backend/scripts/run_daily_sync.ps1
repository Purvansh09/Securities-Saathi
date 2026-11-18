# Daily securities sync + price refresh, for Windows Task Scheduler.
# Runs `python -m app.services.securities_sync all` from backend/ with the project venv, writes a
# dated log to backend/logs/, keeps the last 30, and exits with the job's exit code so Task
# Scheduler's "Last Run Result" shows failures (0 = success).
#
# Temporary home for the schedule; the plan is to move it to a GitHub Actions cron later, which
# will call the same `python -m app.services.securities_sync all` directly.

$ErrorActionPreference = "Stop"
$backend = Split-Path -Parent $PSScriptRoot
$python = Join-Path $backend ".venv\Scripts\python.exe"
$logDir = Join-Path $backend "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("sync-{0:yyyy-MM-dd_HHmm}.log" -f (Get-Date))

Set-Location $backend   # the app reads .env from the working directory

"=== $(Get-Date -Format o) starting securities sync" | Out-File -FilePath $log -Encoding utf8
# cmd /c merges stderr into the log without PowerShell wrapping each stderr line as an error.
cmd /c "`"$python`" -m app.services.securities_sync all >> `"$log`" 2>&1"
$code = $LASTEXITCODE
"=== $(Get-Date -Format o) finished with exit code $code" | Out-File -FilePath $log -Append -Encoding utf8

Get-ChildItem $logDir -Filter "sync-*.log" | Sort-Object LastWriteTime -Descending | Select-Object -Skip 30 |
    Remove-Item -Force

exit $code
