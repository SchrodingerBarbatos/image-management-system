"""审查解决方案对应的回归测试。"""

import os

import pytest
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker

from models import Base, ExportTask, Image, RejectedBarcode, ScanRoot


@pytest.fixture()
def db():
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(bind=engine)
    scoped = scoped_session(sessionmaker(bind=engine))
    try:
        yield scoped
    finally:
        scoped.remove()


@pytest.fixture()
def scan_client(db, monkeypatch):
    import routes.scan as scan_mod

    monkeypatch.setattr(scan_mod, 'session', db)
    monkeypatch.setattr(scan_mod, 'update_all_versions', lambda: None)
    scan_mod._scan_jobs.clear()
    scan_mod._scan_cancel_flags.clear()
    app = Flask(__name__)
    app.register_blueprint(scan_mod.scan_bp, url_prefix='/api')
    app.config['TESTING'] = True
    return app.test_client()


def _root(db, path, *, recursive=False, enabled=True):
    root = ScanRoot(path=str(path), recursive=recursive, enabled=enabled)
    db.add(root)
    db.commit()
    return root


def _image(db, root, *, barcode='4006381333931', status='active', confirmed=True, name='a.jpg'):
    image = Image(
        barcode=barcode,
        image_type='main',
        sequence=1,
        filename=name,
        ext='jpg',
        file_path=os.path.join(root.path, name),
        file_size=100,
        md5_hash='100_123',
        folder_path=root.path,
        folder_ctime='2024-01-01T00:00:00',
        scan_root_id=root.id,
        confirmed=confirmed,
        status=status,
    )
    db.add(image)
    db.commit()
    return image


def test_scan_root_rejects_duplicate_and_recursive_overlap(scan_client, db, tmp_path):
    parent = tmp_path / 'parent'
    child = parent / 'child'
    child.mkdir(parents=True)

    first = scan_client.post('/api/scan-roots', json={
        'path': str(parent), 'recursive': True,
    })
    assert first.status_code == 201

    duplicate = scan_client.post('/api/scan-roots', json={
        'path': str(parent) + os.sep, 'recursive': False,
    })
    assert duplicate.status_code == 409

    nested = scan_client.post('/api/scan-roots', json={
        'path': str(child), 'recursive': False,
    })
    assert nested.status_code == 409


def test_nonrecursive_nested_roots_are_allowed(scan_client, tmp_path):
    parent = tmp_path / 'parent'
    child = parent / 'child'
    child.mkdir(parents=True)

    first = scan_client.post('/api/scan-roots', json={
        'path': str(parent), 'recursive': False,
    })
    second = scan_client.post('/api/scan-roots', json={
        'path': str(child), 'recursive': False,
    })
    assert first.status_code == 201
    assert second.status_code == 201


def test_mixed_scan_root_ids_are_rejected_without_job(scan_client, db, tmp_path, monkeypatch):
    import routes.scan as scan_mod

    root = _root(db, tmp_path)
    monkeypatch.setattr(scan_mod, '_run_scan', lambda *args, **kwargs: None)
    response = scan_client.post('/api/scan', json={'root_ids': [root.id, 999]})
    assert response.status_code == 404
    assert response.get_json()['missing_root_ids'] == [999]
    assert scan_mod._scan_jobs == {}


def test_delete_scan_root_rejects_while_running(scan_client, db, tmp_path):
    import routes.scan as scan_mod

    root = _root(db, tmp_path)
    scan_mod._scan_jobs['running-job'] = scan_mod._make_scan_job([root.id])
    response = scan_client.delete(f'/api/scan-roots/{root.id}')
    assert response.status_code == 409
    assert db.get(ScanRoot, root.id) is not None
    scan_mod._scan_jobs.clear()


def test_delete_scan_root_removes_rejected_metadata_but_keeps_file(scan_client, db, tmp_path):
    root_dir = tmp_path / 'photos'
    root_dir.mkdir()
    root = _root(db, root_dir)
    rejected_file = root_dir / 'bad.jpg'
    rejected_file.write_bytes(b'keep')
    rejected = RejectedBarcode(
        barcode='12345', file_path=str(rejected_file), filename='bad.jpg',
        reason='长度错误', scan_root_id=root.id,
    )
    db.add(rejected)
    db.commit()

    response = scan_client.delete(f'/api/scan-roots/{root.id}')
    assert response.status_code == 200
    assert db.query(RejectedBarcode).count() == 0
    assert rejected_file.exists()


def test_scan_worker_converges_when_audit_log_fails(db, tmp_path, monkeypatch):
    import routes.scan as scan_mod

    root = _root(db, tmp_path)
    monkeypatch.setattr(scan_mod, 'session', db)
    monkeypatch.setattr(scan_mod, '_add_log', lambda *args, **kwargs: (_ for _ in ()).throw(OSError('log unavailable')))
    monkeypatch.setattr(scan_mod, 'count_image_files', lambda *args, **kwargs: 0)
    monkeypatch.setattr(scan_mod, 'scan_root', lambda *args, **kwargs: {
        'added': 0, 'skipped': 0, 'broken_cleaned': 0, 'rejected': 0,
        'affected_barcodes': [],
    })
    job_id = 'audit-failure-job'
    scan_mod._scan_jobs[job_id] = scan_mod._make_scan_job([root.id])
    scan_mod._scan_cancel_flags[job_id] = True
    scan_mod._scan_cancel_flags.pop(job_id)

    scan_mod._run_scan([root.id], 'full', job_id)
    assert scan_mod._scan_jobs[job_id]['status'] == 'done'
    assert job_id not in scan_mod._scan_cancel_flags
    scan_mod._scan_jobs.clear()


