"""Tests for rejected barcodes API."""

import pytest
import json
from flask import Flask
from sqlalchemy import create_engine, event
from sqlalchemy.orm import scoped_session, sessionmaker

from models import Base, RejectedBarcode, ScanRoot
from routes.rejected import rejected_bp


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


@pytest.fixture(scope="function")
def app(sess):
    """Flask test app."""
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(rejected_bp, url_prefix='/api/rejected-barcodes')

    # Mock session
    import routes.rejected as rejected_module
    rejected_module.session = sess

    return app


@pytest.fixture(scope="function")
def client(app):
    """Flask test client."""
    return app.test_client()


def test_list_rejected_barcodes(client, sess):
    """测试查询拒绝记录列表。"""
    sr = ScanRoot(path="fake", enabled=True, recursive=False)
    sess.add(sr)
    sess.commit()

    rejected = RejectedBarcode(
        barcode="12345678",
        file_path="fake/12345678_主图_1.jpg",
        filename="12345678_主图_1.jpg",
        reason="长度 8 不符合 GTIN 要求",
        scan_root_id=sr.id,
    )
    sess.add(rejected)
    sess.commit()

    response = client.get('/api/rejected-barcodes')
    data = json.loads(response.data)

    assert response.status_code == 200
    assert data['total'] == 1
    assert len(data['items']) == 1
    assert data['items'][0]['barcode'] == "12345678"


def test_list_rejected_barcodes_with_filter(client, sess):
    """测试按条码筛选拒绝记录。"""
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
        barcode="87654321",
        file_path="fake/87654321_主图_1.jpg",
        filename="87654321_主图_1.jpg",
        reason="包含非数字字符",
        scan_root_id=sr.id,
    )
    sess.add_all([rejected1, rejected2])
    sess.commit()

    response = client.get('/api/rejected-barcodes?barcode=12345678')
    data = json.loads(response.data)

    assert response.status_code == 200
    assert data['total'] == 1
    assert data['items'][0]['barcode'] == "12345678"


def test_delete_single_rejected_barcode(client, sess):
    """测试删除单个拒绝记录。"""
    sr = ScanRoot(path="fake", enabled=True, recursive=False)
    sess.add(sr)
    sess.commit()

    rejected = RejectedBarcode(
        barcode="12345678",
        file_path="fake/12345678_主图_1.jpg",
        filename="12345678_主图_1.jpg",
        reason="长度 8 不符合 GTIN 要求",
        scan_root_id=sr.id,
    )
    sess.add(rejected)
    sess.commit()

    response = client.delete(f'/api/rejected-barcodes/{rejected.id}')
    data = json.loads(response.data)

    assert response.status_code == 200
    assert "已删除" in data['message']

    # 验证记录已删除
    remaining = sess.query(RejectedBarcode).all()
    assert len(remaining) == 0


def test_delete_single_rejected_barcode_not_found(client, sess):
    """测试删除不存在的拒绝记录返回404。"""
    response = client.delete('/api/rejected-barcodes/99999')
    assert response.status_code == 404


def test_delete_batch_rejected_barcodes(client, sess):
    """测试批量删除拒绝记录。"""
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
        barcode="87654321",
        file_path="fake/87654321_主图_1.jpg",
        filename="87654321_主图_1.jpg",
        reason="包含非数字字符",
        scan_root_id=sr.id,
    )
    sess.add_all([rejected1, rejected2])
    sess.commit()

    response = client.post(
        '/api/rejected-barcodes/delete-batch',
        data=json.dumps({'ids': [rejected1.id, rejected2.id]}),
        content_type='application/json'
    )
    data = json.loads(response.data)

    assert response.status_code == 200
    assert data['deleted_count'] == 2

    # 验证记录已删除
    remaining = sess.query(RejectedBarcode).all()
    assert len(remaining) == 0


# ---------------------------------------------------------------------------
# 安全校验：文件路径不在 scan_root 下时，保留 DB 记录
# ---------------------------------------------------------------------------


def test_delete_rejected_file_outside_root(client, sess, tmp_path):
    """单删：文件路径不在 scan_root 下时返回 403，DB 记录保留。"""
    import os
    root_dir = str(tmp_path / "photos")
    os.makedirs(root_dir)
    sr = ScanRoot(path=root_dir, enabled=True, recursive=False)
    sess.add(sr)
    sess.commit()

    outside_dir = str(tmp_path / "evil")
    os.makedirs(outside_dir)
    outside = outside_dir + os.sep + "bad.jpg"
    with open(outside, 'w') as f:
        f.write('x')

    rejected = RejectedBarcode(
        barcode="12345678", file_path=outside,
        filename="bad.jpg", reason="长度不符合", scan_root_id=sr.id,
    )
    sess.add(rejected)
    sess.commit()

    resp = client.delete(f'/api/rejected-barcodes/{rejected.id}')
    assert resp.status_code == 403
    data = resp.get_json()
    assert '文件删除失败' in data['error']

    # DB preserved
    assert sess.get(RejectedBarcode, rejected.id) is not None


