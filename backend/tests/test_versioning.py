"""Tests for versioning, export filter, and SQLite lock detection.

Uses in-memory SQLite so no disk I/O is needed. Each test gets a fresh
database with the full schema (all tables) created before the test runs.
"""

import json
import sqlite3
import time
from unittest.mock import patch, MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, scoped_session, sessionmaker

from models import Base, Image, ImageVersion, ScanRoot, BarcodeSetting


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="function")
def engine():
    """In-memory SQLite engine with WAL + busy_timeout pragmas."""
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_image(barcode, image_type, folder_ctime, filename="a.jpg", **kw):
    """Minimal Image row factory with sensible defaults."""
    defaults = dict(
        barcode=barcode,
        image_type=image_type,
        folder_ctime=folder_ctime,
        filename=filename,
        ext="jpg",
        file_path=f"/fake/{barcode}/{folder_ctime}/{filename}",
        file_size=100,
        md5_hash="abc",
        content_md5="abc",
        confirmed=True,
        status="active",
        scan_root_id=1,
        sequence=1,
    )
    defaults.update(kw)
    return Image(**defaults)


def _make_version(barcode, image_type, folder_ctime, **kw):
    defaults = dict(
        barcode=barcode,
        image_type=image_type,
        version_label="v1",
        folder_ctime=folder_ctime,
        content_hash="hash1",
        is_latest=False,
        duplicate_mtimes="[]",
    )
    defaults.update(kw)
    return ImageVersion(**defaults)


def _make_setting(barcode, main_ctime="", detail_ctime=""):
    return BarcodeSetting(
        barcode=barcode,
        default_main_ctime=main_ctime,
        default_detail_ctime=detail_ctime,
    )


# ===================================================================
# filter_to_single_version  tests
# ===================================================================


def test_filter_no_settings_no_versions_keeps_all(sess):
    """When no BarcodeSetting or ImageVersion exist, all images pass through."""
    from routes.export import filter_to_single_version

    img1 = _make_image("BC1", "main", "2024-01-01T00:00:00")
    img2 = _make_image("BC1", "main", "2024-02-01T00:00:00")
    sess.add_all([img1, img2])
    sess.commit()

    result = filter_to_single_version([img1, img2], ["BC1"], sess)
    assert len(result) == 2


def test_filter_is_latest_version_used(sess):
    """When ImageVersion.is_latest is set, only matching ctimes are kept."""
    from routes.export import filter_to_single_version

    img_v1 = _make_image("BC1", "main", "2024-01-01T00:00:00")
    img_v2 = _make_image("BC1", "main", "2024-02-01T00:00:00")
    sess.add_all([img_v1, img_v2])
    sess.add(_make_version("BC1", "main", "2024-02-01T00:00:00", is_latest=True))
    sess.commit()

    result = filter_to_single_version([img_v1, img_v2], ["BC1"], sess)
    assert len(result) == 1
    assert result[0].folder_ctime == "2024-02-01T00:00:00"


def test_filter_barcode_setting_overrides_is_latest(sess):
    """BarcodeSetting default ctime takes priority over is_latest flag."""
    from routes.export import filter_to_single_version

    img_v1 = _make_image("BC1", "main", "2024-01-01T00:00:00")
    img_v2 = _make_image("BC1", "main", "2024-02-01T00:00:00")
    sess.add_all([img_v1, img_v2])
    # is_latest points to v2, but user setting prefers v1
    sess.add(_make_version("BC1", "main", "2024-02-01T00:00:00", is_latest=True))
    sess.add(_make_setting("BC1", main_ctime="2024-01-01T00:00:00"))
    sess.commit()

    result = filter_to_single_version([img_v1, img_v2], ["BC1"], sess)
    assert len(result) == 1
    assert result[0].folder_ctime == "2024-01-01T00:00:00"


def test_filter_detail_type_respected(sess):
    """Detail images are filtered independently from main images."""
    from routes.export import filter_to_single_version

    img_main = _make_image("BC1", "main", "2024-01-01T00:00:00", filename="main.jpg")
    img_detail_old = _make_image("BC1", "detail", "2024-01-01T00:00:00", filename="detail_old.jpg")
    img_detail_new = _make_image("BC1", "detail", "2024-03-01T00:00:00", filename="detail_new.jpg")
    sess.add_all([img_main, img_detail_old, img_detail_new])
    sess.add(_make_version("BC1", "detail", "2024-03-01T00:00:00", is_latest=True))
    sess.commit()

    result = filter_to_single_version(
        [img_main, img_detail_old, img_detail_new], ["BC1"], sess
    )
    assert len(result) == 2  # main (passthrough) + detail new


