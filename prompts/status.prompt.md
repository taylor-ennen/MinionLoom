---
title: /status
summary: Get the current status of MinionLoom tasks, minions, or system health.
---

# /status

Use this command to:
- Report the status of a task, minion, or the system
- Summarize recent activity or errors
- Provide actionable next steps

## Usage
/status [task_id|minion|system]
To get the current MinionLoom status from this repository, you can run:

```powershell
cd .github/minions
.\.venv\Scripts\python.exe orchestrator\dag.py status
```
## Example
/status system

---
This prompt is used by the Copilot CLI/chat and MinionLoom minions to provide status updates. (Last updated: 2026-03-13)