"""Tests for batch endpoints — list duplicates, delete duplicates, low versions.

Uses in-memory SQLite and Flask test client to verify that:
- Duplicate listing returns correct groups and counts
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
    import versioning
    import task_engine
    monkeypatch.setattr(routes.batch, "session", sess)
    monkeypatch.setattr(versioning, "session", sess)
    monkeypatch.setattr(task_engine, "session", sess)

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
# list_duplicates  tests
# ===================================================================


def test_list_duplicates_basic(client, sess):
    """Basic correctness: duplicate folders appear in response with correct counts."""
    _make_root(sess)
    ctime_v1 = "2024-06-01T00:00:00"
    ctime_dup = "2024-05-01T00:00:00"

    # Version v1 has one duplicate folder
    _make_image(sess, "BC1", "main", ctime_v1)
    _make_image(sess, "BC1", "main", ctime_dup)
    _make_version(sess, "BC1", "main", ctime_v1, version_label="v1",
                  duplicate_mtimes=json.dumps([ctime_dup]))

    resp = client.get('/api/batch/duplicates')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['total_duplicate_count'] == 1
    assert data['total_barcode_count'] == 1
    assert len(data['groups']) == 1
    g = data['groups'][0]
    assert g['barcode'] == 'BC1'
    assert g['image_type'] == 'main'
    assert g['version_label'] == 'v1'
    assert g['folder_ctime'] == ctime_dup
    assert g['image_count'] == 1


def test_list_duplicates_empty(client, sess):
    """No duplicates: returns empty groups with zero counts."""
    _make_root(sess)
    resp = client.get('/api/batch/duplicates')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['groups'] == []
    assert data['total_duplicate_count'] == 0
    assert data['total_barcode_count'] == 0


def test_list_duplicates_empty_duplicate_mtimes(client, sess):
    """Versions with empty duplicate_mtimes are not included."""
    _make_root(sess)
    ctime = "2024-06-01T00:00:00"
    _make_image(sess, "BC1", "main", ctime)
    _make_version(sess, "BC1", "main", ctime, duplicate_mtimes="[]")

    resp = client.get('/api/batch/duplicates')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['groups'] == []


def test_list_duplicates_multiple_dup_ctimes(client, sess):
    """One version with multiple duplicate_mtimes produces multiple groups."""
    _make_root(sess)
    ctime_v1 = "2024-06-01T00:00:00"
    dup_a = "2024-05-01T00:00:00"
    dup_b = "2024-04-01T00:00:00"

    _make_image(sess, "BC1", "main", ctime_v1)
    _make_image(sess, "BC1", "main", dup_a)
    _make_image(sess, "BC1", "main", dup_b)
    _make_version(sess, "BC1", "main", ctime_v1, version_label="v1",
                  duplicate_mtimes=json.dumps([dup_a, dup_b]))

    resp = client.get('/api/batch/duplicates')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['total_duplicate_count'] == 2
    assert len(data['groups']) == 2
    ctimes = {g['folder_ctime'] for g in data['groups']}
    assert ctimes == {dup_a, dup_b}


def test_list_duplicates_dedup_across_versions(client, sess):
    """Same duplicate key across two versions is reported only once."""
    _make_root(sess)
    ctime_v1 = "2024-06-01T00:00:00"
    ctime_v2 = "2024-07-01T00:00:00"
    dup = "2024-05-01T00:00:00"

    _make_image(sess, "BC1", "main", ctime_v1)
    _make_image(sess, "BC1", "main", ctime_v2)
    _make_image(sess, "BC1", "main", dup)
    _make_version(sess, "BC1", "main", ctime_v1, version_label="v2",
                  duplicate_mtimes=json.dumps([dup]))
    _make_version(sess, "BC1", "main", ctime_v2, version_label="v1",
                  duplicate_mtimes=json.dumps([dup]))

    resp = client.get('/api/batch/duplicates')
    assert resp.status_code == 200
    data = resp.get_json()
    # Same (barcode, image_type, dup_ctime) should be de-duplicated
    assert data['total_duplicate_count'] == 1
    assert len(data['groups']) == 1


def test_list_duplicates_excludes_disabled_roots(client, sess):
    """Images from disabled scan roots are not counted."""
    _make_root(sess, root_id=1, path="/enabled", enabled=True)
    _make_root(sess, root_id=2, path="/disabled", enabled=False)
    ctime_v1 = "2024-06-01T00:00:00"
    dup = "2024-05-01T00:00:00"

    # Image in duplicate folder is on a disabled root — should be excluded
    _make_image(sess, "BC1", "main", ctime_v1, scan_root_id=1)
    _make_image(sess, "BC1", "main", dup, scan_root_id=2)
    _make_version(sess, "BC1", "main", ctime_v1, version_label="v1",
                  duplicate_mtimes=json.dumps([dup]))

    resp = client.get('/api/batch/duplicates')
    assert resp.status_code == 200
    data = resp.get_json()
    # Duplicate folder has no images in enabled roots → excluded from groups
    assert data['total_duplicate_count'] == 0


def test_list_duplicates_multiple_barcodes(client, sess):
    """Duplicates across multiple barcodes are all reported."""
    _make_root(sess)
    ctime_a = "2024-06-01T00:00:00"
    dup_a = "2024-05-01T00:00:00"
    ctime_b = "2024-06-01T00:00:00"
    dup_b = "2024-05-01T00:00:00"

    _make_image(sess, "BC1", "main", ctime_a)
    _make_image(sess, "BC1", "main", dup_a)
    _make_image(sess, "BC2", "detail", ctime_b)
    _make_image(sess, "BC2", "detail", dup_b)
    _make_version(sess, "BC1", "main", ctime_a, duplicate_mtimes=json.dumps([dup_a]))
    _make_version(sess, "BC2", "detail", ctime_b, duplicate_mtimes=json.dumps([dup_b]))

    resp = client.get('/api/batch/duplicates')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['total_duplicate_count'] == 2
    assert data['total_barcode_count'] == 2
    assert len(data['groups']) == 2


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