def test_filter_different_barcodes_independent(sess):
    """Filtering for one barcode doesn't affect another."""
    from routes.export import filter_to_single_version

    bc1_v1 = _make_image("BC1", "main", "2024-01-01T00:00:00")
    bc1_v2 = _make_image("BC1", "main", "2024-02-01T00:00:00")
    bc2 = _make_image("BC2", "main", "2024-05-01T00:00:00")
    sess.add_all([bc1_v1, bc1_v2, bc2])
    sess.add(_make_version("BC1", "main", "2024-02-01T00:00:00", is_latest=True))
    sess.commit()

    result = filter_to_single_version(
        [bc1_v1, bc1_v2, bc2], ["BC1", "BC2"], sess
    )
    assert len(result) == 2


# ===================================================================
# _is_sqlite_locked  tests
# ===================================================================


def test_is_sqlite_locked_with_errorcode():
    from versioning import _is_sqlite_locked

    orig = sqlite3.OperationalError("database is locked")
    # Simulate what sqlite3 does on Python ≥3.11
    orig.sqlite_errorcode = 5
    exc = Exception()
    exc.orig = orig

    assert _is_sqlite_locked(exc) is True


def test_is_sqlite_locked_with_errorname():
    from versioning import _is_sqlite_locked

    orig = sqlite3.OperationalError("database is locked")
    orig.sqlite_errorname = "SQLITE_BUSY"
    exc = Exception()
    exc.orig = orig

    assert _is_sqlite_locked(exc) is True


def test_is_sqlite_locked_fallback_string():
    from versioning import _is_sqlite_locked

    exc = Exception("database is locked")
    assert _is_sqlite_locked(exc) is True


def test_is_sqlite_locked_other_error():
    from versioning import _is_sqlite_locked

    exc = Exception("no such table: foo")
    assert _is_sqlite_locked(exc) is False


def test_is_sqlite_locked_no_orig():
    from versioning import _is_sqlite_locked

    exc = RuntimeError("something else")
    assert _is_sqlite_locked(exc) is False


# ===================================================================
# update_versions_for_barcode  tests
# ===================================================================


def test_update_versions_creates_version_records(sess, monkeypatch):
    """Happy path: versions are created from active, confirmed images."""
    import versioning

    sr = ScanRoot(path="/fake", enabled=True)
    sess.add(sr)
    sess.commit()

    img = _make_image("BC1", "main", "2024-06-01T00:00:00", scan_root_id=sr.id)
    sess.add(img)
    sess.commit()

    monkeypatch.setattr(versioning, "session", sess)

    versioning.update_versions_for_barcode("BC1")

    vers = (
        sess.query(ImageVersion)
        .filter(ImageVersion.barcode == "BC1", ImageVersion.image_type == "main")
        .all()
    )
    assert len(vers) == 1
    assert vers[0].is_latest is True
    assert vers[0].version_label == "v1"


def test_update_versions_retry_on_locked(sess, monkeypatch):
    """When a locked error occurs, the operation is retried."""
    import versioning

    sr = ScanRoot(path="/fake", enabled=True)
    sess.add(sr)
    sess.commit()

    img = _make_image("BC1", "main", "2024-06-01T00:00:00", scan_root_id=sr.id)
    sess.add(img)
    sess.commit()

    call_count = [0]
    original = versioning._do_update_versions_for_barcode

    def flaky(barcode):
        call_count[0] += 1
        if call_count[0] < 3:
            exc = RuntimeError("database is locked")
            exc.orig = MagicMock()
            exc.orig.sqlite_errorcode = 5
            raise exc
        return original(barcode)

    monkeypatch.setattr(versioning, "_do_update_versions_for_barcode", flaky)
    monkeypatch.setattr(versioning, "session", sess)
    monkeypatch.setattr(versioning, "_SQLITE_RETRY_DELAY", 0)

    versioning.update_versions_for_barcode("BC1")

    assert call_count[0] == 3  # 2 failures + 1 success


def test_update_versions_non_locked_raises_immediately(sess, monkeypatch):
    """A non-lock error is not retried and propagates immediately."""
    import versioning

    sr = ScanRoot(path="/fake", enabled=True)
    sess.add(sr)
    sess.commit()

    img = _make_image("BC1", "main", "2024-06-01T00:00:00", scan_root_id=sr.id)
    sess.add(img)
    sess.commit()

    call_count = [0]

    def always_fail(barcode):
        call_count[0] += 1
        raise ValueError("something else")

    monkeypatch.setattr(versioning, "_do_update_versions_for_barcode", always_fail)
    monkeypatch.setattr(versioning, "session", sess)
    monkeypatch.setattr(versioning, "_SQLITE_RETRY_DELAY", 0)

    with pytest.raises(ValueError, match="something else"):
        versioning.update_versions_for_barcode("BC1")

    assert call_count[0] == 1  # no retries for non-lock errors

