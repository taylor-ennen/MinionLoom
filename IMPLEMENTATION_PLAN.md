# MinionLoom Enhancement Implementation Plan

## Purpose
This document defines goals and a task plan for MinionLoom as a self-contained Copilot plugin (dropped into `.github/minions/`).
It is intended to be used by both humans and AI-driven agents running inside the repo.

## High-Level Goals
- The core system must run solely from `.github/minions/` (no hidden side effects outside the folder unless explicitly requested).
- Provide prompt-driven automation via Copilot chat and the Minion orchestrator (`orchestrator/dag.py`).
- Support easy setup and integration in existing repos (VS Code prompt paths, prompt discovery, installer behavior).
- Keep changes atomic, reproducible, and traceable through git worktrees.

## Immediate Objectives
- Implement promptable `/research`, `/status`, and `/continue` commands.
- Ensure `/status` is actually executable through the orchestrator and dashboard.
- Redesign the dashboard UI to a CLI-inspired layout while preserving telemetry.
- Replace all branding with `< MinionLoom >` and confirm no residual legacy naming.
- Ensure the installer behaves non-destructively by default and warns when it writes workspace files.

## Task Breakdown

### 1. Add Promptable `/research` and `/status` Commands
- Integrate Copilot chat instructions and documentation
- Implement endpoints and UI triggers for `/research` and `/status`
- Route requests to Copilot chat/minion system for token efficiency

### 2. Redesign Dashboard GUI (CLI Look)
- Update Flask dashboard templates and CSS for CLI-inspired appearance
- Preserve usability and telemetry features

### 3. Replace Branding
- Search and replace 'Local Minion' with '< MinionLoom >' in UI, docs, and code

### 4. Commit Each Change
- Make atomic, descriptive commits for each major change

### 5. Test and Validate
- Test new commands and dashboard
- Validate branding and UI consistency
- Ensure project remains cohesive and functional

## Progress Log
- [ ] Plan drafted (this file)
- [x] `/research` and `/status` commands implemented
- [ ] Dashboard redesign complete
- [x] Branding replaced
- [x] All changes committed
- [ ] Features tested and validated

---
This plan will be updated as work progresses.
