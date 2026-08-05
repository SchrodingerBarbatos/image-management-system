"""Tests for detail-image fallback logic in ZIP export.

_build_zip export_type parameter controls behavior:
- 'main': only main images, named as main
- 'detail': detail images with main-as-detail fallback for barcodes with no detail
- 'all': main as main, detail as detail, no fallback

Also covers the /api/images/batch-export route with image_type set.
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
    import models
    from models import ScanRoot
    old = exp_mod.UPLOAD_DIR
    exp_mod.UPLOAD_DIR = upload_dir
    # Attach scan_root_id when tests pass 5-tuples (path under the enabled root)
    root = models.session.query(ScanRoot).filter(ScanRoot.enabled == True).first()
    root_id = root.id if root else None
    normalized = []
    for row in img_data:
        if len(row) >= 6:
            normalized.append(row)
        else:
            normalized.append(tuple(row) + (root_id,))
    try:
        from routes.export import _build_zip
        _build_zip(task_id, normalized, flat, export_type=export_type)
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
    sr = ScanRoot(path=images_dir, enabled=True)
    db.add(sr); db.commit()

    img_data = [
        (_create_image_file(images_dir, "A_1.jpg"), "A", "main", 1, "jpg"),
        (_create_image_file(images_dir, "A_2.jpg"), "A", "main", 2, "jpg"),
        (_create_image_file(images_dir, "A_3.jpg"), "A", "main", 3, "jpg"),
    ]

    task = _make_task(db)
    task_id = task.id
    _run_build_zip(task_id, img_data, flat=False, export_type='detail', upload_dir=images_dir)

    names = _zip_names(os.path.join(images_dir, "zips", f"export_{task_id}.zip"))
    assert names == [
        "详情图/A_详情图_1.jpg",
        "详情图/A_详情图_2.jpg",
        "详情图/A_详情图_3.jpg",
    ]


def test_detail_no_fallback_when_detail_exists(db, images_dir):
    """detail export: barcode with detail images -> real detail exported, no fallback."""
    sr = ScanRoot(path=images_dir, enabled=True)
    db.add(sr); db.commit()

    img_data = [
        (_create_image_file(images_dir, "B_d1.jpg"), "B", "detail", 1, "jpg"),
        (_create_image_file(images_dir, "B_d2.jpg"), "B", "detail", 2, "jpg"),
        (_create_image_file(images_dir, "B_m1.jpg"), "B", "main", 1, "jpg"),
    ]

    task = _make_task(db)
    task_id = task.id
    _run_build_zip(task_id, img_data, flat=False, export_type='detail', upload_dir=images_dir)

    names = _zip_names(os.path.join(images_dir, "zips", f"export_{task_id}.zip"))
    assert names == [
        "详情图/B_详情图_1.jpg",
        "详情图/B_详情图_2.jpg",
    ]


def test_detail_mixed_barcodes(db, images_dir):
    """detail export: mixed barcodes - only those missing detail get fallback."""
    sr = ScanRoot(path=images_dir, enabled=True)
    db.add(sr); db.commit()

    img_data = [
        (_create_image_file(images_dir, "A_d.jpg"), "A", "detail", 1, "jpg"),
        (_create_image_file(images_dir, "B_m.jpg"), "B", "main", 1, "jpg"),
    ]

    task = _make_task(db)
    task_id = task.id
    _run_build_zip(task_id, img_data, flat=False, export_type='detail', upload_dir=images_dir)

    names = _zip_names(os.path.join(images_dir, "zips", f"export_{task_id}.zip"))
    assert names == [
        "详情图/A_详情图_1.jpg",
        "详情图/B_详情图_1.jpg",
    ]


def test_detail_flat_mode(db, images_dir):
    """detail export flat mode: fallback at ZIP root."""
    sr = ScanRoot(path=images_dir, enabled=True)
    db.add(sr); db.commit()

    img_data = [
        (_create_image_file(images_dir, "E_1.jpg"), "E", "main", 1, "jpg"),
        (_create_image_file(images_dir, "E_2.jpg"), "E", "main", 2, "jpg"),
    ]

    task = _make_task(db)
    task_id = task.id
    _run_build_zip(task_id, img_data, flat=True, export_type='detail', upload_dir=images_dir)

    names = _zip_names(os.path.join(images_dir, "zips", f"export_{task_id}.zip"))
    assert names == ["E_详情图_1.jpg", "E_详情图_2.jpg"]


# ===========================================================================
# export_type='main'
# ===========================================================================

def test_main_only_exports_main(db, images_dir):
    """main export: only main images written as main."""
    sr = ScanRoot(path=images_dir, enabled=True)
    db.add(sr); db.commit()

    img_data = [
        (_create_image_file(images_dir, "A_m1.jpg"), "A", "main", 1, "jpg"),
        (_create_image_file(images_dir, "A_d1.jpg"), "A", "detail", 1, "jpg"),
    ]

    task = _make_task(db)
    task_id = task.id
    _run_build_zip(task_id, img_data, flat=False, export_type='main', upload_dir=images_dir)

    names = _zip_names(os.path.join(images_dir, "zips", f"export_{task_id}.zip"))
    assert names == ["主图/A_1.jpg"]


def test_main_flat_mode(db, images_dir):
    """main export flat mode: at ZIP root."""
    sr = ScanRoot(path=images_dir, enabled=True)
    db.add(sr); db.commit()

    img_data = [
        (_create_image_file(images_dir, "C_m.jpg"), "C", "main", 1, "jpg"),
    ]

    task = _make_task(db)
    task_id = task.id
    _run_build_zip(task_id, img_data, flat=True, export_type='main', upload_dir=images_dir)

    names = _zip_names(os.path.join(images_dir, "zips", f"export_{task_id}.zip"))
    assert names == ["C_1.jpg"]


def test_main_no_fallback_to_detail(db, images_dir):
    """main export: barcode with only main images should NOT write as detail."""
    sr = ScanRoot(path=images_dir, enabled=True)
    db.add(sr); db.commit()

    img_data = [
        (_create_image_file(images_dir, "D_1.jpg"), "D", "main", 1, "jpg"),
        (_create_image_file(images_dir, "D_2.jpg"), "D", "main", 2, "jpg"),
    ]

    task = _make_task(db)
    task_id = task.id
    _run_build_zip(task_id, img_data, flat=False, export_type='main', upload_dir=images_dir)

    names = _zip_names(os.path.join(images_dir, "zips", f"export_{task_id}.zip"))
    assert names == ["主图/D_1.jpg", "主图/D_2.jpg"]
    assert not any("详情图" in n for n in names)


# ===========================================================================
# export_type='all'
# ===========================================================================

def test_all_exports_main_and_detail(db, images_dir):
    """all export: main as main, detail as detail, no fallback."""
    sr = ScanRoot(path=images_dir, enabled=True)
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

    names = _zip_names(os.path.join(images_dir, "zips", f"export_{task_id}.zip"))
    assert names == [
        "主图/A_1.jpg",
        "主图/B_1.jpg",
        "主图/B_2.jpg",
        "详情图/A_详情图_1.jpg",
    ]


def test_all_no_fallback(db, images_dir):
    """all export: barcode with only main images stays as main, NO detail fallback."""
    sr = ScanRoot(path=images_dir, enabled=True)
    db.add(sr); db.commit()

    img_data = [
        (_create_image_file(images_dir, "C_m.jpg"), "C", "main", 1, "jpg"),
    ]

    task = _make_task(db)
    task_id = task.id
    _run_build_zip(task_id, img_data, flat=False, export_type='all', upload_dir=images_dir)

    names = _zip_names(os.path.join(images_dir, "zips", f"export_{task_id}.zip"))
    assert names == ["主图/C_1.jpg"]
    assert not any("详情图" in n for n in names)


# ===========================================================================
# edge cases
# ===========================================================================

def test_empty_img_data(db, images_dir):
    sr = ScanRoot(path=images_dir, enabled=True)
    db.add(sr); db.commit()

    task = _make_task(db)
    task_id = task.id
    _run_build_zip(task_id, [], flat=False, export_type='detail', upload_dir=images_dir)

    names = _zip_names(os.path.join(images_dir, "zips", f"export_{task_id}.zip"))
    assert names == []
    db.expire_all()
    assert db.get(ExportTask, task_id).status == "done"


def test_missing_files_skipped(db, images_dir):
    sr = ScanRoot(path=images_dir, enabled=True)
    db.add(sr); db.commit()

    img_data = [
        (os.path.join(images_dir, "GONE.jpg"), "G", "main", 1, "jpg"),
    ]

    task = _make_task(db)
    task_id = task.id
    _run_build_zip(task_id, img_data, flat=False, export_type='main', upload_dir=images_dir)

    names = _zip_names(os.path.join(images_dir, "zips", f"export_{task_id}.zip"))
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


# ===========================================================================
# _plan_zip_entries — planned count must match what _build_zip writes
# ===========================================================================

def test_plan_entries_detail_fallback_count():
    from routes.export import _plan_zip_entries

    img_data = [
        ("/fake/a_d.jpg", "A", "detail", 1, "jpg"),
        ("/fake/a_m.jpg", "A", "main", 1, "jpg"),
        ("/fake/b_m1.jpg", "B", "main", 1, "jpg"),
        ("/fake/b_m2.jpg", "B", "main", 2, "jpg"),
    ]
    entries, fallback = _plan_zip_entries(img_data, flat=True, export_type='detail')
    # A has real detail (1), B falls back to its 2 mains
    assert sorted(n for _, n, *_ in entries) == [
        "A_详情图_1.jpg", "B_详情图_1.jpg", "B_详情图_2.jpg",
    ]
    assert fallback == ["B"]


def test_plan_entries_main_excludes_detail():
    from routes.export import _plan_zip_entries

    img_data = [
        ("/fake/a_m.jpg", "A", "main", 1, "jpg"),
        ("/fake/a_d.jpg", "A", "detail", 1, "jpg"),
    ]
    entries, fallback = _plan_zip_entries(img_data, flat=True, export_type='main')
    assert [n for _, n, *_ in entries] == ["A_1.jpg"]
    assert fallback == []


# ===========================================================================
# /api/images/batch-export route with image_type (route-level)
# ===========================================================================

class _SyncThread:
    """Stand-in for threading.Thread that runs the target synchronously,
    keeping all DB access on the test's SQLite connection."""

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        if self._target:
            self._target(*self._args, **self._kwargs)


