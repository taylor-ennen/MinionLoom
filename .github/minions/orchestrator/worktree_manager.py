from pathlib import Path
import subprocess
from typing import Optional

from .dag import MINION_ROOT, MinionExecutionError

def get_worktree_path(task_id: str) -> Path:
    """Return the canonical worktree path for a given task."""
    safe_task_id = ''.join(c if c.isalnum() or c in ('-', '_') else '-' for c in task_id)
    return MINION_ROOT / '.worktrees' / safe_task_id


def ensure_worktree(task_id: str, base_branch: Optional[str] = None) -> Path:
    """
    Ensure a git worktree exists for the given task_id.
    If it does not exist, create it from the given base_branch (default: current branch).
    Returns the Path to the worktree.
    Raises MinionExecutionError on failure.
    """
    worktree_path = get_worktree_path(task_id)
    if worktree_path.exists():
        return worktree_path

    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    branch_name = f"minion/{task_id}"
    # Create a new branch for the worktree if it doesn't exist
    result = subprocess.run([
        'git', 'rev-parse', '--verify', branch_name
    ], cwd=str(MINION_ROOT), capture_output=True, text=True)
    if result.returncode != 0:
        # Branch does not exist, create it from base_branch or HEAD
        base = base_branch or 'HEAD'
        result = subprocess.run([
            'git', 'branch', branch_name, base
        ], cwd=str(MINION_ROOT), capture_output=True, text=True)
        if result.returncode != 0:
            raise MinionExecutionError(f"Failed to create branch {branch_name}: {result.stderr.strip()}")
    # Add the worktree
    result = subprocess.run([
        'git', 'worktree', 'add', str(worktree_path), branch_name
    ], cwd=str(MINION_ROOT), capture_output=True, text=True)
    if result.returncode != 0:
        raise MinionExecutionError(f"Failed to add worktree: {result.stderr.strip()}")
    return worktree_path
