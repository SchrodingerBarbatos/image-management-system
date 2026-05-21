import os, zipfile
from flask import Blueprint, request, jsonify, send_file
from models import session, Image, ImageVersion, ExportTask
from config import UPLOAD_DIR
from thumbnail import thumbnail_exists, generate_thumbnail, get_thumbnail_path
from datetime import datetime

images_bp = Blueprint('images', __name__)

_SORT_WHITELIST = {'barcode', 'image_type', 'sequence', 'filename', 'ext',
                   'file_size', 'folder_path', 'folder_mtime', 'created_at', 'updated_at'}

@images_bp.route('/images', methods=['GET'])
def list_images():
    q = session.query(Image)
    barcode = request.args.get('barcode')
    if barcode:
        q = q.filter(Image.barcode.like(f'%{barcode}%'))
    image_type = request.args.get('image_type')
    if image_type:
        q = q.filter(Image.image_type == image_type)
    scan_root_id = request.args.get('scan_root_id')
    if scan_root_id:
        q = q.filter(Image.scan_root_id == int(scan_root_id))
    confirmed = request.args.get('confirmed')
    if confirmed is not None:
        q = q.filter(Image.confirmed == (confirmed == 'true'))
    sort = request.args.get('sort', 'created_at')
    if sort not in _SORT_WHITELIST:
        sort = 'created_at'
    col = getattr(Image, sort)
    order = col.desc() if request.args.get('order') == 'desc' else col.asc()
    q = q.order_by(order)
    page = int(request.args.get('page', 1))
    page_size = int(request.args.get('page_size', 50))
    total = q.count()
    items = q.offset((page - 1) * page_size).limit(page_size).all()
    return jsonify({
        'items': [_image_to_dict(img) for img in items],
        'total': total, 'page': page, 'page_size': page_size,
    })

@images_bp.route('/images/<int:img_id>', methods=['GET'])
def get_image(img_id):
    img = session.get(Image, img_id)
    if not img:
        return jsonify({'error': 'not found'}), 404
    versions = session.query(ImageVersion).filter(
        ImageVersion.barcode == img.barcode
    ).order_by(ImageVersion.version_label.desc()).all()
    return jsonify({
        'image': _image_to_dict(img),
        'versions': [{
            'id': v.id, 'barcode': v.barcode, 'version_label': v.version_label,
            'folder_mtime': v.folder_mtime, 'content_hash': v.content_hash,
            'is_latest': v.is_latest, 'created_at': v.created_at,
        } for v in versions],
    })

@images_bp.route('/images/<int:img_id>', methods=['PUT'])
def update_image(img_id):
    img = session.get(Image, img_id)
    if not img:
        return jsonify({'error': 'not found'}), 404
    data = request.json
    if 'image_type' in data:
        img.image_type = data['image_type']
    if 'confirmed' in data:
        img.confirmed = data['confirmed']
    img.updated_at = datetime.now().isoformat()
    session.commit()
    return jsonify(_image_to_dict(img))

@images_bp.route('/images/<int:img_id>', methods=['DELETE'])
def delete_image(img_id):
    img = session.get(Image, img_id)
    if not img:
        return jsonify({'error': 'not found'}), 404
    session.delete(img)
    session.commit()
    return jsonify({'message': 'deleted'})

@images_bp.route('/images/<int:img_id>/file')
def serve_file(img_id):
    img = session.get(Image, img_id)
    if not img:
        return jsonify({'error': 'not found'}), 404
    if not os.path.exists(img.file_path):
        img.status = 'broken'
        session.commit()
        return jsonify({'error': 'file not found on disk'}), 404
    return send_file(img.file_path)

@images_bp.route('/thumbnails/<int:img_id>')
def serve_thumbnail(img_id):
    img = session.get(Image, img_id)
    if not img:
        return jsonify({'error': 'not found'}), 404
    if not os.path.exists(img.file_path):
        img.status = 'broken'
        session.commit()
        return jsonify({'error': 'source file not found'}), 404
    if not thumbnail_exists(img_id):
        generate_thumbnail(img_id, img.file_path)
    return send_file(get_thumbnail_path(img_id), mimetype='image/jpeg')

@images_bp.route('/images/batch-delete', methods=['POST'])
def batch_delete():
    data = request.json
    ids = data.get('ids', [])
    if not ids:
        return jsonify({'error': 'ids required'}), 400
    session.query(Image).filter(Image.id.in_(ids)).delete(synchronize_session='fetch')
    session.commit()
    return jsonify({'message': f'deleted {len(ids)} images'})

@images_bp.route('/images/batch-export', methods=['POST'])
def batch_export():
    data = request.json
    ids = data.get('ids', [])
    image_type = data.get('image_type', '')
    if not ids:
        return jsonify({'error': 'ids required'}), 400
    q = session.query(Image).filter(Image.id.in_(ids))
    if image_type:
        q = q.filter(Image.image_type == image_type)
    imgs = q.all()
    task = ExportTask(status='processing')
    session.add(task)
    session.commit()
    zip_name = f'batch_export_{task.id}.zip'
    zip_path = os.path.join(UPLOAD_DIR, zip_name)
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for img in imgs:
            if os.path.exists(img.file_path):
                arcname = f"{img.barcode}/{img.filename}"
                zf.write(img.file_path, arcname)
    task.status = 'done'
    task.zip_path = zip_path
    session.commit()
    return jsonify({'task_id': task.id})

def _image_to_dict(img):
    return {
        'id': img.id, 'barcode': img.barcode, 'image_type': img.image_type,
        'sequence': img.sequence, 'filename': img.filename, 'ext': img.ext,
        'file_path': img.file_path, 'file_size': img.file_size,
        'md5_hash': img.md5_hash, 'folder_path': img.folder_path,
        'folder_mtime': img.folder_mtime, 'scan_root_id': img.scan_root_id,
        'confirmed': img.confirmed, 'status': img.status,
        'created_at': img.created_at, 'updated_at': img.updated_at,
    }
