"""Tests for detail-image fallback logic in ZIP export.

_build_zip export_type parameter controls behavior:
- 'main': only main images, named as main
- 'detail': detail images with main-as-detail fallback for barcodes with no detail
- 'all': main as main, detail as detail, no fallback
"""

import os
import tempfile
import zipfile

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import scoped_session, sessionmaker

import models
from models import Base, ExportTask, Image, ScanRoot


@pytest.fixture()
def db():
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
    path = os.path.join(directory, filename)
    with open(path, "wb") as f:
        f.write(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
    return path


def _make_task(session):
    task = ExportTask(status="processing", barcode_data="{}")
    session.add(task)
    session.commit()
    return task


def _run_build_zip(task_id, img_data, flat, export_type, upload_dir=None):
    import routes.export as exp_mod
    old = exp_mod.UPLOAD_DIR
    exp_mod.UPLOAD_DIR = upload_dir
    try:
        from routes.export import _build_zip
        _build_zip(task_id, img_data, flat, export_type=export_type)
    finally:
        exp_mod.UPLOAD_DIR = old


def _zip_names(zip_path):
    with zipfile.ZipFile(zip_path, "r") as zf:
        return sorted(zf.namelist())


# ===========================================================================
# export_type='detail'
# ===========================================================================

def test_detail_fallback_when_no_detail_images(db, images_dir):
    """detail export: barcode with only main images -> ALL main written as detail."""
    sr = ScanRoot(path="/fake", enabled=True)
    db.add(sr); db.commit()

    img_data = [
        (_create_image_file(images_dir, "A_1.jpg"), "A", "main", 1, "jpg"),
        (_create_image_file(images_dir, "A_2.jpg"), "A", "main", 2, "jpg"),
        (_create_image_file(images_dir, "A_3.jpg"), "A", "main", 3, "jpg"),
    ]

    task = _make_task(db)
    task_id = task.id
    _run_build_zip(task_id, img_data, flat=False, export_type='detail', upload_dir=images_dir)

    names = _zip_names(os.path.join(images_dir, f"export_{task_id}.zip"))
    assert names == [
        "详情图/A_详情图_1.jpg",
        "详情图/A_详情图_2.jpg",
        "详情图/A_详情图_3.jpg",
    ]


def test_detail_no_fallback_when_detail_exists(db, images_dir):
    """detail export: barcode with detail images -> real detail exported, no fallback."""
    sr = ScanRoot(path="/fake", enabled=True)
    db.add(sr); db.commit()

    img_data = [
        (_create_image_file(images_dir, "B_d1.jpg"), "B", "detail", 1, "jpg"),
        (_create_image_file(images_dir, "B_d2.jpg"), "B", "detail", 2, "jpg"),
        (_create_image_file(images_dir, "B_m1.jpg"), "B", "main", 1, "jpg"),
    ]

    task = _make_task(db)
    task_id = task.id
    _run_build_zip(task_id, img_data, flat=False, export_type='detail', upload_dir=images_dir)

    names = _zip_names(os.path.join(images_dir, f"export_{task_id}.zip"))
    assert names == [
        "详情图/B_详情图_1.jpg",
        "详情图/B_详情图_2.jpg",
    ]


def test_detail_mixed_barcodes(db, images_dir):
    """detail export: mixed barcodes - only those missing detail get fallback."""
    sr = ScanRoot(path="/fake", enabled=True)
    db.add(sr); db.commit()

    img_data = [
        (_create_image_file(images_dir, "A_d.jpg"), "A", "detail", 1, "jpg"),
        (_create_image_file(images_dir, "B_m.jpg"), "B", "main", 1, "jpg"),
    ]

    task = _make_task(db)
    task_id = task.id
    _run_build_zip(task_id, img_data, flat=False, export_type='detail', upload_dir=images_dir)

    names = _zip_names(os.path.join(images_dir, f"export_{task_id}.zip"))
    assert names == [
        "详情图/A_详情图_1.jpg",
        "详情图/B_详情图_1.jpg",
    ]


def test_detail_flat_mode(db, images_dir):
    """detail export flat mode: fallback at ZIP root."""
    sr = ScanRoot(path="/fake", enabled=True)
    db.add(sr); db.commit()

    img_data = [
        (_create_image_file(images_dir, "E_1.jpg"), "E", "main", 1, "jpg"),
        (_create_image_file(images_dir, "E_2.jpg"), "E", "main", 2, "jpg"),
    ]

    task = _make_task(db)
    task_id = task.id
    _run_build_zip(task_id, img_data, flat=True, export_type='detail', upload_dir=images_dir)

    names = _zip_names(os.path.join(images_dir, f"export_{task_id}.zip"))
    assert names == ["E_详情图_1.jpg", "E_详情图_2.jpg"]


# ===========================================================================
# export_type='main'
# ===========================================================================

def test_main_only_exports_main(db, images_dir):
    """main export: only main images written as main."""
    sr = ScanRoot(path="/fake", enabled=True)
    db.add(sr); db.commit()

    img_data = [
        (_create_image_file(images_dir, "A_m1.jpg"), "A", "main", 1, "jpg"),
        (_create_image_file(images_dir, "A_d1.jpg"), "A", "detail", 1, "jpg"),
    ]

    task = _make_task(db)
    task_id = task.id
    _run_build_zip(task_id, img_data, flat=False, export_type='main', upload_dir=images_dir)

    names = _zip_names(os.path.join(images_dir, f"export_{task_id}.zip"))
    assert names == ["主图/A_1.jpg"]


def test_main_flat_mode(db, images_dir):
    """main export flat mode: at ZIP root."""
    sr = ScanRoot(path="/fake", enabled=True)
    db.add(sr); db.commit()

    img_data = [
        (_create_image_file(images_dir, "C_m.jpg"), "C", "main", 1, "jpg"),
    ]

    task = _make_task(db)
    task_id = task.id
    _run_build_zip(task_id, img_data, flat=True, export_type='main', upload_dir=images_dir)

    names = _zip_names(os.path.join(images_dir, f"export_{task_id}.zip"))
    assert names == ["C_1.jpg"]


def test_main_no_fallback_to_detail(db, images_dir):
    """main export: barcode with only main images should NOT write as detail."""
    sr = ScanRoot(path="/fake", enabled=True)
    db.add(sr); db.commit()

    img_data = [
        (_create_image_file(images_dir, "D_1.jpg"), "D", "main", 1, "jpg"),
        (_create_image_file(images_dir, "D_2.jpg"), "D", "main", 2, "jpg"),
    ]

    task = _make_task(db)
    task_id = task.id
    _run_build_zip(task_id, img_data, flat=False, export_type='main', upload_dir=images_dir)

    names = _zip_names(os.path.join(images_dir, f"export_{task_id}.zip"))
    assert names == ["主图/D_1.jpg", "主图/D_2.jpg"]
    assert not any("详情图" in n for n in names)


# ===========================================================================
# export_type='all'
# ===========================================================================

def test_all_exports_main_and_detail(db, images_dir):
    """all export: main as main, detail as detail, no fallback."""
    sr = ScanRoot(path="/fake", enabled=True)
    db.add(sr); db.commit()

    img_data = [
        (_create_image_file(images_dir, "A_m.jpg"), "A", "main", 1, "jpg"),
        (_create_image_file(images_dir, "A_d.jpg"), "A", "detail", 1, "jpg"),
        (_create_image_file(images_dir, "B_m1.jpg"), "B", "main", 1, "jpg"),
        (_create_image_file(images_dir, "B_m2.jpg"), "B", "main", 2, "jpg"),
    ]

    task = _make_task(db)
    task_id = task.id
    _run_build_zip(task_id, img_data, flat=False, export_type='all', upload_dir=images_dir)

    names = _zip_names(os.path.join(images_dir, f"export_{task_id}.zip"))
    assert names == [
        "主图/A_1.jpg",
        "主图/B_1.jpg",
        "主图/B_2.jpg",
        "详情图/A_详情图_1.jpg",
    ]


def test_all_no_fallback(db, images_dir):
    """all export: barcode with only main images stays as main, NO detail fallback."""
    sr = ScanRoot(path="/fake", enabled=True)
    db.add(sr); db.commit()

    img_data = [
        (_create_image_file(images_dir, "C_m.jpg"), "C", "main", 1, "jpg"),
    ]

    task = _make_task(db)
    task_id = task.id
    _run_build_zip(task_id, img_data, flat=False, export_type='all', upload_dir=images_dir)

    names = _zip_names(os.path.join(images_dir, f"export_{task_id}.zip"))
    assert names == ["主图/C_1.jpg"]
    assert not any("详情图" in n for n in names)


# ===========================================================================
# edge cases
# ===========================================================================

def test_empty_img_data(db, images_dir):
    sr = ScanRoot(path="/fake", enabled=True)
    db.add(sr); db.commit()

    task = _make_task(db)
    task_id = task.id
    _run_build_zip(task_id, [], flat=False, export_type='detail', upload_dir=images_dir)

    names = _zip_names(os.path.join(images_dir, f"export_{task_id}.zip"))
    assert names == []
    db.expire_all()
    assert db.get(ExportTask, task_id).status == "done"


def test_missing_files_skipped(db, images_dir):
    sr = ScanRoot(path="/fake", enabled=True)
    db.add(sr); db.commit()

    img_data = [
        (os.path.join(images_dir, "GONE.jpg"), "G", "main", 1, "jpg"),
    ]

    task = _make_task(db)
    task_id = task.id
    _run_build_zip(task_id, img_data, flat=False, export_type='main', upload_dir=images_dir)

    names = _zip_names(os.path.join(images_dir, f"export_{task_id}.zip"))
    assert names == []
    db.expire_all()
    assert db.get(ExportTask, task_id).status == "failed"


def test_barcode_counts_not_affected_by_fallback():
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
