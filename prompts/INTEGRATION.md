# MinionLoom Prompt Integration Update

## Custom Prompt Path
This project uses `.github/minions/prompts` as the canonical location for all Copilot CLI/chat and MinionLoom agent prompts, instead of the default `.github/prompts`.

## Integration Instructions
- Ensure any Copilot CLI/chat or VS Code extension/plugin is configured to scan `.github/minions/prompts` for available slash commands.
- If you are using a tool or extension that only looks in `.github/prompts`, you must either:
  - Update its configuration to include `.github/minions/prompts`, or
  - Symlink/copy prompt files to `.github/prompts` (not recommended for this project).

## Current Prompts
- `/research` → `.github/minions/prompts/research.prompt.md`
- `/status` → `.github/minions/prompts/status.prompt.md`
- `/continue` → `.github/minions/prompts/continue.prompt.md`

## Action Items
- Update Copilot CLI/chat or any prompt-consuming tool to use `.github/minions/prompts`.
- Document this path in the main README and developer docs.

---
This file documents the custom prompt path for MinionLoom and should be kept up to date with any changes to prompt management.