from flask import Blueprint, request, jsonify
from models import session, Image, ScanRoot
from routes.batch import _maybe_record_deleted_folder
from versioning import update_all_versions

pending_bp = Blueprint('pending', __name__)

@pending_bp.route('/pending/count', methods=['GET'])
def pending_count():
    count = session.query(Image).filter(
        Image.confirmed == False, Image.status == 'active'
    ).join(ScanRoot, Image.scan_root_id == ScanRoot.id).filter(
        ScanRoot.enabled == True
    ).count()
    return jsonify({'count': count})

@pending_bp.route('/pending', methods=['GET'])
def list_pending():
    imgs = session.query(Image).filter(
        Image.confirmed == False, Image.status == 'active'
    ).join(ScanRoot, Image.scan_root_id == ScanRoot.id).filter(
        ScanRoot.enabled == True
    ).order_by(Image.barcode, Image.sequence).all()
    return jsonify([_pending_to_dict(img) for img in imgs])

@pending_bp.route('/pending/confirm', methods=['POST'])
def confirm_pending():
    data = request.json
    if not isinstance(data, list) or not data:
        return jsonify({'error': 'array of {id, image_type} required'}), 400

    # Validate all items before committing
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            return jsonify({'error': f'item {i}: must be an object'}), 400
        if not isinstance(item.get('id'), int):
            return jsonify({'error': f'item {i}: id must be an integer'}), 400
        img_type = item.get('image_type', 'main')
        if img_type not in ('main', 'detail'):
            return jsonify({'error': f'item {i}: image_type must be "main" or "detail", got {img_type!r}'}), 400

    confirmed = 0
    for item in data:
        img = session.get(Image, item['id'])
        if img and not img.confirmed:
            img.image_type = item.get('image_type', 'main')
            img.confirmed = True
            confirmed += 1
    session.commit()
    update_all_versions()
    return jsonify({'message': f'confirmed {confirmed} images', 'confirmed': confirmed})

@pending_bp.route('/pending/<int:img_id>', methods=['DELETE'])
def ignore_pending(img_id):
    img = session.get(Image, img_id)
    if not img:
        return jsonify({'error': 'not found'}), 404
    barcode = img.barcode
    image_type = img.image_type
    folder_ctime = img.folder_ctime
    session.delete(img)
    _maybe_record_deleted_folder(session, barcode, image_type, folder_ctime)
    session.commit()
    return jsonify({'message': 'ignored'})

def _pending_to_dict(img):
    return {
        'id': img.id, 'barcode': img.barcode, 'sequence': img.sequence,
        'filename': img.filename, 'ext': img.ext,
        'file_path': img.file_path, 'file_size': img.file_size,
        'folder_path': img.folder_path, 'scan_root_id': img.scan_root_id,
        'created_at': img.created_at,
    }
