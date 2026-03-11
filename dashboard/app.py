from __future__ import annotations

import re
import sys
import threading
import time
from pathlib import Path
from queue import Empty, Queue
from typing import Any

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.dag import (
    MinionExecutionError,
    cleanup_task_worktree,
    get_run_detail,
    get_next_retry_identifier,
    get_system_snapshot,
    list_runs,
    mark_stale_runs_interrupted,
    request_task_cancel,
    run_task,
)

app = Flask(__name__)
LOG_QUEUE: Queue[str] = Queue()
ACTIVE_TASKS: dict[str, threading.Thread] = {}
TASK_ID_PATTERN = re.compile(r'^[A-Za-z0-9_-]+$')
DASHBOARD_STARTED_AT = time.time()
RECOVERED_RUN_COUNT = 0
APP_STATE_INITIALIZED = False
APP_STATE_LOCK = threading.Lock()


class PrefixedQueue:
    def __init__(self, base_queue: Queue[str], task_id: str) -> None:
        self._base_queue = base_queue
        self._task_id = task_id

    def put(self, item: str) -> None:
        self._base_queue.put(f'[task:{self._task_id}] {item.rstrip()}')


def enqueue_log(message: str) -> None:
    LOG_QUEUE.put(message.rstrip())


def generate_logs() -> Any:
    while True:
        try:
            line = LOG_QUEUE.get(timeout=1.0)
            yield f'data: {line}\n\n'
        except Empty:
            yield ': keepalive\n\n'


def dashboard_metadata() -> dict[str, Any]:
    return {
        'uptime_seconds': round(time.time() - DASHBOARD_STARTED_AT, 3),
        'recovered_runs': RECOVERED_RUN_COUNT,
        'active_threads': [task_id for task_id, thread in ACTIVE_TASKS.items() if thread.is_alive()],
    }


def ensure_dashboard_ready() -> None:
    global APP_STATE_INITIALIZED, RECOVERED_RUN_COUNT

    if APP_STATE_INITIALIZED:
        return

    with APP_STATE_LOCK:
        if APP_STATE_INITIALIZED:
            return
        RECOVERED_RUN_COUNT = mark_stale_runs_interrupted()
        APP_STATE_INITIALIZED = True


def run_task_in_background(task_id: str, retry_of_task_id: str | None = None, retry_sequence: int = 0) -> None:
    ensure_dashboard_ready()
    prefixed_queue = PrefixedQueue(LOG_QUEUE, task_id)
    try:
        enqueue_log(f'[dashboard] starting task {task_id}')
        run_task(
            task_id=task_id,
            log_queue=prefixed_queue,
            retry_of_task_id=retry_of_task_id,
            retry_sequence=retry_sequence,
        )
        enqueue_log(f'[dashboard] task {task_id} completed successfully')
    except MinionExecutionError as error:
        enqueue_log(f'[dashboard] task {task_id} failed: {error}')
    except Exception as error:
        enqueue_log(f'[dashboard] unexpected failure for {task_id}: {error}')
    finally:
        ACTIVE_TASKS.pop(task_id, None)


def start_background_task(task_id: str, retry_of_task_id: str | None = None, retry_sequence: int = 0) -> threading.Thread:
    worker = threading.Thread(
        target=run_task_in_background,
        args=(task_id, retry_of_task_id, retry_sequence),
        name=f'minion-{task_id}',
        daemon=True,
    )
    ACTIVE_TASKS[task_id] = worker
    worker.start()
    return worker


@app.get('/')
def index() -> str:
    return render_template('index.html')


@app.get('/stream')
def stream() -> Response:
    return Response(stream_with_context(generate_logs()), mimetype='text/event-stream')


@app.get('/api/overview')
def overview() -> Response:
    ensure_dashboard_ready()
    limit = request.args.get('limit', default=20, type=int)
    payload = get_system_snapshot(limit=max(1, min(limit, 100)))
    payload['dashboard'] = dashboard_metadata()
    return jsonify(payload)


