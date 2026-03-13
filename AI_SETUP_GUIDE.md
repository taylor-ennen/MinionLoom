# MinionLoom AI/Prompt Setup Guide

## Overview
This guide documents the required setup for AI agents, Copilot CLI/chat, and VS Code extensions to correctly discover and use MinionLoom slash command prompts. It ensures that any AI or developer can follow these steps to enable promptable commands in this project.

---

## 1. Prompt Directory Structure
- **Canonical prompt location:**
  - `.github/minions/prompts/`
- All prompt files (e.g., `research.prompt.md`, `status.prompt.md`, `continue.prompt.md`) must be placed here.

## 2. AI/Extension Configuration
- **Copilot CLI/chat and any prompt-consuming tool must be configured to scan:**
  - `.github/minions/prompts/`
- If a tool defaults to `.github/prompts/`, update its configuration to include `.github/minions/prompts/`.
- If configuration is not possible, request support from the tool's maintainers or consider a symlink (not recommended for this project).

## 3. Project Integration Steps
1. Place all prompt files in `.github/minions/prompts/`.
2. Document this path in the main `README.md` and in this setup guide.
3. Ensure all AI agents and extensions are pointed to this directory for prompt discovery.
4. Test prompt discovery by running `/research`, `/status`, and `/continue` in Copilot CLI/chat or your AI agent.

## 4. Example Prompts
- `/research` → `.github/minions/prompts/research.prompt.md`
- `/status` → `.github/minions/prompts/status.prompt.md`
- `/continue` → `.github/minions/prompts/continue.prompt.md`

## 5. Troubleshooting
- If a prompt does not appear, verify:
  - The prompt file exists in `.github/minions/prompts/`.
  - The tool/extension is configured to scan this directory.
  - There are no typos in the prompt file names.

## 6. Updating This Guide
- Any changes to prompt management or AI integration must be reflected in this guide.
- Keep this file up to date for all contributors and AI agents.

---
This guide ensures MinionLoom's prompt system is discoverable and maintainable for both humans and AI agents.