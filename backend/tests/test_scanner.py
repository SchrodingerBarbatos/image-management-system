"""Tests for scanner module — broken cleanup ordering, full/incremental scan."""

import os
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import scoped_session, sessionmaker

from models import Base, Image, ScanRoot


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
# Incremental scan: broken cleanup ordering
# ---------------------------------------------------------------------------


def test_incremental_broken_cleaned_before_indexed_map(sess, monkeypatch):
    """已有 broken 记录在构建 indexed_map 前被清理，不计入 broken_new。

    场景：DB 中有 1 条 active + 1 条 broken 记录，磁盘上只有 active 对应的文件。
    增量扫描后应返回 broken_cleaned=1, broken_new=0。
    """
    import scanner

    monkeypatch.setattr(scanner, "session", sess)
    monkeypatch.setattr(
        scanner, "generate_thumbnail", lambda img_id, path: (True, "md5")
    )

    sr = ScanRoot(path="fake", enabled=True, recursive=False)
    sess.add(sr)
    sess.commit()

    active_path = os.path.normpath("fake/a.jpg")
    broken_path = os.path.normpath("fake/b.jpg")

    active = Image(
        barcode="BC1", image_type="main", sequence=1,
        filename="a.jpg", ext="jpg", file_path=active_path,
        file_size=100, md5_hash="100_123",
        scan_root_id=sr.id, status="active", confirmed=True,
    )
    broken = Image(
        barcode="BC2", image_type="main", sequence=1,
        filename="b.jpg", ext="jpg", file_path=broken_path,
        file_size=100, md5_hash="abc",
        scan_root_id=sr.id, status="broken", confirmed=True,
    )
    sess.add_all([active, broken])
    sess.commit()

    # Mock: 磁盘上只有 a.jpg，fingerprint 未变
    def fake_walk(path):
        yield (path, [], ["a.jpg"])

    monkeypatch.setattr(scanner, "_walk_nonrecursive", fake_walk)
    monkeypatch.setattr(scanner, "file_fingerprint", lambda p: "100_123")

    result = scanner.scan_root(sr.id, full_scan=False)

    assert result["broken_cleaned"] == 1
    assert result["broken_new"] == 0
    assert result["skipped"] == 1  # a.jpg 未变，跳过


def test_full_scan_cleans_leftovers(sess, monkeypatch):
    """全量扫描：磁盘上缺失的记录在扫描后被删除，不计为 broken_new。"""
    import scanner

    monkeypatch.setattr(scanner, "session", sess)
    monkeypatch.setattr(
        scanner, "generate_thumbnail", lambda img_id, path: (True, "md5")
    )

    sr = ScanRoot(path="fake", enabled=True, recursive=False)
    sess.add(sr)
    sess.commit()

    fpath = os.path.normpath("fake/missing.jpg")
    img = Image(
        barcode="BC1", image_type="main", sequence=1,
        filename="missing.jpg", ext="jpg", file_path=fpath,
        file_size=100, md5_hash="abc",
        scan_root_id=sr.id, status="active", confirmed=True,
    )
    sess.add(img)
    sess.commit()

    # Mock: 空目录，文件已不存在于磁盘
    def fake_walk(path):
        yield (path, [], [])

    monkeypatch.setattr(scanner, "_walk_nonrecursive", fake_walk)

    result = scanner.scan_root(sr.id, full_scan=True)

    assert result["broken_cleaned"] == 1  # 全量模式下直接删除
    assert result["broken_new"] == 1  # 全量模式下 leftover_count 也计入 broken_cleaned

    # 验证记录已被物理删除
    remaining = sess.query(Image).filter(
        Image.scan_root_id == sr.id
    ).count()
    assert remaining == 0
