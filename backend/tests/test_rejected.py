"""Tests for RejectedBarcode model."""

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import scoped_session, sessionmaker

from models import Base, RejectedBarcode, ScanRoot


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


def test_rejected_barcode_creation(sess):
    """测试创建 RejectedBarcode 记录。"""
    sr = ScanRoot(path="fake", enabled=True, recursive=False)
    sess.add(sr)
    sess.commit()

    rejected = RejectedBarcode(
        barcode="12345678",
        file_path="fake/12345678_主图_1.jpg",
        filename="12345678_主图_1.jpg",
        reason="长度 8 不符合 GTIN 要求（需要 8、12、13 或 14 位）",
        scan_root_id=sr.id,
    )
    sess.add(rejected)
    sess.commit()

    assert rejected.id is not None
    assert rejected.barcode == "12345678"
    assert rejected.file_path == "fake/12345678_主图_1.jpg"
    assert rejected.filename == "12345678_主图_1.jpg"
    assert "长度" in rejected.reason
    assert rejected.scan_root_id == sr.id
    assert rejected.created_at is not None


def test_rejected_barcode_query_by_barcode(sess):
    """测试按条码查询拒绝记录。"""
    sr = ScanRoot(path="fake", enabled=True, recursive=False)
    sess.add(sr)
    sess.commit()

    rejected1 = RejectedBarcode(
        barcode="12345678",
        file_path="fake/12345678_主图_1.jpg",
        filename="12345678_主图_1.jpg",
        reason="长度 8 不符合 GTIN 要求",
        scan_root_id=sr.id,
    )
    rejected2 = RejectedBarcode(
        barcode="12345678",
        file_path="fake/12345678_主图_2.jpg",
        filename="12345678_主图_2.jpg",
        reason="长度 8 不符合 GTIN 要求",
        scan_root_id=sr.id,
    )
    rejected3 = RejectedBarcode(
        barcode="87654321",
        file_path="fake/87654321_主图_1.jpg",
        filename="87654321_主图_1.jpg",
        reason="包含非数字字符",
        scan_root_id=sr.id,
    )
    sess.add_all([rejected1, rejected2, rejected3])
    sess.commit()

    results = sess.query(RejectedBarcode).filter(
        RejectedBarcode.barcode == "12345678"
    ).all()
    assert len(results) == 2


def test_rejected_barcode_query_by_scan_root(sess):
    """测试按扫描目录查询拒绝记录。"""
    sr1 = ScanRoot(path="fake1", enabled=True, recursive=False)
    sr2 = ScanRoot(path="fake2", enabled=True, recursive=False)
    sess.add_all([sr1, sr2])
    sess.commit()

    rejected1 = RejectedBarcode(
        barcode="12345678",
        file_path="fake1/12345678_主图_1.jpg",
        filename="12345678_主图_1.jpg",
        reason="长度 8 不符合 GTIN 要求",
        scan_root_id=sr1.id,
    )
    rejected2 = RejectedBarcode(
        barcode="87654321",
        file_path="fake2/87654321_主图_1.jpg",
        filename="87654321_主图_1.jpg",
        reason="包含非数字字符",
        scan_root_id=sr2.id,
    )
    sess.add_all([rejected1, rejected2])
    sess.commit()

    results = sess.query(RejectedBarcode).filter(
        RejectedBarcode.scan_root_id == sr1.id
    ).all()
    assert len(results) == 1
    assert results[0].barcode == "12345678"
