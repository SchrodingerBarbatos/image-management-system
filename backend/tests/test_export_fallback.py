"""Tests for detail-image fallback logic in ZIP export.

When exporting images, barcodes that have no detail images but do have main
images get ALL their main images written as detail images in the ZIP.
Matching statistics (barcode_counts) must remain truthful — detail count
stays at 0 for those barcodes.
"""

import json
import os
import tempfile
import zipfile

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import scoped_session, sessionmaker

import models
from models import Base, ExportTask, Image, ScanRoot


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db():
    """In-memory SQLite engine + session. Patches models.session so that
    _build_zip (which does ``from models import session``) sees the same DB."""
    eng = create_engine("sqlite:///:memory:", echo=False)

    @event.listens_for(eng, "connect")
    def _(conn, _):
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")

    Base.metadata.create_all(bind=eng)
    factory = sessionmaker(bind=eng)
    sess = scoped_session(factory)

    original = models.session
    models.session = sess
    try:
        yield sess
    finally:
        models.session = original
        sess.remove()


@pytest.fixture()
def images_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


def _create_image_file(directory, filename):
    """Create a tiny file on disk that zip can write."""
    path = os.path.join(directory, filename)
    with open(path, "wb") as f:
        f.write(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
    return path


def _make_task(session):
    task = ExportTask(status="processing", barcode_data="{}")
    session.add(task)
    session.commit()
    return task


def _run_build_zip(task_id, img_data, flat, upload_dir=None):
    """Call _build_zip with UPLOAD_DIR temporarily overridden."""
    import routes.export as exp_mod
    old = exp_mod.UPLOAD_DIR
    exp_mod.UPLOAD_DIR = upload_dir
    try:
        from routes.export import _build_zip
        _build_zip(task_id, img_data, flat)
    finally:
        exp_mod.UPLOAD_DIR = old


def _zip_names(zip_path):
    with zipfile.ZipFile(zip_path, "r") as zf:
        return zf.namelist()


# ---------------------------------------------------------------------------
# Scenario 1: barcode has only main images → ALL written as detail in ZIP
# ---------------------------------------------------------------------------

def test_fallback_when_no_detail_images(db, images_dir):
    """A barcode with main images but no detail images should get ALL main
    images written as detail images in the ZIP."""
    sr = ScanRoot(path="/fake", enabled=True)
    db.add(sr); db.commit()

    f1 = _create_image_file(images_dir, "A_1.jpg")
    f2 = _create_image_file(images_dir, "A_2.jpg")
    f3 = _create_image_file(images_dir, "A_3.jpg")

    # img_data: only main images (as if image_type='all' and no detail images exist)
    img_data = [
        (f1, "A", "main", 1, "jpg"),
        (f2, "A", "main", 2, "jpg"),
        (f3, "A", "main", 3, "jpg"),
    ]

    task = _make_task(db)
    task_id = task.id
    _run_build_zip(task_id, img_data, flat=False, upload_dir=images_dir)

    names = _zip_names(os.path.join(images_dir, f"export_{task_id}.zip"))
    # All 3 main images should appear as detail images
    assert len(names) == 3
    detail_names = sorted([n for n in names if "详情图" in n])
    assert detail_names == [
        "详情图/A_详情图_1.jpg",
        "详情图/A_详情图_2.jpg",
        "详情图/A_详情图_3.jpg",
    ]

    db.expire_all()
    assert db.get(ExportTask, task_id).status == "done"


# ---------------------------------------------------------------------------
# Scenario 2: barcode already has detail images → no fallback
# ---------------------------------------------------------------------------

def test_no_fallback_when_detail_images_exist(db, images_dir):
    """When a barcode already has detail images, fallback should NOT fire."""
    sr = ScanRoot(path="/fake", enabled=True)
    db.add(sr); db.commit()

    file_d1 = _create_image_file(images_dir, "B_detail_1.jpg")
    file_d2 = _create_image_file(images_dir, "B_detail_2.jpg")
    file_m = _create_image_file(images_dir, "B_main.jpg")

    img_data = [
        (file_d1, "B", "detail", 1, "jpg"),
        (file_d2, "B", "detail", 2, "jpg"),
        (file_m, "B", "main", 1, "jpg"),
    ]

    task = _make_task(db)
    task_id = task.id
    _run_build_zip(task_id, img_data, flat=False, upload_dir=images_dir)

    names = _zip_names(os.path.join(images_dir, f"export_{task_id}.zip"))
    # 2 real detail images + 1 main image (as main, not fallback)
    assert len(names) == 3
    assert any("B_详情图_1.jpg" in n for n in names)
    assert any("B_详情图_2.jpg" in n for n in names)
    assert any("B_1.jpg" in n and "详情图" not in n for n in names)


# ---------------------------------------------------------------------------
# Scenario 3: mixed barcodes — some need fallback, some don't
# ---------------------------------------------------------------------------

def test_mixed_barcodes_partial_fallback(db, images_dir):
    """In a batch with multiple barcodes, only those missing detail images
    should get the fallback."""
    sr = ScanRoot(path="/fake", enabled=True)
    db.add(sr); db.commit()

    f_a_detail = _create_image_file(images_dir, "A_detail.jpg")
    f_b_main = _create_image_file(images_dir, "B_main.jpg")

    img_data = [
        (f_a_detail, "A", "detail", 1, "jpg"),
        (f_b_main, "B", "main", 1, "jpg"),
    ]

    task = _make_task(db)
    task_id = task.id
    _run_build_zip(task_id, img_data, flat=False, upload_dir=images_dir)

    names = _zip_names(os.path.join(images_dir, f"export_{task_id}.zip"))
    assert len(names) == 2
    assert any("A_详情图_1.jpg" in n for n in names)
    # B's main image should appear as detail (fallback)
    assert any("B_详情图_1.jpg" in n for n in names)


# ---------------------------------------------------------------------------
# Scenario 4: flat mode — fallback files at ZIP root
# ---------------------------------------------------------------------------

def test_fallback_flat_mode(db, images_dir):
    """In flat mode, fallback detail images should be at ZIP root."""
    sr = ScanRoot(path="/fake", enabled=True)
    db.add(sr); db.commit()

    f1 = _create_image_file(images_dir, "E_1.jpg")
    f2 = _create_image_file(images_dir, "E_2.jpg")

    img_data = [
        (f1, "E", "main", 1, "jpg"),
        (f2, "E", "main", 2, "jpg"),
    ]

    task = _make_task(db)
    task_id = task.id
    _run_build_zip(task_id, img_data, flat=True, upload_dir=images_dir)

    names = _zip_names(os.path.join(images_dir, f"export_{task_id}.zip"))
    assert len(names) == 2
    assert sorted(names) == ["E_详情图_1.jpg", "E_详情图_2.jpg"]


# ---------------------------------------------------------------------------
# Scenario 5: main image file deleted from disk → skipped gracefully
# ---------------------------------------------------------------------------

def test_fallback_skips_missing_files(db, images_dir):
    """Fallback should skip main images whose files no longer exist on disk."""
    sr = ScanRoot(path="/fake", enabled=True)
    db.add(sr); db.commit()

    nonexistent = os.path.join(images_dir, "DELETED.jpg")
    img_data = [(nonexistent, "D", "main", 1, "jpg")]

    task = _make_task(db)
    task_id = task.id
    _run_build_zip(task_id, img_data, flat=False, upload_dir=images_dir)

    names = _zip_names(os.path.join(images_dir, f"export_{task_id}.zip"))
    assert len(names) == 0

    db.expire_all()
    assert db.get(ExportTask, task_id).status == "failed"


# ---------------------------------------------------------------------------
# Scenario 6: barcode with both types — detail takes priority
# ---------------------------------------------------------------------------

def test_detail_takes_priority_over_main(db, images_dir):
    """When a barcode has both detail and main images, real detail images
    are exported (not the main images as fallback)."""
    sr = ScanRoot(path="/fake", enabled=True)
    db.add(sr); db.commit()

    f_d = _create_image_file(images_dir, "F_detail.jpg")
    f_m = _create_image_file(images_dir, "F_main.jpg")

    img_data = [
        (f_d, "F", "detail", 1, "jpg"),
        (f_m, "F", "main", 1, "jpg"),
    ]

    task = _make_task(db)
    task_id = task.id
    _run_build_zip(task_id, img_data, flat=False, upload_dir=images_dir)

    names = _zip_names(os.path.join(images_dir, f"export_{task_id}.zip"))
    assert len(names) == 2
    # Detail image appears as detail
    assert any("F_详情图_1.jpg" in n for n in names)
    # Main image appears as main (NOT as fallback detail)
    assert any("F_1.jpg" in n and "详情图" not in n for n in names)


# ---------------------------------------------------------------------------
# Scenario 7: statistics are NOT modified by fallback
# ---------------------------------------------------------------------------

def test_barcode_counts_not_affected_by_fallback():
    """_compute_barcode_counts should return truthful counts regardless of
    whether fallback images were added to the ZIP."""
    from routes.export import _compute_barcode_counts

    class FakeImg:
        def __init__(self, barcode, image_type):
            self.barcode = barcode
            self.image_type = image_type

    imgs = [FakeImg("A", "main")] * 3 + [FakeImg("B", "detail")] * 2
    counts = _compute_barcode_counts(imgs, ["A", "B"])

    assert counts["A"]["main"] == 3
    assert counts["A"]["detail"] == 0  # truthful: no detail images exist
    assert counts["B"]["main"] == 0
    assert counts["B"]["detail"] == 2


# ---------------------------------------------------------------------------
# Scenario 8: empty img_data → empty ZIP
# ---------------------------------------------------------------------------

def test_empty_img_data_creates_empty_zip(db, images_dir):
    """When img_data is empty, an empty ZIP should be created."""
    sr = ScanRoot(path="/fake", enabled=True)
    db.add(sr); db.commit()

    task = _make_task(db)
    task_id = task.id
    _run_build_zip(task_id, [], flat=False, upload_dir=images_dir)

    names = _zip_names(os.path.join(images_dir, f"export_{task_id}.zip"))
    assert len(names) == 0

    db.expire_all()
    assert db.get(ExportTask, task_id).status == "done"
