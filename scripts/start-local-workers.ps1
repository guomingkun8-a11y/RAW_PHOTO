param(
    [int]$WorkerReplicas = 2
)

$ErrorActionPreference = "Stop"

if ($WorkerReplicas -lt 2 -or $WorkerReplicas -gt 4) {
    throw "WorkerReplicas must be between 2 and 4"
}

$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Logs = Join-Path $Root "logs"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python venv not found: $Python"
}

New-Item -ItemType Directory -Force -Path $Logs | Out-Null

Write-Host "Starting $WorkerReplicas local image worker process(es)..."
for ($index = 1; $index -le $WorkerReplicas; $index++) {
    $outLog = Join-Path $Logs "worker-$index.log"
    $errLog = Join-Path $Logs "worker-$index.err.log"
    $process = Start-Process `
        -FilePath $Python `
        -ArgumentList @("worker.py") `
        -WorkingDirectory $Root `
        -RedirectStandardOutput $outLog `
        -RedirectStandardError $errLog `
        -WindowStyle Hidden `
        -PassThru
    Write-Host "worker-$index pid=$($process.Id)"
}

Write-Host "Logs: $Logs\worker-*.log"
