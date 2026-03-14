# MinionLoom Installation & Setup (AI-friendly)

This document is the single source of truth for how MinionLoom must be installed, configured, and extended. It is written to be directly usable by AI agents and by humans.

**Important architectural rule:**
- All orchestration, state, and task management must be handled in Python and persisted in the database.
- Do not use subprocesses to emit/parse JSON for internal state.
- All new features and refactors must follow this pattern. (See Phase 0.1 plan, March 13, 2026.)

---

## 1. Requirements (must be satisfied first)


### 1.1 Python
- Python 3.10+ must be available on `PATH`.

**Example:**
```bash
python --version
```


### 1.2 Git
- Git must be available on `PATH`.

**Example:**
```bash
git --version
```


### 1.3 GitHub Copilot CLI
- The Copilot CLI must be installed and available as `copilot`.

**Example:**
```bash
copilot --version
```

---

## 2. Install Python dependencies (required)

MinionLoom depends on Python packages listed in:
- `.github/minions/requirements.txt`

### Option A (preferred): use your existing Python environment
If you already have a venv or Python environment you want to use, activate it and run:

```bash
python -m pip install -r .github/minions/requirements.txt
```

### Option B: create a new venv in this repository (recommended for clean installs)

```bash
python -m venv .venv
# Activate (platform-specific):
# Windows (PowerShell): .\.venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate

python -m pip install -r .github/minions/requirements.txt
```

---

## 3. Configure prompt discovery (required)

MinionLoom’s prompts live in:
- `.github/minions/prompts/`

Any tool that consumes Copilot prompts must be configured to scan that path.

### Example: VS Code setting
```json
{
  "copilot.prompts.paths": [
    ".github/minions/prompts"
  ]
}
```

---

## sqlite-vec Extension Requirement

MinionLoom uses the sqlite-vec extension for vector search and embedding storage in SQLite. This extension is NOT included with standard Python or SQLite installations.

### How to Obtain sqlite-vec
- Download or build the sqlite-vec extension from the official repository: https://github.com/asg017/sqlite-vec
- Place the compiled `vec0` (or platform-appropriate) shared library in a known location on your system.

### How to Load sqlite-vec in Python
Add the following code before any vector operations:

```python
import sqlite3
conn = sqlite3.connect('minion_state.db')
try:
    conn.enable_load_extension(True)
    conn.load_extension('/path/to/vec0')  # Update with your actual path
except Exception as e:
    raise RuntimeError(f"Failed to load sqlite-vec extension: {e}")
```

- If the extension is not loaded, vector operations will fail with `no such module: vec0`.
- Ensure your Python environment allows extension loading (may require admin privileges).

### Troubleshooting
- If you see `sqlite3.OperationalError: no such module: vec0`, the extension is not loaded or not found.
- Check your Python, SQLite, and OS compatibility with the extension.

---

## 4. Verify installation (minimal sanity checks)

### 4.1 Check that Python deps load
```bash
python -c "import orchestrator; print('ok')"
```

### 4.2 Check that prompts are usable (status command)
```bash
python .github/minions/orchestrator/dag.py status
```

If these succeed, the environment is correctly configured.

---

## 5. Worktree Management (as of March 13, 2026)

- MinionLoom provisions isolated git worktrees for each task using Python-native logic (see `.github/minions/orchestrator/worktree_manager.py`).
- All orchestration and state are managed in Python and persisted in the SQLite database (`minion_state.db`).
- The previous PowerShell-based `env_manager.ps1` is no longer used or required.
- Worktree paths and status are updated directly in the database; no subprocess-based JSON state passing is used.
- Contributors and AI agents must follow this pattern for all future orchestration and state management.

---

---

## 5. How to use the prompts

Once configured, use Copilot CLI/chat with the following slash commands:
- `/research`
- `/status`
- `/continue`

Each maps to a file in `.github/minions/prompts/`.

---


## 6. Notes for AI agents and contributors
- Treat this file as the authoritative install and extension procedure.
- All orchestration, state, and task management must be handled in Python and persisted in the database. Do not use subprocesses to emit/parse JSON for internal state.
- Run the checks exactly as written; if a command fails, report the failure output.
- Do not assume any paths exist unless explicitly created by a command in this document.

---

This guide is intentionally procedural and minimally prescriptive so it can be followed reliably by both humans and AI agents.
