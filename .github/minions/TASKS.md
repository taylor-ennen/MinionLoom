# MinionLoom Task Tracker

This file lists the core work items needed to meet the project goals and ensure the plugin works as a drop-in in `.github/minions/`.

## Goals
- MinionLoom should work as a self-contained plugin under `.github/minions/`.
- It should not modify host repository files unless explicitly requested.
- It should expose promptable commands to Copilot CLI/chat and enable orchestration via a git-backed worktree system.

## Task List

### ✅ Completed
- [x] Add `/research`, `/status`, `/continue` prompt definitions in `.github/minions/prompts/`
- [x] Rename branding from "Local Minion" to "< MinionLoom >"
- [x] Add documentation: README, AI setup guide, onboarding checklist, audit report
- [x] Provide `scripts/setup-vscode-prompt-path.ps1` helper for VS Code prompt discovery
- [x] Ensure installer does not modify host repo unless asked (integration mode)
- [x] Add plugin manifest `plugin.json` (renamed to `minionloom`)

### 🔜 In Progress
- [ ] Redesign dashboard UI to a CLI-inspired layout (still in Flask)
- [ ] Implement `/status` command behavior in the dashboard/API

### 🚧 Next / Pending Actions
- [ ] Add a MinionLoom task that uses the worktree system to orchestrate a simple end-to-end run (e.g., start a task, monitor status, stop)
- [ ] Add tests or self-check tasks that validate prompt discovery and integration (e.g., run `setup-vscode-prompt-path.ps1`, then confirm `.vscode/settings.json` contains the path)
- [ ] Decide and document an OSS license (MIT, Apache, etc.)

---

## Using the Minion System for Work
MinionLoom includes an orchestrator engine (`orchestrator/dag.py`) that can run tasks via Git worktrees and track state in `minion_state.db`.

To run a diagnostic task (from the repository root):

```powershell
.\.venv\Scripts\python.exe .github/minions/orchestrator/dag.py diagnostic-local-clean
```

To configure VS Code prompt discovery (from the repository root):

```powershell
.\.venv\Scripts\powershell.exe .github/minions/scripts/setup-vscode-prompt-path.ps1
```

---

Keep this file updated as work progresses to provide a single source of truth for remaining tasks.
