param(
    [Parameter(Mandatory = $true)]
    [string]$TaskID
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-RelativePath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FromPath,
        [Parameter(Mandatory = $true)]
        [string]$ToPath
    )

    $fromUri = [System.Uri]::new((Resolve-Path -LiteralPath $FromPath).Path.TrimEnd('\\') + '\\')
    $toUri = [System.Uri]::new((Resolve-Path -LiteralPath $ToPath).Path)
    $relativeUri = $fromUri.MakeRelativeUri($toUri)
    return [System.Uri]::UnescapeDataString($relativeUri.ToString()).Replace('/', '\\')
}

function Test-DeveloperModeEnabled {
    $registryPath = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock'
    if (-not (Test-Path -LiteralPath $registryPath)) {
        return $false
    }

    try {
        $value = Get-ItemPropertyValue -LiteralPath $registryPath -Name 'AllowDevelopmentWithoutDevLicense' -ErrorAction Stop
        return $value -eq 1
    } catch {
        return $false
    }
}

function Get-UniqueBranchName {
    param(
        [Parameter(Mandatory = $true)]
        [string]$BaseName
    )

    $candidate = $BaseName
    $suffix = 0

    while ($true) {
        & git show-ref --verify --quiet "refs/heads/$candidate"
        if ($LASTEXITCODE -ne 0) {
            return $candidate
        }

        $suffix += 1
        $timestamp = Get-Date -Format 'yyyyMMddHHmmss'
        $candidate = "$BaseName-$timestamp-$suffix"
    }
}

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$minionRoot = Split-Path -Parent $scriptRoot
$workspaceRoot = Split-Path -Parent (Split-Path -Parent $minionRoot)
$workspaceName = Split-Path -Leaf $workspaceRoot
$taskSlug = ($TaskID -replace '[^A-Za-z0-9_-]', '-').Trim('-')
if ([string]::IsNullOrWhiteSpace($taskSlug)) {
    $taskSlug = 'task'
}

$branchBase = "feature/minion-$taskSlug"
$result = [ordered]@{
    worktree_path = ''
    status = 'failure'
}
$exitCode = 1

try {
    if (-not (Test-DeveloperModeEnabled)) {
        [Console]::Error.WriteLine('SEVERE: Windows Developer Mode is disabled. mklink operations will likely fail without elevation.')
    }

    Push-Location $minionRoot
    try {
        & git config core.symlinks true | Out-Null

        $branchName = Get-UniqueBranchName -BaseName $branchBase

        # NOTE: The runtime worktree location is under LOCALAPPDATA to avoid
        # cluttering the user workspace with temporary git worktree directories.
        # This used to be named "LocalMinion"; rename it to MinionLoom for branding.
        $runtimeRoot = Join-Path (Join-Path $env:LOCALAPPDATA 'MinionLoom\worktrees') $workspaceName
        New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
        $worktreePath = Join-Path $runtimeRoot "minion-workspace-$taskSlug"

        if (Test-Path -LiteralPath $worktreePath) {
            $suffix = Get-Date -Format 'yyyyMMddHHmmss'
            $worktreePath = "$worktreePath-$suffix"
        }

        $gitOutput = & git worktree add $worktreePath -b $branchName 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "git worktree add failed: $($gitOutput -join [Environment]::NewLine)"
        }
    } finally {
        Pop-Location
    }

    Set-Location $worktreePath

    $sharedVenvPath = Join-Path $workspaceRoot '.venv'
    if (-not (Test-Path -LiteralPath $sharedVenvPath)) {
        throw "Shared virtual environment not found at $sharedVenvPath. Run install_minions.ps1 first."
    }

    if (Test-Path -LiteralPath '.venv') {
        Remove-Item -LiteralPath '.venv' -Force -Recurse
    }

    & cmd /d /c mklink /D .venv $sharedVenvPath | Out-Null
    if ($LASTEXITCODE -ne 0) {
        [Console]::Error.WriteLine('WARNING: Directory symlink creation failed. Falling back to a junction for .venv sharing.')
        if (Test-Path -LiteralPath '.venv') {
            Remove-Item -LiteralPath '.venv' -Force -Recurse
        }

        & cmd /d /c mklink /J .venv $sharedVenvPath | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw 'Failed to create either a directory symlink or junction for .venv.'
        }
    }

    if (-not (Test-Path -LiteralPath '.venv')) {
        throw 'Failed to create .venv symbolic link inside the worktree.'
    }

    $result.worktree_path = (Resolve-Path -LiteralPath $worktreePath).Path
    $result.status = 'success'
    $exitCode = 0
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
} finally {
    $result | ConvertTo-Json -Compress
    exit $exitCode
}