class _BuildRecorder:
    """Records _build_zip calls; keeps the real function for replay."""

    def __init__(self, real):
        self.real = real
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))

    def replay(self, idx, upload_dir):
        """Run the real _build_zip with the recorded args under upload_dir."""
        import routes.export as exp_mod
        (args, kwargs) = self.calls[idx]
        old = exp_mod.UPLOAD_DIR
        exp_mod.UPLOAD_DIR = upload_dir
        try:
            self.real(*args, **kwargs)
        finally:
            exp_mod.UPLOAD_DIR = old
        return args


@pytest.fixture()
def captured_builds(monkeypatch):
    """Replace _build_zip with a recorder; tests replay the real build later."""
    import routes.export as exp_mod
    recorder = _BuildRecorder(exp_mod._build_zip)
    monkeypatch.setattr(exp_mod, "_build_zip", recorder)
    return recorder


@pytest.fixture()
def client(db, monkeypatch, captured_builds):
    import threading
    import routes.images
    import routes.export
    from flask import Flask

    monkeypatch.setattr(routes.images, "session", db)
    monkeypatch.setattr(routes.export, "session", db)
    monkeypatch.setattr(threading, "Thread", _SyncThread)

    app = Flask(__name__)
    app.register_blueprint(routes.images.images_bp, url_prefix="/api")
    app.register_blueprint(routes.export.export_bp, url_prefix="/api")
    app.config["TESTING"] = True
    return app.test_client()


