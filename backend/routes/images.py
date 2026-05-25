import os, zipfile, time, json, re
from flask import Blueprint, request, jsonify, send_file
from models import session, Image, ImageVersion, ExportTask, BarcodeSetting, ScanRoot
from config import UPLOAD_DIR
from thumbnail import thumbnail_exists, generate_thumbnail, get_thumbnail_path
from versioning import update_versions_for_barcode
from datetime import datetime

images_bp = Blueprint('images', __name__)

_ISO_RE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?$')

_SORT_WHITELIST = {'barcode', 'image_type', 'sequence', 'filename', 'ext',
                   'file_size', 'folder_path', 'folder_mtime', 'created_at', 'updated_at'}

_BARCODE_SORT_WHITELIST = {'barcode', 'main_count', 'detail_count', 'main_versions', 'detail_versions'}

@images_bp.route('/barcodes', methods=['GET'])
def list_barcodes():
    """Aggregate images by barcode. Returns one row per barcode with counts."""
    from sqlalchemy import func, case, desc, asc

    barcode_filter = request.args.get('barcode')
    filters = [Image.status == 'active', ScanRoot.enabled == True]
    if barcode_filter:
        filters.append(Image.barcode.like(f'%{barcode_filter}%'))

    # Subquery for main version counts per barcode
    main_vc_sub = session.query(
        ImageVersion.barcode,
        func.count(ImageVersion.id).label('vc')
    ).filter(ImageVersion.image_type == 'main').group_by(ImageVersion.barcode).subquery()

    # Subquery for detail version counts per barcode
    detail_vc_sub = session.query(
        ImageVersion.barcode,
        func.count(ImageVersion.id).label('vc')
    ).filter(ImageVersion.image_type == 'detail').group_by(ImageVersion.barcode).subquery()

    # Main aggregation query
    q = session.query(
        Image.barcode,
        func.sum(case((Image.image_type == 'main', 1), else_=0)).label('main_count'),
        func.sum(case((Image.image_type == 'detail', 1), else_=0)).label('detail_count'),
        func.coalesce(main_vc_sub.c.vc, 0).label('main_versions'),
        func.coalesce(detail_vc_sub.c.vc, 0).label('detail_versions'),
    ).filter(*filters).join(
        ScanRoot, Image.scan_root_id == ScanRoot.id
    ).outerjoin(
        main_vc_sub, Image.barcode == main_vc_sub.c.barcode
    ).outerjoin(
        detail_vc_sub, Image.barcode == detail_vc_sub.c.barcode
    ).group_by(Image.barcode)

    # Count distinct barcodes for total
    total = session.query(func.count(func.distinct(Image.barcode))).filter(*filters).join(
        ScanRoot, Image.scan_root_id == ScanRoot.id
    ).scalar()

    # Sort
    sort = request.args.get('sort', 'barcode')
    if sort not in _BARCODE_SORT_WHITELIST:
        sort = 'barcode'
    reverse = request.args.get('order') == 'desc'
    sort_col = Image.barcode if sort == 'barcode' else sort
    q = q.order_by(desc(sort_col) if reverse else asc(sort_col))

    # Paginate
    page = int(request.args.get('page', 1))
    page_size = int(request.args.get('page_size', 50))
    rows = q.offset((page - 1) * page_size).limit(page_size).all()

    return jsonify({
        'items': [{
            'barcode': r.barcode,
            'main_count': r.main_count,
            'detail_count': r.detail_count,
            'main_versions': r.main_versions,
            'detail_versions': r.detail_versions,
        } for r in rows],
        'total': total, 'page': page, 'page_size': page_size,
    })

@images_bp.route('/images', methods=['GET'])
def list_images():
    q = session.query(Image).join(
        ScanRoot, Image.scan_root_id == ScanRoot.id
    ).filter(ScanRoot.enabled == True)
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
    root = session.get(ScanRoot, img.scan_root_id)
    if not root:
        return jsonify({'error': 'not found'}), 404
    if not root.enabled:
        return jsonify({'error': 'scan root is disabled'}), 403
    versions = session.query(ImageVersion).filter(
        ImageVersion.barcode == img.barcode
    ).order_by(ImageVersion.image_type.desc(), ImageVersion.folder_mtime.desc()).all()
    return jsonify({
        'image': _image_to_dict(img),
        'versions': [{
            'id': v.id, 'barcode': v.barcode, 'image_type': v.image_type, 'version_label': v.version_label,
            'folder_mtime': v.folder_mtime, 'content_hash': v.content_hash,
            'is_latest': v.is_latest, 'created_at': v.created_at,
            'duplicate_mtimes': json.loads(v.duplicate_mtimes) if v.duplicate_mtimes else [],
        } for v in versions],
    })

