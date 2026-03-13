# MinionLoom Enhancement Implementation Plan

## Overview
This document tracks the implementation plan and progress for the following enhancements:
- Promptable `/research` and `/status` commands using Copilot chat instructions and documentation
- Dashboard redesign with a CLI-inspired look (still Flask-based)
- Replace all 'Local Minion' branding with '< MinionLoom >'
- Commit each change separately
- Use minions to optimize Copilot chat token usage where possible
- Test features and maintain project cohesion

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
- [ ] `/research` and `/status` commands implemented
- [ ] Dashboard redesign complete
- [ ] Branding replaced
- [ ] All changes committed
- [ ] Features tested and validated

---
This plan will be updated as work progresses.
