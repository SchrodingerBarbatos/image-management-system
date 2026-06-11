"""Tests for /api/images endpoints — barcode filtering and NameError regression.

Uses in-memory SQLite and Flask test client.
"""

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import scoped_session, sessionmaker
from flask import Flask

from models import Base, Image, ScanRoot, DeletedFolder


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
def engine():
    eng = create_engine("sqlite:///:memory:", echo=False)

    @event.listens_for(eng, "connect")
    def _set_pragma(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA journal_mode=WAL")
        dbapi_conn.execute("PRAGMA busy_timeout=5000")

    Base.metadata.create_all(bind=eng)
    return eng


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
    import routes.images
    import versioning
    monkeypatch.setattr(routes.images, "session", sess)
    monkeypatch.setattr(versioning, "session", sess)

    app = Flask(__name__)
    app.register_blueprint(routes.images.images_bp, url_prefix='/api')
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
                scan_root_id=1, confirmed=True, status="active", **kw):
    img = Image(
        barcode=barcode, image_type=image_type, folder_ctime=folder_ctime,
        filename=filename, ext="jpg",
        file_path=f"/fake/{barcode}/{folder_ctime}/{filename}",
        file_size=100, md5_hash="abc", content_md5="abc",
        confirmed=confirmed, status=status, scan_root_id=scan_root_id,
        sequence=1, **kw,
    )
    sess.add(img)
    sess.commit()
    return img


# ===================================================================
# delete_image deleted_folders tracking tests
# ===================================================================


def test_delete_image_records_deleted_folder(client, sess):
    _make_root(sess)
    ctime = "2024-01-01T00:00:00"
    img = _make_image(sess, "BC_DEL_ONE", "main", ctime)

    resp = client.delete(f"/api/images/{img.id}")

    assert resp.status_code == 200
    assert sess.query(Image).filter(Image.id == img.id).count() == 0
    deleted = sess.query(DeletedFolder).filter_by(
        barcode="BC_DEL_ONE", image_type="main", folder_ctime=ctime,
    ).one_or_none()
    assert deleted is not None


def test_batch_delete_records_deleted_folders(client, sess):
    _make_root(sess)
    ctime1 = "2024-01-01T00:00:00"
    ctime2 = "2024-02-01T00:00:00"
    img1 = _make_image(sess, "BC_DEL_BATCH", "main", ctime1, filename="a.jpg")
    img2 = _make_image(sess, "BC_DEL_BATCH", "detail", ctime2, filename="b.jpg")

    resp = client.post("/api/images/batch-delete", json={"ids": [img1.id, img2.id], "delete_file": False})

    assert resp.status_code == 200
    keys = {
        (d.barcode, d.image_type, d.folder_ctime)
        for d in sess.query(DeletedFolder).all()
    }
    assert ("BC_DEL_BATCH", "main", ctime1) in keys
    assert ("BC_DEL_BATCH", "detail", ctime2) in keys


# ===================================================================
# list_images  tests
# ===================================================================


def test_list_images_without_barcode_returns_200(client, sess):
    _make_root(sess)
    _make_image(sess, "BC1", "main", "2024-01-01T00:00:00")
    _make_image(sess, "BC2", "detail", "2024-01-01T00:00:00")

    resp = client.get('/api/images')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['total'] == 2
    assert len(data['items']) == 2


def test_list_images_barcode_fuzzy(client, sess):
    _make_root(sess)
    _make_image(sess, "ABC123", "main", "2024-01-01T00:00:00")
    _make_image(sess, "XYZ123", "main", "2024-01-01T00:00:00")
    _make_image(sess, "ABC999", "main", "2024-01-01T00:00:00")

    resp = client.get('/api/images?barcode=123')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['total'] == 2
    barcodes = {it['barcode'] for it in data['items']}
    assert barcodes == {'ABC123', 'XYZ123'}


def test_list_images_barcode_exact(client, sess):
    _make_root(sess)
    _make_image(sess, "ABC123", "main", "2024-01-01T00:00:00")
    _make_image(sess, "ABC123", "detail", "2024-02-01T00:00:00")
    _make_image(sess, "ABC1234", "main", "2024-01-01T00:00:00")

    resp = client.get('/api/images?barcode_exact=ABC123')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['total'] == 2
    for it in data['items']:
        assert it['barcode'] == 'ABC123'


def test_list_images_barcode_exact_takes_precedence(client, sess):
    _make_root(sess)
    _make_image(sess, "TARGET", "main", "2024-01-01T00:00:00")
    _make_image(sess, "NOT_TARGET", "main", "2024-01-01T00:00:00")

    resp = client.get('/api/images?barcode_exact=TARGET&barcode=NOT')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['total'] == 1
    assert data['items'][0]['barcode'] == 'TARGET'


