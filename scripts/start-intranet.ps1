param(
    [string]$EnvFile = ".env.intranet",
    [int]$WorkerReplicas = 2,
    [int]$AgentWorkerReplicas = 2,
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
    Write-Host "Create it from .env.example, fill passwords/OSS/relay settings, then run this script again."
    exit 1
}

if ($WorkerReplicas -lt 2 -or $WorkerReplicas -gt 4) {
    throw "WorkerReplicas must be between 2 and 4 for this deployment profile"
}

if ($AgentWorkerReplicas -lt 1 -or $AgentWorkerReplicas -gt 4) {
    throw "AgentWorkerReplicas must be between 1 and 4 for this deployment profile"
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
$up += @(
    "--scale", "worker=$WorkerReplicas",
    "--scale", "agent-worker=$AgentWorkerReplicas",
    "mysql", "redis", "schema-init", "app", "worker", "agent-worker"
)

Write-Host "Starting intranet stack with $WorkerReplicas image worker replica(s) and $AgentWorkerReplicas Agent worker replica(s)..."
& docker @compose @up

Write-Host ""
Write-Host "Current service status:"
& docker @compose "ps"

Write-Host ""
Write-Host "Intranet entry: http://<server-lan-ip>:8000"
Write-Host "Monitoring page: http://<server-lan-ip>:8000/monitoring"
