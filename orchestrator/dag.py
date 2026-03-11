from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue
from typing import Any, Protocol

try:
    import sqlite_vec
except ImportError:
    sqlite_vec = None

MINION_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = MINION_ROOT / 'minion_state.db'
ENV_MANAGER_PATH = MINION_ROOT / 'scripts' / 'env_manager.ps1'
MAX_REFLECTIONS = 2


class QueueLike(Protocol):
    def put(self, item: str) -> None:
        ...


class MinionExecutionError(RuntimeError):
    pass


@dataclass(slots=True)
class TaskContext:
    task_id: str
    worktree_path: Path
    branch_name: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def emit_log(message: str, log_queue: QueueLike | None = None) -> None:
    if log_queue is not None:
        log_queue.put(message)
    else:
        print(message, flush=True)


def initialize_database() -> sqlite3.Connection:
    if sqlite_vec is None:
        raise MinionExecutionError('sqlite-vec is not installed. Run install_minions.ps1 before starting the orchestrator.')

    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.enable_load_extension(True)

    if hasattr(sqlite_vec, 'load'):
        sqlite_vec.load(connection)
    else:
        connection.load_extension(sqlite_vec.loadable_path())

    connection.execute(
        '''
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            branch_name TEXT,
            worktree_path TEXT,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        '''
    )
    connection.execute(
        '''
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            phase TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        '''
    )
    connection.execute(
        '''
        CREATE TABLE IF NOT EXISTS reflections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            attempt INTEGER NOT NULL,
            failure_output TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        '''
    )
    connection.execute(
        '''
        CREATE VIRTUAL TABLE IF NOT EXISTS vector_memory USING vec0(
            embedding float[1536]
        )
        '''
    )
    connection.commit()
    return connection


def record_event(connection: sqlite3.Connection, task_id: str, phase: str, message: str) -> None:
    connection.execute(
        'INSERT INTO events (task_id, phase, message, created_at) VALUES (?, ?, ?, ?)',
        (task_id, phase, message, utc_now()),
    )
    connection.commit()


def update_run(
    connection: sqlite3.Connection,
    task_id: str,
    status: str,
    worktree_path: Path | None = None,
    branch_name: str | None = None,
) -> None:
    existing = connection.execute('SELECT id FROM runs WHERE task_id = ?', (task_id,)).fetchone()
    now = utc_now()
    if existing is None:
        connection.execute(
            'INSERT INTO runs (task_id, branch_name, worktree_path, status, started_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)',
            (task_id, branch_name, str(worktree_path) if worktree_path else None, status, now, now),
        )
    else:
        connection.execute(
            'UPDATE runs SET branch_name = COALESCE(?, branch_name), worktree_path = COALESCE(?, worktree_path), status = ?, updated_at = ? WHERE task_id = ?',
            (branch_name, str(worktree_path) if worktree_path else None, status, now, task_id),
        )
    connection.commit()


def run_checked(command: list[str], cwd: Path, phase: str, log_queue: QueueLike | None) -> subprocess.CompletedProcess[str]:
    emit_log(f'[{phase}] running: {subprocess.list2cmdline(command)}', log_queue)
    result = subprocess.run(command, cwd=str(cwd), capture_output=True, text=True, check=False)
    if result.stdout.strip():
        for line in result.stdout.splitlines():
            emit_log(f'[{phase}] {line}', log_queue)
    if result.stderr.strip():
        for line in result.stderr.splitlines():
            emit_log(f'[{phase}] {line}', log_queue)
    if result.returncode != 0:
        raise MinionExecutionError(f'{phase} failed with exit code {result.returncode}.')
    return result


