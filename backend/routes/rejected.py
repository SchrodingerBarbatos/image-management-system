"""API endpoints for rejected barcodes."""

import os
from flask import Blueprint, request, jsonify
from sqlalchemy import func
from models import session, RejectedBarcode, ScanRoot
from sqlalchemy.orm import aliased

rejected_bp = Blueprint('rejected', __name__)


def safe_remove_rejected_file(rejected, sess):
    """Safely remove a rejected barcode file with root/path validation.
    Returns (True, None) on success, (False, reason) on failure."""
    root = sess.get(ScanRoot, rejected.scan_root_id)
    if not root:
        return False, f'找不到扫描目录 (scan_root_id={rejected.scan_root_id})'
    real_file = os.path.realpath(rejected.file_path)
    real_root = os.path.realpath(root.path)
    try:
        if os.path.commonpath([real_file, real_root]) != real_root:
            return False, '文件路径不在所属扫描目录下'
    except ValueError:
        return False, '文件路径与扫描目录不在同一驱动器'
    try:
        os.remove(rejected.file_path)
        return True, None
    except FileNotFoundError:
        return True, None
    except OSError as e:
        return False, f'系统删除失败: {e}'


@rejected_bp.route('', methods=['GET'])
def list_rejected():
    """查询被拒绝的条码记录。"""
    from routes._utils import parse_pagination
    try:
        page, page_size = parse_pagination(default_page_size=20, max_page_size=500)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    barcode = request.args.get('barcode', type=str)
    scan_root_id = request.args.get('scan_root_id', type=int)
    start_date = request.args.get('start_date', type=str)
    end_date = request.args.get('end_date', type=str)

    sr = aliased(ScanRoot)
    query = session.query(RejectedBarcode, sr.path).outerjoin(
        sr, RejectedBarcode.scan_root_id == sr.id
    )

    if barcode:
        query = query.filter(RejectedBarcode.barcode == barcode)
    if scan_root_id:
        query = query.filter(RejectedBarcode.scan_root_id == scan_root_id)
    if start_date:
        query = query.filter(RejectedBarcode.created_at >= start_date)
    if end_date:
        query = query.filter(RejectedBarcode.created_at <= end_date + 'T23:59:59')

    total = query.count()
    items = query.order_by(RejectedBarcode.created_at.desc()).offset(
        (page - 1) * page_size
    ).limit(page_size).all()

    return jsonify({
        'items': [{
            'id': r.id,
            'barcode': r.barcode,
            'file_path': r.file_path,
            'filename': r.filename,
            'reason': r.reason,
            'scan_root_id': r.scan_root_id,
            'scan_root_path': root_path or '',
            'created_at': r.created_at,
        } for r, root_path in items],
        'total': total,
        'page': page,
        'page_size': page_size,
    })


@rejected_bp.route('/<int:id>', methods=['DELETE'])
def delete_rejected(id):
    """删除单个被拒绝的条码记录。"""
    rejected = session.get(RejectedBarcode, id)
    if not rejected:
        return jsonify({'error': '记录不存在'}), 404

    ok, reason = safe_remove_rejected_file(rejected, session)
    if not ok:
        return jsonify({'error': f'文件删除失败，已取消数据库删除: {reason}'}), 403

    session.delete(rejected)
    session.commit()

    return jsonify({'message': '已删除'})


@rejected_bp.route('/delete-batch', methods=['POST'])
def delete_batch():
    """批量删除被拒绝的条码记录。"""
    data = request.json
    if not data or 'ids' not in data:
        return jsonify({'error': '请提供要删除的记录ID列表'}), 400

    ids = data['ids']
    if not isinstance(ids, list):
        return jsonify({'error': 'ids 必须是列表'}), 400

    failed_items = []
    deleted_count = 0

    for id in ids:
        rejected = session.get(RejectedBarcode, id)
        if not rejected:
            continue

        ok, reason = safe_remove_rejected_file(rejected, session)
        if ok:
            session.delete(rejected)
            deleted_count += 1
        else:
            failed_items.append({'id': rejected.id, 'file_path': rejected.file_path, 'reason': reason})

    session.commit()

    return jsonify({
        'message': f'已删除 {deleted_count} 条记录',
        'deleted_count': deleted_count,
        'failed_items': failed_items,
    })


@rejected_bp.route('/delete-all', methods=['POST'])
def delete_all():
    """全选删除被拒绝的条码记录。"""
    data = request.json or {}

    query = session.query(RejectedBarcode)

    if 'barcode' in data and data['barcode']:
        query = query.filter(RejectedBarcode.barcode == data['barcode'])
    if 'scan_root_id' in data and data['scan_root_id']:
        query = query.filter(RejectedBarcode.scan_root_id == data['scan_root_id'])
    if 'start_date' in data and data['start_date']:
        query = query.filter(RejectedBarcode.created_at >= data['start_date'])
    if 'end_date' in data and data['end_date']:
        query = query.filter(RejectedBarcode.created_at <= data['end_date'] + 'T23:59:59')

    # 先逐条安全删文件，成功后删 DB，失败保留 DB
    rejected_items = query.all()
    deleted_count = 0
    failed_items = []

    for rejected in rejected_items:
        ok, reason = safe_remove_rejected_file(rejected, session)
        if ok:
            session.delete(rejected)
            deleted_count += 1
        else:
            failed_items.append({'id': rejected.id, 'file_path': rejected.file_path, 'reason': reason})

    session.commit()

    return jsonify({
        'message': f'已删除 {deleted_count} 条记录',
        'deleted_count': deleted_count,
        'failed_items': failed_items,
    })


@rejected_bp.route('/stats', methods=['GET'])
def get_stats():
    """获取拒绝记录统计信息。"""
    total = session.query(RejectedBarcode).count()

    # 按原因统计
    reason_stats = session.query(
        RejectedBarcode.reason,
        func.count(RejectedBarcode.id)
    ).group_by(RejectedBarcode.reason).all()

    by_reason = {}
    for reason, count in reason_stats:
        # 按原因前缀归类（去掉变量部分）
        if reason.startswith("长度"):
            key = "长度不符合GTIN要求"
        elif "非数字字符" in reason:
            key = "包含非数字字符"
        elif reason.startswith("校验位"):
            key = "校验位错误"
        else:
            key = reason
        by_reason[key] = by_reason.get(key, 0) + count

    # 按扫描目录统计
    root_stats = session.query(
        RejectedBarcode.scan_root_id,
        func.count(RejectedBarcode.id)
    ).group_by(RejectedBarcode.scan_root_id).all()

    by_scan_root = {str(rid): count for rid, count in root_stats}

    return jsonify({
        'total': total,
        'by_reason': by_reason,
        'by_scan_root': by_scan_root,
    })
