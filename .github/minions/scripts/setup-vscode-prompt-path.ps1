<#
.SYNOPSIS
Ensures VS Code is configured to use the MinionLoom prompt folder.

.DESCRIPTION
This script updates (or creates) the parent workspace's
.vscode/settings.json to include `.github/minions/prompts` in
`copilot.prompts.paths`.

It does NOT overwrite other settings. It merges this setting into any existing file.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$minionRoot = Split-Path -Parent $scriptRoot
$dotGithub = Split-Path -Parent $minionRoot
# Repository root is the parent of .github
$repoRoot = Split-Path -Parent $dotGithub

# Always use the standard workspace .vscode folder (repo root).
$vscodeDir = Join-Path $repoRoot '.vscode'
if (-not (Test-Path $vscodeDir)) {
    New-Item -ItemType Directory -Path $vscodeDir -Force | Out-Null
}

$settingsPath = Join-Path $vscodeDir 'settings.json'

function Read-JsonFile($path) {
    if (-not (Test-Path $path)) { return @{} }
    $raw = Get-Content -LiteralPath $path -Raw -Encoding UTF8
    if ([string]::IsNullOrWhiteSpace($raw)) { return @{} }
    return $raw | ConvertFrom-Json
}

$settings = Read-JsonFile $settingsPath

if (-not ($settings.PSObject.Properties.Name -contains 'copilot.prompts.paths')) {
    $settings.'copilot.prompts.paths' = @()
}

if (-not ($settings.'copilot.prompts.paths' -contains '.github/minions/prompts')) {
    $settings.'copilot.prompts.paths' += '.github/minions/prompts'
}

$settings | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $settingsPath -Encoding UTF8
Write-Host "Updated VS Code settings at $settingsPath to include .github/minions/prompts (prompt path configured)."