@images_bp.route('/images/<int:img_id>', methods=['PUT'])
def update_image(img_id):
    img = session.get(Image, img_id)
    if not img:
        return jsonify({'error': 'not found'}), 404
    root = session.get(ScanRoot, img.scan_root_id)
    if not root:
        return jsonify({'error': 'not found'}), 404
    if not root.enabled:
        return jsonify({'error': 'scan root is disabled'}), 403
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
    root = session.get(ScanRoot, img.scan_root_id)
    if not root:
        return jsonify({'error': 'not found'}), 404
    if not root.enabled:
        return jsonify({'error': 'scan root is disabled'}), 403
    delete_file = request.args.get('delete_file', 'false').lower() == 'true'
    barcode = img.barcode
    if delete_file:
        try:
            os.remove(img.file_path)
        except OSError:
            pass
    session.delete(img)
    session.commit()
    update_versions_for_barcode(barcode)
    return jsonify({'message': 'deleted', 'file_deleted': delete_file})

@images_bp.route('/images/<int:img_id>/file')
def serve_file(img_id):
    img = session.get(Image, img_id)
    if not img:
        return jsonify({'error': 'not found'}), 404
    root = session.get(ScanRoot, img.scan_root_id)
    if not root:
        return jsonify({'error': 'not found'}), 404
    if not root.enabled:
        return jsonify({'error': 'scan root is disabled'}), 403
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
    root = session.get(ScanRoot, img.scan_root_id)
    if not root:
        return jsonify({'error': 'not found'}), 404
    if not root.enabled:
        return jsonify({'error': 'scan root is disabled'}), 403
    if not os.path.exists(img.file_path):
        img.status = 'broken'
        session.commit()
        return jsonify({'error': 'source file not found'}), 404

    thumb_path = get_thumbnail_path(img_id)
    if not thumbnail_exists(img_id):
        ok, md5 = generate_thumbnail(img_id, img.file_path)
        if not ok:
            return jsonify({'error': 'thumbnail generation failed'}), 500
        if md5 and not img.content_md5:
            img.content_md5 = md5
            session.commit()

    # HTTP caching: use thumbnail file's mtime as ETag / Last-Modified
    try:
        stat = os.stat(thumb_path)
        etag = f'"{img_id}-{int(stat.st_mtime)}"'
        last_modified = time.strftime('%a, %d %b %Y %H:%M:%S GMT', time.gmtime(stat.st_mtime))
    except OSError:
        etag = f'"{img_id}"'
        last_modified = None

    # Check If-None-Match / If-Modified-Since for 304
    if_none_match = request.headers.get('If-None-Match', '')
    if etag == if_none_match:
        return '', 304

    if last_modified:
        if_modified_since = request.headers.get('If-Modified-Since', '')
        if if_modified_since and if_modified_since == last_modified:
            return '', 304

    response = send_file(thumb_path, mimetype='image/jpeg')
    response.headers['ETag'] = etag
    response.headers['Cache-Control'] = 'public, max-age=86400'
    if last_modified:
        response.headers['Last-Modified'] = last_modified
    return response

@images_bp.route('/images/batch-delete', methods=['POST'])
def batch_delete():
    data = request.json
    ids = data.get('ids', [])
    if not ids:
        return jsonify({'error': 'ids required'}), 400
    delete_file = data.get('delete_file', False)
    # Collect barcodes before deletion
    barcodes = {r[0] for r in session.query(Image.barcode).filter(
        Image.id.in_(ids)).distinct().all()}
    if delete_file:
        imgs = session.query(Image).filter(Image.id.in_(ids)).all()
        for img in imgs:
            try:
                os.remove(img.file_path)
            except OSError:
                pass
    session.query(Image).filter(Image.id.in_(ids)).delete(synchronize_session='fetch')
    session.commit()
    for bc in barcodes:
        update_versions_for_barcode(bc)
    return jsonify({'message': f'deleted {len(ids)} images'})

@images_bp.route('/images/batch-export', methods=['POST'])
def batch_export():
    data = request.json
    ids = data.get('ids', [])
    image_type = data.get('image_type', '')
    flat = data.get('flat', False)
    if not ids:
        return jsonify({'error': 'ids required'}), 400
    q = session.query(Image).filter(Image.id.in_(ids)).join(
        ScanRoot, Image.scan_root_id == ScanRoot.id
    ).filter(ScanRoot.enabled == True)
    if image_type and image_type != 'all':
        q = q.filter(Image.image_type == image_type)
    imgs = q.all()

    # Filter to single version: user-chosen default, or latest version as fallback
    scanroot_excluded = len(ids) - len(imgs)
    barcodes_in = list(set(img.barcode for img in imgs))
    if barcodes_in:
        # Lazy import to avoid circular dependency (routes.export imports from models)
        from routes.export import filter_to_single_version
        imgs = filter_to_single_version(imgs, barcodes_in, session)
    version_filtered = len(ids) - scanroot_excluded - len(imgs)
    task = ExportTask(status='processing')
    session.add(task)
    session.commit()

    from routes.export import _build_zip
    import threading
    img_data = [(img.file_path, img.barcode, img.image_type, img.sequence, img.ext) for img in imgs]
    threading.Thread(target=_build_zip, args=(task.id, img_data, flat), daemon=True).start()

    return jsonify({'task_id': task.id, 'total': len(imgs), 'scanroot_excluded': scanroot_excluded, 'version_filtered': version_filtered})

