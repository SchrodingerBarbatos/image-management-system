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


def test_scan_refreshes_legacy_fingerprint_and_preserves_current_classification(
    sess, monkeypatch, tmp_path,
):
    import hashlib
    import os
    from PIL import Image as PillowImage
    import scanner

    monkeypatch.setattr(scanner, 'session', sess)
    monkeypatch.setattr('thumbnail.THUMBNAIL_DIR', str(tmp_path / 'thumbnails'))
    src = tmp_path / 'source'
    src.mkdir()
    path = src / '5901234123457_主图_1.png'
    fixed_second = 1_700_000_000 * 10**9

    def write_image(color, offset):
        PillowImage.new('RGB', (32, 32), color).save(path, compress_level=0)
        os.utime(path, ns=(fixed_second, fixed_second + offset))

    write_image('red', 100_000_000)
    root = ScanRoot(path=str(src), enabled=True, recursive=False)
    sess.add(root)
    sess.commit()
    first = scanner.scan_root(root.id, full_scan=True)
    assert first['added'] == 1
    row = sess.query(Image).one()
    row_id = row.id
    initial_size = row.file_size
    row.md5_hash = f'{initial_size}_{fixed_second // 10**9}'
    row.image_type = 'detail'
    row.confirmed = False
    sess.commit()

    migrated = scanner.scan_root(root.id, full_scan=False)
    row = sess.get(Image, row_id)
    assert migrated['added'] == 1
    assert row.md5_hash.startswith('v2:')
    previous_fingerprint = row.md5_hash
    previous_md5 = row.content_md5
    assert row.image_type == 'detail'
    assert row.confirmed is False
    assert scanner.scan_root(root.id, full_scan=False)['skipped'] == 1

    write_image('blue', 600_000_000)
    assert path.stat().st_size == initial_size
    assert int(path.stat().st_mtime) == fixed_second // 10**9
    changed = scanner.scan_root(root.id, full_scan=False)
    row = sess.get(Image, row_id)
    assert changed['added'] == 1
    assert row.md5_hash != previous_fingerprint
    assert row.content_md5 != previous_md5
    assert row.content_md5 == hashlib.md5(path.read_bytes()).hexdigest()
    assert row.image_type == 'detail'
    assert row.confirmed is False
    with PillowImage.open(tmp_path / 'thumbnails' / f'{row_id}.jpg') as thumbnail:
        red, _, blue = thumbnail.getpixel((100, 100))
        assert blue > 200 and red < 30


@pytest.mark.parametrize('stop_phase', ['before_thumbnail', 'after_thumbnail'])
def test_legacy_rewrite_cancel_resumes_thumbnail_hash_and_version(
    sess, monkeypatch, tmp_path, stop_phase,
):
    import hashlib
    from PIL import Image as PillowImage
    import scanner
    from models import ImageVersion
    from versioning import compute_content_hash, update_versions_for_barcode

    monkeypatch.setattr(scanner, 'session', sess)
    thumb_dir = tmp_path / 'thumbnails'
    monkeypatch.setattr('thumbnail.THUMBNAIL_DIR', str(thumb_dir))
    source_dir = tmp_path / 'source'
    source_dir.mkdir()
    barcode = '5901234123457'
    source = source_dir / f'{barcode}_主图_1.png'
    fixed_second = 1_700_000_000 * 10**9

    def write_image(color, offset):
        PillowImage.new('RGB', (32, 32), color).save(source, compress_level=0)
        os.utime(source, ns=(fixed_second, fixed_second + offset))

    write_image('red', 100_000_000)
    root = ScanRoot(path=str(source_dir), enabled=True, recursive=False)
    sess.add(root)
    sess.commit()
    root_id = root.id
    scanner.scan_root(root_id, full_scan=True)
    row = sess.query(Image).one()
    row_id = row.id
    old_digest = row.content_md5
    old_size = row.file_size
    old_fp = f'{old_size}_{fixed_second // 10**9}'
    row.md5_hash = old_fp
    row.image_type = 'detail'
    sess.commit()
    update_versions_for_barcode(barcode, sess=sess)
    old_version_hash = sess.query(ImageVersion).one().content_hash
    thumb_path = thumb_dir / f'{row_id}.jpg'
    old_thumbnail = thumb_path.read_bytes()

    write_image('blue', 600_000_000)
    assert source.stat().st_size == old_size
    assert int(source.stat().st_mtime) == fixed_second // 10**9
    expected_digest = hashlib.md5(source.read_bytes()).hexdigest()
    assert expected_digest != old_digest

    cancel_at_batch_start = iter([False, True])
    stop_requested = False

    def is_cancelled():
        if stop_phase == 'before_thumbnail':
            return next(cancel_at_batch_start)
        return stop_requested

    def progress(phase, **kwargs):
        nonlocal stop_requested
        if stop_phase == 'after_thumbnail' and phase == 'thumbnails':
            stop_requested = True

    with pytest.raises(scanner.ScanCancelled):
        scanner.scan_root(root_id, progress_callback=progress, is_cancelled=is_cancelled)
    cancelled = sess.get(Image, row_id)
    assert cancelled.md5_hash == old_fp and cancelled.content_md5 == old_digest
    assert sess.query(ImageVersion).one().content_hash == old_version_hash
    if stop_phase == 'before_thumbnail':
        assert thumb_path.read_bytes() == old_thumbnail

    resumed = scanner.scan_root(root_id)
    assert resumed['added'] == 1 and resumed['affected_barcodes'] == [barcode]
    refreshed = sess.get(Image, row_id)
    assert refreshed.md5_hash == scanner.file_fingerprint(source)
    assert refreshed.content_md5 == expected_digest
    assert refreshed.image_type == 'detail' and refreshed.confirmed
    for changed_barcode in resumed['affected_barcodes']:
        update_versions_for_barcode(changed_barcode, sess=sess)
    version = sess.query(ImageVersion).one()
    assert version.image_type == 'detail' and version.is_latest
    assert version.content_hash == compute_content_hash([refreshed]) != old_version_hash
    with PillowImage.open(thumb_path) as thumbnail:
        red, _, blue = thumbnail.getpixel((100, 100))
        assert blue > 200 and red < 30
    assert scanner.scan_root(root_id)['skipped'] == 1