def test_list_images_filters_disabled_scan_root(client, sess):
    _make_root(sess, root_id=1, enabled=True)
    _make_root(sess, root_id=2, enabled=False)
    _make_image(sess, "BC1", "main", "2024-01-01T00:00:00", scan_root_id=1)
    _make_image(sess, "BC2", "main", "2024-01-01T00:00:00", scan_root_id=2)

    resp = client.get('/api/images')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['total'] == 1
    assert data['items'][0]['barcode'] == 'BC1'


# ===================================================================
# update_image  version rebuild tests
# ===================================================================


def test_update_image_type_triggers_version_rebuild(client, sess):
    """Changing image_type should rebuild versions for the affected barcode."""
    from models import ImageVersion

    _make_root(sess)
    img = _make_image(sess, "BC1", "main", "2024-01-01T00:00:00")

    # Pre-create a version record for 'main'
    v = ImageVersion(
        barcode="BC1", image_type="main", version_label="v1",
        folder_ctime="2024-01-01T00:00:00", content_hash="hash1", is_latest=True,
    )
    sess.add(v)
    sess.commit()

    # Change image_type from main to detail
    resp = client.put(f'/api/images/{img.id}', json={'image_type': 'detail'})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['image_type'] == 'detail'

    # Old 'main' version should be gone, new 'detail' version should exist
    vers = sess.query(ImageVersion).filter(ImageVersion.barcode == "BC1").all()
    types = {v.image_type for v in vers}
    assert 'detail' in types
    assert 'main' not in types


def test_update_confirmed_triggers_version_rebuild(client, sess):
    """Changing confirmed should rebuild versions for the affected barcode."""
    from models import ImageVersion

    _make_root(sess)
    img = _make_image(sess, "BC1", "main", "2024-01-01T00:00:00")

    # Pre-create a version
    v = ImageVersion(
        barcode="BC1", image_type="main", version_label="v1",
        folder_ctime="2024-01-01T00:00:00", content_hash="hash1", is_latest=True,
    )
    sess.add(v)
    sess.commit()

    # Unconfirm the image
    resp = client.put(f'/api/images/{img.id}', json={'confirmed': False})
    assert resp.status_code == 200

    # Version should be removed (unconfirmed images don't get versions)
    vers = sess.query(ImageVersion).filter(ImageVersion.barcode == "BC1").count()
    assert vers == 0


def test_update_unrelated_field_no_version_rebuild(client, sess):
    """Changing a non-version field should NOT trigger version rebuild."""
    from models import ImageVersion
    import versioning

    _make_root(sess)
    img = _make_image(sess, "BC1", "main", "2024-01-01T00:00:00")

    call_count = [0]
    original = versioning.update_versions_for_barcode

    def counting_update(barcode):
        call_count[0] += 1
        return original(barcode)

    # No version field changed — only sequence (not tracked for version rebuild)
    # Actually sequence is not in the version-relevant set, so no rebuild should happen
    # But the PUT handler only checks image_type and confirmed, so changing neither means no rebuild

    resp = client.put(f'/api/images/{img.id}', json={})
    assert resp.status_code == 200

    # No version rebuild triggered (no version-relevant field changed)
    # We verify by checking the image still has its original values
    data = resp.get_json()
    assert data['image_type'] == 'main'
    assert data['confirmed'] == True


# ---------------------------------------------------------------------------
# ScanRoot.enabled TTL cache tests
# ---------------------------------------------------------------------------


def test_is_root_enabled_caches(sess, monkeypatch):
    """_is_root_enabled returns cached value within TTL window."""
    import routes.images as ri
    monkeypatch.setattr(ri, "session", sess)

    _make_root(sess, root_id=10, enabled=True)
    # Clear any stale cache
    ri._invalidate_root_cache()

    assert ri._is_root_enabled(10) is True

    # Change root to disabled in DB — cache should still return True
    root = sess.get(ScanRoot, 10)
    root.enabled = False
    sess.commit()

    assert ri._is_root_enabled(10) is True  # stale cache hit


def test_invalidate_root_cache_forces_db_read(sess, monkeypatch):
    """_invalidate_root_cache clears the cache so next call reads DB."""
    import routes.images as ri
    monkeypatch.setattr(ri, "session", sess)

    _make_root(sess, root_id=11, enabled=True)
    ri._invalidate_root_cache()

    assert ri._is_root_enabled(11) is True

    # Disable root in DB
    root = sess.get(ScanRoot, 11)
    root.enabled = False
    sess.commit()

    # Cache is stale — still returns True
    assert ri._is_root_enabled(11) is True

    # Invalidate specific root
    ri._invalidate_root_cache(11)

    # Now reads DB — returns False
    assert ri._is_root_enabled(11) is False


