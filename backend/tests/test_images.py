"""Tests for /api/images endpoints — barcode filtering and NameError regression.

Uses in-memory SQLite and Flask test client.
"""

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import scoped_session, sessionmaker
from flask import Flask

from models import Base, Image, ScanRoot


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
