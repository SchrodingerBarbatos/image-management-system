import json, logging, threading, traceback, datetime
from models import session, BatchTask

_log = logging.getLogger(__name__)

_task_lock = threading.Lock()
_thread_local = threading.local()

TASK_HANDLERS = {}

VALID_TASK_TYPES = {'duplicate_scan', 'low_version_scan'}


def _get_thread_session():
    """Return the thread-local scoped_session if one was installed by
    _start_task_thread, otherwise fall back to the module-level session.

    This lets background threads operate on their own session without
    mutating module globals, while request-thread code keeps using the
    unmodified module-level scoped_session.
    """
    return getattr(_thread_local, 'session', None) or session


def register_handler(task_type, handler):
    """Register a task handler function. Called at import time."""
    TASK_HANDLERS[task_type] = handler


def create_task(task_type, params=None, auto_start=True):
    """Create a task. If same type has no running task, start immediately;
    otherwise queue it. Deduplicates by params_json if a queued/running task
    of the same type already exists with identical params.

    Returns (task_dict, is_new) where is_new=False means an existing task was returned.
    """
    if task_type not in VALID_TASK_TYPES:
        raise ValueError(f"Invalid task_type: {task_type}")
    params = params or {}
    params_json = json.dumps(params, sort_keys=True, ensure_ascii=False)

    with _task_lock:
        # Dedup: same type + same params already queued/running?
        existing = session.query(BatchTask).filter(
            BatchTask.task_type == task_type,
            BatchTask.status.in_(['queued', 'running']),
            BatchTask.params_json == params_json,
        ).first()
        if existing:
            _log.info("Dedup: returning existing %s task %d", task_type, existing.id)
            return _task_to_dict(existing), False

        task = BatchTask(
            task_type=task_type,
            status='queued',
            params_json=params_json,
        )
        session.add(task)
        session.commit()

        # Auto-start if no running task of same type
        if auto_start:
            running = session.query(BatchTask).filter(
                BatchTask.task_type == task_type,
                BatchTask.status == 'running',
            ).first()
            if not running:
                task.status = 'running'
                task.started_at = datetime.datetime.now().isoformat()
                session.commit()
                _start_task_thread(task)

        return _task_to_dict(task), True


def _start_task_thread(task):
    """Launch a background thread to execute the task handler."""
    handler = TASK_HANDLERS.get(task.task_type)
    if not handler:
        _log.error("No handler for task_type %s", task.task_type)
        return

    # Extract primitives before spawning thread — the ORM task object
    # belongs to a session that must not be shared across threads.
    task_id = task.id
    task_type = task.task_type

    # Grab the engine bind so the thread can create its own scoped_session.
    bind = session.get_bind()

    def _run():
        from sqlalchemy.orm import scoped_session as _ss, sessionmaker as _sm

        # Thread-dedicated scoped_session — installed via thread-local so
        # handlers, update_task_progress, finish_task, and _on_task_done
        # all see it without mutating any module-level globals.
        thread_sess = _ss(_sm(bind=bind))
        _thread_local.session = thread_sess
        try:
            handler(task_id)
        except Exception as e:
            _log.exception("Handler for task %d failed", task_id)
            try:
                t = thread_sess.get(BatchTask, task_id)
                if t and t.status == 'running':
                    t.status = 'error'
                    t.error_message = f"{e}\n{traceback.format_exc()}"
                    t.finished_at = datetime.datetime.now().isoformat()
                    thread_sess.commit()
            except Exception:
                thread_sess.rollback()
        finally:
            thread_sess.remove()
            del _thread_local.session
            try:
                _on_task_done(task_type)
            except Exception:
                _log.exception("_on_task_done failed for task_type %s", task_type)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()


def _on_task_done(task_type):
    """After a task finishes, start the next queued task of same type if any."""
    sess = _get_thread_session()
    with _task_lock:
        next_task = sess.query(BatchTask).filter(
            BatchTask.task_type == task_type,
            BatchTask.status == 'queued',
        ).order_by(BatchTask.created_at).first()
        if next_task:
            next_task.status = 'running'
            next_task.started_at = datetime.datetime.now().isoformat()
            sess.commit()
            _start_task_thread(next_task)


def cancel_task(task_id):
    """Cancel a queued task. Running tasks cannot be cancelled."""
    with _task_lock:
        task = session.get(BatchTask, task_id)
        if not task:
            return None
        if task.status != 'queued':
            return {'error': '只有排队中的任务可以取消', 'status': task.status}
        task.status = 'cancelled'
        task.finished_at = datetime.datetime.now().isoformat()
        session.commit()
        return _task_to_dict(task)


def get_task(task_id):
    """Get a single task by ID as dict."""
    task = session.get(BatchTask, task_id)
    if not task:
        return None
    return _task_to_dict(task)


def get_tasks(task_type=None, status=None):
    """List tasks, optionally filtered by type and/or status."""
    q = session.query(BatchTask)
    if task_type:
        q = q.filter(BatchTask.task_type == task_type)
    if status:
        q = q.filter(BatchTask.status == status)
    q = q.order_by(BatchTask.created_at.desc())
    return [_task_to_dict(t) for t in q.all()]


def get_running_task(task_type):
    """Get the currently running task for a given type, or None."""
    task = session.query(BatchTask).filter(
        BatchTask.task_type == task_type,
        BatchTask.status == 'running',
    ).first()
    return _task_to_dict(task) if task else None


def delete_task(task_id):
    """Delete a task record and its results. Only queued/done/error/cancelled/interrupted tasks."""
    with _task_lock:
        task = session.get(BatchTask, task_id)
        if not task:
            return None
        if task.status == 'running':
            return {'error': '运行中的任务不能删除'}
        from models import DuplicateScanResult, LowVersionScanResult
        if task.task_type == 'duplicate_scan':
            session.query(DuplicateScanResult).filter(DuplicateScanResult.task_id == task_id).delete()
        elif task.task_type == 'low_version_scan':
            session.query(LowVersionScanResult).filter(LowVersionScanResult.task_id == task_id).delete()
        session.delete(task)
        session.commit()
        return {'ok': True}


def update_task_progress(task_id, progress=None, total=None, result_count=None):
    """Update task progress fields. Called by handler."""
    sess = _get_thread_session()
    with _task_lock:
        task = sess.get(BatchTask, task_id)
        if not task:
            return
        if progress is not None:
            task.progress = progress
        if total is not None:
            task.total = total
        if result_count is not None:
            task.result_count = result_count
        sess.commit()


def finish_task(task_id, result_count=0, error_message=''):
    """Mark a task as done or error. Called by handler in finally block."""
    sess = _get_thread_session()
    with _task_lock:
        task = sess.get(BatchTask, task_id)
        if not task:
            return
        if error_message:
            task.status = 'error'
            task.error_message = error_message
        else:
            task.status = 'done'
            task.result_count = result_count
        task.finished_at = datetime.datetime.now().isoformat()
        sess.commit()


def _task_to_dict(task):
    return {
        'id': task.id,
        'task_type': task.task_type,
        'status': task.status,
        'progress': task.progress,
        'total': task.total,
        'result_count': task.result_count,
        'error_message': task.error_message,
        'params_json': task.params_json,
        'created_at': task.created_at,
        'started_at': task.started_at,
        'finished_at': task.finished_at,
    }