@app.get('/api/tasks')
def tasks() -> Response:
    ensure_dashboard_ready()
    limit = request.args.get('limit', default=100, type=int)
    return jsonify({'runs': list_runs(limit=max(1, min(limit, 250)))})


@app.get('/api/task/<task_id>')
def task_detail(task_id: str) -> Response:
    ensure_dashboard_ready()
    payload = get_run_detail(task_id)
    if payload is None:
        return jsonify({'error': 'Task not found.'}), 404
    return jsonify(payload)


@app.post('/start_task/<task_id>')
def start_task(task_id: str) -> Response:
    ensure_dashboard_ready()
    if not TASK_ID_PATTERN.fullmatch(task_id):
        return jsonify({'error': 'Task identifiers may only contain letters, numbers, underscores, and hyphens.'}), 400

    existing_payload = get_run_detail(task_id)
    if existing_payload is not None:
        return jsonify({'error': 'Task identifier already exists. Use retry for an existing run.'}), 409

    existing = ACTIVE_TASKS.get(task_id)
    if existing and existing.is_alive():
        return jsonify({'status': 'already-running', 'task_id': task_id}), 202

    start_background_task(task_id)
    return jsonify({'status': 'started', 'task_id': task_id}), 202


@app.post('/api/task/<task_id>/cancel')
def cancel_task(task_id: str) -> Response:
    ensure_dashboard_ready()
    try:
        payload = request_task_cancel(task_id)
    except MinionExecutionError as error:
        return jsonify({'error': str(error)}), 400
    if payload.get('status') in {'complete', 'failed', 'interrupted'}:
        message = f'Task {task_id} is already terminal; no cancellation was needed.'
        status = 'already-terminal'
    else:
        message = f'Cancel requested for {task_id}.'
        status = 'cancel-requested'
    enqueue_log(f'[dashboard] {message}')
    return jsonify({'status': status, 'task_id': task_id, 'run': payload, 'message': message})


@app.post('/api/task/<task_id>/retry')
def retry_task(task_id: str) -> Response:
    ensure_dashboard_ready()
    try:
        retry_task_id, retry_sequence = get_next_retry_identifier(task_id)
    except MinionExecutionError as error:
        return jsonify({'error': str(error)}), 400

    start_background_task(retry_task_id, retry_of_task_id=task_id, retry_sequence=retry_sequence)
    enqueue_log(f'[dashboard] retry requested for {task_id} as {retry_task_id}')
    return jsonify(
        {
            'status': 'started',
            'task_id': retry_task_id,
            'source_task_id': task_id,
            'retry_sequence': retry_sequence,
            'message': f'Retry {retry_sequence} for {task_id} started as {retry_task_id}.',
        }
    ), 202


@app.post('/api/task/<task_id>/cleanup')
def cleanup_task(task_id: str) -> Response:
    ensure_dashboard_ready()
    try:
        payload = cleanup_task_worktree(task_id)
    except MinionExecutionError as error:
        return jsonify({'error': str(error)}), 400
    enqueue_log(f'[dashboard] cleanup completed for {task_id}')
    return jsonify({'status': 'cleanup-complete', 'task_id': task_id, 'run': payload, 'message': f'Cleanup completed for {task_id}.'})


@app.get('/health')
def health() -> Response:
    ensure_dashboard_ready()
    snapshot = get_system_snapshot(limit=5)
    return jsonify(
        {
            'status': 'ok',
            'database': snapshot['database'],
            'counts': snapshot['counts'],
            'active_tasks': dashboard_metadata()['active_threads'],
            'uptime_seconds': dashboard_metadata()['uptime_seconds'],
        }
    )


if __name__ == '__main__':
    ensure_dashboard_ready()
    app.run(host='127.0.0.1', port=5000, debug=False, threaded=True)