def _add_image(db, images_dir, barcode, image_type, seq, scan_root_id):
    filename = f"{barcode}_{image_type}_{seq}.jpg"
    path = _create_image_file(images_dir, filename)
    img = Image(
        barcode=barcode, image_type=image_type,
        folder_ctime="2024-01-01T00:00:00",
        filename=filename, ext="jpg", file_path=path, file_size=104,
        md5_hash="abc", content_md5="abc", confirmed=True, status="active",
        scan_root_id=scan_root_id, sequence=seq,
    )
    db.add(img)
    db.commit()
    return img


def test_batch_export_detail_route_fallback(client, db, images_dir, captured_builds):
    """image_type='detail': query fetches main+detail, fallback fills barcodes
    without detail, response total matches actual ZIP entries, report stays truthful."""
    sr = ScanRoot(path=images_dir, enabled=True)
    db.add(sr); db.commit()

    imgs = [
        _add_image(db, images_dir, "A", "main", 1, sr.id),
        _add_image(db, images_dir, "A", "detail", 1, sr.id),
        _add_image(db, images_dir, "A", "detail", 2, sr.id),
        _add_image(db, images_dir, "B", "main", 1, sr.id),
        _add_image(db, images_dir, "B", "main", 2, sr.id),
    ]

    resp = client.post("/api/images/batch-export", json={
        "ids": [i.id for i in imgs], "image_type": "detail", "flat": False,
    })
    assert resp.status_code == 200
    body = resp.get_json()
    # A -> 2 real detail, B -> 2 main as fallback = 4 ZIP entries
    assert body["total"] == 4
    assert body["scanroot_excluded"] == 0
    assert body["version_filtered"] == 0

    # Report: real detail for A; B's main-as-detail fallback counted under detail
    task = db.get(ExportTask, body["task_id"])
    from routes.export import _parse_export_payload
    counts, _stats = _parse_export_payload(task.barcode_data)
    # Before build, planned counts are stored under barcodes
    assert counts["A"] == {"main": 0, "detail": 2}
    assert counts["B"] == {"main": 0, "detail": 2}

    # Replay the real build with the captured args -> fallback lands in ZIP
    args = captured_builds.replay(0, upload_dir=images_dir)
    task_id, img_data, flat, export_type = args
    assert export_type == "detail"
    names = _zip_names(os.path.join(images_dir, "zips", f"export_{task_id}.zip"))
    assert names == [
        "详情图/A_详情图_1.jpg",
        "详情图/A_详情图_2.jpg",
        "详情图/B_详情图_1.jpg",
        "详情图/B_详情图_2.jpg",
    ]
    db.expire_all()
    done = db.get(ExportTask, task_id)
    assert done.status == "done"
    assert done.total_images == 4
    actual, stats = _parse_export_payload(done.barcode_data)
    assert stats.get("written_count") == 4
    assert actual["A"]["detail"] == 2
    assert actual["B"]["detail"] == 2


