# MinionLoom

MinionLoom is a modular, agent-driven automation and orchestration system for local and remote tasks, featuring a dashboard, promptable Copilot CLI/chat commands, and extensible AI agent support.

---

## Quick Start
- Clone this repo
- Run the installer in `.github/minions/install_minions.ps1`
- Start the dashboard: `.github/minions/dashboard/app.py`

---

## Promptable Commands & AI Integration
- **All Copilot CLI/chat and AI agent prompts are located in:**
  - `.github/minions/prompts/`
- Supported commands: `/research`, `/status`, `/continue` (see prompts folder for more)
- For setup and integration, see [`.github/minions/AI_SETUP_GUIDE.md`](.github/minions/AI_SETUP_GUIDE.md)
- To configure VS Code for prompt discovery, run: `.github/minions/scripts/setup-vscode-prompt-path.ps1`

---

## Onboarding for Contributors & AI Agents
- Read the [AI/Prompt Setup Guide](.github/minions/AI_SETUP_GUIDE.md) for prompt path and integration steps
- Ensure your tools/extensions are configured to scan `.github/minions/prompts/`
- Test prompt discovery with `/research`, `/status`, `/continue`

---

## Project Structure
- `.github/minions/` — All minion code, dashboard, orchestrator, and prompts
- `.github/minions/prompts/` — All promptable Copilot/AI commands

---

## Troubleshooting
- If prompts do not appear, verify your tool is scanning `.github/minions/prompts/`
- See the setup guide for more help

---

## License
MIT
