from flask import Blueprint, request, jsonify
from models import session, Image, ScanRoot
from versioning import update_all_versions

pending_bp = Blueprint('pending', __name__)

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
    data = request.json  # [{id, image_type}, ...]
    if not data:
        return jsonify({'error': 'array of {id, image_type} required'}), 400
    for item in data:
        img = session.get(Image, item['id'])
        if img and not img.confirmed:
            img.image_type = item.get('image_type', 'main')
            img.confirmed = True
    session.commit()
    update_all_versions()
    return jsonify({'message': f'confirmed {len(data)} images'})

@pending_bp.route('/pending/<int:img_id>', methods=['DELETE'])
def ignore_pending(img_id):
    img = session.get(Image, img_id)
    if not img:
        return jsonify({'error': 'not found'}), 404
    session.delete(img)
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