def test_delete_batch_rejected_mixed(client, sess, tmp_path):
    """批删：混合 inside/outside 路径，成功项删 DB，失败项保留。"""
    import os
    root_dir = str(tmp_path / "photos")
    os.makedirs(root_dir)
    sr = ScanRoot(path=root_dir, enabled=True, recursive=False)
    sess.add(sr)
    sess.commit()

    # Inside root — will succeed (file doesn't exist, but path is inside root → FileNotFoundError → ok)
    inside = root_dir + os.sep + "good.jpg"
    rej_ok = RejectedBarcode(
        barcode="111", file_path=inside,
        filename="good.jpg", reason="r", scan_root_id=sr.id,
    )

    # Outside root — will fail
    outside_dir = str(tmp_path / "evil")
    os.makedirs(outside_dir)
    outside = outside_dir + os.sep + "bad.jpg"
    with open(outside, 'w') as f:
        f.write('x')
    rej_bad = RejectedBarcode(
        barcode="222", file_path=outside,
        filename="bad.jpg", reason="r", scan_root_id=sr.id,
    )

    sess.add_all([rej_ok, rej_bad])
    sess.commit()

    resp = client.post(
        '/api/rejected-barcodes/delete-batch',
        json={'ids': [rej_ok.id, rej_bad.id]},
    )
    data = resp.get_json()
    assert resp.status_code == 200
    assert data['deleted_count'] == 1
    assert len(data['failed_items']) == 1
    assert data['failed_items'][0]['id'] == rej_bad.id

    # Success item DB deleted, failed item DB preserved
    assert sess.get(RejectedBarcode, rej_ok.id) is None
    assert sess.get(RejectedBarcode, rej_bad.id) is not None


def test_delete_all_rejected_mixed(client, sess, tmp_path):
    """全删：混合路径，成功项删 DB，失败项保留。"""
    import os
    root_dir = str(tmp_path / "photos")
    os.makedirs(root_dir)
    sr = ScanRoot(path=root_dir, enabled=True, recursive=False)
    sess.add(sr)
    sess.commit()

    inside = root_dir + os.sep + "good.jpg"
    rej_ok = RejectedBarcode(
        barcode="111", file_path=inside,
        filename="good.jpg", reason="r", scan_root_id=sr.id,
    )

    outside_dir = str(tmp_path / "evil")
    os.makedirs(outside_dir)
    outside = outside_dir + os.sep + "bad.jpg"
    with open(outside, 'w') as f:
        f.write('x')
    rej_bad = RejectedBarcode(
        barcode="222", file_path=outside,
        filename="bad.jpg", reason="r", scan_root_id=sr.id,
    )

    sess.add_all([rej_ok, rej_bad])
    sess.commit()

    resp = client.post('/api/rejected-barcodes/delete-all', json={})
    data = resp.get_json()
    assert resp.status_code == 200
    assert data['deleted_count'] == 1
    assert len(data['failed_items']) == 1

    assert sess.get(RejectedBarcode, rej_ok.id) is None
    assert sess.get(RejectedBarcode, rej_bad.id) is not None


def test_delete_batch_validation(client, sess):
    """测试批量删除的输入验证。"""
    # 没有ids字段
    response = client.post(
        '/api/rejected-barcodes/delete-batch',
        data=json.dumps({}),
        content_type='application/json'
    )
    assert response.status_code == 400

    # ids不是列表
    response = client.post(
        '/api/rejected-barcodes/delete-batch',
        data=json.dumps({'ids': 'not a list'}),
        content_type='application/json'
    )
    assert response.status_code == 400


def test_get_rejected_barcodes_stats(client, sess):
    """测试获取拒绝记录统计信息。"""
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
        barcode="87654321",
        file_path="fake/87654321_主图_1.jpg",
        filename="87654321_主图_1.jpg",
        reason="包含非数字字符",
        scan_root_id=sr.id,
    )
    sess.add_all([rejected1, rejected2])
    sess.commit()

    response = client.get('/api/rejected-barcodes/stats')
    data = json.loads(response.data)

    assert response.status_code == 200
    assert data['total'] == 2
    assert any("长度" in key for key in data['by_reason'])
    assert any("非数字字符" in key for key in data['by_reason'])


def test_list_rejected_barcodes_pagination(client, sess):
    """测试分页功能。"""
    sr = ScanRoot(path="fake", enabled=True, recursive=False)
    sess.add(sr)
    sess.commit()

    # 创建5条记录
    for i in range(5):
        rejected = RejectedBarcode(
            barcode=f"1234567{i}",
            file_path=f"fake/1234567{i}_主图_1.jpg",
            filename=f"1234567{i}_主图_1.jpg",
            reason="长度 8 不符合 GTIN 要求",
            scan_root_id=sr.id,
        )
        sess.add(rejected)
    sess.commit()

    # 第一页，每页2条
    response = client.get('/api/rejected-barcodes?page=1&page_size=2')
    data = json.loads(response.data)

    assert response.status_code == 200
    assert data['total'] == 5
    assert len(data['items']) == 2
    assert data['page'] == 1
    assert data['page_size'] == 2

    # 第三页，每页2条（应该只有1条）
    response = client.get('/api/rejected-barcodes?page=3&page_size=2')
    data = json.loads(response.data)

    assert response.status_code == 200
    assert data['total'] == 5
    assert len(data['items']) == 1
