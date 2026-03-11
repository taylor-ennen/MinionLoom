# Minion Loom - WIP

Minion Loom is a local orchestration plugin that runs task-specific minion branches and worktrees, tracks runs in SQLite, and exposes a Flask dashboard for dispatch, telemetry, retries, cancellation, and cleanup.

## What Is Included

- PowerShell-based installation and worktree management
- Python orchestrator and persistent SQLite run tracking
- Flask dashboard with live stream and task controls
- Copilot plugin files, task templates, and agent/skill definitions

## Requirements

- Windows with PowerShell
- Python 3 on PATH via `py` or `python`
- Git
- GitHub Copilot CLI available as `copilot`

## Install

From the `.github/minions` directory:

```powershell
.\install_minions.ps1
```

If you want the installer to also wire tasks and Copilot instructions into the parent workspace:

```powershell
.\install_minions.ps1 -IntegrateWithParent
```

The installer will:

- create `.venv`
- install Python dependencies from `requirements.txt`
- install the local Copilot plugin
- optionally merge the provided VS Code task and Copilot instructions into the parent workspace

## Run The Dashboard

From the `.github/minions` directory:

```powershell
.\.venv\Scripts\python.exe dashboard\app.py
```

Then open:

```text
http://127.0.0.1:5000
```

## Notes

- Runtime databases and local artifacts are not tracked by git.
- Task worktrees are created under `%LOCALAPPDATA%\LocalMinion\worktrees`.
- This repo is designed for local Windows execution, not hosted deployment.
- If you publish this project, publish the nested `.github/minions` repo unless you also intend to expose the parent workspace.