@pytest.mark.parametrize('interruption', ['after_segment', 'version_error'])
def test_scan_worker_recovers_committed_changes_after_interruption(
    sess, monkeypatch, tmp_path, interruption,
):
    from PIL import Image as PillowImage
    import models
    import routes.scan as scan_route
    import scanner
    import versioning
    from models import ImageVersion, PendingScanVersion

    monkeypatch.setattr(scanner, 'session', sess)
    monkeypatch.setattr(scan_route, 'session', sess)
    monkeypatch.setattr(models, 'session', sess)
    monkeypatch.setattr(scanner, '_THUMB_BATCH_SIZE', 1)
    monkeypatch.setattr('thumbnail.THUMBNAIL_DIR', str(tmp_path / 'thumbnails'))
    monkeypatch.setattr(scan_route, 'count_image_files', lambda *args, **kwargs: 1)

    src = tmp_path / 'source'
    src.mkdir()
    second_dir = src / 'second'
    second_dir.mkdir()
    barcode = '5901234123457'
    path = src / f'{barcode}_主图_1.png'
    PillowImage.new('RGB', (32, 32), 'red').save(path, compress_level=0)
    fixed_second = 1_700_000_000 * 10**9
    os.utime(path, ns=(fixed_second, fixed_second + 100_000_000))
    root = ScanRoot(path=str(src), enabled=True, recursive=True)
    sess.add(root)
    sess.commit()
    root_id = root.id
    scanner.scan_root(root_id)
    initial = sess.get(PendingScanVersion, barcode)
    versioning.update_versions_for_barcode(
        barcode, sess=sess, pending_scan_revision=initial.revision)
    old_version_hash = sess.query(ImageVersion.content_hash).scalar()
    old_fingerprint = sess.query(Image.md5_hash).scalar()
    assert sess.get(PendingScanVersion, barcode) is None

    PillowImage.new('RGB', (32, 32), 'blue').save(path, compress_level=0)
    os.utime(path, ns=(fixed_second, fixed_second + 600_000_000))
    interrupted_id = f'test-{interruption}'
    retry_id = f'test-retry-{interruption}'

    def run_job(job_id):
        job = scan_route._make_scan_job([root_id])
        scan_route._scan_jobs[job_id] = job
        scan_route._run_scan([root_id], 'incremental', job_id=job_id)
        return job

    try:
        with monkeypatch.context() as interrupt:
            if interruption == 'after_segment':
                def cancel_after_first_directory(*args, **kwargs):
                    yield str(src), [second_dir.name], [path.name]
                    scan_route._scan_cancel_flags[interrupted_id] = True
                    yield str(second_dir), [], []
                interrupt.setattr(scanner.os, 'walk', cancel_after_first_directory)
            else:
                def fail_mid_rebuild(_images):
                    raise RuntimeError('simulated version rebuild failure')
                interrupt.setattr(versioning, 'compute_content_hash', fail_mid_rebuild)

            result = run_job(interrupted_id)
            assert result['status'] == (
                'cancelled' if interruption == 'after_segment' else 'error')
        assert sess.query(Image.md5_hash).scalar() != old_fingerprint
        assert sess.get(PendingScanVersion, barcode) is not None
        assert sess.query(ImageVersion.content_hash).scalar() == old_version_hash

        result = run_job(retry_id)
        assert result['status'] == 'done'
        assert result['skipped'] == 1 and result['added'] == 0
        assert sess.get(PendingScanVersion, barcode) is None
        assert sess.query(ImageVersion.content_hash).scalar() == (
            versioning.compute_content_hash(sess.query(Image).all()))
        assert sess.query(ImageVersion.content_hash).scalar() != old_version_hash
    finally:
        for job_id in (interrupted_id, retry_id):
            scan_route._scan_jobs.pop(job_id, None)
            scan_route._scan_cancel_flags.pop(job_id, None)
