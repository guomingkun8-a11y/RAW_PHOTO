param([switch]$CheckOnly)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$PluginHome = Join-Path $Root "backend\services\dsh_agent\plugins"
$Profile = Join-Path $PluginHome "profiles\agent-teams"
$SecurityPatch = Join-Path $Root "backend\services\dsh_agent\no-tools.patch.yml"

$Node = (Get-Command node -ErrorAction Stop).Source

if (-not $CheckOnly) {
    Push-Location -LiteralPath $Profile
    try {
        & pnpm install --frozen-lockfile --ignore-scripts --prefer-offline --fetch-retries=1 --fetch-timeout=60000
        if ($LASTEXITCODE -ne 0) { throw "Plugin installation failed." }
    } finally {
        Pop-Location
    }
}

$Package = Join-Path $Profile "node_modules\@nanmicoder\dsh-agent-teams\package.json"
if (-not (Test-Path -LiteralPath $Package -PathType Leaf)) {
    throw "AgentTeams is not installed. Run this script without -CheckOnly."
}
$Installed = Get-Content -LiteralPath $Package -Raw | ConvertFrom-Json
if ($Installed.name -ne "@nanmicoder/dsh-agent-teams" -or $Installed.version -ne "0.1.29") {
    throw "Unexpected AgentTeams package identity or version."
}
$HostPackage = Join-Path $Profile "node_modules\@deepseek-ai\dsh\package.json"
$HostEntry = Join-Path $Profile "node_modules\@deepseek-ai\dsh\lib\bin.js"
if (-not (Test-Path -LiteralPath $HostPackage -PathType Leaf)) {
    throw "The compatible plugin host is not installed."
}
$HostManifest = Get-Content -LiteralPath $HostPackage -Raw | ConvertFrom-Json
if ($HostManifest.version -ne "0.2.1-rc.2") { throw "Unexpected DSH plugin host version." }

$PreviousHome = $env:DSH_HOME
try {
    $env:DSH_HOME = $PluginHome
    $Composed = & $Node $HostEntry --profile agent-teams --patch $SecurityPatch --dump-config
    if ($LASTEXITCODE -ne 0) { throw "SDK profile compatibility check failed." }
    if (-not (($Composed -join "`n") -match "@nanmicoder/dsh-agent-teams")) {
        throw "AgentTeams was not included in the SDK profile."
    }
} finally {
    $env:DSH_HOME = $PreviousHome
}

Write-Output "AgentTeams 0.1.29 is installed in the project-owned SDK validation profile."
Write-Output "Its isolated DSH host is pinned to 0.2.1-rc.2; the application SDK installation is unchanged."
Write-Output "This does not enable team execution for application users."
