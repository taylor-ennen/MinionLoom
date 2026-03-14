# MinionLoom Copilot Instructions

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
