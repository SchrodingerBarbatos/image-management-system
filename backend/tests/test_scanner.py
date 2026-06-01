"""Tests for scanner module — broken cleanup ordering, full/incremental scan."""

import os
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import scoped_session, sessionmaker

from models import Base, Image, ScanRoot, RejectedBarcode


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
        scanner, "generate_thumbnail", lambda img_id, path: (True, "md5", "phash123")
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
        folder_path="fake",
        scan_root_id=sr.id, status="active", confirmed=True,
    )
    broken = Image(
        barcode="BC2", image_type="main", sequence=1,
        filename="b.jpg", ext="jpg", file_path=broken_path,
        file_size=100, md5_hash="abc",
        folder_path="fake",
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
        scanner, "generate_thumbnail", lambda img_id, path: (True, "md5", "phash123")
    )

    sr = ScanRoot(path="fake", enabled=True, recursive=False)
    sess.add(sr)
    sess.commit()

    fpath = os.path.normpath("fake/missing.jpg")
    img = Image(
        barcode="BC1", image_type="main", sequence=1,
        filename="missing.jpg", ext="jpg", file_path=fpath,
        file_size=100, md5_hash="abc",
        folder_path="fake",
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


# ---------------------------------------------------------------------------
# GTIN validation during scan
# ---------------------------------------------------------------------------


def test_scan_rejects_invalid_gtin(sess, monkeypatch):
    """扫描时拒绝非 GTIN 格式的条码。"""
    import scanner

    monkeypatch.setattr(scanner, "session", sess)
    monkeypatch.setattr(
        scanner, "generate_thumbnail", lambda img_id, path: (True, "md5", "phash123")
    )

    sr = ScanRoot(path="fake", enabled=True, recursive=False)
    sess.add(sr)
    sess.commit()

    # Mock: 磁盘上有两个文件，一个有效 GTIN，一个无效
    def fake_walk(path):
        yield (path, [], ["4006381333931_主图_1.jpg", "12345_主图_1.jpg"])

    monkeypatch.setattr(scanner, "_walk_nonrecursive", fake_walk)
    monkeypatch.setattr(scanner, "file_fingerprint", lambda p: "100_123")
    monkeypatch.setattr(os.path, "getsize", lambda p: 100)

    result = scanner.scan_root(sr.id, full_scan=True)

    # 有效 GTIN 应该被添加
    assert result["added"] == 1

    # 无效 GTIN 应该被拒绝
    assert result["rejected"] == 1

    # 验证拒绝记录已创建
    rejected = sess.query(RejectedBarcode).all()
    assert len(rejected) == 1
    assert rejected[0].barcode == "12345"
    assert "长度" in rejected[0].reason

    # 验证无效 GTIN 没有创建 Image 记录
    images = sess.query(Image).all()
    assert len(images) == 1
    assert images[0].barcode == "4006381333931"


def test_scan_rejects_gtin_with_invalid_check_digit(sess, monkeypatch):
    """扫描时拒绝校验位错误的 GTIN。"""
    import scanner

    monkeypatch.setattr(scanner, "session", sess)
    monkeypatch.setattr(
        scanner, "generate_thumbnail", lambda img_id, path: (True, "md5", "phash123")
    )

    sr = ScanRoot(path="fake", enabled=True, recursive=False)
    sess.add(sr)
    sess.commit()

    # Mock: 磁盘上有一个校验位错误的 GTIN
    def fake_walk(path):
        yield (path, [], ["4006381333932_主图_1.jpg"])

    monkeypatch.setattr(scanner, "_walk_nonrecursive", fake_walk)
    monkeypatch.setattr(scanner, "file_fingerprint", lambda p: "100_123")
    monkeypatch.setattr(os.path, "getsize", lambda p: 100)

    result = scanner.scan_root(sr.id, full_scan=True)

    assert result["added"] == 0
    assert result["rejected"] == 1

    rejected = sess.query(RejectedBarcode).all()
    assert len(rejected) == 1
    assert rejected[0].barcode == "4006381333932"
    assert "校验位错误" in rejected[0].reason


# ---------------------------------------------------------------------------
# delete_scan_root: version cleanup
# ---------------------------------------------------------------------------


def test_delete_scan_root_cleans_orphan_versions(sess, monkeypatch):
    """删除扫描目录后，受影响 barcode 的 ImageVersion 应被清理。"""
    import routes.scan
    import versioning
    from models import ImageVersion

    monkeypatch.setattr(routes.scan, "session", sess)
    monkeypatch.setattr(versioning, "session", sess)

    sr = ScanRoot(path="fake", enabled=True, recursive=False)
    sess.add(sr)
    sess.commit()

    # Create images and versions for two barcodes
    for bc in ("BC1", "BC2"):
        img = Image(
            barcode=bc, image_type="main", sequence=1,
            filename=f"{bc}.jpg", ext="jpg",
            file_path=f"fake/{bc}.jpg",
            file_size=100, md5_hash="abc",
            scan_root_id=sr.id, status="active", confirmed=True,
        )
        sess.add(img)
    sess.commit()

    # Create version records
    for bc in ("BC1", "BC2"):
        v = ImageVersion(
            barcode=bc, image_type="main", version_label="v1",
            folder_ctime="2024-01-01T00:00:00", content_hash="hash1", is_latest=True,
        )
        sess.add(v)
    sess.commit()

    # Verify versions exist
    assert sess.query(ImageVersion).count() == 2

    # Delete the scan root
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(routes.scan.scan_bp, url_prefix='/api')
    with app.test_client() as client:
        resp = client.delete(f'/api/scan-roots/{sr.id}')
        assert resp.status_code == 200

    # Images should be deleted
    assert sess.query(Image).count() == 0

    # Versions should also be cleaned up (orphan versions removed)
    remaining_versions = sess.query(ImageVersion).count()
    assert remaining_versions == 0, f"Expected 0 orphan versions, got {remaining_versions}"