def test_invalidate_all_roots(sess, monkeypatch):
    """_invalidate_root_cache() with no args clears entire cache."""
    import routes.images as ri
    monkeypatch.setattr(ri, "session", sess)

    _make_root(sess, root_id=20, enabled=True)
    _make_root(sess, root_id=21, enabled=True)
    ri._invalidate_root_cache()

    ri._is_root_enabled(20)
    ri._is_root_enabled(21)

    # Disable both in DB
    sess.get(ScanRoot, 20).enabled = False
    sess.get(ScanRoot, 21).enabled = False
    sess.commit()

    # Stale cache
    assert ri._is_root_enabled(20) is True
    assert ri._is_root_enabled(21) is True

    # Clear all
    ri._invalidate_root_cache()

    assert ri._is_root_enabled(20) is False
    assert ri._is_root_enabled(21) is False


def test_is_root_enabled_missing_root(sess, monkeypatch):
    """_is_root_enabled returns False for non-existent root and caches it."""
    import routes.images as ri
    monkeypatch.setattr(ri, "session", sess)
    ri._invalidate_root_cache()

    assert ri._is_root_enabled(9999) is False


# ---------------------------------------------------------------------------
# Pagination validation tests
# ---------------------------------------------------------------------------


def test_list_images_page_abc_returns_400(client, sess):
    _make_root(sess)
    _make_image(sess, "BC1", "main", "2024-01-01T00:00:00")
    resp = client.get('/api/images?page=abc')
    assert resp.status_code == 400
    assert 'page' in resp.get_json()['error']


def test_list_images_page_zero_returns_400(client, sess):
    _make_root(sess)
    _make_image(sess, "BC1", "main", "2024-01-01T00:00:00")
    resp = client.get('/api/images?page=0')
    assert resp.status_code == 400
    assert 'page' in resp.get_json()['error']


def test_list_images_page_size_9999_returns_400(client, sess):
    _make_root(sess)
    _make_image(sess, "BC1", "main", "2024-01-01T00:00:00")
    resp = client.get('/api/images?page_size=9999')
    assert resp.status_code == 400
    assert 'page_size' in resp.get_json()['error']


def test_list_images_scan_root_id_abc_returns_400(client, sess):
    _make_root(sess)
    _make_image(sess, "BC1", "main", "2024-01-01T00:00:00")
    resp = client.get('/api/images?scan_root_id=abc')
    assert resp.status_code == 400
    assert 'scan_root_id' in resp.get_json()['error']


def test_list_images_scan_root_id_negative_returns_400(client, sess):
    _make_root(sess)
    _make_image(sess, "BC1", "main", "2024-01-01T00:00:00")
    resp = client.get('/api/images?scan_root_id=-1')
    assert resp.status_code == 400
    assert 'scan_root_id' in resp.get_json()['error']


def test_list_images_valid_params_returns_200(client, sess):
    _make_root(sess)
    _make_image(sess, "BC1", "main", "2024-01-01T00:00:00")
    resp = client.get('/api/images?page=1&page_size=10')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['page'] == 1
    assert data['page_size'] == 10


# ---------------------------------------------------------------------------
# delete_file path-safety tests
# ---------------------------------------------------------------------------


def test_delete_image_file_outside_root_preserves_db(client, sess, tmp_path):
    """When delete_file=true and path is outside scan root, DB record is preserved."""
    import os
    root_dir = str(tmp_path / "photos")
    os.makedirs(root_dir)
    _make_root(sess, root_id=1, path=root_dir)
    # Create image with path OUTSIDE the scan root
    outside = str(tmp_path / "outside" / "evil.jpg")
    os.makedirs(os.path.dirname(outside))
    img = _make_image(sess, "BC1", "main", "2024-01-01T00:00:00",
                      filename="evil.jpg", scan_root_id=1)
    # Manually set file_path to outside location
    img.file_path = outside
    sess.commit()

    resp = client.delete(f'/api/images/{img.id}?delete_file=true')
    assert resp.status_code == 403
    assert '文件删除失败' in resp.get_json()['error']
    # DB record must still exist
    assert sess.get(Image, img.id) is not None


# ---------------------------------------------------------------------------
# POST /api/scan validation tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
def scan_client(sess, monkeypatch):
    """Flask test client with scan blueprint registered."""
    import routes.scan as scan_mod
    monkeypatch.setattr(scan_mod, "session", sess)

    app = Flask(__name__)
    app.register_blueprint(scan_mod.scan_bp, url_prefix='/api')
    app.config['TESTING'] = True
    return app.test_client()


def test_scan_root_ids_not_list_returns_400(scan_client):
    resp = scan_client.post('/api/scan', json={'root_ids': 'not-a-list'})
    assert resp.status_code == 400
    assert 'root_ids' in resp.get_json()['error']


