from flask import Blueprint, request, jsonify
from models import session, Image, ScanRoot
from routes.batch import _maybe_record_deleted_folder
from versioning import update_all_versions
from routes._utils import (
    JSONPayloadError,
    json_payload_error_response,
    require_json_array,
)

pending_bp = Blueprint('pending', __name__)


def _pending_query(sess):
    """Return the one authoritative pending-image business predicate."""
    return sess.query(Image).join(
        ScanRoot, Image.scan_root_id == ScanRoot.id
    ).filter(
        Image.confirmed.is_(False),
        Image.status == 'active',
        ScanRoot.enabled.is_(True),
    )

@pending_bp.route('/pending/count', methods=['GET'])
def pending_count():
    count = _pending_query(session).count()
    return jsonify({'count': count})

@pending_bp.route('/pending', methods=['GET'])
def list_pending():
    imgs = _pending_query(session).order_by(Image.barcode, Image.sequence).all()
    return jsonify([_pending_to_dict(img) for img in imgs])

@pending_bp.route('/pending/confirm', methods=['POST'])
def confirm_pending():
    try:
        data = require_json_array()
    except JSONPayloadError as e:
        return json_payload_error_response(e)
    if not data:
        return jsonify({'error': 'array of {id, image_type} required'}), 400

    # Validate all items before committing
    unique_items = []
    seen_ids = set()
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            return jsonify({'error': f'item {i}: must be an object'}), 400
        if isinstance(item.get('id'), bool) or not isinstance(item.get('id'), int):
            return jsonify({'error': f'item {i}: id must be an integer'}), 400
        img_type = item.get('image_type', 'main')
        if img_type not in ('main', 'detail'):
            return jsonify({'error': f'item {i}: image_type must be "main" or "detail", got {img_type!r}'}), 400
        if item['id'] not in seen_ids:
            seen_ids.add(item['id'])
            unique_items.append({'id': item['id'], 'image_type': img_type})

    ids = [item['id'] for item in unique_items]
    pending_images = _pending_query(session).filter(Image.id.in_(ids)).all()
    found_ids = {img.id for img in pending_images}
    invalid_ids = [img_id for img_id in ids if img_id not in found_ids]
    if invalid_ids:
        return jsonify({
            'error': '部分图片已不在待确认列表中，整批未提交',
            'invalid_ids': invalid_ids,
        }), 409

    image_type_by_id = {item['id']: item['image_type'] for item in unique_items}
    for img in pending_images:
        img.image_type = image_type_by_id[img.id]
        img.confirmed = True
    confirmed = len(pending_images)
    session.commit()
    update_all_versions()
    return jsonify({'message': f'confirmed {confirmed} images', 'confirmed': confirmed})

@pending_bp.route('/pending/<int:img_id>', methods=['DELETE'])
def ignore_pending(img_id):
    img = _pending_query(session).filter(Image.id == img_id).one_or_none()
    if not img:
        if session.get(Image, img_id) is not None:
            return jsonify({'error': '图片已不在待确认列表中'}), 409
        return jsonify({'error': 'not found'}), 404
    barcode = img.barcode
    image_type = img.image_type
    folder_ctime = img.folder_ctime
    root_id = img.scan_root_id
    session.delete(img)
    _maybe_record_deleted_folder(session, barcode, image_type, folder_ctime, {root_id})
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
