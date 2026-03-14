from __future__ import annotations

import argparse
import json
import os
import queue
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

try:
    import sqlite_vec
except ImportError:
    sqlite_vec = None

MINION_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = MINION_ROOT / 'minion_state.db'
ENV_MANAGER_PATH = MINION_ROOT / 'scripts' / 'env_manager.ps1'
MAX_REFLECTIONS = 2
IMPLEMENTER_AGENT = 'minionloom/implementer'
ALLOWED_MODELS = ('gpt-4.1', 'gpt-5-mini')
RUNNING_STATUSES = {'queued', 'running'}
TERMINAL_STATUSES = {'complete', 'failed', 'interrupted'}
ACTIVE_PROCESSES: dict[str, subprocess.Popen[str]] = {}
ACTIVE_PROCESSES_LOCK = threading.Lock()


class QueueLike(Protocol):
    def put(self, item: str) -> None:
        ...


class MinionExecutionError(RuntimeError):
    pass


class ControlRequestedError(MinionExecutionError):
    pass


@dataclass(slots=True)
class TaskContext:
    task_id: str
    worktree_path: Path
    branch_name: str
    minion_designation: str
    task_type: str


@dataclass(slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


def should_skip_implementation(task_id: str) -> bool:
    normalized_task_id = task_id.lower()
    return (
        normalized_task_id.startswith('selftest')
        or normalized_task_id.startswith('diagnostic')
        or normalized_task_id.startswith('status')
    )


def determine_task_type(task_id: str) -> str:
    return 'diagnostic' if should_skip_implementation(task_id) else 'implementation'


def build_minion_designation(task_id: str) -> str:
    normalized = ''.join(character if character.isalnum() else '-' for character in task_id.upper()).strip('-')
    if not normalized:
        normalized = 'TASK'
    return f'MINION-{normalized}'


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def calculate_duration_seconds(started_at: str | None, completed_at: str | None) -> float | None:
    started = parse_timestamp(started_at)
    if started is None:
        return None
    ended = parse_timestamp(completed_at) or datetime.now(timezone.utc)
    return max((ended - started).total_seconds(), 0.0)


_EMBEDDING_MODEL: Any | None = None


def _get_local_embedding_model() -> Any | None:
    """Return a locally hosted embedding model, if available.

    This avoids external network calls for embedding generation. The default
    model is `all-MiniLM-L6-v2`, but `MINIONLOOM_EMBEDDING_MODEL` can override it.

    If the dependency is not installed, this returns None.
    """
    global _EMBEDDING_MODEL

    if _EMBEDDING_MODEL is not None:
        return _EMBEDDING_MODEL

    try:
        import onnxruntime as ort
        from transformers import AutoTokenizer
    except Exception:
        # We require ONNX runtime and transformers for the local-only ONNX embedding path.
        return None

    # If onnxruntime is available, prefer loading a quantized ONNX MiniLM model
    models_dir = MINION_ROOT / '.github' / 'minions' / 'embedding_model'
    if models_dir.exists():
        # find a quantized ONNX file (local vendored copy)
        candidates = list(models_dir.glob('*.quant.onnx'))
        if candidates:
            model_path = str(candidates[0])
            # look for a local tokenizer directory under the same folder
            tokenizer_dirs = [p for p in models_dir.iterdir() if p.is_dir() and ('MiniLM' in p.name or 'tokenizer' in p.name)]
            if not tokenizer_dirs:
                # No local tokenizer present; do not attempt network downloads — disable local ONNX embedding.
                return None
            tokenizer_dir = str(tokenizer_dirs[0])
            try:
                tokenizer = AutoTokenizer.from_pretrained(tokenizer_dir, local_files_only=True)
                session = ort.InferenceSession(model_path)
                _EMBEDDING_MODEL = (session, tokenizer)
                return _EMBEDDING_MODEL
            except Exception:
                # If loading the ONNX session or tokenizer fails, do not fall back to other frameworks.
                return None

    return None


def emit_log(message: str, log_queue: QueueLike | None = None) -> None:
    if log_queue is not None:
        log_queue.put(message)
    else:
        print(message, flush=True)


def assert_allowed_model(model: str) -> str:
    if model not in ALLOWED_MODELS:
        raise MinionExecutionError(f'Model {model} is not permitted. Allowed models: {", ".join(ALLOWED_MODELS)}.')
    return model


def ensure_column(connection: sqlite3.Connection, table_name: str, column_name: str, column_definition: str) -> None:
    existing_columns = {
        row['name']
        for row in connection.execute(f'PRAGMA table_info({table_name})').fetchall()
    }
    if column_name not in existing_columns:
        connection.execute(f'ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}')


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
            task_id TEXT NOT NULL UNIQUE,
            task_type TEXT NOT NULL DEFAULT 'implementation',
            minion_designation TEXT,
            branch_name TEXT,
            worktree_path TEXT,
            status TEXT NOT NULL,
            current_phase TEXT,
            active_model TEXT,
            reflection_attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            control_state TEXT NOT NULL DEFAULT 'idle',
            control_requested_at TEXT,
            control_completed_at TEXT,
            worktree_cleanup_status TEXT NOT NULL DEFAULT 'pending',
            retry_of_task_id TEXT,
            retry_sequence INTEGER NOT NULL DEFAULT 0,
            finalize_summary TEXT,
            commit_sha TEXT,
            remote_name TEXT,
            remote_url TEXT,
            push_status TEXT,
            pull_request_status TEXT,
            started_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT
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
        CREATE TABLE IF NOT EXISTS phase_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            phase TEXT NOT NULL,
            status TEXT NOT NULL,
            detail TEXT,
            model TEXT,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            duration_seconds REAL
        )
        '''
    )
    connection.execute(
        '''
        CREATE VIRTUAL TABLE IF NOT EXISTS vector_memory USING vec0(
            task_id TEXT UNIQUE,
            embedding float[384],
            spec TEXT
        )
        '''
    )

    ensure_column(connection, 'runs', 'task_type', "TEXT NOT NULL DEFAULT 'implementation'")
    ensure_column(connection, 'runs', 'minion_designation', 'TEXT')
    ensure_column(connection, 'runs', 'current_phase', 'TEXT')
    ensure_column(connection, 'runs', 'active_model', 'TEXT')
    ensure_column(connection, 'runs', 'reflection_attempts', 'INTEGER NOT NULL DEFAULT 0')
    ensure_column(connection, 'runs', 'last_error', 'TEXT')
    ensure_column(connection, 'runs', 'control_state', "TEXT NOT NULL DEFAULT 'idle'")
    ensure_column(connection, 'runs', 'control_requested_at', 'TEXT')
    ensure_column(connection, 'runs', 'control_completed_at', 'TEXT')
    ensure_column(connection, 'runs', 'worktree_cleanup_status', "TEXT NOT NULL DEFAULT 'pending'")
    ensure_column(connection, 'runs', 'retry_of_task_id', 'TEXT')
    ensure_column(connection, 'runs', 'retry_sequence', 'INTEGER NOT NULL DEFAULT 0')
    ensure_column(connection, 'runs', 'finalize_summary', 'TEXT')
    ensure_column(connection, 'runs', 'commit_sha', 'TEXT')
    ensure_column(connection, 'runs', 'remote_name', 'TEXT')
    ensure_column(connection, 'runs', 'remote_url', 'TEXT')
    ensure_column(connection, 'runs', 'push_status', 'TEXT')
    ensure_column(connection, 'runs', 'pull_request_status', 'TEXT')
    ensure_column(connection, 'runs', 'completed_at', 'TEXT')

    connection.execute(
        '''
        UPDATE runs
        SET status = 'complete',
            completed_at = COALESCE(completed_at, updated_at),
            current_phase = COALESCE(current_phase, 'finalize'),
            active_model = COALESCE(active_model, 'idle')
        WHERE status NOT IN ('queued', 'running', 'complete', 'failed', 'interrupted')
        '''
    )

    rows_needing_backfill = connection.execute(
        '''
          SELECT task_id, task_type, minion_designation, current_phase, active_model, completed_at, updated_at, status,
                    control_state, worktree_cleanup_status
        FROM runs
        WHERE task_type IS NULL
           OR task_type = ''
           OR minion_designation IS NULL
           OR minion_designation = ''
           OR current_phase IS NULL
           OR current_phase = ''
           OR active_model IS NULL
           OR active_model = ''
              OR control_state IS NULL
              OR control_state = ''
              OR worktree_cleanup_status IS NULL
              OR worktree_cleanup_status = ''
           OR (completed_at IS NULL AND status IN ('complete', 'failed', 'interrupted'))
        '''
    ).fetchall()
    for row in rows_needing_backfill:
        inferred_task_type = row['task_type'] or determine_task_type(row['task_id'])
        inferred_designation = row['minion_designation'] or build_minion_designation(row['task_id'])
        inferred_phase = row['current_phase'] or ('finalize' if row['status'] in TERMINAL_STATUSES else 'unknown')
        inferred_model = row['active_model'] or ('diagnostic-only' if inferred_task_type == 'diagnostic' else 'idle')
        inferred_completed_at = row['completed_at'] or (row['updated_at'] if row['status'] in TERMINAL_STATUSES else None)
        inferred_control_state = row['control_state'] or 'idle'
        inferred_cleanup_status = row['worktree_cleanup_status'] or ('available' if row['status'] in TERMINAL_STATUSES else 'pending')
        connection.execute(
            '''
            UPDATE runs
            SET task_type = ?,
                minion_designation = ?,
                current_phase = ?,
                active_model = ?,
                control_state = ?,
                worktree_cleanup_status = ?,
                completed_at = COALESCE(completed_at, ?)
            WHERE task_id = ?
            ''',
            (
                inferred_task_type,
                inferred_designation,
                inferred_phase,
                inferred_model,
                inferred_control_state,
                inferred_cleanup_status,
                inferred_completed_at,
                row['task_id'],
            ),
        )

    connection.commit()
    return connection


def serialize_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def serialize_run(row: sqlite3.Row) -> dict[str, Any]:
    payload = serialize_row(row) or {}
    payload['task_type'] = payload.get('task_type') or determine_task_type(payload['task_id'])
    payload['minion_designation'] = payload.get('minion_designation') or build_minion_designation(payload['task_id'])
    payload['current_phase'] = payload.get('current_phase') or ('finalize' if payload.get('status') in TERMINAL_STATUSES else 'unknown')
    payload['active_model'] = payload.get('active_model') or ('diagnostic-only' if payload['task_type'] == 'diagnostic' else 'idle')
    payload['control_state'] = payload.get('control_state') or 'idle'
    payload['worktree_cleanup_status'] = payload.get('worktree_cleanup_status') or ('available' if payload.get('status') in TERMINAL_STATUSES else 'pending')
    payload['elapsed_seconds'] = calculate_duration_seconds(payload.get('started_at'), payload.get('completed_at'))
    payload['is_active'] = payload.get('status') in RUNNING_STATUSES
    payload['worktree_name'] = Path(payload['worktree_path']).name if payload.get('worktree_path') else None
    payload['can_cancel'] = payload['is_active'] and payload['control_state'] != 'cancel-requested'
    payload['can_retry'] = payload.get('status') in TERMINAL_STATUSES
    payload['can_cleanup'] = (not payload['is_active']) and bool(payload.get('worktree_path')) and payload['worktree_cleanup_status'] not in {'removed', 'missing', 'not-applicable'}
    return payload


def serialize_phase(row: sqlite3.Row) -> dict[str, Any]:
    payload = serialize_row(row) or {}
    if payload.get('duration_seconds') is None:
        payload['duration_seconds'] = calculate_duration_seconds(payload.get('started_at'), payload.get('completed_at'))
    return payload


def record_event(connection: sqlite3.Connection, task_id: str, phase: str, message: str) -> None:
    connection.execute(
        'INSERT INTO events (task_id, phase, message, created_at) VALUES (?, ?, ?, ?)',
        (task_id, phase, message, utc_now()),
    )
    connection.commit()


def update_run(connection: sqlite3.Connection, task_id: str, **fields: Any) -> None:
    now = utc_now()
    existing = connection.execute('SELECT id FROM runs WHERE task_id = ?', (task_id,)).fetchone()

    if existing is None:
        payload: dict[str, Any] = {
            'task_id': task_id,
            'task_type': 'implementation',
            'minion_designation': None,
            'branch_name': None,
            'worktree_path': None,
            'status': 'queued',
            'current_phase': 'queued',
            'active_model': 'idle',
            'reflection_attempts': 0,
            'last_error': None,
            'control_state': 'idle',
            'control_requested_at': None,
            'control_completed_at': None,
            'worktree_cleanup_status': 'pending',
            'retry_of_task_id': None,
            'retry_sequence': 0,
            'finalize_summary': None,
            'commit_sha': None,
            'remote_name': None,
            'remote_url': None,
            'push_status': None,
            'pull_request_status': None,
            'started_at': now,
            'updated_at': now,
            'completed_at': None,
        }
        payload.update(fields)
        columns = ', '.join(payload.keys())
        placeholders = ', '.join(['?'] * len(payload))
        connection.execute(
            f'INSERT INTO runs ({columns}) VALUES ({placeholders})',
            tuple(payload.values()),
        )
    else:
        payload = dict(fields)
        payload['updated_at'] = now
        assignments = ', '.join(f'{key} = ?' for key in payload)
        connection.execute(
            f'UPDATE runs SET {assignments} WHERE task_id = ?',
            tuple(payload.values()) + (task_id,),
        )

    connection.commit()


def start_phase(
    connection: sqlite3.Connection,
    task_id: str,
    phase: str,
    *,
    detail: str | None = None,
    model: str | None = None,
) -> int:
    started_at = utc_now()
    connection.execute(
        'INSERT INTO phase_runs (task_id, phase, status, detail, model, started_at) VALUES (?, ?, ?, ?, ?, ?)',
        (task_id, phase, 'running', detail, model, started_at),
    )
    phase_id = int(connection.execute('SELECT last_insert_rowid()').fetchone()[0])
    update_run(
        connection,
        task_id,
        status='running',
        current_phase=phase,
        active_model=model or 'deterministic',
    )
    if detail:
        record_event(connection, task_id, phase, f'started: {detail}')
    return phase_id


def finish_phase(
    connection: sqlite3.Connection,
    phase_id: int,
    *,
    status: str,
    detail: str | None = None,
) -> None:
    row = connection.execute(
        'SELECT task_id, phase, detail, started_at FROM phase_runs WHERE id = ?',
        (phase_id,),
    ).fetchone()
    if row is None:
        return

    completed_at = utc_now()
    duration_seconds = calculate_duration_seconds(row['started_at'], completed_at)
    final_detail = detail if detail is not None else row['detail']
    connection.execute(
        'UPDATE phase_runs SET status = ?, detail = ?, completed_at = ?, duration_seconds = ? WHERE id = ?',
        (status, final_detail, completed_at, duration_seconds, phase_id),
    )
    connection.commit()

    if final_detail:
        record_event(connection, row['task_id'], row['phase'], f'{status}: {final_detail}')
    else:
        record_event(connection, row['task_id'], row['phase'], f'{status}: {row["phase"]}')


def build_copilot_command(prompt: str, model: str) -> list[str]:
    selected_model = assert_allowed_model(model)
    return [
        'copilot',
        '--agent',
        IMPLEMENTER_AGENT,
        '-p',
        prompt,
        '--autopilot',
        '--yolo',
        '--max-autopilot-continues',
        '10',
        '--model',
        selected_model,
    ]


def get_run_row(connection: sqlite3.Connection, task_id: str) -> sqlite3.Row | None:
    return connection.execute('SELECT * FROM runs WHERE task_id = ?', (task_id,)).fetchone()


def is_cancel_requested(connection: sqlite3.Connection, task_id: str) -> bool:
    row = get_run_row(connection, task_id)
    return bool(row and row['control_state'] == 'cancel-requested')


def register_active_process(task_id: str, process: subprocess.Popen[str]) -> None:
    with ACTIVE_PROCESSES_LOCK:
        ACTIVE_PROCESSES[task_id] = process


def clear_active_process(task_id: str, process: subprocess.Popen[str]) -> None:
    with ACTIVE_PROCESSES_LOCK:
        if ACTIVE_PROCESSES.get(task_id) is process:
            ACTIVE_PROCESSES.pop(task_id, None)


def terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return

    if os.name == 'nt':
        subprocess.run(
            ['taskkill', '/PID', str(process.pid), '/T', '/F'],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            check=False,
        )
    else:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def request_task_cancel(task_id: str) -> dict[str, Any]:
    connection = initialize_database()
    try:
        row = get_run_row(connection, task_id)
        if row is None:
            raise MinionExecutionError(f'Task {task_id} was not found.')
        if row['status'] in TERMINAL_STATUSES:
            return serialize_run(row)

        now = utc_now()
        update_run(
            connection,
            task_id,
            control_state='cancel-requested',
            control_requested_at=now,
            control_completed_at=None,
        )
        record_event(connection, task_id, 'control', 'Cancel requested from dashboard')
        with ACTIVE_PROCESSES_LOCK:
            process = ACTIVE_PROCESSES.get(task_id)
        if process is not None:
            terminate_process(process)
        updated = get_run_row(connection, task_id)
        return serialize_run(updated) if updated is not None else {'task_id': task_id, 'control_state': 'cancel-requested'}
    finally:
        connection.close()


def get_next_retry_identifier(task_id: str) -> tuple[str, int]:
    connection = initialize_database()
    try:
        row = get_run_row(connection, task_id)
        if row is None:
            raise MinionExecutionError(f'Task {task_id} was not found.')
        if row['status'] not in TERMINAL_STATUSES:
            raise MinionExecutionError(f'Task {task_id} must be complete, failed, or interrupted before it can be retried.')

        retry_source = row['retry_of_task_id'] or task_id
        next_sequence = int(
            connection.execute(
                'SELECT COALESCE(MAX(retry_sequence), 0) + 1 FROM runs WHERE task_id = ? OR retry_of_task_id = ?',
                (retry_source, retry_source),
            ).fetchone()[0]
        )
        return f'{retry_source}-retry-{next_sequence}', next_sequence
    finally:
        connection.close()


def cleanup_task_worktree_with_connection(
    connection: sqlite3.Connection,
    task_id: str,
    *,
    event_phase: str,
    update_control_state: bool,
) -> dict[str, Any]:
    row = get_run_row(connection, task_id)
    if row is None:
        raise MinionExecutionError(f'Task {task_id} was not found.')
    if row['status'] in RUNNING_STATUSES:
        raise MinionExecutionError(f'Task {task_id} is still active and cannot be cleaned up.')

    base_updates: dict[str, Any] = {}
    if update_control_state:
        base_updates['control_state'] = 'cleanup-complete'
        base_updates['control_completed_at'] = utc_now()

    worktree_path = row['worktree_path']
    if not worktree_path:
        update_run(connection, task_id, worktree_cleanup_status='not-applicable', **base_updates)
        record_event(connection, task_id, event_phase, 'Cleanup skipped because no worktree path was recorded')
    elif not Path(worktree_path).exists():
        update_run(connection, task_id, worktree_cleanup_status='missing', **base_updates)
        record_event(connection, task_id, event_phase, 'Cleanup completed because the worktree directory was already absent')
    else:
        result = subprocess.run(
            ['git', 'worktree', 'remove', '--force', worktree_path],
            cwd=str(MINION_ROOT),
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            check=False,
        )
        if result.returncode != 0:
            update_run(connection, task_id, worktree_cleanup_status='failed')
            raise MinionExecutionError(result.stderr.strip() or result.stdout.strip() or f'Unable to remove worktree for task {task_id}.')
        update_run(connection, task_id, worktree_cleanup_status='removed', **base_updates)
        record_event(connection, task_id, event_phase, f'Worktree cleanup completed for {worktree_path}')

    updated = get_run_row(connection, task_id)
    return serialize_run(updated) if updated is not None else {'task_id': task_id, 'worktree_cleanup_status': 'removed'}


def cleanup_task_worktree(task_id: str) -> dict[str, Any]:
    connection = initialize_database()
    try:
        return cleanup_task_worktree_with_connection(
            connection,
            task_id,
            event_phase='control',
            update_control_state=True,
        )
    finally:
        connection.close()


def ensure_branch_visible_in_graph(context: TaskContext, connection: sqlite3.Connection, log_queue: QueueLike | None) -> None:
    marker_message = f'Initialize < MinionLoom > task branch for {context.task_id}'
    result = execute_process(
        [
            'git',
            '-c', 'user.name=< MinionLoom >',
            '-c', 'user.email=minionloom@users.noreply.github.com',
            'commit', '--allow-empty', '-m', marker_message,
        ],
        cwd=context.worktree_path,
        phase='setup',
        task_id=context.task_id,
        connection=connection,
        log_queue=log_queue,
    )
    if result.returncode != 0:
        raise MinionExecutionError(f'Unable to create branch marker commit for {context.task_id}.')


def should_auto_cleanup_worktree(context: TaskContext) -> bool:
    return context.task_type == 'diagnostic'


def auto_cleanup_finished_worktree(context: TaskContext, connection: sqlite3.Connection, log_queue: QueueLike | None) -> None:
    if not should_auto_cleanup_worktree(context):
        return

    try:
        cleanup_task_worktree_with_connection(
            connection,
            context.task_id,
            event_phase='system',
            update_control_state=False,
        )
        emit_log(f'[cleanup] auto-removed diagnostic worktree for {context.task_id}', log_queue)
    except MinionExecutionError as error:
        record_event(connection, context.task_id, 'system', f'Automatic cleanup failed: {error}')
        emit_log(f'[cleanup] automatic cleanup failed for {context.task_id}: {error}', log_queue)


def drain_stream(stream: Any, channel: str, output_queue: queue.Queue[tuple[str, str]]) -> None:
    try:
        for line in iter(stream.readline, ''):
            if line:
                output_queue.put((channel, line.rstrip()))
    finally:
        stream.close()


def execute_process(
    command: list[str],
    *,
    cwd: Path,
    phase: str,
    task_id: str,
    connection: sqlite3.Connection,
    log_queue: QueueLike | None,
    log_stdout: bool = True,
    log_stderr: bool = True,
) -> CommandResult:
    emit_log(f'[{phase}] running: {subprocess.list2cmdline(command)}', log_queue)
    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding='utf-8',
        errors='replace',
        bufsize=1,
    )
    register_active_process(task_id, process)

    output_queue: queue.Queue[tuple[str, str]] = queue.Queue()
    threads = []
    for stream, channel in ((process.stdout, 'stdout'), (process.stderr, 'stderr')):
        if stream is None:
            continue
        worker = threading.Thread(target=drain_stream, args=(stream, channel, output_queue), daemon=True)
        worker.start()
        threads.append(worker)

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    cancellation_requested = False

    try:
        while True:
            while True:
                try:
                    channel, line = output_queue.get_nowait()
                except queue.Empty:
                    break
                if channel == 'stdout':
                    stdout_lines.append(line)
                    if log_stdout and line:
                        emit_log(f'[{phase}] {line}', log_queue)
                        record_event(connection, task_id, phase, line)
                else:
                    stderr_lines.append(line)
                    if log_stderr and line:
                        emit_log(f'[{phase}] {line}', log_queue)
                        record_event(connection, task_id, phase, line)

            if process.poll() is not None:
                break

            if is_cancel_requested(connection, task_id) and not cancellation_requested:
                cancellation_requested = True
                emit_log(f'[{phase}] cancellation requested; terminating active process', log_queue)
                record_event(connection, task_id, 'control', f'Cancelling process during {phase}')
                terminate_process(process)

            time.sleep(0.1)
    finally:
        clear_active_process(task_id, process)
        for worker in threads:
            worker.join(timeout=1.0)

    while True:
        try:
            channel, line = output_queue.get_nowait()
        except queue.Empty:
            break
        if channel == 'stdout':
            stdout_lines.append(line)
            if log_stdout and line:
                emit_log(f'[{phase}] {line}', log_queue)
                record_event(connection, task_id, phase, line)
        else:
            stderr_lines.append(line)
            if log_stderr and line:
                emit_log(f'[{phase}] {line}', log_queue)
                record_event(connection, task_id, phase, line)

    return_code = process.wait()
    if cancellation_requested:
        raise ControlRequestedError(f'Task {task_id} cancelled during {phase}.')
    return CommandResult(returncode=return_code, stdout='\n'.join(stdout_lines), stderr='\n'.join(stderr_lines))


def run_checked(command: list[str], cwd: Path, phase: str, log_queue: QueueLike | None) -> subprocess.CompletedProcess[str]:
    raise NotImplementedError('run_checked no longer supports execution without task context.')


def setup_environment(task_id: str, connection: sqlite3.Connection, log_queue: QueueLike | None) -> TaskContext:
    from .worktree_manager import ensure_worktree
    phase_id = start_phase(
        connection,
        task_id,
        'setup',
        detail='Provision worktree and attach shared virtual environment',
    )
    try:
        worktree_path = ensure_worktree(task_id)
        branch_name = subprocess.run(
            ['git', 'branch', '--show-current'],
            cwd=str(worktree_path),
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            check=False,
        ).stdout.strip()
        context = TaskContext(
            task_id=task_id,
            worktree_path=worktree_path,
            branch_name=branch_name,
            minion_designation=build_minion_designation(task_id),
            task_type=determine_task_type(task_id),
        )
        update_run(
            connection,
            task_id,
            branch_name=branch_name,
            worktree_path=str(worktree_path),
            minion_designation=context.minion_designation,
        )
        ensure_branch_visible_in_graph(context, connection, log_queue)
        finish_phase(connection, phase_id, status='completed', detail=f'Assigned worktree {worktree_path.name}')
        return context
    except ControlRequestedError as error:
        finish_phase(connection, phase_id, status='interrupted', detail=str(error))
        raise
    except Exception as error:
        finish_phase(connection, phase_id, status='failed', detail=str(error))
        raise MinionExecutionError(f'Worktree setup failed: {error}')


def hydrate_requirements(context: TaskContext, connection: sqlite3.Connection, log_queue: QueueLike | None) -> str:
    """
    Hydrate requirements for a task:
    - Loads task spec from tasks/{task_id}.md if available, else falls back to copilot-instructions.md.
    - Embeds the spec using Nomic and stores in vector_memory (task_id, embedding, spec).
    - Always commit changes after editing this logic. (See repo instructions.)
    """
    import os
    try:
        from nomic import embed
    except ImportError:
        embed = None  # optional; we prefer local models and may run without nomic

    phase_id = start_phase(connection, context.task_id, 'hydrate', detail='Load local instructions and task context')
    tasks_dir = MINION_ROOT.parent / 'tasks'
    spec_path = tasks_dir / f'{context.task_id}.md'
    if spec_path.exists():
        spec = spec_path.read_text(encoding='utf-8').strip()
        emit_log(f'[hydrate] loaded task spec: {spec_path}', log_queue)
    else:
        instructions_path = MINION_ROOT / 'templates' / 'copilot-instructions.md'
        spec = instructions_path.read_text(encoding='utf-8').strip()
        emit_log(f'[hydrate] loaded repository instructions: {instructions_path}', log_queue)

    # Embed the spec (prefer a local model to avoid external network calls).
    embedding: list[float] | None = None

    model = _get_local_embedding_model()
    if model is not None:
        try:
            # two supported types returned by _get_local_embedding_model:
            # - SentenceTransformer instance: call .encode(...)
            # - (onnxruntime.InferenceSession, tokenizer) tuple: run session with tokenizer
            if isinstance(model, tuple):
                session, tokenizer = model
                # prepare inputs
                try:
                    import numpy as np
                except Exception:
                    emit_log('[hydrate] numpy required for ONNX embedding but missing', log_queue)
                    session = None

                if session is not None:
                    tokens = tokenizer([spec], return_tensors='np', padding=True)
                    input_names = {n.name: n for n in session.get_inputs()}
                    feed = {}
                    for name in ['input_ids', 'input_ids:0', 'ids', 'input']:
                        if name in input_names and 'input_ids' in tokens:
                            feed[name] = tokens['input_ids']
                    for name in ['attention_mask', 'attention_mask:0', 'mask']:
                        if name in input_names and 'attention_mask' in tokens:
                            feed[name] = tokens['attention_mask']
                    for k, v in tokens.items():
                        if k in input_names:
                            feed[k] = v
                    if not feed:
                        arrs = list(tokens.values())
                        for i, (nname, n) in enumerate(input_names.items()):
                            if i < len(arrs):
                                feed[nname] = arrs[i]
                    outs = session.run(None, feed)
                    out = outs[0]
                    if out.ndim == 3:
                        mask = tokens.get('attention_mask')
                        if mask is not None:
                            mask = mask.astype(np.float32)
                            lens = mask.sum(axis=1, keepdims=True)
                            pooled = (out * mask[:, :, None]).sum(axis=1) / np.maximum(lens, 1)
                        else:
                            pooled = out.mean(axis=1)
                        vec = pooled[0]
                    elif out.ndim == 2:
                        vec = out[0]
                    else:
                        vec = out.flatten()
                    embedding = vec.astype(float).tolist()
                    emit_log('[hydrate] generated local embedding using ONNX MiniLM', log_queue)
            else:
                # No other model types are supported for this local-only harness.
                emit_log('[hydrate] found model of unsupported type; skipping embedding', log_queue)
        except Exception as e:
            emit_log(f'[hydrate] local embedding generation failed: {e}', log_queue)
            record_event(connection, context.task_id, 'hydrate', f'Local embedding failed: {e}')

    if embedding is None:
        # No external providers are used for this harness; record and continue.
        emit_log('[hydrate] no embedding available (local models not found)', log_queue)
        record_event(connection, context.task_id, 'hydrate', 'No local embedding model available')

    # Store (or update) the record. sqlite-vec accepts JSON text for the vector column.
    # Ensure we insert JSON text (empty list if embedding unavailable) to avoid NULL insert errors.
    vector_json: str = json.dumps(embedding if embedding is not None else [])

    connection.execute(
        'INSERT OR REPLACE INTO vector_memory (task_id, embedding, spec) VALUES (?, ?, ?)',
        (context.task_id, vector_json, spec),
    )
    connection.commit()
    emit_log(f'[hydrate] stored task spec for {context.task_id} in vector_memory (embedded: {embedding is not None})', log_queue)

    finish_phase(connection, phase_id, status='completed', detail='Task spec hydrated and embedded')
    return spec


def stream_process_output(
    process: subprocess.Popen[str],
    phase: str,
    phase_id: int,
    task_id: str,
    connection: sqlite3.Connection,
    log_queue: QueueLike | None,
) -> int:
    if process.stdout is None:
        raise MinionExecutionError(f'{phase} did not expose stdout for streaming.')

    register_active_process(task_id, process)
    cancellation_requested = False

    while process.poll() is None:
        line = process.stdout.readline()
        if line:
            cleaned = line.rstrip()
            emit_log(f'[{phase}] {cleaned}', log_queue)
            record_event(connection, task_id, phase, cleaned)
        else:
            time.sleep(0.1)

        if is_cancel_requested(connection, task_id) and not cancellation_requested:
            cancellation_requested = True
            emit_log(f'[{phase}] cancellation requested; terminating active process', log_queue)
            record_event(connection, task_id, 'control', f'Cancelling process during {phase}')
            terminate_process(process)

    for line in process.stdout.readlines():
        cleaned = line.rstrip()
        if cleaned:
            emit_log(f'[{phase}] {cleaned}', log_queue)
            record_event(connection, task_id, phase, cleaned)

    clear_active_process(task_id, process)
    return_code = process.wait()
    if cancellation_requested:
        finish_phase(connection, phase_id, status='interrupted', detail=f'Task cancelled during {phase}')
        raise ControlRequestedError(f'Task {task_id} cancelled during {phase}.')
    return return_code


def run_implementation(context: TaskContext, hydrated_context: str, connection: sqlite3.Connection, log_queue: QueueLike | None) -> None:
    if should_skip_implementation(context.task_id):
        phase_id = start_phase(connection, context.task_id, 'implement', detail='Diagnostic self-test does not invoke Copilot')
        emit_log('[implement] self-test mode active; skipping Copilot implementation phase', log_queue)

        if context.task_id.lower().startswith('status'):
            snapshot = get_system_snapshot(limit=20)
            emit_log(f'[implement] status snapshot: {json.dumps(snapshot, indent=2)}', log_queue)

        finish_phase(connection, phase_id, status='skipped', detail='Self-test mode active')
        update_run(connection, context.task_id, active_model='diagnostic-only')
        return

    phase_id = start_phase(
        connection,
        context.task_id,
        'implement',
        detail='Execute implementation agent in worktree',
        model='gpt-4.1',
    )
    base_prompt = f'Implement the requirements described in task {context.task_id}. Ensure all tests pass.'
    args = build_copilot_command(base_prompt, 'gpt-4.1')
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
        encoding='utf-8',
        errors='replace',
        bufsize=1,
    )
    return_code = stream_process_output(process, 'implement', phase_id, context.task_id, connection, log_queue)
    if return_code != 0:
        finish_phase(connection, phase_id, status='failed', detail=f'Implementation agent exited with code {return_code}')
        raise MinionExecutionError(f'Implementation agent exited with code {return_code}.')
    finish_phase(connection, phase_id, status='completed', detail='Implementation agent completed successfully')


def build_test_command(worktree_path: Path) -> list[str]:
    escaped_worktree_path = str(worktree_path).replace("'", "''")
    script = r"""
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Set-Location 'WORKTREE_PATH_PLACEHOLDER'

if (
    (Test-Path '.\plugin.json') -and
    (Test-Path '.\orchestrator\dag.py') -and
    (Test-Path '.\dashboard\app.py')
) {
    $venvPython = Join-Path $PWD '.venv\Scripts\python.exe'
    if (-not (Test-Path $venvPython)) {
        Write-Error 'Expected < MinionLoom > virtual environment was not found for self-test mode.'
        exit 1
    }

    & $venvPython -c "import sys; from pathlib import Path; root = Path.cwd(); sys.path.insert(0, str(root)); import orchestrator.dag; import dashboard.app; conn = orchestrator.dag.initialize_database(); conn.close(); print('minionloom self-test ok')"
    exit $LASTEXITCODE
}

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
""".strip().replace('WORKTREE_PATH_PLACEHOLDER', escaped_worktree_path)
    return ['pwsh', '-NoProfile', '-Command', script]


def run_validation_attempt(
    context: TaskContext,
    connection: sqlite3.Connection,
    log_queue: QueueLike | None,
    attempt: int,
) -> subprocess.CompletedProcess[str]:
    detail = f'Run validation attempt {attempt}'
    phase_id = start_phase(connection, context.task_id, 'validation', detail=detail)
    command = build_test_command(context.worktree_path)
    emit_log('[validation] running PowerShell validation pipeline', log_queue)
    try:
        result = execute_process(
            command,
            cwd=context.worktree_path,
            phase='validation',
            task_id=context.task_id,
            connection=connection,
            log_queue=log_queue,
        )
    except ControlRequestedError as error:
        finish_phase(connection, phase_id, status='interrupted', detail=str(error))
        raise

    status = 'completed' if result.returncode == 0 else 'failed'
    summary = 'Validation succeeded' if result.returncode == 0 else (result.stderr.strip() or result.stdout.strip() or 'Validation failed')
    finish_phase(connection, phase_id, status=status, detail=summary)
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

    update_run(connection, context.task_id, reflection_attempts=attempt)
    phase_id = start_phase(
        connection,
        context.task_id,
        'reflection',
        detail=f'Repair validation failure, attempt {attempt}',
        model='gpt-5-mini',
    )
    prompt = f'The test failed with the following output: {failure_output}. Fix the implementation.'
    args = build_copilot_command(prompt, 'gpt-5-mini')
    emit_log('[reflect] model active: gpt-5-mini', log_queue)
    emit_log(f'[reflect] reflection attempt {attempt}: {subprocess.list2cmdline(args)}', log_queue)
    process = subprocess.Popen(
        args,
        cwd=str(context.worktree_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding='utf-8',
        errors='replace',
        bufsize=1,
    )
    return_code = stream_process_output(process, 'reflect', phase_id, context.task_id, connection, log_queue)
    if return_code != 0:
        finish_phase(connection, phase_id, status='failed', detail=f'Reflection attempt {attempt} exited with code {return_code}')
        raise MinionExecutionError(f'Reflection attempt {attempt} exited with code {return_code}.')
    finish_phase(connection, phase_id, status='completed', detail=f'Reflection attempt {attempt} completed')


def ensure_tests_pass(context: TaskContext, connection: sqlite3.Connection, log_queue: QueueLike | None) -> None:
    result = run_validation_attempt(context, connection, log_queue, attempt=1)
    if result.returncode == 0:
        emit_log('[validation] test suite passed on first attempt', log_queue)
        return

    if should_skip_implementation(context.task_id):
        failure_output = result.stderr.strip() or result.stdout.strip() or 'Unknown validation failure.'
        raise MinionExecutionError(f'Self-test validation failed: {failure_output}')

    for attempt in range(1, MAX_REFLECTIONS + 1):
        failure_output = result.stderr.strip() or result.stdout.strip() or 'Unknown validation failure.'
        emit_log(f'[validation] attempt {attempt} failed; invoking bounded reflection', log_queue)
        reflect_and_fix(context, connection, log_queue, failure_output, attempt)
        result = run_validation_attempt(context, connection, log_queue, attempt=attempt + 1)
        if result.returncode == 0:
            emit_log(f'[validation] tests passed after reflection attempt {attempt}', log_queue)
            return

    raise MinionExecutionError('Tests failed after the maximum of two reflection attempts.')


def finalize(context: TaskContext, connection: sqlite3.Connection, log_queue: QueueLike | None) -> None:
    if should_skip_implementation(context.task_id):
        phase_id = start_phase(connection, context.task_id, 'finalize', detail='Diagnostic self-test skips Git finalization')
        emit_log('[finalize] self-test mode active; skipping commit, push, and pull request creation', log_queue)
        finish_phase(connection, phase_id, status='skipped', detail='Self-test mode active')
        update_run(
            connection,
            context.task_id,
            status='complete',
            completed_at=utc_now(),
            active_model='diagnostic-only',
            finalize_summary='Diagnostic self-test skipped Git finalization.',
            push_status='skipped',
            pull_request_status='skipped',
            worktree_cleanup_status='available',
        )
        return

    phase_id = start_phase(connection, context.task_id, 'finalize', detail='Commit, push, and open pull request')
    emit_log('[finalize] preparing repository for commit and push', log_queue)
    try:
        result = execute_process(
            ['git', 'add', '.'],
            cwd=context.worktree_path,
            phase='finalize',
            task_id=context.task_id,
            connection=connection,
            log_queue=log_queue,
        )
        if result.returncode != 0:
            raise MinionExecutionError(f'finalize failed with exit code {result.returncode}.')
    except ControlRequestedError as error:
        finish_phase(connection, phase_id, status='interrupted', detail=str(error))
        raise

    staged_status = subprocess.run(
        ['git', 'diff', '--cached', '--quiet'],
        cwd=str(context.worktree_path),
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        check=False,
    )
    if staged_status.returncode == 0:
        emit_log('[finalize] no staged changes detected after validation; skipping commit, push, and PR creation', log_queue)
        finish_phase(connection, phase_id, status='skipped', detail='No staged changes detected')
        update_run(
            connection,
            context.task_id,
            status='complete',
            completed_at=utc_now(),
            active_model='idle',
            finalize_summary='Validation completed with no repository changes to commit.',
            push_status='skipped-no-changes',
            pull_request_status='skipped-no-changes',
            worktree_cleanup_status='available',
        )
        return

    commit_result = execute_process(
        ['git', 'commit', '-m', f'Automated Minion Implementation for {context.task_id}'],
        cwd=context.worktree_path,
        phase='finalize',
        task_id=context.task_id,
        connection=connection,
        log_queue=log_queue,
    )
    if commit_result.returncode != 0:
        finish_phase(connection, phase_id, status='failed', detail='Commit failed')
        raise MinionExecutionError(f'finalize failed with exit code {commit_result.returncode}.')

    commit_sha = subprocess.run(
        ['git', 'rev-parse', 'HEAD'],
        cwd=str(context.worktree_path),
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        check=False,
    ).stdout.strip() or None

    remote_check = subprocess.run(
        ['git', 'remote', 'get-url', 'origin'],
        cwd=str(context.worktree_path),
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        check=False,
    )
    if remote_check.returncode != 0:
        emit_log('[finalize] no origin remote is configured; skipping push and pull request creation for local run', log_queue)
        finish_phase(connection, phase_id, status='completed', detail='Committed locally; remote not configured')
        update_run(
            connection,
            context.task_id,
            status='complete',
            completed_at=utc_now(),
            active_model='idle',
            commit_sha=commit_sha,
            remote_name='origin',
            remote_url=None,
            push_status='skipped-no-remote',
            pull_request_status='skipped-no-remote',
            finalize_summary='Committed locally; no origin remote was configured.',
            worktree_cleanup_status='available',
        )
        return

    remote_url = remote_check.stdout.strip()
    push_result = execute_process(
        ['git', 'push', '--set-upstream', 'origin', context.branch_name],
        cwd=context.worktree_path,
        phase='finalize',
        task_id=context.task_id,
        connection=connection,
        log_queue=log_queue,
    )
    if push_result.returncode != 0:
        finish_phase(connection, phase_id, status='failed', detail='Push failed')
        raise MinionExecutionError(f'finalize failed with exit code {push_result.returncode}.')

    pr_prompt = (
        'Use the built-in MCP server to open a GitHub pull request for the current branch. '
        f'The task identifier is {context.task_id}.'
    )
    pr_result = execute_process(
        build_copilot_command(pr_prompt, 'gpt-4.1'),
        cwd=context.worktree_path,
        phase='finalize',
        task_id=context.task_id,
        connection=connection,
        log_queue=log_queue,
    )
    if pr_result.returncode != 0:
        finish_phase(connection, phase_id, status='failed', detail='Pull request flow failed')
        raise MinionExecutionError(f'finalize failed with exit code {pr_result.returncode}.')
    finish_phase(connection, phase_id, status='completed', detail='Commit, push, and pull request flow completed')
    update_run(
        connection,
        context.task_id,
        status='complete',
        completed_at=utc_now(),
        active_model='idle',
        commit_sha=commit_sha,
        remote_name='origin',
        remote_url=remote_url,
        push_status='pushed',
        pull_request_status='requested',
        finalize_summary='Commit, push, and pull request flow completed successfully.',
        worktree_cleanup_status='available',
    )


def run_task(
    task_id: str,
    log_queue: QueueLike | None = None,
    *,
    retry_of_task_id: str | None = None,
    retry_sequence: int = 0,
) -> None:
    connection = initialize_database()
    context: TaskContext | None = None
    minion_designation = build_minion_designation(task_id)
    task_type = determine_task_type(task_id)
    update_run(
        connection,
        task_id,
        task_type=task_type,
        minion_designation=minion_designation,
        status='running',
        current_phase='queued',
        active_model='idle',
        reflection_attempts=0,
        last_error=None,
        control_state='idle',
        control_requested_at=None,
        control_completed_at=None,
        retry_of_task_id=retry_of_task_id,
        retry_sequence=retry_sequence,
        finalize_summary=None,
        commit_sha=None,
        remote_name=None,
        remote_url=None,
        push_status=None,
        pull_request_status=None,
        worktree_cleanup_status='pending',
        completed_at=None,
    )
    emit_log(f'[dag] starting task {task_id}', log_queue)

    try:
        context = setup_environment(task_id, connection, log_queue)
        prompt = hydrate_requirements(context, connection, log_queue)
        run_implementation(context, prompt, connection, log_queue)
        ensure_tests_pass(context, connection, log_queue)
        finalize(context, connection, log_queue)
        update_run(connection, task_id, status='complete', completed_at=utc_now(), last_error=None)
        emit_log(f'[dag] task complete: {task_id}', log_queue)
    except ControlRequestedError as error:
        update_run(
            connection,
            task_id,
            status='interrupted',
            current_phase='cancelled',
            last_error=str(error),
            completed_at=utc_now(),
            active_model='idle',
            control_state='cancelled',
            control_completed_at=utc_now(),
            finalize_summary='Run interrupted by dashboard control action before finalization completed.',
        )
        record_event(connection, task_id, 'control', str(error))
        raise
    except Exception as error:
        update_run(
            connection,
            task_id,
            status='failed',
            current_phase='error',
            last_error=str(error),
            completed_at=utc_now(),
            active_model='idle',
        )
        record_event(connection, task_id, 'error', str(error))
        raise
    finally:
        if context is not None:
            auto_cleanup_finished_worktree(context, connection, log_queue)
        connection.close()


def get_system_snapshot(limit: int = 20) -> dict[str, Any]:
    connection = initialize_database()
    try:
        counts = {
            row['status']: row['count']
            for row in connection.execute('SELECT status, COUNT(*) AS count FROM runs GROUP BY status').fetchall()
        }
        runs = [
            serialize_run(row)
            for row in connection.execute(
                'SELECT * FROM runs ORDER BY updated_at DESC LIMIT ?',
                (limit,),
            ).fetchall()
        ]
        phase_totals = [
            serialize_phase(row)
            for row in connection.execute(
                '''
                SELECT task_id, phase, status, detail, model, started_at, completed_at, duration_seconds
                FROM phase_runs
                ORDER BY started_at DESC
                LIMIT ?
                ''',
                (limit * 2,),
            ).fetchall()
        ]
        disallowed_model_usages = connection.execute(
            'SELECT COUNT(*) FROM phase_runs WHERE model IS NOT NULL AND model NOT IN (?, ?)',
            ALLOWED_MODELS,
        ).fetchone()[0]
        return {
            'database': {
                'path': str(DB_PATH),
                'status': 'connected',
                'sqlite_vec_loaded': sqlite_vec is not None,
            },
            'counts': {
                'total_runs': sum(counts.values()),
                'active_runs': counts.get('running', 0) + counts.get('queued', 0),
                'completed_runs': counts.get('complete', 0),
                'failed_runs': counts.get('failed', 0),
                'interrupted_runs': counts.get('interrupted', 0),
            },
            'model_policy': {
                'allowed_models': list(ALLOWED_MODELS),
                'strict_enforced': True,
                'disallowed_model_usages': disallowed_model_usages,
            },
            'runs': runs,
            'recent_phases': phase_totals,
            'generated_at': utc_now(),
        }
    finally:
        connection.close()


def get_run_detail(task_id: str) -> dict[str, Any] | None:
    connection = initialize_database()
    try:
        run = connection.execute('SELECT * FROM runs WHERE task_id = ?', (task_id,)).fetchone()
        if run is None:
            return None
        phases = [
            serialize_phase(row)
            for row in connection.execute(
                'SELECT * FROM phase_runs WHERE task_id = ? ORDER BY id ASC',
                (task_id,),
            ).fetchall()
        ]
        events = [
            serialize_row(row)
            for row in connection.execute(
                'SELECT * FROM events WHERE task_id = ? ORDER BY id DESC LIMIT 80',
                (task_id,),
            ).fetchall()
        ]
        reflections = [
            serialize_row(row)
            for row in connection.execute(
                'SELECT * FROM reflections WHERE task_id = ? ORDER BY attempt ASC',
                (task_id,),
            ).fetchall()
        ]
        return {
            'run': serialize_run(run),
            'phases': phases,
            'events': events,
            'reflections': reflections,
        }
    finally:
        connection.close()


def list_runs(limit: int = 100) -> list[dict[str, Any]]:
    connection = initialize_database()
    try:
        rows = connection.execute('SELECT * FROM runs ORDER BY updated_at DESC LIMIT ?', (limit,)).fetchall()
        return [serialize_run(row) for row in rows]
    finally:
        connection.close()


def mark_stale_runs_interrupted() -> int:
    connection = initialize_database()
    try:
        now = utc_now()
        cursor = connection.execute(
            '''
            UPDATE runs
            SET status = 'interrupted',
                current_phase = 'offline',
                last_error = COALESCE(last_error, 'Dashboard restarted before active task state could be reconciled.'),
                control_state = CASE WHEN control_state = 'cancel-requested' THEN 'cancelled' ELSE control_state END,
                control_completed_at = CASE WHEN control_state = 'cancel-requested' THEN COALESCE(control_completed_at, ?) ELSE control_completed_at END,
                completed_at = COALESCE(completed_at, ?),
                updated_at = ?
            WHERE status IN ('queued', 'running')
            ''',
            (now, now, now),
        )
        connection.commit()
        return int(cursor.rowcount)
    finally:
        connection.close()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Run the < MinionLoom > DAG orchestrator.')
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