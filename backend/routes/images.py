import os, zipfile, time, json, re, threading
from flask import Blueprint, request, jsonify, send_file
from models import session, Image, ImageVersion, ExportTask, BarcodeSetting, ScanRoot
from config import UPLOAD_DIR
from thumbnail import thumbnail_exists, generate_thumbnail, get_thumbnail_path
from versioning import update_versions_for_barcode
from db_retry import with_sqlite_lock_retry
from routes._utils import parse_pagination, safe_remove_image_file
from datetime import datetime

images_bp = Blueprint('images', __name__)

_ISO_RE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?$')

# Per-image locks to prevent concurrent thumbnail generation for the same image.
# Concurrent generates cause PermissionError on Windows (writes to the same file)
# and unnecessary duplicate work.
_thumb_gen_locks: dict[int, threading.Lock] = {}
_thumb_gen_locks_guard = threading.Lock()

# TTL cache for ScanRoot.enabled — these rarely change, but every thumbnail/file
# request needs to check them.  Avoids one ScanRoot query per image request.
_sr_enabled_cache: dict[int, tuple[bool, float]] = {}
_SR_CACHE_TTL = 30.0  # seconds


def _is_root_enabled(root_id: int) -> bool:
    """Check ScanRoot.enabled with a short TTL cache to avoid N+1 queries."""
    now = time.monotonic()
    cached = _sr_enabled_cache.get(root_id)
    if cached is not None:
        enabled, ts = cached
        if now - ts < _SR_CACHE_TTL:
            return enabled
    root = session.get(ScanRoot, root_id)
    if not root:
        _sr_enabled_cache[root_id] = (False, now)
        return False
    enabled = root.enabled
    _sr_enabled_cache[root_id] = (enabled, now)
    return enabled


def _invalidate_root_cache(root_id: int | None = None):
    """Invalidate the ScanRoot.enabled cache. Called after ScanRoot changes."""
    if root_id is not None:
        _sr_enabled_cache.pop(root_id, None)
    else:
        _sr_enabled_cache.clear()


def _get_thumb_lock(img_id: int) -> threading.Lock:
    """Return a per-image lock, creating one if this image_id hasn't been seen."""
    with _thumb_gen_locks_guard:
        lock = _thumb_gen_locks.get(img_id)
        if lock is None:
            lock = threading.Lock()
            _thumb_gen_locks[img_id] = lock
        return lock

_SORT_WHITELIST = {'barcode', 'image_type', 'sequence', 'filename', 'ext',
                   'file_size', 'folder_path', 'folder_ctime', 'created_at', 'updated_at'}

_BARCODE_SORT_WHITELIST = {'barcode', 'main_count', 'detail_count', 'main_versions', 'detail_versions'}

_MAX_PAGE_SIZE = 500

_IN_CHUNK_SIZE = 500


