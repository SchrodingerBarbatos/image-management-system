"""Tests for detail-image fallback logic in ZIP export.

When exporting detail images, barcodes that have no detail images but do have
main images should get one main image written as a fallback detail image in the
ZIP. Matching statistics (barcode_counts) must remain truthful — detail count
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


def _run_build_zip(task_id, img_data, flat, main_img_data=None, upload_dir=None):
    """Call _build_zip with UPLOAD_DIR temporarily overridden."""
    import routes.export as exp_mod
    old = exp_mod.UPLOAD_DIR
    exp_mod.UPLOAD_DIR = upload_dir
    try:
        from routes.export import _build_zip
        _build_zip(task_id, img_data, flat, main_img_data=main_img_data)
    finally:
        exp_mod.UPLOAD_DIR = old


# ---------------------------------------------------------------------------
# Scenario 1: barcode has only main images → fallback detail image in ZIP
# ---------------------------------------------------------------------------

def test_fallback_when_no_detail_images(db, images_dir):
    """A barcode with main images but no detail images should get one main
    image written as a fallback detail image in the ZIP."""
    sr = ScanRoot(path="/fake", enabled=True)
    db.add(sr); db.commit()

    file_a = _create_image_file(images_dir, "A_main.jpg")
    img_data = [(file_a, "A", "main", 1, "jpg")]
    main_img_data = [(file_a, "A", "main", 1, "jpg")]

    task = _make_task(db)
    task_id = task.id
    _run_build_zip(task_id, img_data, flat=False, main_img_data=main_img_data,
                   upload_dir=images_dir)

    zip_path = os.path.join(images_dir, f"export_{task_id}.zip")
    assert os.path.exists(zip_path)
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        assert any("详情图" in n for n in names), f"Expected detail folder, got: {names}"
        assert any("A_详情图_1.jpg" in n for n in names), f"Expected fallback file, got: {names}"

    db.expire_all()
    assert db.get(ExportTask, task_id).status == "done"


# ---------------------------------------------------------------------------
# Scenario 2: barcode already has detail images → no fallback
# ---------------------------------------------------------------------------

def test_no_fallback_when_detail_images_exist(db, images_dir):
    """When a barcode already has detail images, fallback should NOT fire."""
    sr = ScanRoot(path="/fake", enabled=True)
    db.add(sr); db.commit()

    file_d = _create_image_file(images_dir, "B_detail.jpg")
    file_m = _create_image_file(images_dir, "B_main.jpg")
    img_data = [(file_d, "B", "detail", 1, "jpg")]
    main_img_data = [(file_m, "B", "main", 1, "jpg")]

    task = _make_task(db)
    task_id = task.id
    _run_build_zip(task_id, img_data, flat=False, main_img_data=main_img_data,
                   upload_dir=images_dir)

    zip_path = os.path.join(images_dir, f"export_{task_id}.zip")
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        assert len(names) == 1, f"Expected exactly 1 file, got: {names}"
        assert "B_详情图_1.jpg" in names[0]


# ---------------------------------------------------------------------------
# Scenario 3: mixed barcodes — some need fallback, some don't
# ---------------------------------------------------------------------------

def test_mixed_barcodes_partial_fallback(db, images_dir):
    """In a batch with multiple barcodes, only those missing detail images
    should get the fallback."""
    sr = ScanRoot(path="/fake", enabled=True)
    db.add(sr); db.commit()

    file_a = _create_image_file(images_dir, "A_detail.jpg")
    file_b = _create_image_file(images_dir, "B_main.jpg")
    img_data = [(file_a, "A", "detail", 1, "jpg")]
    main_img_data = [(file_b, "B", "main", 1, "jpg")]

    task = _make_task(db)
    task_id = task.id
    _run_build_zip(task_id, img_data, flat=False, main_img_data=main_img_data,
                   upload_dir=images_dir)

    zip_path = os.path.join(images_dir, f"export_{task_id}.zip")
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        assert len(names) == 2, f"Expected 2 files, got: {names}"
        assert any("A_详情图_1.jpg" in n for n in names)
        assert any("B_详情图_1.jpg" in n for n in names)


# ---------------------------------------------------------------------------
# Scenario 4: no main_img_data → normal behavior, no fallback
# ---------------------------------------------------------------------------

def test_no_fallback_when_main_img_data_is_none(db, images_dir):
    """When main_img_data is None (exporting main images or all), no fallback."""
    sr = ScanRoot(path="/fake", enabled=True)
    db.add(sr); db.commit()

    file_m = _create_image_file(images_dir, "C_main.jpg")
    img_data = [(file_m, "C", "main", 1, "jpg")]

    task = _make_task(db)
    task_id = task.id
    _run_build_zip(task_id, img_data, flat=False, main_img_data=None,
                   upload_dir=images_dir)

    zip_path = os.path.join(images_dir, f"export_{task_id}.zip")
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        assert len(names) == 1
        assert "C_1.jpg" in names[0]
        assert "详情图" not in names[0]


# ---------------------------------------------------------------------------
# Scenario 5: main image file deleted from disk → fallback skipped
# ---------------------------------------------------------------------------

def test_fallback_skips_missing_files(db, images_dir):
    """Fallback should skip main images whose files no longer exist on disk."""
    sr = ScanRoot(path="/fake", enabled=True)
    db.add(sr); db.commit()

    nonexistent = os.path.join(images_dir, "DELETED_main.jpg")
    img_data = []
    main_img_data = [(nonexistent, "D", "main", 1, "jpg")]

    task = _make_task(db)
    task_id = task.id
    _run_build_zip(task_id, img_data, flat=False, main_img_data=main_img_data,
                   upload_dir=images_dir)

    zip_path = os.path.join(images_dir, f"export_{task_id}.zip")
    with zipfile.ZipFile(zip_path, "r") as zf:
        assert len(zf.namelist()) == 0

    db.expire_all()
    assert db.get(ExportTask, task_id).status == "failed"


# ---------------------------------------------------------------------------
# Scenario 6: flat mode — fallback files at ZIP root
# ---------------------------------------------------------------------------

def test_fallback_flat_mode(db, images_dir):
    """In flat mode, fallback detail images should be at ZIP root."""
    sr = ScanRoot(path="/fake", enabled=True)
    db.add(sr); db.commit()

    file_m = _create_image_file(images_dir, "E_main.jpg")
    img_data = []
    main_img_data = [(file_m, "E", "main", 1, "jpg")]

    task = _make_task(db)
    task_id = task.id
    _run_build_zip(task_id, img_data, flat=True, main_img_data=main_img_data,
                   upload_dir=images_dir)

    zip_path = os.path.join(images_dir, f"export_{task_id}.zip")
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        assert len(names) == 1
        assert names[0] == "E_详情图_1.jpg"


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
    assert counts["A"]["detail"] == 0
    assert counts["B"]["main"] == 0
    assert counts["B"]["detail"] == 2