def test_broken_file_with_same_fingerprint_is_restored(db, tmp_path, monkeypatch):
    import scanner

    root = _root(db, tmp_path)
    image = _image(db, root, status='broken')
    monkeypatch.setattr(scanner, 'session', db)
    monkeypatch.setattr(scanner, '_walk_nonrecursive', lambda path: [(path, [], [image.filename])])
    monkeypatch.setattr(scanner, 'file_fingerprint', lambda path: '100_123')
    monkeypatch.setattr(scanner, 'thumbnail_exists', lambda image_id: True)
    result = scanner.scan_root(root.id, full_scan=True)
    db.refresh(image)
    assert result['skipped'] == 1
    assert image.status == 'active'
    assert image.barcode in result['affected_barcodes']


@pytest.fixture()
def pending_client(db, monkeypatch):
    import routes.pending as pending_mod

    monkeypatch.setattr(pending_mod, 'session', db)
    monkeypatch.setattr(pending_mod, 'update_all_versions', lambda: None)
    app = Flask(__name__)
    app.register_blueprint(pending_mod.pending_bp, url_prefix='/api')
    return app.test_client()


def test_pending_confirm_is_all_or_nothing_and_ignores_only_pending(pending_client, db, tmp_path):
    root = _root(db, tmp_path)
    pending = _image(db, root, barcode='4006381333931', confirmed=False, name='pending.jpg')
    confirmed = _image(db, root, barcode='4006381333932', confirmed=True, name='confirmed.jpg')

    response = pending_client.post('/api/pending/confirm', json=[
        {'id': pending.id, 'image_type': 'main'},
        {'id': confirmed.id, 'image_type': 'detail'},
    ])
    assert response.status_code == 409
    db.refresh(pending)
    assert pending.confirmed is False

    ignored = pending_client.delete(f'/api/pending/{confirmed.id}')
    assert ignored.status_code == 409
    assert db.get(Image, confirmed.id) is not None


def test_pending_rejects_non_json_body(pending_client):
    response = pending_client.post('/api/pending/confirm', data='[]')
    assert response.status_code == 415


@pytest.fixture()
def export_client(db, monkeypatch):
    import routes.export as export_mod
    import routes.images as images_mod

    monkeypatch.setattr(export_mod, 'session', db)
    monkeypatch.setattr(images_mod, 'session', db)

    class NoopThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            return None

    monkeypatch.setattr(images_mod.threading, 'Thread', NoopThread)
    app = Flask(__name__)
    app.register_blueprint(images_mod.images_bp, url_prefix='/api')
    app.register_blueprint(export_mod.export_bp, url_prefix='/api')
    return app.test_client()


def test_batch_export_excludes_non_exportable_images(export_client, db, tmp_path):
    enabled = _root(db, tmp_path / 'enabled')
    disabled = _root(db, tmp_path / 'disabled', enabled=False)
    os.makedirs(enabled.path, exist_ok=True)
    os.makedirs(disabled.path, exist_ok=True)
    active = _image(db, enabled, name='active.jpg')
    broken = _image(db, enabled, status='broken', name='broken.jpg')
    unconfirmed = _image(db, enabled, confirmed=False, name='pending.jpg')
    disabled_image = _image(db, disabled, name='disabled.jpg')

    response = export_client.post('/api/images/batch-export', json={
        'ids': [active.id, broken.id, unconfirmed.id, disabled_image.id],
        'image_type': 'main',
    })
    assert response.status_code == 200
    body = response.get_json()
    assert body['total'] == 1
    assert body['scanroot_excluded'] == 3


def test_batch_export_planning_failure_does_not_create_processing_task(export_client, db, tmp_path, monkeypatch):
    import routes.export as export_mod

    root = _root(db, tmp_path)
    image = _image(db, root)
    monkeypatch.setattr(export_mod, '_plan_zip_entries', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('plan failed')))
    response = export_client.post('/api/images/batch-export', json={'ids': [image.id]})
    assert response.status_code == 500
    assert db.query(ExportTask).filter(ExportTask.status == 'processing').count() == 0


def test_batch_export_thread_start_failure_marks_task_failed(export_client, db, tmp_path, monkeypatch):
    import routes.images as images_mod

    root = _root(db, tmp_path)
    image = _image(db, root)

    class FailingThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            raise RuntimeError('thread failed')

    monkeypatch.setattr(images_mod.threading, 'Thread', FailingThread)
    response = export_client.post('/api/images/batch-export', json={'ids': [image.id]})
    assert response.status_code == 500
    assert db.query(ExportTask).filter(ExportTask.status == 'processing').count() == 0
    assert db.query(ExportTask).filter(ExportTask.status == 'failed').count() == 1


def test_thumbnail_lock_lease_waiters_share_entry():
    import routes.images as images_mod

    images_mod._thumb_gen_locks.clear()
    first = images_mod._get_thumb_lock(7)
    second = images_mod._get_thumb_lock(7)
    assert first is second
    assert first.refcount == 2
    images_mod._release_thumb_lock(7, first)
    assert len(images_mod._thumb_gen_locks) == 1
    images_mod._release_thumb_lock(7, second)
    assert images_mod._thumb_gen_locks == {}


def test_bind_host_defaults_local_and_allows_optional_token_for_lan():
    import app as app_mod

    assert app_mod._select_bind_host(cfg={}) == '127.0.0.1'
    assert app_mod._select_bind_host(debug=True, cfg={'lan_mode': True}) == '127.0.0.1'
    assert app_mod._select_bind_host(lan_requested=True, cfg={}) == '0.0.0.0'
