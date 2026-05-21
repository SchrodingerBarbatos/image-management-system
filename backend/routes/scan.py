import os
from flask import Blueprint, request, jsonify
from models import session, ScanRoot, Image
from scanner import scan_root
from versioning import update_all_versions

scan_bp = Blueprint('scan', __name__)

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
    return jsonify({
        'id': root.id, 'path': root.path,
        'recursive': root.recursive, 'enabled': root.enabled
    }), 201

@scan_bp.route('/scan-roots/<int:root_id>', methods=['DELETE'])
def delete_scan_root(root_id):
    root = session.get(ScanRoot, root_id)
    if not root:
        return jsonify({'error': 'not found'}), 404
    session.query(Image).filter(Image.scan_root_id == root_id).delete()
    session.delete(root)
    session.commit()
    return jsonify({'message': 'deleted'})

@scan_bp.route('/scan', methods=['POST'])
def trigger_scan():
    data = request.get_json(silent=True) or {}
    allow_fuzzy = data.get('allow_fuzzy', False)
    root_id = data.get('root_id')
    if root_id:
        result = scan_root(root_id, allow_fuzzy=allow_fuzzy)
        update_all_versions()
        return jsonify(result)
    roots = session.query(ScanRoot).filter(ScanRoot.enabled == True).all()
    total = {'added': 0, 'skipped': 0, 'broken_cleaned': 0}
    for r in roots:
        res = scan_root(r.id, allow_fuzzy=allow_fuzzy)
        for k in total:
            total[k] += res.get(k, 0)
    update_all_versions()
    return jsonify(total)
