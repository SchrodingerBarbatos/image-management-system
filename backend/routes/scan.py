import os, json
from flask import Blueprint, request, jsonify
from models import session, ScanRoot, Image, ScanLog
from scanner import scan_root
from versioning import update_all_versions

scan_bp = Blueprint('scan', __name__)

def _add_log(action, status, message, details=''):
    log = ScanLog(action=action, status=status, message=message, details=details)
    session.add(log)
    session.commit()

@scan_bp.route('/scan-roots', methods=['GET'])
def list_scan_roots():
    roots = session.query(ScanRoot).all()
    return jsonify([{
        'id': r.id, 'path': r.path, 'recursive': r.recursive, 'enabled': r.enabled,
        'allow_fuzzy': r.allow_fuzzy, 'fuzzy_image_type': r.fuzzy_image_type,
    } for r in roots])

@scan_bp.route('/scan-roots', methods=['POST'])
def add_scan_root():
    data = request.json
    if not data or 'path' not in data:
        return jsonify({'error': 'path is required'}), 400
    if not os.path.isdir(data['path']):
        return jsonify({'error': 'path does not exist'}), 400
    root = ScanRoot(
        path=data['path'],
        recursive=data.get('recursive', True),
        enabled=True,
        allow_fuzzy=data.get('allow_fuzzy', False),
        fuzzy_image_type=data.get('fuzzy_image_type', 'main'),
    )
    session.add(root)
    session.commit()
    _add_log('add_root', 'success', f'已添加扫描目录: {root.path}')
    return jsonify({
        'id': root.id, 'path': root.path,
        'recursive': root.recursive, 'enabled': root.enabled,
        'allow_fuzzy': root.allow_fuzzy, 'fuzzy_image_type': root.fuzzy_image_type,
    }), 201

@scan_bp.route('/scan-roots/<int:root_id>', methods=['PUT'])
def update_scan_root(root_id):
    root = session.get(ScanRoot, root_id)
    if not root:
        return jsonify({'error': 'not found'}), 404
    data = request.json
    if 'recursive' in data:
        root.recursive = data['recursive']
    if 'enabled' in data:
        root.enabled = data['enabled']
        enabled_changed = True
    else:
        enabled_changed = False
    if 'allow_fuzzy' in data:
        root.allow_fuzzy = data['allow_fuzzy']
    if 'fuzzy_image_type' in data:
        root.fuzzy_image_type = data['fuzzy_image_type']
    session.commit()
    if enabled_changed:
        update_all_versions()
    return jsonify({
        'id': root.id, 'path': root.path,
        'recursive': root.recursive, 'enabled': root.enabled,
        'allow_fuzzy': root.allow_fuzzy, 'fuzzy_image_type': root.fuzzy_image_type,
    })

@scan_bp.route('/scan-roots/<int:root_id>', methods=['DELETE'])
def delete_scan_root(root_id):
    root = session.get(ScanRoot, root_id)
    if not root:
        return jsonify({'error': 'not found'}), 404
    session.query(Image).filter(Image.scan_root_id == root_id).delete()
    session.delete(root)
    session.commit()
    _add_log('delete_root', 'info', f'已删除扫描目录: {root.path}')
    return jsonify({'message': 'deleted'})

@scan_bp.route('/scan-roots/check-new', methods=['POST'])
def check_new_roots():
    """Check which root_ids have no images (never scanned)."""
    data = request.get_json(silent=True) or {}
    root_ids = data.get('root_ids', [])
    if not root_ids:
        return jsonify({'new_root_ids': []})
    scanned = session.query(Image.scan_root_id).filter(
        Image.scan_root_id.in_(root_ids)
    ).distinct().all()
    scanned_ids = {r[0] for r in scanned}
    new_ids = [rid for rid in root_ids if rid not in scanned_ids]
    return jsonify({'new_root_ids': new_ids})

@scan_bp.route('/scan', methods=['POST'])
def trigger_scan():
    data = request.get_json(silent=True) or {}
    allow_fuzzy = data.get('allow_fuzzy', False)
    root_ids = data.get('root_ids')
    scan_mode = data.get('scan_mode', 'full')
    full_scan = scan_mode == 'full'

    _add_log('scan', 'info', f"扫描开始 - {'全量' if full_scan else '增量'}模式", json.dumps({'allow_fuzzy': allow_fuzzy, 'root_ids': root_ids}))

    try:
        if not root_ids:
            return jsonify({'error': '请指定要扫描的目录'}), 400
        roots = session.query(ScanRoot).filter(ScanRoot.id.in_(root_ids)).all()

        if not roots:
            return jsonify({'error': '没有可扫描的目录'}), 400

        total = {'added': 0, 'skipped': 0, 'broken_cleaned': 0}
        for r in roots:
            res = scan_root(r.id, allow_fuzzy=allow_fuzzy, full_scan=full_scan)
            for k in total:
                total[k] += res.get(k, 0)
        update_all_versions()
        _add_log('scan', 'success',
            f"扫描完成: 新增 {total['added']}, 跳过 {total['skipped']}",
            json.dumps(total))
        return jsonify(total)
    except Exception as e:
        _add_log('scan', 'error', f'扫描失败: {str(e)}')
        return jsonify({'error': str(e)}), 500

@scan_bp.route('/scan-logs', methods=['GET'])
def list_scan_logs():
    logs = session.query(ScanLog).order_by(ScanLog.created_at.desc()).limit(100).all()
    return jsonify([{
        'id': l.id, 'action': l.action, 'status': l.status,
        'message': l.message, 'details': l.details, 'created_at': l.created_at,
    } for l in logs])
