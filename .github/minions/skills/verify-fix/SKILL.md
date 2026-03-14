---
name: verify-fix
description: Run PowerShell-based validation, inspect failures, and apply surgical fixes before retrying.
---

# Verify And Fix

Use this skill when an implementation has been produced and the next step is validation followed by a bounded repair loop.

1. Run the project's validation entrypoint from PowerShell so the command reflects the Windows execution environment.
2. Capture both stdout and stderr. If stderr is empty, treat failing stdout as the diagnostic payload.
3. Extract the first concrete failure site before editing anything: file path, assertion, exception type, stack frame, or failing command.
4. Make a minimal fix aimed at that failure instead of broad speculative rewrites.
5. Re-run the same validation path immediately.
6. If the same class of failure persists after two repair passes, stop and surface the exact remaining error.

When repairing code, preserve existing style, keep the patch narrow, and avoid changing unrelated files.