@images_bp.route('/barcodes/<barcode>/duplicate-images', methods=['DELETE'])
def delete_duplicate_images(barcode):
    """Delete images from a specific duplicate folder_mtime for a barcode."""
    folder_mtime = request.args.get('folder_mtime', '')
    image_type = request.args.get('image_type', '')
    delete_file = request.args.get('delete_file', 'false').lower() == 'true'

    if not folder_mtime or not image_type:
        return jsonify({'error': 'folder_mtime and image_type are required'}), 400

    if not _ISO_RE.match(folder_mtime):
        return jsonify({'error': 'folder_mtime must be ISO8601 format'}), 400

    imgs = session.query(Image).filter(
        Image.barcode == barcode,
        Image.folder_mtime == folder_mtime,
        Image.image_type == image_type,
    ).join(ScanRoot, Image.scan_root_id == ScanRoot.id).filter(
        ScanRoot.enabled == True
    ).all()

    if not imgs:
        return jsonify({'error': 'no duplicate images found'}), 404

    deleted = 0
    for img in imgs:
        if delete_file:
            try:
                os.remove(img.file_path)
            except OSError:
                pass
        session.delete(img)
        deleted += 1

    session.commit()
    update_versions_for_barcode(barcode)

    return jsonify({'message': f'deleted {deleted} duplicate images', 'deleted': deleted})


def _image_to_dict(img):
    return {
        'id': img.id, 'barcode': img.barcode, 'image_type': img.image_type,
        'sequence': img.sequence, 'filename': img.filename, 'ext': img.ext,
        'file_path': img.file_path, 'file_size': img.file_size,
        'md5_hash': img.md5_hash, 'content_md5': img.content_md5,
        'folder_path': img.folder_path,
        'folder_mtime': img.folder_mtime, 'scan_root_id': img.scan_root_id,
        'confirmed': img.confirmed, 'status': img.status,
        'created_at': img.created_at, 'updated_at': img.updated_at,
    }

@images_bp.route('/versions/<int:version_id>', methods=['DELETE'])
def delete_version(version_id):
    v = session.get(ImageVersion, version_id)
    if not v:
        return jsonify({'error': 'not found'}), 404
    delete_file = request.args.get('delete_file', 'false').lower() == 'true'
    barcode = v.barcode
    folder_mtime = v.folder_mtime

    # Delete all images belonging to this version
    imgs = session.query(Image).filter(
        Image.barcode == barcode,
        Image.folder_mtime == folder_mtime,
        Image.image_type == v.image_type,
    ).all()
    for img in imgs:
        if delete_file:
            try:
                os.remove(img.file_path)
            except OSError:
                pass
        session.delete(img)

    # Delete the version record itself
    session.delete(v)
    session.commit()

    # Re-sequence remaining versions for this barcode
    update_versions_for_barcode(barcode)

    return jsonify({'message': f'deleted version and {len(imgs)} images'})

@images_bp.route('/barcode-settings/<barcode>', methods=['GET'])
def get_barcode_setting(barcode):
    s = session.query(BarcodeSetting).filter(BarcodeSetting.barcode == barcode).first()
    if not s:
        return jsonify({'barcode': barcode, 'default_main_mtime': '', 'default_detail_mtime': ''})
    return jsonify({
        'barcode': s.barcode,
        'default_main_mtime': s.default_main_mtime,
        'default_detail_mtime': s.default_detail_mtime,
    })

@images_bp.route('/barcode-settings/<barcode>', methods=['PUT'])
def update_barcode_setting(barcode):
    data = request.json
    s = session.query(BarcodeSetting).filter(BarcodeSetting.barcode == barcode).first()
    if not s:
        s = BarcodeSetting(barcode=barcode)
        session.add(s)
    if 'default_main_mtime' in data:
        s.default_main_mtime = data['default_main_mtime']
    if 'default_detail_mtime' in data:
        s.default_detail_mtime = data['default_detail_mtime']
    session.commit()
    return jsonify({
        'barcode': s.barcode,
        'default_main_mtime': s.default_main_mtime,
        'default_detail_mtime': s.default_detail_mtime,
    })
