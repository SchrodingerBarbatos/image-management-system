"""Integration tests for GTIN validation in the scan pipeline."""

import os
import pytest
from unittest.mock import patch
from sqlalchemy import create_engine, event
from sqlalchemy.orm import scoped_session, sessionmaker

from models import Base, Image, RejectedBarcode, ScanRoot


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


@pytest.fixture
def scan_dir(tmp_path):
    """Create a temp directory with test image files."""
    # Non-GTIN barcode (length 8)
    (tmp_path / "12345678_主图_1.jpg").write_bytes(b'\xff\xd8\xff\xe0' + b'\x00' * 100)
    # Non-GTIN barcode (length 11, wrong GTIN length)
    (tmp_path / "12345678901_主图_1.jpg").write_bytes(b'\xff\xd8\xff\xe0' + b'\x00' * 100)
    # Valid GTIN-13 barcode: 5901234123457
    (tmp_path / "5901234123457_主图_1.jpg").write_bytes(b'\xff\xd8\xff\xe0' + b'\x00' * 100)
    return tmp_path


def test_scan_creates_rejected_for_non_gtin(sess, scan_dir):
    """扫描时非 GTIN 条码应创建 RejectedBarcode 记录，不创建 Image 记录。"""
    sr = ScanRoot(path=str(scan_dir), enabled=True, recursive=False)
    sess.add(sr)
    sess.commit()

    import scanner
    import models

    # Mock session in scanner module
    original_session = models.session
    scanner.session = sess
    models.session = sess

    try:
        with patch('scanner.generate_thumbnail', return_value=(None, 'fake_md5')):
            result = scanner.scan_root(sr.id, full_scan=True)
    finally:
        scanner.session = original_session
        models.session = original_session

    # Should have 2 rejected (12345678 and 12345678901)
    assert result['rejected'] == 2, f"Expected 2 rejected, got {result['rejected']}"

    # Should have 1 added (valid GTIN-13)
    assert result['added'] == 1, f"Expected 1 added, got {result['added']}"

    # Verify RejectedBarcode records
    rejected = sess.query(RejectedBarcode).all()
    assert len(rejected) == 2
    rejected_barcodes = {r.barcode for r in rejected}
    assert '12345678' in rejected_barcodes
    assert '12345678901' in rejected_barcodes

    # Verify no Image records for rejected barcodes
    images = sess.query(Image).all()
    assert len(images) == 1
    assert images[0].barcode == '5901234123457'


def test_scan_rescan_revalidates_existing_gtin(sess, scan_dir):
    """重扫时如果文件条码变为非 GTIN，应删除 Image 并创建 RejectedBarcode。"""
    sr = ScanRoot(path=str(scan_dir), enabled=True, recursive=False)
    sess.add(sr)
    sess.commit()

    import scanner
    import models

    original_session = models.session
    scanner.session = sess
    models.session = sess

    try:
        # First scan: all files processed
        with patch('scanner.generate_thumbnail', return_value=(None, 'fake_md5')):
            result1 = scanner.scan_root(sr.id, full_scan=True)

        assert result1['added'] == 1
        assert result1['rejected'] == 2

        # Now rename the valid GTIN file to a non-GTIN name
        old_path = str(scan_dir / "5901234123457_主图_1.jpg")
        new_path = str(scan_dir / "12345_主图_1.jpg")
        os.rename(old_path, new_path)

        # Second scan: revalidate
        with patch('scanner.generate_thumbnail', return_value=(None, 'new_md5')):
            result2 = scanner.scan_root(sr.id, full_scan=True)

        # The old Image should be deleted, new RejectedBarcode created
        assert result2['rejected'] >= 1

        images = sess.query(Image).all()
        assert len(images) == 0

        rejected = sess.query(RejectedBarcode).filter(
            RejectedBarcode.barcode == '12345'
        ).all()
        assert len(rejected) == 1
    finally:
        scanner.session = original_session
        models.session = original_session
