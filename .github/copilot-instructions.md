# MinionLoom Copilot Instructions

## Purpose
This file is used by Copilot chat and Copilot CLI to guide AI behavior when operating in this repository. It is the single source of truth for AI instructions and is loaded on every prompt by Copilot.

## Goals
- Treat this repository as a **drop-in plugin** under `.github/minions/`.
- Avoid modifying files outside `.github/minions/` unless the user explicitly asks.
- Ensure all work is **traceable**, **testable**, and **reversible** via git worktrees.
- If the project appears to be part of a larger repo, refer to the outer repo's README/goal documents for high-level guidance.

## Rules of Instruction
- Write deterministic, fully type-hinted Python code.
- Prefer minimal standard-library solutions over heavy frameworks unless a dependency is already required for the task.
- Keep diffs surgical, preserve existing behavior when possible, and make error handling explicit.
- When running tests or commands, capture concrete failure output before editing files.
- After each phase or significant edit, all code and documentation changes MUST be committed to version control before proceeding. This ensures traceability, testability, and reversibility. Do not proceed to the next step until changes are committed.

## Prompt Guidelines
- Prompts are defined in `.github/minions/prompts/`.
- For `/status`, run the MinionLoom status task from the repository root:
  ```powershell
  .\.venv\Scripts\python.exe .github/minions/orchestrator/dag.py status
  ```
- For `/continue`, follow the current implementation plan and take the next logical step.

## OSS & Testing Considerations
- The `tests/` directory is excluded from the open-source distribution via `.gitignore`. Local-only test scripts (e.g., for sqlite-vec integration) are not part of the OSS release and are for developer validation only.
- Advanced vector search (embedding similarity) requires the `sqlite-vec` extension, which is not bundled with Python or SQLite. This must be installed separately for local vector search features.
- Basic SQLite features require no setup and work out of the box. Vector search is optional and only needed for AI/embedding use cases.
- This application is designed for local, user-installed operation. SQLite is chosen for its zero-setup, cross-platform nature, and suitability for single-user, local-first workflows. If multi-user or networked scenarios are needed, consider a client-server DB (e.g., Postgres) with pgvector.