def setup_environment(task_id: str, connection: sqlite3.Connection, log_queue: QueueLike | None) -> TaskContext:
    command = ['pwsh', '-NoProfile', '-File', str(ENV_MANAGER_PATH), '-TaskID', task_id]
    emit_log('[setup] invoking env_manager.ps1', log_queue)
    result = subprocess.run(command, cwd=str(MINION_ROOT), capture_output=True, text=True, check=False)

    if result.stderr.strip():
        for line in result.stderr.splitlines():
            emit_log(f'[setup] {line}', log_queue)

    raw_stdout = result.stdout.strip()
    if not raw_stdout:
        raise MinionExecutionError('env_manager.ps1 did not emit JSON to stdout.')

    try:
        payload = json.loads(raw_stdout)
    except json.JSONDecodeError as error:
        raise MinionExecutionError(f'Unable to parse env_manager.ps1 output: {raw_stdout}') from error

    if result.returncode != 0 or payload.get('status') != 'success':
        raise MinionExecutionError(f'env_manager.ps1 reported failure: {payload}')

    worktree_path = Path(str(payload['worktree_path']))
    branch_name = subprocess.run(
        ['git', 'branch', '--show-current'],
        cwd=str(worktree_path),
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    context = TaskContext(task_id=task_id, worktree_path=worktree_path, branch_name=branch_name)
    update_run(connection, task_id, 'environment-ready', worktree_path=worktree_path, branch_name=branch_name)
    record_event(connection, task_id, 'setup', f'Created worktree at {worktree_path}')
    return context


def hydrate_requirements(context: TaskContext, connection: sqlite3.Connection, log_queue: QueueLike | None) -> str:
    instructions_path = MINION_ROOT / 'templates' / 'copilot-instructions.md'
    instructions = instructions_path.read_text(encoding='utf-8').strip()
    record_event(connection, context.task_id, 'hydrate', 'Constructed implementation prompt from template instructions.')
    emit_log(f'[hydrate] loaded repository instructions: {instructions}', log_queue)
    return instructions


def stream_process_output(
    process: subprocess.Popen[str],
    phase: str,
    task_id: str,
    connection: sqlite3.Connection,
    log_queue: QueueLike | None,
) -> int:
    if process.stdout is None:
        raise MinionExecutionError(f'{phase} did not expose stdout for streaming.')

    while process.poll() is None:
        line = process.stdout.readline()
        if line:
            cleaned = line.rstrip()
            emit_log(f'[{phase}] {cleaned}', log_queue)
            record_event(connection, task_id, phase, cleaned)
        else:
            time.sleep(0.1)

    for line in process.stdout.readlines():
        cleaned = line.rstrip()
        if cleaned:
            emit_log(f'[{phase}] {cleaned}', log_queue)
            record_event(connection, task_id, phase, cleaned)

    return process.wait()


def run_implementation(context: TaskContext, hydrated_context: str, connection: sqlite3.Connection, log_queue: QueueLike | None) -> None:
    base_prompt = f'Implement the requirements described in task {context.task_id}. Ensure all tests pass.'
    args = [
        'copilot',
        '--agent',
        'implementer',
        '-p',
        base_prompt,
        '--autopilot',
        '--yolo',
        '--max-autopilot-continues',
        '10',
        '--model',
        'gpt-4.1',
    ]
    if hydrated_context:
        emit_log('[implement] hydration context loaded before implementation run', log_queue)
    emit_log('[implement] model active: gpt-4.1', log_queue)
    emit_log(f'[implement] running: {subprocess.list2cmdline(args)}', log_queue)
    process = subprocess.Popen(
        args,
        cwd=str(context.worktree_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    return_code = stream_process_output(process, 'implement', context.task_id, connection, log_queue)
    if return_code != 0:
        raise MinionExecutionError(f'Implementation agent exited with code {return_code}.')
    update_run(connection, context.task_id, 'implementation-complete')


def build_test_command(worktree_path: Path) -> list[str]:
    script = r"""
param([string]$WorktreePath)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Set-Location $WorktreePath

if (Test-Path '.\scripts\test.ps1') {
    & pwsh -NoProfile -File '.\scripts\test.ps1'
    exit $LASTEXITCODE
}

$venvPython = Join-Path $PWD '.venv\Scripts\python.exe'
if (Test-Path $venvPython) {
    & $venvPython -m pytest
    exit $LASTEXITCODE
}

if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 -m pytest
    exit $LASTEXITCODE
}

if (Get-Command python -ErrorAction SilentlyContinue) {
    & python -m pytest
    exit $LASTEXITCODE
}

if (Test-Path '.\package.json' -and (Get-Command npm -ErrorAction SilentlyContinue)) {
    & npm test
    exit $LASTEXITCODE
}

Write-Error 'No supported test suite was detected for this worktree.'
exit 1
""".strip()
    return ['pwsh', '-NoProfile', '-Command', script, str(worktree_path)]


def run_validation(context: TaskContext, connection: sqlite3.Connection, log_queue: QueueLike | None) -> subprocess.CompletedProcess[str]:
    command = build_test_command(context.worktree_path)
    emit_log('[validation] running PowerShell validation pipeline', log_queue)
    result = subprocess.run(command, cwd=str(context.worktree_path), capture_output=True, text=True, check=False)
    if result.stdout.strip():
        for line in result.stdout.splitlines():
            emit_log(f'[validation] {line}', log_queue)
            record_event(connection, context.task_id, 'validation', line)
    if result.stderr.strip():
        for line in result.stderr.splitlines():
            emit_log(f'[validation] {line}', log_queue)
            record_event(connection, context.task_id, 'validation', line)
    return result


def reflect_and_fix(
    context: TaskContext,
    connection: sqlite3.Connection,
    log_queue: QueueLike | None,
    failure_output: str,
    attempt: int,
) -> None:
    connection.execute(
        'INSERT INTO reflections (task_id, attempt, failure_output, created_at) VALUES (?, ?, ?, ?)',
        (context.task_id, attempt, failure_output, utc_now()),
    )
    connection.commit()

    prompt = f'The test failed with the following output: {failure_output}. Fix the implementation.'
    args = [
        'copilot',
        '--agent',
        'implementer',
        '-p',
        prompt,
        '--autopilot',
        '--yolo',
        '--max-autopilot-continues',
        '10',
        '--model',
        'gpt-5-mini',
    ]
    emit_log('[reflect] model active: gpt-5-mini', log_queue)
    emit_log(f'[reflect] reflection attempt {attempt}: {subprocess.list2cmdline(args)}', log_queue)
    process = subprocess.Popen(
        args,
        cwd=str(context.worktree_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    return_code = stream_process_output(process, 'reflect', context.task_id, connection, log_queue)
    if return_code != 0:
        raise MinionExecutionError(f'Reflection attempt {attempt} exited with code {return_code}.')


def ensure_tests_pass(context: TaskContext, connection: sqlite3.Connection, log_queue: QueueLike | None) -> None:
    result = run_validation(context, connection, log_queue)
    if result.returncode == 0:
        update_run(connection, context.task_id, 'validated')
        emit_log('[validation] test suite passed on first attempt', log_queue)
        return

    for attempt in range(1, MAX_REFLECTIONS + 1):
        failure_output = (result.stderr.strip() or result.stdout.strip() or 'Unknown validation failure.')
        emit_log(f'[validation] attempt {attempt} failed; invoking bounded reflection', log_queue)
        reflect_and_fix(context, connection, log_queue, failure_output, attempt)
        result = run_validation(context, connection, log_queue)
        if result.returncode == 0:
            update_run(connection, context.task_id, 'validated')
            emit_log(f'[validation] tests passed after reflection attempt {attempt}', log_queue)
            return

    raise MinionExecutionError('Tests failed after the maximum of two reflection attempts.')


def finalize(context: TaskContext, connection: sqlite3.Connection, log_queue: QueueLike | None) -> None:
    emit_log('[finalize] preparing repository for commit and push', log_queue)
    run_checked(['git', 'add', '.'], context.worktree_path, 'finalize', log_queue)

    staged_status = subprocess.run(
        ['git', 'diff', '--cached', '--quiet'],
        cwd=str(context.worktree_path),
        capture_output=True,
        text=True,
        check=False,
    )
    if staged_status.returncode == 0:
        emit_log('[finalize] no staged changes detected after validation; skipping commit, push, and PR creation', log_queue)
        update_run(connection, context.task_id, 'complete', worktree_path=context.worktree_path, branch_name=context.branch_name)
        return

    run_checked(
        ['git', 'commit', '-m', f'Automated Minion Implementation for {context.task_id}'],
        context.worktree_path,
        'finalize',
        log_queue,
    )

    remote_check = subprocess.run(
        ['git', 'remote', 'get-url', 'origin'],
        cwd=str(context.worktree_path),
        capture_output=True,
        text=True,
        check=False,
    )
    if remote_check.returncode != 0:
        raise MinionExecutionError('No origin remote is configured for the minion worktree repository.')

    run_checked(['git', 'push', '--set-upstream', 'origin', context.branch_name], context.worktree_path, 'finalize', log_queue)

    pr_prompt = (
        'Use the built-in MCP server to open a GitHub pull request for the current branch. '
        f'The task identifier is {context.task_id}.'
    )
    run_checked(
        [
            'copilot',
            '--agent',
            'implementer',
            '-p',
            pr_prompt,
            '--autopilot',
            '--yolo',
            '--max-autopilot-continues',
            '10',
            '--model',
            'gpt-4.1',
        ],
        context.worktree_path,
        'finalize',
        log_queue,
    )
    update_run(connection, context.task_id, 'complete', worktree_path=context.worktree_path, branch_name=context.branch_name)


def run_task(task_id: str, log_queue: QueueLike | None = None) -> None:
    connection = initialize_database()
    update_run(connection, task_id, 'starting')
    emit_log(f'[dag] starting task {task_id}', log_queue)

    try:
        context = setup_environment(task_id, connection, log_queue)
        prompt = hydrate_requirements(context, connection, log_queue)
        run_implementation(context, prompt, connection, log_queue)
        ensure_tests_pass(context, connection, log_queue)
        finalize(context, connection, log_queue)
        emit_log(f'[dag] task complete: {task_id}', log_queue)
    except Exception as error:
        update_run(connection, task_id, 'failed')
        record_event(connection, task_id, 'error', str(error))
        raise
    finally:
        connection.close()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Run the Local Minion DAG orchestrator.')
    parser.add_argument('task_id', help='Identifier for the task to execute.')
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])

    try:
        run_task(args.task_id, log_queue=None)
    except MinionExecutionError as error:
        emit_log(f'[dag] execution failed: {error}')
        return 1
    except Exception as error:
        emit_log(f'[dag] unexpected error: {error}')
        return 1

    return 0


if __name__ == '__main__':
    raise SystemExit(main())