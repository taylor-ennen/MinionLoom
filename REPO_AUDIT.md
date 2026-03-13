# MinionLoom Repository Audit

## Goal
Ensure MinionLoom functions as a **drop-in plugin** inside a `.github/minions` folder, without modifying or requiring files outside that folder unless explicitly requested.

## Findings
- All functional code and assets are contained in `.github/minions/`.
- The root repository currently contains only `README.md` and `.vscode/` (which is typical for a repo but not required by the plugin).
- The install script (`install_minions.ps1`) can modify the parent workspace (VS Code tasks, GitHub instructions, `.gitignore`) only when run with the `-IntegrateWithParent` flag.
- Prompt files are stored in `.github/minions/prompts/` and visible to agents.

## Required Adjustments (Completed)
- Updated branding to < MinionLoom > throughout code and UI.
- Added an explicit VS Code setup script (`scripts/setup-vscode-prompt-path.ps1`) instead of automatic changes.
- Documented prompt path and setup in README, setup guide, and onboarding checklist.
- Added warnings to `install_minions.ps1` when running in integration mode (explicit user opt-in).

## Outstanding Recommendations
- Continue to avoid any automatic modifications to the parent workspace unless explicitly requested.
- Keep the plugin self-contained; only `.github/minions` should be required for basic operation.

---

## Quick Checks
- [ ] `.github/minions/prompts/` contains `/research`, `/status`, `/continue` prompt definitions.
- [ ] `install_minions.ps1` can be run without modifying parent workspace.
- [ ] `scripts/setup-vscode-prompt-path.ps1` exists for optional VS Code prompt path setup.
- [ ] `plugin.json` is named `minionloom` and points to internal `agents/`, `skills/`, and `hooks.json`.
