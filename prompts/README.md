# MinionLoom Prompt Integration Instructions

## Overview
This document describes how to integrate promptable `/research` and `/status` slash commands into the Copilot CLI/chat and MinionLoom agent system.

## Prompt Folder Structure
All prompt definitions for slash commands should be placed in:

```
.github/minions/prompts/
```

Each prompt is a Markdown file named `<command>.prompt.md` (e.g., `research.prompt.md`, `status.prompt.md`).

## Usage in Copilot CLI/Chat
- The Copilot CLI/chat should scan the `prompts` folder for available slash commands.
- When a user enters a slash command (e.g., `/research`), the corresponding prompt file is loaded and used to guide the AI agent's response.
- Prompts are designed to be reusable by both Copilot CLI/chat and MinionLoom agents.

## Example Prompts
- `/research` → `prompts/research.prompt.md`
- `/status` → `prompts/status.prompt.md`

## VSCode Inclusion
- To enable VSCode or other tools to discover and use these prompts, ensure the `prompts` folder is included in the extension or plugin manifest if packaging for distribution.
- Document the prompt path in your README or developer docs for easy reference.

---
This file should be updated as new slash commands are added.