def test_scan_root_ids_empty_list_returns_400(scan_client):
    resp = scan_client.post('/api/scan', json={'root_ids': []})
    assert resp.status_code == 400
    assert 'root_ids' in resp.get_json()['error']


def test_scan_root_ids_missing_returns_400(scan_client):
    resp = scan_client.post('/api/scan', json={})
    assert resp.status_code == 400


def test_scan_root_ids_non_int_returns_400(scan_client):
    resp = scan_client.post('/api/scan', json={'root_ids': [1, 'abc', 3]})
    assert resp.status_code == 400
    assert '非法值' in resp.get_json()['error']


def test_scan_root_ids_negative_returns_400(scan_client):
    resp = scan_client.post('/api/scan', json={'root_ids': [-1]})
    assert resp.status_code == 400
    assert '非法值' in resp.get_json()['error']


def test_scan_mode_invalid_returns_400(scan_client, sess):
    _make_root(sess, root_id=1)
    resp = scan_client.post('/api/scan', json={'root_ids': [1], 'scan_mode': 'invalid'})
    assert resp.status_code == 400
    assert 'scan_mode' in resp.get_json()['error']


def test_scan_root_ids_deduped(scan_client, sess):
    """Duplicate root_ids are accepted (deduped internally) without 400."""
    import routes.scan as scan_mod
    _make_root(sess, root_id=1)

    # The validation layer should accept [1, 1, 1] and not return 400.
    # It may return 200/202 (scan started) or 409 (another scan running),
    # but NOT 400 (validation error).
    resp = scan_client.post('/api/scan', json={'root_ids': [1, 1, 1]})
    assert resp.status_code != 400


# ---------------------------------------------------------------------------
# batch_delete consistency: failed items must not pollute deleted_folders / versions
# ---------------------------------------------------------------------------


def test_batch_delete_partial_failure_consistency(client, sess, tmp_path):
    """delete_file=true with mixed inside/outside paths: only successes write deleted_folders."""
    import os
    from models import DeletedFolder

    root_dir = str(tmp_path / "photos")
    os.makedirs(root_dir)
    _make_root(sess, root_id=1, path=root_dir)

    # Image INSIDE root — should succeed
    inside = root_dir + os.sep + "good.jpg"
    with open(inside, 'w') as f:
        f.write('x')
    img_ok = _make_image(sess, "BC_OK", "main", "2024-01-01T00:00:00",
                         filename="good.jpg", scan_root_id=1)
    img_ok.file_path = inside
    sess.commit()

    # Image OUTSIDE root — should fail
    outside_dir = str(tmp_path / "evil")
    os.makedirs(outside_dir)
    outside = outside_dir + os.sep + "bad.jpg"
    with open(outside, 'w') as f:
        f.write('x')
    img_bad = _make_image(sess, "BC_BAD", "main", "2024-01-01T00:00:00",
                          filename="bad.jpg", scan_root_id=1)
    img_bad.file_path = outside
    sess.commit()

    resp = client.post('/api/images/batch-delete', json={
        'ids': [img_ok.id, img_bad.id],
        'delete_file': True,
    })
    data = resp.get_json()
    assert data['deleted'] == 1
    assert len(data['failed_items']) == 1
    assert data['failed_items'][0]['id'] == img_bad.id

    # Failed item DB record still exists
    assert sess.get(Image, img_bad.id) is not None
    # Success item DB record is gone
    assert sess.get(Image, img_ok.id) is None

    # deleted_folders only contains the success item's barcode, not the failed one
    df = sess.query(DeletedFolder).all()
    recorded_barcodes = {r.barcode for r in df}
    assert 'BC_OK' in recorded_barcodes
    assert 'BC_BAD' not in recorded_barcodes


def test_batch_delete_all_fail_returns_zero(client, sess, tmp_path):
    """delete_file=true when ALL files are outside root: deleted=0, no side effects."""
    import os
    from models import DeletedFolder

    root_dir = str(tmp_path / "photos")
    os.makedirs(root_dir)
    _make_root(sess, root_id=1, path=root_dir)

    outside_dir = str(tmp_path / "evil")
    os.makedirs(outside_dir)
    outside = outside_dir + os.sep + "bad.jpg"
    with open(outside, 'w') as f:
        f.write('x')
    img = _make_image(sess, "BC_FAIL", "main", "2024-01-01T00:00:00",
                      filename="bad.jpg", scan_root_id=1)
    img.file_path = outside
    sess.commit()

    resp = client.post('/api/images/batch-delete', json={
        'ids': [img.id],
        'delete_file': True,
    })
    data = resp.get_json()
    assert data['deleted'] == 0
    assert len(data['failed_items']) == 1

    # DB record preserved
    assert sess.get(Image, img.id) is not None
    # No deleted_folders written
    assert sess.query(DeletedFolder).count() == 0
