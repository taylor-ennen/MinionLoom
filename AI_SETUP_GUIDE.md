# MinionLoom Installation & Setup (AI-friendly)

This document is the single source of truth for how MinionLoom must be installed and configured so that it works correctly.
It is written to be directly usable by AI agents and by humans.

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

## 5. How to use the prompts

Once configured, use Copilot CLI/chat with the following slash commands:
- `/research`
- `/status`
- `/continue`

Each maps to a file in `.github/minions/prompts/`.

---

## 6. Notes for AI agents
- Treat this file as the authoritative install procedure.
- Run the checks exactly as written; if a command fails, report the failure output.
- Do not assume any paths exist unless explicitly created by a command in this document.

---

This guide is intentionally procedural and minimally prescriptive so it can be followed reliably by both humans and AI agents.
