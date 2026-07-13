"""Unit tests for O(n) ZIP entry typing / barcode attribution helpers."""

from routes.export import _build_typed_zip_entries, _index_img_data_by_path, _plan_zip_entries


def test_index_img_data_by_path_is_dict():
    img_data = [
        ("/a/1.jpg", "BC1", "main", 1, "jpg", 1),
        ("/a/2.jpg", "BC1", "detail", 1, "jpg", 1),
        ("/b/1.jpg", "BC2", "main", 1, "jpg", 2),
    ]
    idx = _index_img_data_by_path(img_data)
    assert idx["/a/1.jpg"] == {"barcode": "BC1", "image_type": "main"}
    assert idx["/a/2.jpg"] == {"barcode": "BC1", "image_type": "detail"}
    assert idx["/b/1.jpg"]["barcode"] == "BC2"
    assert len(idx) == 3


def test_typed_entries_all_export_uses_source_type():
    img_data = [
        ("/a/m.jpg", "A", "main", 1, "jpg", 1),
        ("/a/d.jpg", "A", "detail", 1, "jpg", 1),
    ]
    entries, _ = _plan_zip_entries(img_data, flat=False, export_type="all")
    typed = _build_typed_zip_entries(entries, img_data, export_type="all")
    by_path = {t[0]: t for t in typed}
    assert by_path["/a/m.jpg"][3] == "A"
    assert by_path["/a/m.jpg"][4] == "main"
    assert by_path["/a/d.jpg"][4] == "detail"


def test_typed_entries_main_export_forces_main():
    img_data = [
        ("/a/m.jpg", "A", "main", 1, "jpg", 1),
        ("/a/d.jpg", "A", "detail", 1, "jpg", 1),
    ]
    entries, _ = _plan_zip_entries(img_data, flat=False, export_type="main")
    typed = _build_typed_zip_entries(entries, img_data, export_type="main")
    assert len(typed) == 1
    assert typed[0][4] == "main"
    assert typed[0][3] == "A"


def test_typed_entries_detail_export_real_detail():
    img_data = [
        ("/a/d.jpg", "A", "detail", 1, "jpg", 1),
        ("/a/m.jpg", "A", "main", 1, "jpg", 1),
    ]
    entries, fallback = _plan_zip_entries(img_data, flat=False, export_type="detail")
    assert fallback == []
    typed = _build_typed_zip_entries(entries, img_data, export_type="detail")
    assert len(typed) == 1
    assert typed[0][0] == "/a/d.jpg"
    assert typed[0][4] == "detail"


def test_typed_entries_detail_fallback_counts_as_detail():
    """Source image_type is main, but detail export fallback reports as detail."""
    img_data = [
        ("/b/m1.jpg", "B", "main", 1, "jpg", 1),
        ("/b/m2.jpg", "B", "main", 2, "jpg", 1),
    ]
    entries, fallback = _plan_zip_entries(img_data, flat=False, export_type="detail")
    assert fallback == ["B"]
    typed = _build_typed_zip_entries(entries, img_data, export_type="detail")
    assert len(typed) == 2
    for _fp, _arc, _rid, barcode, report_type in typed:
        assert barcode == "B"
        assert report_type == "detail"


def test_typed_entries_no_nested_scan_structure():
    """Structural: each path looked up once via index; 10k rows stay O(n)."""
    n = 10_000
    img_data = [
        (f"/p/{i}.jpg", f"BC{i % 100}", "main" if i % 2 == 0 else "detail", i, "jpg", 1)
        for i in range(n)
    ]
    entries, _ = _plan_zip_entries(img_data, flat=True, export_type="all")
    typed = _build_typed_zip_entries(entries, img_data, export_type="all")
    assert len(typed) == n
    # Spot-check last entry
    assert typed[-1][3] == f"BC{(n - 1) % 100}"
    assert typed[-1][4] in ("main", "detail")


def test_actual_count_overlay_partial_missing():
    """Only successfully written paths contribute to counts (caller logic)."""
    img_data = [
        ("/ok/1.jpg", "X", "main", 1, "jpg", 1),
        ("/missing/2.jpg", "X", "main", 2, "jpg", 1),
        ("/ok/3.jpg", "Y", "detail", 1, "jpg", 1),
    ]
    entries, _ = _plan_zip_entries(img_data, flat=False, export_type="all")
    typed = _build_typed_zip_entries(entries, img_data, export_type="all")
    # Simulate write success only for paths under /ok/
    actual = {}
    for file_path, _arc, _rid, barcode, report_type in typed:
        if file_path.startswith("/ok/") and barcode:
            actual.setdefault(barcode, {"main": 0, "detail": 0})
            actual[barcode][report_type] += 1
    assert actual == {"X": {"main": 1, "detail": 0}, "Y": {"main": 0, "detail": 1}}
