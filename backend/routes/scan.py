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
        'id': r.id, 'path': r.path, 'recursive': r.recursive, 'enabled': r.enabled
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
    )
    session.add(root)
    session.commit()
    _add_log('add_root', 'success', f'已添加扫描目录: {root.path}')
    return jsonify({
        'id': root.id, 'path': root.path,
        'recursive': root.recursive, 'enabled': root.enabled
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
    session.commit()
    return jsonify({
        'id': root.id, 'path': root.path,
        'recursive': root.recursive, 'enabled': root.enabled
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

@scan_bp.route('/scan', methods=['POST'])
def trigger_scan():
    data = request.get_json(silent=True) or {}
    allow_fuzzy = data.get('allow_fuzzy', False)
    root_id = data.get('root_id')

    _add_log('scan', 'info', '扫描开始', json.dumps({'allow_fuzzy': allow_fuzzy}))

    try:
        if root_id:
            result = scan_root(root_id, allow_fuzzy=allow_fuzzy)
            update_all_versions()
            _add_log('scan', 'success',
                f"扫描完成: 新增 {result.get('added',0)}, 跳过 {result.get('skipped',0)}",
                json.dumps(result))
            return jsonify(result)
        roots = session.query(ScanRoot).filter(ScanRoot.enabled == True).all()
        total = {'added': 0, 'skipped': 0, 'broken_cleaned': 0}
        for r in roots:
            res = scan_root(r.id, allow_fuzzy=allow_fuzzy)
            for k in total:
                total[k] += res.get(k, 0)
        update_all_versions()
        _add_log('scan', 'success',
            f"全量扫描完成: 新增 {total['added']}, 跳过 {total['skipped']}",
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
