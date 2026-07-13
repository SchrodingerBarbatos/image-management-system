"""Path confinement helpers — commonpath based, not startswith."""

import os

import pytest

from routes._utils import is_path_under_root


def test_path_under_root_accepts_child(tmp_path):
    root = tmp_path / "photos"
    root.mkdir()
    child = root / "a" / "b.jpg"
    child.parent.mkdir()
    child.write_bytes(b"x")
    ok, reason = is_path_under_root(str(child), str(root))
    assert ok is True
    assert reason is None


def test_path_under_root_rejects_sibling_prefix(tmp_path):
    """'/data/photos2' must NOT be considered under '/data/photos'."""
    root = tmp_path / "photos"
    root.mkdir()
    sibling = tmp_path / "photos2"
    sibling.mkdir()
    evil = sibling / "x.jpg"
    evil.write_bytes(b"x")
    ok, reason = is_path_under_root(str(evil), str(root))
    assert ok is False
    assert reason


def test_path_under_root_rejects_outside(tmp_path):
    root = tmp_path / "photos"
    root.mkdir()
    outside = tmp_path / "other" / "x.jpg"
    outside.parent.mkdir()
    outside.write_bytes(b"x")
    ok, _ = is_path_under_root(str(outside), str(root))
    assert ok is False


def test_path_under_root_accepts_exact_root_file(tmp_path):
    root = tmp_path / "photos"
    root.mkdir()
    f = root / "x.jpg"
    f.write_bytes(b"x")
    ok, _ = is_path_under_root(str(f), str(root))
    assert ok is True


@pytest.mark.skipif(os.name != "nt", reason="Windows-only drive check")
def test_path_under_root_different_drives_returns_false():
    # If only one drive exists this still shouldn't crash
    ok, reason = is_path_under_root("D:\\a\\b.jpg", "C:\\root")
    assert ok is False
    assert reason