def test_download_zip_allows_partial_failed(client, db, images_dir, captured_builds, monkeypatch):
    """partial_failed export tasks remain downloadable."""
    import os
    import routes.export as exp_mod
    monkeypatch.setattr(exp_mod, "UPLOAD_DIR", images_dir)
    sr = ScanRoot(path=images_dir, enabled=True)
    db.add(sr); db.commit()
    imgs = [
        _add_image(db, images_dir, "P", "main", 1, sr.id),
    ]
    resp = client.post("/api/images/batch-export", json={
        "ids": [i.id for i in imgs], "image_type": "main", "flat": False,
    })
    assert resp.status_code == 200
    body = resp.get_json()
    task_id = body["task_id"]
    # Force partial_failed with a real zip present under controlled UPLOAD_DIR
    captured_builds.replay(0, upload_dir=images_dir)
    db.expire_all()
    task = db.get(ExportTask, task_id)
    zip_path = os.path.join(images_dir, "zips", f"export_{task_id}.zip")
    assert os.path.isfile(zip_path)
    task.status = "partial_failed"
    task.error_message = "实际写入 1 / 计划 2"
    task.zip_path = zip_path
    db.commit()

    dl = client.get(f"/api/export/download/{task_id}")
    assert dl.status_code == 200, dl.get_json() if dl.is_json else dl.data
    assert dl.mimetype == "application/zip"


def test_download_zip_rejects_processing(client, db):
    task = ExportTask(status="processing", barcode_data="{}")
    db.add(task); db.commit()
    dl = client.get(f"/api/export/download/{task.id}")
    assert dl.status_code == 404


def test_batch_export_main_route_no_fallback(client, db, images_dir, captured_builds):
    """image_type='main': type filter applies, no fallback, total == main count."""
    sr = ScanRoot(path=images_dir, enabled=True)
    db.add(sr); db.commit()

    m = _add_image(db, images_dir, "C", "main", 1, sr.id)
    d = _add_image(db, images_dir, "C", "detail", 1, sr.id)

    resp = client.post("/api/images/batch-export", json={
        "ids": [m.id, d.id], "image_type": "main", "flat": False,
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["total"] == 1

    args = captured_builds.replay(0, upload_dir=images_dir)
    task_id, img_data, flat, export_type = args
    assert export_type == "main"
    names = _zip_names(os.path.join(images_dir, "zips", f"export_{task_id}.zip"))
    assert names == ["主图/C_1.jpg"]


def test_batch_export_default_all(client, db, images_dir, captured_builds):
    """No image_type: export_type defaults to 'all', no fallback."""
    sr = ScanRoot(path=images_dir, enabled=True)
    db.add(sr); db.commit()

    m = _add_image(db, images_dir, "D", "main", 1, sr.id)

    resp = client.post("/api/images/batch-export", json={"ids": [m.id]})
    assert resp.status_code == 200
    assert resp.get_json()["total"] == 1

    (args, _kw) = captured_builds.calls[0]
    assert args[3] == "all"
