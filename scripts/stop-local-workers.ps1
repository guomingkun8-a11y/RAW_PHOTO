$ErrorActionPreference = "Stop"

$processes = Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -match 'worker\.py' -and $_.CommandLine -match 'raw photo'
}

if (-not $processes) {
    Write-Host "No local RAW_PHOTO worker process found."
    exit 0
}

$ids = @($processes | ForEach-Object { [int]$_.ProcessId })
Write-Host "Stopping local worker process(es): $($ids -join ', ')"
Stop-Process -Id $ids -Force -ErrorAction SilentlyContinue
