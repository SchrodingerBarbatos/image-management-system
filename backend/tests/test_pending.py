"""Tests for pending endpoints — count, list, confirm, ignore.

Uses in-memory SQLite and Flask test client to verify:
- GET /api/pending/count returns correct count
- count only includes confirmed=False, status='active', enabled scan roots
- count matches /api/pending list length
- confirm updates confirmed=True
- ignore deletes the image
"""

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import scoped_session, sessionmaker
from flask import Flask

from models import Base, Image, ScanRoot


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
    import routes.pending
    import versioning
    monkeypatch.setattr(routes.pending, "session", sess)
    monkeypatch.setattr(versioning, "session", sess)

    app = Flask(__name__)
    app.register_blueprint(routes.pending.pending_bp, url_prefix='/api')
    app.config['TESTING'] = True
    return app.test_client()


def _make_root(sess, root_id=1, path="/fake", enabled=True):
    sr = ScanRoot(id=root_id, path=path, enabled=enabled, recursive=False)
    sess.add(sr)
    sess.commit()
    return sr


def _make_image(sess, barcode, image_type="main", folder_ctime="2024-01-01T00:00:00",
                filename="a.jpg", scan_root_id=1, confirmed=False, status="active"):
    img = Image(
        barcode=barcode, image_type=image_type, folder_ctime=folder_ctime,
        filename=filename, ext="jpg", file_path=f"/fake/{barcode}/{folder_ctime}/{filename}",
        file_size=100, md5_hash="abc", content_md5="abc",
        confirmed=confirmed, status=status,
        scan_root_id=scan_root_id, sequence=1,
    )
    sess.add(img)
    sess.commit()
    return img


# ===================================================================
# /pending/count  tests
# ===================================================================


def test_pending_count_returns_json(client, sess):
    """GET /api/pending/count returns JSON with count key."""
    _make_root(sess)
    resp = client.get('/api/pending/count')
    assert resp.status_code == 200
    data = resp.get_json()
    assert 'count' in data
    assert isinstance(data['count'], int)


def test_pending_count_zero_when_none(client, sess):
    """Returns count=0 when no pending images exist."""
    _make_root(sess)
    resp = client.get('/api/pending/count')
    assert resp.status_code == 200
    assert resp.get_json()['count'] == 0


def test_pending_count_only_unconfirmed(client, sess):
    """count only includes confirmed == False."""
    _make_root(sess)
    _make_image(sess, "BC1", confirmed=False)
    _make_image(sess, "BC2", confirmed=True)
    resp = client.get('/api/pending/count')
    assert resp.get_json()['count'] == 1


def test_pending_count_only_active(client, sess):
    """count only includes status == 'active'."""
    _make_root(sess)
    _make_image(sess, "BC1", confirmed=False, status="active")
    _make_image(sess, "BC2", confirmed=False, status="ignored")
    resp = client.get('/api/pending/count')
    assert resp.get_json()['count'] == 1


def test_pending_count_only_enabled_roots(client, sess):
    """count only includes ScanRoot.enabled == True."""
    _make_root(sess, root_id=1, path="/enabled", enabled=True)
    _make_root(sess, root_id=2, path="/disabled", enabled=False)
    _make_image(sess, "BC1", scan_root_id=1, confirmed=False)
    _make_image(sess, "BC2", scan_root_id=2, confirmed=False)
    resp = client.get('/api/pending/count')
    assert resp.get_json()['count'] == 1


def test_pending_count_matches_list_length(client, sess):
    """/pending/count equals the length of /pending list."""
    _make_root(sess)
    for i in range(5):
        _make_image(sess, f"BC{i}", confirmed=False)
    # add one that should not appear
    _make_image(sess, "BC_confirmed", confirmed=True)
    _make_image(sess, "BC_inactive", confirmed=False, status="ignored")

    count_resp = client.get('/api/pending/count')
    list_resp = client.get('/api/pending')
    assert count_resp.get_json()['count'] == len(list_resp.get_json())
    assert count_resp.get_json()['count'] == 5


# ===================================================================
# /pending  tests
# ===================================================================


def test_pending_list_returns_array(client, sess):
    """GET /api/pending returns a JSON array."""
    _make_root(sess)
    resp = client.get('/api/pending')
    assert resp.status_code == 200
    assert isinstance(resp.get_json(), list)


def test_pending_list_excludes_confirmed(client, sess):
    """List excludes confirmed images."""
    _make_root(sess)
    _make_image(sess, "BC1", confirmed=False)
    _make_image(sess, "BC2", confirmed=True)
    resp = client.get('/api/pending')
    items = resp.get_json()
    assert len(items) == 1
    assert items[0]['barcode'] == 'BC1'


# ===================================================================
# /pending/confirm  tests
# ===================================================================


def test_pending_confirm_sets_confirmed(client, sess):
    """POST /api/pending/confirm sets confirmed=True."""
    _make_root(sess)
    img = _make_image(sess, "BC1", confirmed=False)
    resp = client.post('/api/pending/confirm', json=[
        {'id': img.id, 'image_type': 'main'},
    ])
    assert resp.status_code == 200
    assert resp.get_json()['confirmed'] == 1
    assert img.confirmed == True
    assert img.image_type == 'main'


def test_pending_confirm_validation(client, sess):
    """POST /api/pending/confirm validates input."""
    _make_root(sess)
    # Empty array
    resp = client.post('/api/pending/confirm', json=[])
    assert resp.status_code == 400
    # Not an array
    resp = client.post('/api/pending/confirm', json={})
    assert resp.status_code == 400
    # Invalid image_type
    resp = client.post('/api/pending/confirm', json=[
        {'id': 1, 'image_type': 'invalid'},
    ])
    assert resp.status_code == 400


# ===================================================================
# /pending/<id> DELETE  tests
# ===================================================================


def test_pending_ignore_deletes_image(client, sess):
    """DELETE /api/pending/<id> deletes the image."""
    _make_root(sess)
    img = _make_image(sess, "BC1", confirmed=False)
    resp = client.delete(f'/api/pending/{img.id}')
    assert resp.status_code == 200
    assert resp.get_json()['message'] == 'ignored'
    assert sess.get(Image, img.id) is None


def test_pending_ignore_not_found(client, sess):
    """DELETE /api/pending/<id> returns 404 for non-existent image."""
    _make_root(sess)
    resp = client.delete('/api/pending/99999')
    assert resp.status_code == 404