@images_bp.route('/barcodes', methods=['GET'])
def list_barcodes():
    """Aggregate images by barcode. Returns one row per barcode with counts."""
    from sqlalchemy import func, case, desc, asc

    barcode_filter = request.args.get('barcode')
    filters = [Image.status == 'active', ScanRoot.enabled == True]
    if barcode_filter:
        filters.append(Image.barcode.like(f'%{barcode_filter}%'))

    # Single subquery for both main+detail version counts (was 2 subqueries + 2 outerjoins)
    ver_sub = session.query(
        ImageVersion.barcode,
        func.sum(case((ImageVersion.image_type == 'main', 1), else_=0)).label('main_vc'),
        func.sum(case((ImageVersion.image_type == 'detail', 1), else_=0)).label('detail_vc'),
    ).group_by(ImageVersion.barcode).subquery()

    # Main aggregation query
    q = session.query(
        Image.barcode,
        func.sum(case((Image.image_type == 'main', 1), else_=0)).label('main_count'),
        func.sum(case((Image.image_type == 'detail', 1), else_=0)).label('detail_count'),
        func.coalesce(ver_sub.c.main_vc, 0).label('main_versions'),
        func.coalesce(ver_sub.c.detail_vc, 0).label('detail_versions'),
    ).filter(*filters).join(
        ScanRoot, Image.scan_root_id == ScanRoot.id
    ).outerjoin(
        ver_sub, Image.barcode == ver_sub.c.barcode
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
    # sort_col may be a string label (e.g. 'main_count'), resolved by SQLAlchemy via query labels.
    # Must stay in sync with label names in the SELECT clause above.
    q = q.order_by(desc(sort_col) if reverse else asc(sort_col))

    # Paginate — cap page_size to prevent accidental large queries
    try:
        page, page_size = parse_pagination(default_page_size=50, max_page_size=_MAX_PAGE_SIZE)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
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

@images_bp.route('/barcodes/image-ids', methods=['POST'])
def batch_barcode_image_ids():
    """Return all image IDs for a list of barcodes in a single request.
    Replaces N paginated /images calls for batch operations."""
    data = request.json
    barcodes = data.get('barcodes', [])
    if not barcodes:
        return jsonify({'image_ids': [], 'barcode_counts': {}})

    # Chunked IN query to avoid oversized SQL
    image_ids = []
    barcode_counts = {}
    for i in range(0, len(barcodes), _IN_CHUNK_SIZE):
        chunk = barcodes[i:i + _IN_CHUNK_SIZE]
        rows = session.query(
            Image.barcode,
            Image.id,
        ).filter(
            Image.barcode.in_(chunk),
            Image.status == 'active',
            Image.confirmed == True,
        ).join(ScanRoot, Image.scan_root_id == ScanRoot.id).filter(
            ScanRoot.enabled == True,
        ).all()
        for barcode, img_id in rows:
            image_ids.append(img_id)
            barcode_counts[barcode] = barcode_counts.get(barcode, 0) + 1

    return jsonify({'image_ids': image_ids, 'barcode_counts': barcode_counts})


@images_bp.route('/images', methods=['GET'])
def list_images():
    q = session.query(Image).join(
        ScanRoot, Image.scan_root_id == ScanRoot.id
    ).filter(ScanRoot.enabled == True)
    barcode_exact = request.args.get('barcode_exact')
    barcode = request.args.get('barcode')
    if barcode_exact:
        q = q.filter(Image.barcode == barcode_exact)
    elif barcode:
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
    # Cap page_size to prevent accidental large queries
    page_size = min(int(request.args.get('page_size', 50)), _MAX_PAGE_SIZE)
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
    ).order_by(ImageVersion.image_type.desc(), ImageVersion.folder_ctime.desc()).all()
    return jsonify({
        'image': _image_to_dict(img),
        'versions': [{
            'id': v.id, 'barcode': v.barcode, 'image_type': v.image_type, 'version_label': v.version_label,
            'folder_ctime': v.folder_ctime, 'content_hash': v.content_hash,
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

    # Track whether version-relevant fields changed
    version_dirty = False
    old_image_type = img.image_type
    old_confirmed = img.confirmed

    if 'image_type' in data:
        if data['image_type'] not in ('main', 'detail'):
            return jsonify({'error': 'image_type must be "main" or "detail"'}), 400
        if data['image_type'] != old_image_type:
            img.image_type = data['image_type']
            version_dirty = True
    if 'confirmed' in data:
        if data['confirmed'] != old_confirmed:
            img.confirmed = data['confirmed']
            version_dirty = True
    img.updated_at = datetime.now().isoformat()
    session.commit()

    # Rebuild versions if version-relevant fields changed
    if version_dirty:
        update_versions_for_barcode(img.barcode)

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
    folder_ctime = img.folder_ctime
    image_type = img.image_type
    if delete_file:
        safe_remove_image_file(img, session)
    session.delete(img)
    from routes.batch import _record_deleted_folder
    _record_deleted_folder(session, barcode, image_type, folder_ctime)
    session.commit()
    update_versions_for_barcode(barcode)
    return jsonify({'message': 'deleted', 'file_deleted': delete_file})

@images_bp.route('/images/<int:img_id>/file')
def serve_file(img_id):
    img = session.get(Image, img_id)
    if not img:
        return jsonify({'error': 'not found'}), 404
    if not _is_root_enabled(img.scan_root_id):
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
    if not _is_root_enabled(img.scan_root_id):
        return jsonify({'error': 'scan root is disabled'}), 403
    if not os.path.exists(img.file_path):
        img.status = 'broken'
        session.commit()
        return jsonify({'error': 'source file not found'}), 404

    thumb_path = get_thumbnail_path(img_id)
    if not thumbnail_exists(img_id):
        # Serialize concurrent requests for the same image's thumbnail.
        # First waiter generates; subsequent waiters find the file already exists.
        lock = _get_thumb_lock(img_id)
        with lock:
            # Re-check inside lock: another thread may have generated it
            if not thumbnail_exists(img_id):
                ok, md5, phash = generate_thumbnail(img_id, img.file_path)
                if not ok:
                    return jsonify({'error': 'thumbnail generation failed'}), 500
                changed = False
                if md5 and not img.content_md5:
                    img.content_md5 = md5
                    changed = True
                if phash and not img.phash:
                    img.phash = phash
                    changed = True
                if changed:
                    session.commit()
        # Evict lock entry: thumbnail now exists on disk, so future requests
        # skip this entire block. Prevents unbounded growth of _thumb_gen_locks.
        with _thumb_gen_locks_guard:
            _thumb_gen_locks.pop(img_id, None)

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

    # Reject if any IDs belong to disabled scan roots
    disabled = session.query(Image.id).join(
        ScanRoot, Image.scan_root_id == ScanRoot.id
    ).filter(
        Image.id.in_(ids), ScanRoot.enabled == False
    ).all()
    disabled_ids = {r[0] for r in disabled}
    if disabled_ids:
        return jsonify({
            'error': '部分图片属于已禁用的扫描目录',
            'disabled_ids': list(disabled_ids),
        }), 403

    # Collect barcodes before deletion
    barcodes = {r[0] for r in session.query(Image.barcode).filter(
        Image.id.in_(ids)).distinct().all()}
    deleted_folder_keys = {
        (r.barcode, r.image_type, r.folder_ctime)
        for r in session.query(Image.barcode, Image.image_type, Image.folder_ctime)
        .filter(Image.id.in_(ids)).distinct().all()
    }
    if delete_file:
        imgs = session.query(Image).filter(Image.id.in_(ids)).all()
        for img in imgs:
            safe_remove_image_file(img, session)
    deleted = session.query(Image).filter(Image.id.in_(ids)).delete(synchronize_session='fetch')
    from routes.batch import _record_deleted_folder
    for bc, it, ctime in deleted_folder_keys:
        _record_deleted_folder(session, bc, it, ctime)
    session.commit()

    for bc in barcodes:
        update_versions_for_barcode(bc)
    return jsonify({'message': f'deleted {deleted} images', 'deleted': deleted})

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
    # No type filter for 'all' or 'detail' — detail exports need main images
    # available as the fallback source (see _plan_zip_entries in routes.export)
    if image_type and image_type not in ('all', 'detail'):
        q = q.filter(Image.image_type == image_type)
    imgs = q.all()

    # Filter to single version: user-chosen default, or latest version as fallback
    scanroot_excluded = len(ids) - len(imgs)
    barcodes_in = list(set(img.barcode for img in imgs))
    if barcodes_in:
        from routes.export import filter_to_single_version
        imgs = filter_to_single_version(imgs, barcodes_in, session)
    version_filtered = len(ids) - scanroot_excluded - len(imgs)

    from routes.export import _compute_barcode_counts, _export_lock
    barcode_counts = _compute_barcode_counts(imgs)
    # When exporting a specific type, zero out the other type for the report
    if image_type in ('main', 'detail'):
        other = 'detail' if image_type == 'main' else 'main'
        for bc in barcode_counts:
            barcode_counts[bc][other] = 0

    # Concurrency guard: same lock + running check as /export/zip
    with _export_lock:
        running = session.query(ExportTask).filter(ExportTask.status == 'processing').first()
        if running:
            return jsonify({'error': '已有导出任务正在执行中，请等待完成'}), 409

        task = ExportTask(status='processing', barcode_data=json.dumps(barcode_counts, ensure_ascii=False))
        session.add(task)
        session.commit()

    from routes.export import _build_zip, _plan_zip_entries
    import threading
    img_data = [(img.file_path, img.barcode, img.image_type, img.sequence, img.ext) for img in imgs]
    # Accurate entry count for the sync response — detail exports may rename
    # main images as fallback, so the ZIP entry count can differ from len(imgs)
    planned_entries, _ = _plan_zip_entries(img_data, flat, image_type or 'all')

    threading.Thread(target=_build_zip, args=(task.id, img_data, flat, image_type or 'all'), daemon=True).start()

    return jsonify({'task_id': task.id, 'total': len(planned_entries), 'scanroot_excluded': scanroot_excluded, 'version_filtered': version_filtered})

@images_bp.route('/barcodes/<barcode>/duplicate-images', methods=['DELETE'])
def delete_duplicate_images(barcode):
    """Delete images from a specific duplicate folder_ctime for a barcode."""
    folder_ctime = request.args.get('folder_ctime', '')
    image_type = request.args.get('image_type', '')
    delete_file = request.args.get('delete_file', 'false').lower() == 'true'

    if not folder_ctime or not image_type:
        return jsonify({'error': 'folder_ctime and image_type are required'}), 400

    if not _ISO_RE.match(folder_ctime):
        return jsonify({'error': 'folder_ctime must be ISO8601 format'}), 400

    imgs = session.query(Image).filter(
        Image.barcode == barcode,
        Image.folder_ctime == folder_ctime,
        Image.image_type == image_type,
    ).all()

    if not imgs:
        return jsonify({'error': 'no duplicate images found'}), 404

    deleted = 0
    for img in imgs:
        if delete_file:
            safe_remove_image_file(img, session)
        session.delete(img)
        deleted += 1

    from routes.batch import _record_deleted_folder
    _record_deleted_folder(session, barcode, image_type, folder_ctime)
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
        'folder_ctime': img.folder_ctime, 'scan_root_id': img.scan_root_id,
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
    folder_ctime = v.folder_ctime

    # Delete all images belonging to this version
    imgs = session.query(Image).filter(
        Image.barcode == barcode,
        Image.folder_ctime == folder_ctime,
        Image.image_type == v.image_type,
    ).all()
    for img in imgs:
        if delete_file:
            safe_remove_image_file(img, session)
        session.delete(img)

    # Delete the version record itself
    session.delete(v)
    from routes.batch import _record_deleted_folder
    _record_deleted_folder(session, barcode, v.image_type, folder_ctime)
    session.commit()

    # Re-sequence remaining versions for this barcode
    update_versions_for_barcode(barcode)

    return jsonify({'message': f'deleted version and {len(imgs)} images'})

@images_bp.route('/barcode-settings/<barcode>', methods=['GET'])
def get_barcode_setting(barcode):
    s = session.query(BarcodeSetting).filter(BarcodeSetting.barcode == barcode).first()
    if not s:
        return jsonify({'barcode': barcode, 'default_main_ctime': '', 'default_detail_ctime': ''})
    return jsonify({
        'barcode': s.barcode,
        'default_main_ctime': s.default_main_ctime,
        'default_detail_ctime': s.default_detail_ctime,
    })

@images_bp.route('/barcode-settings/<barcode>', methods=['PUT'])
def update_barcode_setting(barcode):
    data = request.json
    s = session.query(BarcodeSetting).filter(BarcodeSetting.barcode == barcode).first()
    if not s:
        s = BarcodeSetting(barcode=barcode)
        session.add(s)
    if 'default_main_ctime' in data:
        s.default_main_ctime = data['default_main_ctime']
    if 'default_detail_ctime' in data:
        s.default_detail_ctime = data['default_detail_ctime']
    session.commit()
    return jsonify({
        'barcode': s.barcode,
        'default_main_ctime': s.default_main_ctime,
        'default_detail_ctime': s.default_detail_ctime,
    })
