param(
    [string]$EnvFile = ".env.intranet"
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$ComposeFile = Join-Path $Root "docker-compose.enterprise.yml"
$ResolvedEnvFile = Join-Path $Root $EnvFile

if (-not (Test-Path -LiteralPath $ResolvedEnvFile)) {
    Write-Host "Env file not found: $ResolvedEnvFile"
    Write-Host "Falling back to docker compose without --env-file."
    & docker compose -f $ComposeFile down
    exit $LASTEXITCODE
}

& docker compose --env-file $ResolvedEnvFile -f $ComposeFile down
