"""API endpoints for rejected barcodes."""

import os
from flask import Blueprint, request, jsonify
from sqlalchemy import func
from models import session, RejectedBarcode, ScanRoot
from sqlalchemy.orm import aliased

rejected_bp = Blueprint('rejected', __name__)


@rejected_bp.route('', methods=['GET'])
def list_rejected():
    """查询被拒绝的条码记录。"""
    page = request.args.get('page', 1, type=int)
    page_size = request.args.get('page_size', 20, type=int)
    barcode = request.args.get('barcode', type=str)
    scan_root_id = request.args.get('scan_root_id', type=int)
    start_date = request.args.get('start_date', type=str)
    end_date = request.args.get('end_date', type=str)

    sr = aliased(ScanRoot)
    query = session.query(RejectedBarcode, sr.path).outerjoin(
        sr, RejectedBarcode.scan_root_id == sr.id
    )

    if barcode:
        query = query.filter(RejectedBarcode.barcode.like(f'%{barcode}%'))
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

    # 尝试删除文件
    deleted_file = False
    try:
        if os.path.exists(rejected.file_path):
            os.remove(rejected.file_path)
            deleted_file = True
    except OSError:
        pass

    session.delete(rejected)
    session.commit()

    return jsonify({
        'message': '已删除',
        'deleted_file': deleted_file,
    })


@rejected_bp.route('/delete-batch', methods=['POST'])
def delete_batch():
    """批量删除被拒绝的条码记录。"""
    data = request.json
    if not data or 'ids' not in data:
        return jsonify({'error': '请提供要删除的记录ID列表'}), 400

    ids = data['ids']
    if not isinstance(ids, list):
        return jsonify({'error': 'ids 必须是列表'}), 400

    failed_files = []
    deleted_count = 0

    for id in ids:
        rejected = session.get(RejectedBarcode, id)
        if not rejected:
            continue

        # 尝试删除文件
        try:
            if os.path.exists(rejected.file_path):
                os.remove(rejected.file_path)
        except OSError:
            failed_files.append(rejected.file_path)

        session.delete(rejected)
        deleted_count += 1

    session.commit()

    return jsonify({
        'message': f'已删除 {deleted_count} 条记录',
        'deleted_count': deleted_count,
        'failed_files': failed_files,
    })


@rejected_bp.route('/delete-all', methods=['POST'])
def delete_all():
    """全选删除被拒绝的条码记录。"""
    data = request.json or {}

    query = session.query(RejectedBarcode)

    if 'barcode' in data and data['barcode']:
        query = query.filter(RejectedBarcode.barcode.like(f"%{data['barcode']}%"))
    if 'scan_root_id' in data and data['scan_root_id']:
        query = query.filter(RejectedBarcode.scan_root_id == data['scan_root_id'])
    if 'start_date' in data and data['start_date']:
        query = query.filter(RejectedBarcode.created_at >= data['start_date'])
    if 'end_date' in data and data['end_date']:
        query = query.filter(RejectedBarcode.created_at <= data['end_date'] + 'T23:59:59')

    # 先取文件路径用于删除文件
    file_paths = [fp for (fp,) in query.with_entities(RejectedBarcode.file_path).all()]
    failed_files = []

    for fp in file_paths:
        try:
            if os.path.exists(fp):
                os.remove(fp)
        except OSError:
            failed_files.append(fp)

    # 批量删除数据库记录
    deleted_count = query.delete()
    session.commit()

    return jsonify({
        'message': f'已删除 {deleted_count} 条记录',
        'deleted_count': deleted_count,
        'failed_files': failed_files,
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
