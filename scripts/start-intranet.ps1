param(
    [string]$EnvFile = ".env.intranet",
    [int]$WorkerReplicas = 1,
    [switch]$Build
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$ComposeFile = Join-Path $Root "docker-compose.enterprise.yml"
$ResolvedEnvFile = Join-Path $Root $EnvFile

if (-not (Test-Path -LiteralPath $ComposeFile)) {
    throw "Compose file not found: $ComposeFile"
}

if (-not (Test-Path -LiteralPath $ResolvedEnvFile)) {
    Write-Host "Env file not found: $ResolvedEnvFile"
    Write-Host "Create it from .env.example, fill passwords/Qiniu/relay settings, then run this script again."
    exit 1
}

if ($WorkerReplicas -lt 1) {
    throw "WorkerReplicas must be >= 1"
}

$compose = @(
    "compose",
    "--env-file", $ResolvedEnvFile,
    "-f", $ComposeFile
)

$up = @("up", "-d")
if ($Build) {
    $up += "--build"
}
$up += @("--scale", "worker=$WorkerReplicas", "mysql", "redis", "schema-init", "app", "worker")

Write-Host "Starting intranet stack with $WorkerReplicas worker replica(s)..."
& docker @compose @up

Write-Host ""
Write-Host "Current service status:"
& docker @compose "ps"

Write-Host ""
Write-Host "Intranet entry: http://<server-lan-ip>:8000"
Write-Host "Monitoring page: http://<server-lan-ip>:8000/monitoring"
