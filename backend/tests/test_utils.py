"""Tests for routes/_utils.py — pagination parsing and safe file removal."""

import os
import tempfile
import pytest
from unittest.mock import MagicMock
from sqlalchemy import create_engine, event
from sqlalchemy.orm import scoped_session, sessionmaker

from models import Base, ScanRoot, Image


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


# ---------------------------------------------------------------------------
# parse_pagination tests
# ---------------------------------------------------------------------------


def _flask_app():
    """Create a minimal Flask app for request context."""
    from flask import Flask
    app = Flask(__name__)
    app.config['TESTING'] = True
    return app


def test_parse_pagination_defaults():
    from routes._utils import parse_pagination
    app = _flask_app()
    with app.test_request_context('/?page=1&page_size=50'):
        page, page_size = parse_pagination()
        assert page == 1
        assert page_size == 50


def test_parse_pagination_custom_defaults():
    from routes._utils import parse_pagination
    app = _flask_app()
    with app.test_request_context('/?page=3&page_size=100'):
        page, page_size = parse_pagination(default_page_size=20, max_page_size=500)
        assert page == 3
        assert page_size == 100


def test_parse_pagination_uses_defaults_when_missing():
    from routes._utils import parse_pagination
    app = _flask_app()
    with app.test_request_context('/'):
        page, page_size = parse_pagination(default_page_size=25)
        assert page == 1
        assert page_size == 25


def test_parse_pagination_rejects_non_numeric_page():
    from routes._utils import parse_pagination
    app = _flask_app()
    with app.test_request_context('/?page=abc'):
        with pytest.raises(ValueError, match='page'):
            parse_pagination()


def test_parse_pagination_rejects_non_numeric_page_size():
    from routes._utils import parse_pagination
    app = _flask_app()
    with app.test_request_context('/?page=1&page_size=xyz'):
        with pytest.raises(ValueError, match='page_size'):
            parse_pagination()


def test_parse_pagination_rejects_page_zero():
    from routes._utils import parse_pagination
    app = _flask_app()
    with app.test_request_context('/?page=0'):
        with pytest.raises(ValueError, match='page 必须 >= 1'):
            parse_pagination()


def test_parse_pagination_rejects_negative_page():
    from routes._utils import parse_pagination
    app = _flask_app()
    with app.test_request_context('/?page=-5'):
        with pytest.raises(ValueError, match='page 必须 >= 1'):
            parse_pagination()


def test_parse_pagination_rejects_page_size_zero():
    from routes._utils import parse_pagination
    app = _flask_app()
    with app.test_request_context('/?page=1&page_size=0'):
        with pytest.raises(ValueError, match='page_size'):
            parse_pagination()


def test_parse_pagination_rejects_page_size_too_large():
    from routes._utils import parse_pagination
    app = _flask_app()
    with app.test_request_context('/?page=1&page_size=999'):
        with pytest.raises(ValueError, match='page_size'):
            parse_pagination()


def test_parse_pagination_accepts_max_page_size():
    from routes._utils import parse_pagination
    app = _flask_app()
    with app.test_request_context('/?page=1&page_size=500'):
        page, page_size = parse_pagination()
        assert page_size == 500


def test_parse_pagination_accepts_page_size_one():
    from routes._utils import parse_pagination
    app = _flask_app()
    with app.test_request_context('/?page=1&page_size=1'):
        page, page_size = parse_pagination()
        assert page_size == 1


def test_parse_pagination_float_rejected():
    from routes._utils import parse_pagination
    app = _flask_app()
    with app.test_request_context('/?page=1.5'):
        with pytest.raises(ValueError, match='page'):
            parse_pagination()


# ---------------------------------------------------------------------------
# safe_remove_image_file tests
# ---------------------------------------------------------------------------


def _make_root(sess, root_id, path, enabled=True):
    sr = ScanRoot(id=root_id, path=path, enabled=enabled, recursive=False)
    sess.add(sr)
    sess.commit()
    return sr


def _make_image(sess, img_id, file_path, scan_root_id):
    img = Image(
        id=img_id, barcode='BC', image_type='main', folder_ctime='2024-01-01T00:00:00',
        filename='test.jpg', ext='jpg', file_path=file_path, file_size=100,
        md5_hash='abc', content_md5='abc', confirmed=True, status='active',
        scan_root_id=scan_root_id, sequence=1,
    )
    sess.add(img)
    sess.commit()
    return img


def test_safe_remove_deletes_file_inside_root(sess, tmp_path):
    """File under scan root is deleted successfully."""
    from routes._utils import safe_remove_image_file

    root_dir = tmp_path / "photos"
    root_dir.mkdir()
    test_file = root_dir / "image.jpg"
    test_file.write_text("data")

    _make_root(sess, root_id=1, path=str(root_dir))
    img = _make_image(sess, img_id=1, file_path=str(test_file), scan_root_id=1)

    ok, reason = safe_remove_image_file(img, sess)
    assert ok is True
    assert reason is None
    assert not test_file.exists()


def test_safe_remove_refuses_file_outside_root(sess, tmp_path):
    """File outside scan root is NOT deleted."""
    from routes._utils import safe_remove_image_file

    root_dir = tmp_path / "photos"
    root_dir.mkdir()
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    victim = other_dir / "secret.txt"
    victim.write_text("important")

    _make_root(sess, root_id=1, path=str(root_dir))
    img = _make_image(sess, img_id=1, file_path=str(victim), scan_root_id=1)

    ok, reason = safe_remove_image_file(img, sess)
    assert ok is False
    assert '不在' in reason or '驱动器' in reason
    assert victim.exists()  # file NOT deleted


def test_safe_remove_returns_true_for_missing_file(sess, tmp_path):
    """If file is already gone, returns True (not an error)."""
    from routes._utils import safe_remove_image_file

    root_dir = tmp_path / "photos"
    root_dir.mkdir()
    missing = root_dir / "gone.jpg"

    _make_root(sess, root_id=1, path=str(root_dir))
    img = _make_image(sess, img_id=1, file_path=str(missing), scan_root_id=1)

    ok, reason = safe_remove_image_file(img, sess)
    assert ok is True
    assert reason is None


def test_safe_remove_refuses_when_root_not_found(sess, tmp_path):
    """If scan root doesn't exist in DB, refuse to delete."""
    from routes._utils import safe_remove_image_file

    test_file = tmp_path / "orphan.jpg"
    test_file.write_text("data")

    img = _make_image(sess, img_id=1, file_path=str(test_file), scan_root_id=999)

    ok, reason = safe_remove_image_file(img, sess)
    assert ok is False
    assert '找不到' in reason
    assert test_file.exists()
