param(
    [switch]$IntegrateWithParent
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Invoke-CommandArray {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Command,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $commandTail = @()
    if ($Command.Length -gt 1) {
        $commandTail = $Command[1..($Command.Length - 1)]
    }

    & $Command[0] @commandTail @Arguments
}

function Get-PythonCommand {
    $candidates = @(
        @('py', '-3'),
        @('python')
    )

    foreach ($candidate in $candidates) {
        $commandName = $candidate[0]
        if (Get-Command $commandName -ErrorAction SilentlyContinue) {
            return $candidate
        }
    }

    throw 'Unable to find a usable Python launcher. Install Python 3 or add py.exe/python.exe to PATH.'
}

function Read-JsonFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        return $null
    }

    $raw = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    if ([string]::IsNullOrWhiteSpace($raw)) {
        return $null
    }

    return $raw | ConvertFrom-Json
}

function Merge-TaskDefinitions {
    param(
        [Parameter(Mandatory = $true)]
        [pscustomobject]$TemplateConfig,
        [Parameter(Mandatory = $false)]
        [pscustomobject]$ExistingConfig
    )

    $mergedTasks = New-Object System.Collections.Generic.List[object]
    $seenLabels = New-Object System.Collections.Generic.HashSet[string]

    if ($ExistingConfig -and $ExistingConfig.tasks) {
        foreach ($task in $ExistingConfig.tasks) {
            if ($null -ne $task.label) {
                [void]$seenLabels.Add([string]$task.label)
            }
            $mergedTasks.Add($task)
        }
    }

    foreach ($task in $TemplateConfig.tasks) {
        $label = [string]$task.label
        if ($seenLabels.Contains($label)) {
            for ($index = 0; $index -lt $mergedTasks.Count; $index++) {
                if ([string]$mergedTasks[$index].label -eq $label) {
                    $mergedTasks[$index] = $task
                    break
                }
            }
        } else {
            [void]$seenLabels.Add($label)
            $mergedTasks.Add($task)
        }
    }

    return [pscustomobject]@{
        version = if ($ExistingConfig -and $ExistingConfig.version) { $ExistingConfig.version } else { $TemplateConfig.version }
        tasks = $mergedTasks
    }
}

$minionRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$githubDir = Split-Path -Parent $minionRoot
$projectRoot = Split-Path -Parent $githubDir
$parentVscodeDir = Join-Path $projectRoot '.vscode'
$parentGithubDir = Join-Path $projectRoot '.github'
$parentGitignore = Join-Path $projectRoot '.gitignore'
$templateTasksPath = Join-Path $minionRoot 'templates\tasks.json'
$targetTasksPath = Join-Path $parentVscodeDir 'tasks.json'
$templateInstructionsPath = Join-Path $minionRoot 'templates\copilot-instructions.md'
$targetInstructionsPath = Join-Path $parentGithubDir 'copilot-instructions.md'
$venvPath = Join-Path $minionRoot '.venv'
$pythonLauncher = Get-PythonCommand

if (-not (Test-Path -LiteralPath $venvPath)) {
    Invoke-CommandArray -Command $pythonLauncher -Arguments @('-m', 'venv', $venvPath)
}

$venvPython = Join-Path $venvPath 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "Virtual environment creation failed. Expected interpreter not found at $venvPython."
}

& $venvPython -m pip install -r (Join-Path $minionRoot 'requirements.txt')

if ($IntegrateWithParent) {
    New-Item -ItemType Directory -Path $parentVscodeDir -Force | Out-Null
    New-Item -ItemType Directory -Path $parentGithubDir -Force | Out-Null

    $templateTasks = Read-JsonFile -Path $templateTasksPath
    if ($null -eq $templateTasks) {
        throw "Template tasks configuration is missing or invalid at $templateTasksPath."
    }

    $existingTasks = Read-JsonFile -Path $targetTasksPath
    $mergedTasks = Merge-TaskDefinitions -TemplateConfig $templateTasks -ExistingConfig $existingTasks
    $mergedTasks | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $targetTasksPath -Encoding UTF8

    Copy-Item -LiteralPath $templateInstructionsPath -Destination $targetInstructionsPath -Force

    $gitignoreEntries = @('*.db', '*.sqlite')
    if (-not (Test-Path -LiteralPath $parentGitignore)) {
        New-Item -ItemType File -Path $parentGitignore -Force | Out-Null
    }

    $gitignoreContent = Get-Content -LiteralPath $parentGitignore -ErrorAction SilentlyContinue
    foreach ($entry in $gitignoreEntries) {
        if ($gitignoreContent -notcontains $entry) {
            Add-Content -LiteralPath $parentGitignore -Value $entry
        }
    }
}

Push-Location $projectRoot
try {
    if (-not (Get-Command copilot -ErrorAction SilentlyContinue)) {
        throw 'The GitHub Copilot CLI is not available on PATH. Install it before running install_minions.ps1.'
    }

    & copilot plugin install './.github/minions'
} finally {
    Pop-Location
}

if ($IntegrateWithParent) {
    Write-Host 'MinionLoom installation completed successfully with parent integration (workspace files may be modified).'
    Write-Host 'WARNING: This mode writes to VS Code and GitHub workspace files. Use only when you want explicit integration.'

    # Configure VS Code to include the MinionLoom prompt path (copilot.prompts.paths)
    & "$minionRoot\scripts\setup-vscode-prompt-path.ps1"
} else {
    Write-Host 'MinionLoom installation completed successfully in local-only mode (no workspace files modified).'
}