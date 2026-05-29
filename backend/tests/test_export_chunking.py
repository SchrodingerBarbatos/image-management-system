"""Tests for export module — chunked IN query and barcode dedup."""

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import scoped_session, sessionmaker

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


def _make_image(barcode, image_type="main", folder_ctime="2024-01-01T00:00:00",
                filename="a.jpg", scan_root_id=1, **kw):
    defaults = dict(
        barcode=barcode, image_type=image_type, folder_ctime=folder_ctime,
        filename=filename, ext="jpg",
        file_path=f"/fake/{barcode}/{folder_ctime}/{filename}",
        file_size=100, md5_hash="abc", content_md5="abc",
        confirmed=True, status="active", scan_root_id=scan_root_id, sequence=1,
    )
    defaults.update(kw)
    return Image(**defaults)


# ---------------------------------------------------------------------------
# Chunked IN query
# ---------------------------------------------------------------------------


def test_chunked_in_query_returns_all_matching(sess):
    """All matching rows should be returned regardless of chunk size."""
    from routes.export import _chunked_in_query

    sr = ScanRoot(path="/fake", enabled=True)
    sess.add(sr)
    sess.commit()

    for i in range(10):
        sess.add(_make_image(f"BC{i:03d}", scan_root_id=sr.id))
    sess.commit()

    barcodes = [f"BC{i:03d}" for i in range(10)]
    results = _chunked_in_query(
        Image.barcode, barcodes,
        sess.query(Image.barcode, Image.id),
        chunk_size=3,  # Force multiple chunks
    )
    assert len(results) == 10


def test_chunked_in_query_empty_values(sess):
    """Empty values list should return empty results."""
    from routes.export import _chunked_in_query

    results = _chunked_in_query(
        Image.barcode, [],
        sess.query(Image.barcode, Image.id),
    )
    assert results == []


def test_chunked_in_query_no_matches(sess):
    """Non-matching barcodes should return empty results."""
    from routes.export import _chunked_in_query

    sr = ScanRoot(path="/fake", enabled=True)
    sess.add(sr)
    sess.commit()
    sess.add(_make_image("BC001", scan_root_id=sr.id))
    sess.commit()

    results = _chunked_in_query(
        Image.barcode, ["NONEXISTENT"],
        sess.query(Image.barcode, Image.id),
    )
    assert len(results) == 0


def test_chunked_in_query_single_chunk(sess):
    """When values fit in one chunk, behavior should be identical to non-chunked."""
    from routes.export import _chunked_in_query

    sr = ScanRoot(path="/fake", enabled=True)
    sess.add(sr)
    sess.commit()

    sess.add(_make_image("BC001", scan_root_id=sr.id))
    sess.add(_make_image("BC002", scan_root_id=sr.id))
    sess.commit()

    results = _chunked_in_query(
        Image.barcode, ["BC001", "BC002"],
        sess.query(Image.barcode, Image.id),
        chunk_size=500,
    )
    assert len(results) == 2


# ---------------------------------------------------------------------------
# Barcode dedup in generate_zip
# ---------------------------------------------------------------------------


def test_barcode_dedup_preserves_order():
    """dict.fromkeys dedup should preserve first-occurrence order."""
    barcodes_raw = ["BC1", "BC2", "BC1", "BC3", "BC2"]
    deduped = list(dict.fromkeys(barcodes_raw))
    assert deduped == ["BC1", "BC2", "BC3"]


def test_barcode_dedup_single_occurrence():
    """Already-unique barcodes should pass through unchanged."""
    barcodes_raw = ["BC1", "BC2", "BC3"]
    deduped = list(dict.fromkeys(barcodes_raw))
    assert deduped == ["BC1", "BC2", "BC3"]


def test_barcode_dedup_all_same():
    """All-same barcodes should reduce to one."""
    barcodes_raw = ["BC1", "BC1", "BC1"]
    deduped = list(dict.fromkeys(barcodes_raw))
    assert deduped == ["BC1"]


def test_filter_to_single_version_chunked(sess):
    """filter_to_single_version should work correctly with many barcodes
    (exercises the chunked IN query path)."""
    from routes.export import filter_to_single_version

    sr = ScanRoot(path="/fake", enabled=True)
    sess.add(sr)
    sess.commit()

    imgs = []
    for i in range(20):
        img = _make_image(f"BC{i:03d}", scan_root_id=sr.id)
        sess.add(img)
        imgs.append(img)

    # Set is_latest for first 10 barcodes
    for i in range(10):
        sess.add(ImageVersion(
            barcode=f"BC{i:03d}", image_type="main",
            version_label="v1", folder_ctime="2024-01-01T00:00:00",
            content_hash="hash1", is_latest=True,
        ))
    sess.commit()

    barcodes = [f"BC{i:03d}" for i in range(20)]
    result = filter_to_single_version(imgs, barcodes, sess)

    # First 10 barcodes: filtered to matching ctime (all have same ctime)
    # Last 10 barcodes: no version data → pass through
    assert len(result) == 20  # All match because ctimes align
