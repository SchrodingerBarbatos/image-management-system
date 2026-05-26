"""Tests for batch delete endpoints — server-side revalidation and edge cases.

Uses in-memory SQLite and Flask test client to verify that:
- Valid duplicate/low-version items are deleted successfully
- Expired items (data changed since preview) return 409
- Items belonging to disabled scan roots return 403
"""

import json
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import scoped_session, sessionmaker
from flask import Flask

from models import Base, Image, ImageVersion, ScanRoot


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
def engine():
    """In-memory SQLite engine."""
    eng = create_engine("sqlite:///:memory:", echo=False)

    @event.listens_for(eng, "connect")
    def _set_pragma(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA journal_mode=WAL")
        dbapi_conn.execute("PRAGMA busy_timeout=5000")

    Base.metadata.create_all(bind=eng)
    return eng


@pytest.fixture(scope="function")
def sess(engine):
    """Scoped session factory bound to the in-memory engine."""
    factory = sessionmaker(bind=engine)
    scoped = scoped_session(factory)
    try:
        yield scoped
    finally:
        scoped.remove()


@pytest.fixture(scope="function")
def client(sess, monkeypatch):
    """Flask test client with batch blueprint and test session."""
    import routes.batch
    monkeypatch.setattr(routes.batch, "session", sess)

    app = Flask(__name__)
    app.register_blueprint(routes.batch.batch_bp, url_prefix='/api')
    app.config['TESTING'] = True
    return app.test_client()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


# ===================================================================
# delete_duplicates  tests
# ===================================================================


def test_delete_duplicates_success(client, sess):
    """Valid duplicate items are deleted successfully."""
    _make_root(sess)
    ctime = "2024-06-01T00:00:00"
    _make_image(sess, "BC1", "main", ctime)
    _make_version(sess, "BC1", "main", ctime, duplicate_mtimes=json.dumps([ctime]))

    resp = client.post('/api/batch/delete-duplicates', json={
        'items': [{'barcode': 'BC1', 'image_type': 'main', 'folder_ctime': ctime}],
        'delete_files': False,
    })

    assert resp.status_code == 200
    data = resp.get_json()
    assert data['deleted_image_count'] == 1
    assert data['deleted_item_count'] == 1
    assert data['affected_barcodes'] == ['BC1']

    remaining = sess.query(Image).filter(Image.barcode == 'BC1').count()
    assert remaining == 0


def test_delete_duplicates_expired_item_returns_409(client, sess):
    """An item whose duplicate_mtimes no longer contains the folder_ctime returns 409."""
    _make_root(sess)
    ctime = "2024-06-01T00:00:00"
    _make_image(sess, "BC1", "main", ctime)
    # Version exists but duplicate_mtimes is empty — not a valid duplicate
    _make_version(sess, "BC1", "main", ctime, duplicate_mtimes="[]")

    resp = client.post('/api/batch/delete-duplicates', json={
        'items': [{'barcode': 'BC1', 'image_type': 'main', 'folder_ctime': ctime}],
        'delete_files': False,
    })

    assert resp.status_code == 409
    data = resp.get_json()
    assert '第0项不是有效的重复文件夹' in data['error']
    assert data['invalid_index'] == 0

    remaining = sess.query(Image).filter(Image.barcode == 'BC1').count()
    assert remaining == 1  # not deleted


def test_delete_duplicates_disabled_root_returns_403(client, sess):
    """Items belonging to a disabled scan root return 403."""
    _make_root(sess, root_id=1, path="/enabled", enabled=True)
    _make_root(sess, root_id=2, path="/disabled", enabled=False)
    ctime = "2024-06-01T00:00:00"

    _make_image(sess, "BC1", "main", ctime, scan_root_id=2)  # disabled root
    _make_version(sess, "BC1", "main", ctime, duplicate_mtimes=json.dumps([ctime]))

    resp = client.post('/api/batch/delete-duplicates', json={
        'items': [{'barcode': 'BC1', 'image_type': 'main', 'folder_ctime': ctime}],
        'delete_files': False,
    })

    assert resp.status_code == 403
    data = resp.get_json()
    assert '已禁用的扫描目录' in data['error']
    assert data['disabled_count'] == 1


def test_delete_duplicates_multiple_items(client, sess):
    """Multiple items across barcodes are all deleted."""
    _make_root(sess)
    ctime1 = "2024-06-01T00:00:00"
    ctime2 = "2024-07-01T00:00:00"
    _make_image(sess, "BC1", "main", ctime1)
    _make_image(sess, "BC2", "detail", ctime2)
    _make_version(sess, "BC1", "main", ctime1, duplicate_mtimes=json.dumps([ctime1]))
    _make_version(sess, "BC2", "detail", ctime2, duplicate_mtimes=json.dumps([ctime2]))

    resp = client.post('/api/batch/delete-duplicates', json={
        'items': [
            {'barcode': 'BC1', 'image_type': 'main', 'folder_ctime': ctime1},
            {'barcode': 'BC2', 'image_type': 'detail', 'folder_ctime': ctime2},
        ],
        'delete_files': False,
    })

    assert resp.status_code == 200
    data = resp.get_json()
    assert data['deleted_image_count'] == 2
    assert data['deleted_item_count'] == 2
    assert set(data['affected_barcodes']) == {'BC1', 'BC2'}


def test_delete_duplicates_validation_errors(client):
    """Basic input validation: missing fields, invalid image_type, invalid ISO format."""
    # Missing barcode
    resp = client.post('/api/batch/delete-duplicates', json={
        'items': [{'image_type': 'main', 'folder_ctime': '2024-01-01T00:00:00'}],
        'delete_files': False,
    })
    assert resp.status_code == 400

    # Invalid image_type
    resp = client.post('/api/batch/delete-duplicates', json={
        'items': [{'barcode': 'BC1', 'image_type': 'invalid', 'folder_ctime': '2024-01-01T00:00:00'}],
        'delete_files': False,
    })
    assert resp.status_code == 400

    # Invalid folder_ctime format
    resp = client.post('/api/batch/delete-duplicates', json={
        'items': [{'barcode': 'BC1', 'image_type': 'main', 'folder_ctime': 'not-a-date'}],
        'delete_files': False,
    })
    assert resp.status_code == 400

    # Empty items
    resp = client.post('/api/batch/delete-duplicates', json={
        'items': [],
        'delete_files': False,
    })
    assert resp.status_code == 400


# ===================================================================
# delete_low_versions  tests
# ===================================================================


def test_delete_low_versions_success(client, sess):
    """Low-version items meeting threshold criteria are deleted."""
    _make_root(sess)
    ctime = "2024-06-01T00:00:00"
    _make_image(sess, "BC1", "main", ctime)
    _make_version(sess, "BC1", "main", ctime, version_label="v2", is_latest=False)

    # Another version exists, so total_versions=2 > 1 and count=1 < threshold=3
    # Insert one more version record to make total_versions=2
    _make_version(sess, "BC1", "main", "2024-07-01T00:00:00",
                  version_label="v1", is_latest=True)

    resp = client.post('/api/batch/delete-low-versions', json={
        'items': [{'barcode': 'BC1', 'image_type': 'main', 'folder_ctime': ctime}],
        'delete_files': False,
        'main_threshold': 3,
        'detail_threshold': 0,
    })

    assert resp.status_code == 200
    data = resp.get_json()
    assert data['deleted_image_count'] == 1
    assert data['deleted_item_count'] == 1


def test_delete_low_versions_no_images_returns_409(client, sess):
    """An item whose images have been removed returns 409."""
    _make_root(sess)
    ctime_missing = "2024-06-01T00:00:00"
    ctime_present = "2024-07-01T00:00:00"

    # One version with images, one without
    _make_image(sess, "BC1", "main", ctime_present)
    _make_version(sess, "BC1", "main", ctime_present, version_label="v2")
    _make_version(sess, "BC1", "main", ctime_missing, version_label="v1")

    resp = client.post('/api/batch/delete-low-versions', json={
        'items': [{'barcode': 'BC1', 'image_type': 'main', 'folder_ctime': ctime_missing}],
        'delete_files': False,
        'main_threshold': 3,
        'detail_threshold': 0,
    })

    assert resp.status_code == 409
    data = resp.get_json()
    assert '已无有效图片' in data['error']
    assert data['invalid_index'] == 0


def test_delete_low_versions_above_threshold_returns_409(client, sess):
    """An item meeting the threshold (count >= threshold) returns 409."""
    _make_root(sess)
    ctime = "2024-06-01T00:00:00"
    # Create 5 images — count=5 >= threshold=3 → not eligible for deletion
    for i in range(5):
        _make_image(sess, "BC1", "main", ctime, filename=f"img{i}.jpg",
                    file_path=f"/fake/BC1/{ctime}/img{i}.jpg")
    _make_version(sess, "BC1", "main", ctime, version_label="v2")
    _make_version(sess, "BC1", "main", "2024-07-01T00:00:00", version_label="v1")

    resp = client.post('/api/batch/delete-low-versions', json={
        'items': [{'barcode': 'BC1', 'image_type': 'main', 'folder_ctime': ctime}],
        'delete_files': False,
        'main_threshold': 3,
        'detail_threshold': 0,
    })

    assert resp.status_code == 409
    data = resp.get_json()
    assert '不符合删除条件' in data['error']


def test_delete_low_versions_only_version_returns_409(client, sess):
    """An item that is the only version (total_versions=1) returns 409."""
    _make_root(sess)
    ctime = "2024-06-01T00:00:00"
    _make_image(sess, "BC1", "main", ctime)
    _make_version(sess, "BC1", "main", ctime, version_label="v1")

    resp = client.post('/api/batch/delete-low-versions', json={
        'items': [{'barcode': 'BC1', 'image_type': 'main', 'folder_ctime': ctime}],
        'delete_files': False,
        'main_threshold': 3,
        'detail_threshold': 0,
    })

    assert resp.status_code == 409


def test_delete_low_versions_disabled_root_returns_403(client, sess):
    """Items belonging to a disabled scan root return 403."""
    _make_root(sess, root_id=1, path="/enabled", enabled=True)
    _make_root(sess, root_id=2, path="/disabled", enabled=False)
    ctime = "2024-06-01T00:00:00"

    _make_image(sess, "BC1", "main", ctime, scan_root_id=2)  # disabled root
    _make_version(sess, "BC1", "main", ctime, version_label="v2")
    _make_version(sess, "BC1", "main", "2024-07-01T00:00:00", version_label="v1")

    resp = client.post('/api/batch/delete-low-versions', json={
        'items': [{'barcode': 'BC1', 'image_type': 'main', 'folder_ctime': ctime}],
        'delete_files': False,
        'main_threshold': 3,
        'detail_threshold': 0,
    })

    assert resp.status_code == 403
    data = resp.get_json()
    assert '已禁用的扫描目录' in data['error']


def test_delete_low_versions_validation_errors(client):
    """Basic input validation for low-version delete."""
    # Missing main_threshold
    resp = client.post('/api/batch/delete-low-versions', json={
        'items': [{'barcode': 'BC1', 'image_type': 'main', 'folder_ctime': '2024-01-01T00:00:00'}],
        'delete_files': False,
    })
    assert resp.status_code == 400

    # Both thresholds zero
    resp = client.post('/api/batch/delete-low-versions', json={
        'items': [{'barcode': 'BC1', 'image_type': 'main', 'folder_ctime': '2024-01-01T00:00:00'}],
        'delete_files': False,
        'main_threshold': 0,
        'detail_threshold': 0,
    })
    assert resp.status_code == 400
