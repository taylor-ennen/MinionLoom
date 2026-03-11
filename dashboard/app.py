from __future__ import annotations

import re
import sys
import threading
from pathlib import Path
from queue import Empty, Queue
from typing import Any

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.dag import MinionExecutionError, run_task

app = Flask(__name__)
LOG_QUEUE: Queue[str] = Queue()
ACTIVE_TASKS: dict[str, threading.Thread] = {}
TASK_ID_PATTERN = re.compile(r'^[A-Za-z0-9_-]+$')


def enqueue_log(message: str) -> None:
    LOG_QUEUE.put(message.rstrip())


def generate_logs() -> Any:
    while True:
        try:
            line = LOG_QUEUE.get(timeout=1.0)
            yield f"data: {line}\n\n"
        except Empty:
            yield ': keepalive\n\n'


def run_task_in_background(task_id: str) -> None:
    try:
        enqueue_log(f"[dashboard] starting task {task_id}")
        run_task(task_id=task_id, log_queue=LOG_QUEUE)
        enqueue_log(f"[dashboard] task {task_id} completed successfully")
    except MinionExecutionError as error:
        enqueue_log(f"[dashboard] task {task_id} failed: {error}")
    except Exception as error:
        enqueue_log(f"[dashboard] unexpected failure for {task_id}: {error}")
    finally:
        ACTIVE_TASKS.pop(task_id, None)


@app.get('/')
def index() -> str:
    return render_template('index.html')


@app.get('/stream')
def stream() -> Response:
    return Response(stream_with_context(generate_logs()), mimetype='text/event-stream')


@app.post('/start_task/<task_id>')
def start_task(task_id: str) -> Response:
    if not TASK_ID_PATTERN.fullmatch(task_id):
        return jsonify({'error': 'Task identifiers may only contain letters, numbers, underscores, and hyphens.'}), 400

    existing = ACTIVE_TASKS.get(task_id)
    if existing and existing.is_alive():
        return jsonify({'status': 'already-running', 'task_id': task_id}), 202

    worker = threading.Thread(target=run_task_in_background, args=(task_id,), name=f'minion-{task_id}', daemon=True)
    ACTIVE_TASKS[task_id] = worker
    worker.start()
    return jsonify({'status': 'started', 'task_id': task_id}), 202


@app.get('/health')
def health() -> Response:
    return jsonify({'status': 'ok', 'active_tasks': [task_id for task_id, thread in ACTIVE_TASKS.items() if thread.is_alive()]})


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=False, threaded=True)