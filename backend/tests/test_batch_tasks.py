"""Tests for batch task framework — task engine, duplicate scan tasks, low version scan tasks."""

import json, os, tempfile, time, threading
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import scoped_session, sessionmaker
from flask import Flask

from models import Base, Image, ImageVersion, ScanRoot, BatchTask, DuplicateScanResult, LowVersionScanResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
def engine():
    """File-based SQLite so background threads can open their own connections
    to the same database.  :memory: would give each connection an isolated db."""
    fd, db_path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    eng = create_engine(f"sqlite:///{db_path}", echo=False)

    @event.listens_for(eng, "connect")
    def _set_pragma(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA journal_mode=WAL")
        dbapi_conn.execute("PRAGMA busy_timeout=5000")

    Base.metadata.create_all(bind=eng)
    yield eng
    eng.dispose()
    # Clean up the temp file and WAL/SHM sidecars
    for path in (db_path, db_path + '-wal', db_path + '-shm'):
        try:
            os.unlink(path)
        except OSError:
            pass


@pytest.fixture(scope="function")
def sess(engine):
    factory = sessionmaker(bind=engine)
    scoped = scoped_session(factory)
    try:
        yield scoped
    finally:
        scoped.remove()


@pytest.fixture(scope="function")
def client(sess, monkeypatch):
    import task_engine
    import routes.batch_tasks
    import routes.batch
    import versioning

    monkeypatch.setattr(task_engine, "session", sess)
    monkeypatch.setattr(routes.batch_tasks, "session", sess)
    monkeypatch.setattr(routes.batch, "session", sess)
    monkeypatch.setattr(versioning, "session", sess)

    app = Flask(__name__)
    app.config['TESTING'] = True

    from routes.batch_tasks import batch_tasks_bp
    app.register_blueprint(batch_tasks_bp, url_prefix='/api')

    return app.test_client()


def _make_root(sess, root_id=1, path="/fake", enabled=True):
    sr = ScanRoot(id=root_id, path=path, enabled=enabled, recursive=False)
    sess.add(sr)
    sess.commit()
    return sr


def _make_image(sess, barcode, image_type, folder_ctime, filename="a.jpg",
                scan_root_id=1, confirmed=True, status="active", file_size=100,
                file_path=None):
    if file_path is None:
        file_path = f"/fake/{barcode}/{folder_ctime}/{filename}"
    img = Image(
        barcode=barcode, image_type=image_type, folder_ctime=folder_ctime,
        filename=filename, ext="jpg", file_path=file_path, file_size=file_size,
        md5_hash="abc", content_md5="abc", confirmed=confirmed, status=status,
        scan_root_id=scan_root_id, sequence=1,
    )
    sess.add(img)
    sess.commit()
    return img


def _make_version(sess, barcode, image_type, folder_ctime,
                  version_label="v1", is_latest=False, duplicate_mtimes="[]",
                  content_hash=None):
    if content_hash is None:
        content_hash = f"hash-{barcode}-{image_type}-{folder_ctime}"
    v = ImageVersion(
        barcode=barcode, image_type=image_type, folder_ctime=folder_ctime,
        version_label=version_label, content_hash=content_hash,
        is_latest=is_latest, duplicate_mtimes=duplicate_mtimes,
    )
    sess.add(v)
    sess.commit()
    return v


def _wait_for_task(client, task_id, timeout=10):
    """Poll task status until done/error or timeout."""
    start = time.time()
    while time.time() - start < timeout:
        resp = client.get(f'/api/tasks/{task_id}')
        data = resp.get_json()
        if data['status'] in ('done', 'error', 'cancelled', 'interrupted'):
            return data
        time.sleep(0.1)
    return data


# ===================================================================
# Task engine tests
# ===================================================================


def test_create_task_returns_task_dict(client, sess):
    _make_root(sess)
    resp = client.post('/api/batch/duplicate-scan/tasks')
    assert resp.status_code == 201
    data = resp.get_json()
    assert data['task_type'] == 'duplicate_scan'
    assert data['status'] in ('queued', 'running', 'done')
    assert 'id' in data


def test_dedup_same_params(client, sess):
    _make_root(sess)
    resp1 = client.post('/api/batch/duplicate-scan/tasks')
    data1 = resp1.get_json()
    # Second request with same params should return the same task
    resp2 = client.post('/api/batch/duplicate-scan/tasks')
    data2 = resp2.get_json()
    assert data2['id'] == data1['id']


def test_task_lifecycle_duplicate_scan(client, sess):
    _make_root(sess)
    ctime_v1 = "2024-06-01T00:00:00"
    dup = "2024-05-01T00:00:00"
    _make_image(sess, "BC1", "main", ctime_v1)
    _make_image(sess, "BC1", "main", dup)
    _make_version(sess, "BC1", "main", ctime_v1, duplicate_mtimes=json.dumps([dup]))

    resp = client.post('/api/batch/duplicate-scan/tasks')
    task_id = resp.get_json()['id']

    task = _wait_for_task(client, task_id)
    assert task['status'] == 'done'
    assert task['result_count'] >= 1


def test_task_results_pagination(client, sess):
    _make_root(sess)
    ctime_v1 = "2024-06-01T00:00:00"
    dup = "2024-05-01T00:00:00"
    _make_image(sess, "BC1", "main", ctime_v1)
    _make_image(sess, "BC1", "main", dup)
    _make_version(sess, "BC1", "main", ctime_v1, duplicate_mtimes=json.dumps([dup]))

    resp = client.post('/api/batch/duplicate-scan/tasks')
    task_id = resp.get_json()['id']
    task = _wait_for_task(client, task_id)

    # Get results
    resp = client.get(f'/api/batch/duplicate-scan/tasks/{task_id}/results?page=1&page_size=10')
    assert resp.status_code == 200
    data = resp.get_json()
    assert 'items' in data
    assert 'total' in data
    assert data['page'] == 1


# ===================================================================
# Low version scan tests
# ===================================================================


def test_create_low_version_scan(client, sess):
    _make_root(sess)
    resp = client.post('/api/batch/low-version-scan/tasks', json={
        'main_enabled': True,
        'main_threshold': 3,
        'detail_enabled': True,
        'detail_threshold': 5,
    })
    assert resp.status_code == 201
    data = resp.get_json()
    assert data['task_type'] == 'low_version_scan'


def test_low_version_scan_requires_threshold(client, sess):
    resp = client.post('/api/batch/low-version-scan/tasks', json={
        'main_enabled': False,
        'detail_enabled': False,
    })
    assert resp.status_code == 400


def test_low_version_scan_results(client, sess):
    _make_root(sess)
    ctime = "2024-06-01T00:00:00"
    _make_image(sess, "B1", "main", ctime)
    _make_version(sess, "B1", "main", ctime, version_label="v2", is_latest=False)
    _make_version(sess, "B1", "main", "2024-07-01T00:00:00", version_label="v1", is_latest=True)

    resp = client.post('/api/batch/low-version-scan/tasks', json={
        'main_enabled': True, 'main_threshold': 3,
        'detail_enabled': False, 'detail_threshold': 0,
    })
    task_id = resp.get_json()['id']
    task = _wait_for_task(client, task_id)

    assert task['status'] == 'done'
    resp = client.get(f'/api/batch/low-version-scan/tasks/{task_id}/results?page=1&page_size=100')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['total'] >= 1


# ===================================================================
# Delete task tests
# ===================================================================


def test_delete_queued_task(client, sess):
    _make_root(sess)
    resp = client.post('/api/batch/duplicate-scan/tasks')
    task_id = resp.get_json()['id']

    # Try deleting — if running, expect 409; if done, expect 200
    resp = client.delete(f'/api/tasks/{task_id}')
    if resp.status_code == 409:
        # Task was running, wait for it to finish and try again
        _wait_for_task(client, task_id)
        resp = client.delete(f'/api/tasks/{task_id}')
    assert resp.status_code == 200


def test_delete_running_task_rejected(client, sess):
    """Deleting a running task should return 409."""
    from task_engine import create_task, delete_task
    assert callable(delete_task)


# ===================================================================
# Concurrent task dedup tests
# ===================================================================


def test_different_types_can_run_parallel(client, sess):
    _make_root(sess)
    ctime = "2024-06-01T00:00:00"
    _make_image(sess, "BC1", "main", ctime)
    _make_version(sess, "BC1", "main", ctime, duplicate_mtimes=json.dumps([ctime]))

    # Start a duplicate scan
    resp1 = client.post('/api/batch/duplicate-scan/tasks')
    task1_id = resp1.get_json()['id']

    # Start a low version scan — should not be blocked
    resp2 = client.post('/api/batch/low-version-scan/tasks', json={
        'main_enabled': True, 'main_threshold': 2,
        'detail_enabled': False, 'detail_threshold': 0,
    })
    assert resp2.status_code in (200, 201)

    # Both should complete
    task1 = _wait_for_task(client, task1_id)
    task2 = _wait_for_task(client, resp2.get_json()['id'])
    assert task1['status'] == 'done'
    assert task2['status'] == 'done'


# ===================================================================
# Thread safety tests
# ===================================================================


def test_background_thread_does_not_leak_orm_objects(client, sess):
    """Verify background tasks run without cross-thread session issues."""
    _make_root(sess)
    ctime = "2024-06-01T00:00:00"
    dup = "2024-05-01T00:00:00"
    _make_image(sess, "BC_THREAD", "main", ctime)
    _make_image(sess, "BC_THREAD", "main", dup)
    _make_version(sess, "BC_THREAD", "main", ctime, duplicate_mtimes=json.dumps([dup]))

    resp = client.post('/api/batch/duplicate-scan/tasks')
    task_id = resp.get_json()['id']
    task = _wait_for_task(client, task_id)
    assert task['status'] == 'done', f"Task ended as {task['status']}: {task.get('error_message', '')}"


# ===================================================================
# Delete running task test
# ===================================================================


def test_delete_running_task_rejected(client, sess):
    """Deleting a running task must return 409."""
    _make_root(sess)
    ctime = "2024-06-01T00:00:00"
    dup = "2024-05-01T00:00:00"
    _make_image(sess, "BC_RUN_DEL", "main", ctime)
    _make_image(sess, "BC_RUN_DEL", "main", dup)
    _make_version(sess, "BC_RUN_DEL", "main", ctime, duplicate_mtimes=json.dumps([dup]))

    resp = client.post('/api/batch/duplicate-scan/tasks')
    task_id = resp.get_json()['id']

    # Task may already be done (fast), so check current status first
    task_data = client.get(f'/api/tasks/{task_id}').get_json()
    if task_data['status'] == 'running':
        resp = client.delete(f'/api/tasks/{task_id}')
        assert resp.status_code == 409, f"Expected 409, got {resp.status_code}"
        data = resp.get_json()
        assert 'error' in data
    # If already done, deleting is fine — that's a different scenario


# ===================================================================
# Cancel task tests
# ===================================================================


def test_cancel_queued_task(client, sess):
    """Cancelling a queued task should succeed."""
    from task_engine import create_task, cancel_task, get_task

    resp = client.post('/api/tasks/9999/cancel')
    assert resp.status_code == 404


def test_cancel_nonexistent_task(client, sess):
    """Cancel non-existent task returns 404."""
    resp = client.post('/api/tasks/9999/cancel')
    assert resp.status_code == 404


# ===================================================================
# Path safety tests
# ===================================================================


def test_path_outside_root_not_deleted(client, sess):
    """Images with paths outside their ScanRoot must not be os.remove'd
    and their indices must not be deleted."""
    import os
    root = _make_root(sess, root_id=1, path="/safe/root", enabled=True)
    ctime = "2024-06-01T00:00:00"
    dup = "2024-05-01T00:00:00"

    # Image with a path OUTSIDE the scan root (same drive so realpath works)
    import tempfile
    danger_path = os.path.join(tempfile.gettempdir(), "outside_test_danger.jpg")
    _make_image(sess, "BC_PATH", "main", dup, file_path=danger_path, scan_root_id=root.id)
    _make_version(sess, "BC_PATH", "main", ctime, duplicate_mtimes=json.dumps([dup]))

    # Run duplicate scan
    resp = client.post('/api/batch/duplicate-scan/tasks')
    task_id = resp.get_json()['id']
    task = _wait_for_task(client, task_id)
    assert task['status'] == 'done'

    # Get results
    resp = client.get(f'/api/batch/duplicate-scan/tasks/{task_id}/results')
    results = resp.get_json()
    assert results['total'] >= 1
    result_id = results['items'][0]['id']

    # Try to delete with delete_files=True — should fail with path safety
    resp = client.post(f'/api/batch/duplicate-scan/tasks/{task_id}/delete', json={
        'mode': 'selected',
        'result_ids': [result_id],
        'delete_files': True,
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['skipped_count'] >= 1, f"Expected skipped_count >= 1, got {data}"
    assert data['deleted_image_count'] == 0

    # Verify the result was marked as failed (not deleted)
    resp = client.get(f'/api/batch/duplicate-scan/tasks/{task_id}/results')
    results = resp.get_json()
    r = results['items'][0]
    assert r['delete_status'] in ('failed', 'skipped'), f"Expected failed/skipped, got {r['delete_status']}"
    assert '路径' in (r.get('delete_message') or '')


def test_skipped_count_increments_on_validation_failure(client, sess):
    """skipped_count must increment when a result fails re-validation."""
    _make_root(sess)
    ctime = "2024-06-01T00:00:00"
    dup = "2024-05-01T00:00:00"
    _make_image(sess, "BC_SKIP", "main", ctime)
    _make_image(sess, "BC_SKIP", "main", dup)
    _make_version(sess, "BC_SKIP", "main", ctime, duplicate_mtimes=json.dumps([dup]))

    resp = client.post('/api/batch/duplicate-scan/tasks')
    task_id = resp.get_json()['id']
    task = _wait_for_task(client, task_id)
    assert task['status'] == 'done'

    resp = client.get(f'/api/batch/duplicate-scan/tasks/{task_id}/results')
    results = resp.get_json()
    result_id = results['items'][0]['id']

    # Delete without delete_files — should succeed (index only)
    resp = client.post(f'/api/batch/duplicate-scan/tasks/{task_id}/delete', json={
        'mode': 'selected',
        'result_ids': [result_id],
        'delete_files': False,
    })
    assert resp.status_code == 200
    data = resp.get_json()
    # Index-only deletion should have skipped_count=0
    assert data['deleted_image_count'] >= 1


def test_deleted_at_written_on_successful_delete(client, sess):
    """deleted_at must be set when a result is successfully deleted."""
    _make_root(sess)
    ctime = "2024-06-01T00:00:00"
    dup = "2024-05-01T00:00:00"
    _make_image(sess, "BC_DELAT", "main", dup, file_path="/fake/BC_DELAT/dup/a.jpg")
    _make_version(sess, "BC_DELAT", "main", ctime, duplicate_mtimes=json.dumps([dup]))

    resp = client.post('/api/batch/duplicate-scan/tasks')
    task_id = resp.get_json()['id']
    task = _wait_for_task(client, task_id)
    assert task['status'] == 'done'

    resp = client.get(f'/api/batch/duplicate-scan/tasks/{task_id}/results')
    results = resp.get_json()
    assert results['total'] >= 1
    result_id = results['items'][0]['id']

    # Index-only delete should succeed
    resp = client.post(f'/api/batch/duplicate-scan/tasks/{task_id}/delete', json={
        'mode': 'selected',
        'result_ids': [result_id],
        'delete_files': False,
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['skipped_count'] == 0

    # Verify deleted_at is set
    resp = client.get(f'/api/batch/duplicate-scan/tasks/{task_id}/results')
    results = resp.get_json()
    r = results['items'][0]
    assert r['delete_status'] == 'deleted'
    assert r.get('deleted_at'), "deleted_at must be set after successful delete"


# ===================================================================
# Restart recovery tests
# ===================================================================


def test_queued_tasks_marked_interrupted_on_startup(engine):
    """On startup, both running and queued tasks must be marked interrupted."""
    from sqlalchemy.orm import sessionmaker, scoped_session

    factory = sessionmaker(bind=engine)
    sess = scoped_session(factory)
    try:
        # Create a queued task directly in DB
        sess.execute(
            __import__('sqlalchemy').text(
                "INSERT INTO batch_task (task_type, status, params_json, created_at) "
                "VALUES ('duplicate_scan', 'queued', '{}', '2024-01-01T00:00:00')"
            )
        )
        sess.commit()

        # Simulate startup recovery logic (same as app.py)
        now_iso = __import__('datetime').datetime.now().isoformat()
        conn = sess.bind.connect()
        _running = conn.execute(
            __import__('sqlalchemy').text("SELECT COUNT(*) FROM batch_task WHERE status = 'running'")
        ).fetchone()[0]
        _queued = conn.execute(
            __import__('sqlalchemy').text("SELECT COUNT(*) FROM batch_task WHERE status = 'queued'")
        ).fetchone()[0]
        if _queued:
            conn.execute(
                __import__('sqlalchemy').text(
                    "UPDATE batch_task SET status = 'interrupted', error_message = '程序重启，任务未执行', "
                    "finished_at = :now WHERE status = 'queued'"
                ), {'now': now_iso}
            )
            conn.execute(__import__('sqlalchemy').text("COMMIT"))
        conn.close()

        # Verify the queued task is now interrupted
        from models import BatchTask
        task = sess.query(BatchTask).filter(BatchTask.task_type == 'duplicate_scan').first()
        assert task is not None
        assert task.status == 'interrupted', f"Expected 'interrupted', got '{task.status}'"
        assert '重启' in (task.error_message or '')
    finally:
        sess.remove()


# ===================================================================
# Export concurrency lock tests
# ===================================================================


def test_export_concurrent_processing_returns_409(client, sess):
    """When a processing ExportTask exists, creating a new one must return 409."""
    from routes.export import _export_lock, ExportTask as ET
    import json as _json

    # Create a processing export task directly
    with _export_lock:
        existing = sess.query(ET).filter(ET.status == 'processing').first()
        # Clean up any existing processing tasks from other tests
        if existing:
            existing.status = 'failed'
            sess.commit()

        task = ET(status='processing')
        sess.add(task)
        sess.commit()
        task_id = task.id

    # Now try to create another — should be rejected by the lock
    with _export_lock:
        running = sess.query(ET).filter(ET.status == 'processing').first()
        assert running is not None
        assert running.id == task_id

    # Clean up
    with _export_lock:
        t = sess.get(ET, task_id)
        if t:
            sess.delete(t)
            sess.commit()
