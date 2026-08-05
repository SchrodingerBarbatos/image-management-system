import os, zipfile, time, json, re, threading
from flask import Blueprint, request, jsonify, send_file
from models import session, Image, ImageVersion, ExportTask, BarcodeSetting, ScanRoot
from config import UPLOAD_DIR
from thumbnail import thumbnail_exists, generate_thumbnail, get_thumbnail_path
from versioning import update_versions_for_barcode
from db_retry import with_sqlite_lock_retry
from routes._utils import (
    JSONPayloadError,
    exportable_image_query,
    json_payload_error_response,
    parse_pagination,
    require_json_object,
    require_positive_int_list,
    safe_remove_image_file,
)
from datetime import datetime

images_bp = Blueprint('images', __name__)

_ISO_RE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?$')

# Per-image locks to prevent concurrent thumbnail generation for the same image.
# Concurrent generates cause PermissionError on Windows (writes to the same file)
# and unnecessary duplicate work.
class _ThumbLockEntry:
    """A per-image lock plus the number of request leases using it."""

    def __init__(self):
        self.lock = threading.Lock()
        self.refcount = 0


_thumb_gen_locks: dict[int, _ThumbLockEntry] = {}
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


def _get_thumb_lock(img_id: int) -> _ThumbLockEntry:
    """Acquire a reference-counted per-image thumbnail lock lease."""
    with _thumb_gen_locks_guard:
        entry = _thumb_gen_locks.get(img_id)
        if entry is None:
            entry = _ThumbLockEntry()
            _thumb_gen_locks[img_id] = entry
        entry.refcount += 1
        return entry


def _release_thumb_lock(img_id: int, entry: _ThumbLockEntry):
    """Release a thumbnail lock lease and remove only the same idle entry."""
    with _thumb_gen_locks_guard:
        current = _thumb_gen_locks.get(img_id)
        if current is not entry:
            return
        entry.refcount -= 1
        if entry.refcount <= 0:
            _thumb_gen_locks.pop(img_id, None)

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
    try:
        data = require_json_object()
    except JSONPayloadError as e:
        return json_payload_error_response(e)
    barcodes = data.get('barcodes', [])
    if not isinstance(barcodes, list) or any(not isinstance(b, str) or not b for b in barcodes):
        return jsonify({'error': 'barcodes 必须为非空字符串数组'}), 400
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
    ).filter(
        ScanRoot.enabled == True,
        Image.status == 'active',
    )
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
        try:
            scan_root_id = int(scan_root_id)
        except (ValueError, TypeError):
            return jsonify({'error': 'scan_root_id 必须为正整数'}), 400
        if scan_root_id < 1:
            return jsonify({'error': 'scan_root_id 必须为正整数'}), 400
        q = q.filter(Image.scan_root_id == scan_root_id)
    confirmed = request.args.get('confirmed')
    if confirmed is not None:
        q = q.filter(Image.confirmed == (confirmed == 'true'))
    sort = request.args.get('sort', 'created_at')
    if sort not in _SORT_WHITELIST:
        sort = 'created_at'
    col = getattr(Image, sort)
    order = col.desc() if request.args.get('order') == 'desc' else col.asc()
    q = q.order_by(order)
    try:
        page, page_size = parse_pagination(default_page_size=50, max_page_size=_MAX_PAGE_SIZE)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
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
    try:
        data = require_json_object()
    except JSONPayloadError as e:
        return json_payload_error_response(e)

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
        if not isinstance(data['confirmed'], bool):
            return jsonify({'error': 'confirmed must be a boolean'}), 400
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
        ok, reason = safe_remove_image_file(img, session)
        if not ok:
            return jsonify({'error': f'文件删除失败，已取消数据库删除: {reason}'}), 403
    root_id = img.scan_root_id
    session.delete(img)
    from routes.batch import _maybe_record_deleted_folder
    _maybe_record_deleted_folder(session, barcode, image_type, folder_ctime, {root_id})
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
    root = session.get(ScanRoot, img.scan_root_id)
    if not root:
        return jsonify({'error': 'not found'}), 404
    from routes._utils import is_path_under_root
    ok, reason = is_path_under_root(img.file_path, root.path)
    if not ok:
        return jsonify({'error': reason or '文件路径不在所属扫描目录下'}), 403
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
    root = session.get(ScanRoot, img.scan_root_id)
    if not root:
        return jsonify({'error': 'not found'}), 404
    from routes._utils import is_path_under_root
    ok, reason = is_path_under_root(img.file_path, root.path)
    if not ok:
        return jsonify({'error': reason or '文件路径不在所属扫描目录下'}), 403
    if not os.path.exists(img.file_path):
        img.status = 'broken'
        session.commit()
        return jsonify({'error': 'source file not found'}), 404

    thumb_path = get_thumbnail_path(img_id)
    if not thumbnail_exists(img_id):
        # Serialize concurrent requests for the same image's thumbnail.
        # First waiter generates; subsequent waiters find the file already exists.
        lock_entry = _get_thumb_lock(img_id)
        try:
            with lock_entry.lock:
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
        finally:
            # Keep the shared entry alive while waiters still hold a lease.
            # It is removed only when the last waiter exits, including failure.
            _release_thumb_lock(img_id, lock_entry)

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
    try:
        data = require_json_object()
        ids = require_positive_int_list(data.get('ids'), 'ids')
    except JSONPayloadError as e:
        return json_payload_error_response(e)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    delete_file = data.get('delete_file', False)
    if not isinstance(delete_file, bool):
        return jsonify({'error': 'delete_file must be a boolean'}), 400

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
    if delete_file:
        imgs = session.query(Image).filter(Image.id.in_(ids)).all()
        ok_ids = []
        failed_items = []
        for img in imgs:
            ok, reason = safe_remove_image_file(img, session)
            if ok:
                ok_ids.append(img.id)
            else:
                failed_items.append({'id': img.id, 'file_path': img.file_path, 'reason': reason})
        delete_ids = ok_ids
    else:
        delete_ids = ids
        failed_items = []

    if not delete_ids:
        # Nothing to delete — skip DB delete, deleted_folders, version update
        return jsonify({
            'message': 'deleted 0 images', 'deleted': 0,
            'file_deleted': delete_file, 'failed_items': failed_items,
        })

    # Collect barcodes and folder keys ONLY from images that will actually be deleted
    rows = session.query(
        Image.id, Image.barcode, Image.image_type, Image.folder_ctime, Image.scan_root_id,
    ).filter(Image.id.in_(delete_ids)).all()
    barcodes = {r.barcode for r in rows}
    # key -> set of scan_root_ids that had images deleted
    deleted_folder_roots: dict[tuple, set] = {}
    for r in rows:
        key = (r.barcode, r.image_type, r.folder_ctime)
        deleted_folder_roots.setdefault(key, set()).add(r.scan_root_id)

    deleted = session.query(Image).filter(Image.id.in_(delete_ids)).delete(synchronize_session='fetch')
    from routes.batch import _maybe_record_deleted_folder
    for (bc, it, ctime), root_ids in deleted_folder_roots.items():
        _maybe_record_deleted_folder(session, bc, it, ctime, root_ids)
    session.commit()

    for bc in barcodes:
        update_versions_for_barcode(bc)
    return jsonify({
        'message': f'deleted {deleted} images',
        'deleted': deleted,
        'file_deleted': delete_file,
        'failed_items': failed_items,
    })

@images_bp.route('/images/batch-export', methods=['POST'])
def batch_export():
    try:
        data = require_json_object()
        ids = require_positive_int_list(data.get('ids'), 'ids')
    except JSONPayloadError as e:
        return json_payload_error_response(e)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    image_type = data.get('image_type', '')
    flat = data.get('flat', False)
    if image_type not in ('', 'all', 'main', 'detail'):
        return jsonify({'error': 'image_type must be all, main, or detail'}), 400
    if not isinstance(flat, bool):
        return jsonify({'error': 'flat must be a boolean'}), 400
    from routes.export import _build_zip, _plan_zip_entries, _export_lock
    with _export_lock:
        running = session.query(ExportTask).filter(
            ExportTask.status == 'processing'
        ).first()
        if running:
            return jsonify({'error': '已有导出任务正在执行中，请等待完成'}), 409

    q = exportable_image_query(session).filter(Image.id.in_(ids))
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

    if not imgs:
        return jsonify({
            'error': '没有符合导出条件的图片',
            'excluded_ids': ids,
        }), 400

    import threading
    img_data = [
        (img.file_path, img.barcode, img.image_type, img.sequence, img.ext, img.scan_root_id)
        for img in imgs
    ]
    # Plan before creating the task, so planning failures cannot leave a
    # durable task stuck in processing.
    planned_entries, _ = _plan_zip_entries(img_data, flat, image_type or 'all')
    if not planned_entries:
        return jsonify({'error': '没有可写入 ZIP 的图片'}), 400

    from routes.export import _compute_barcode_counts
    planned_counts = _compute_barcode_counts(imgs, export_type=image_type or 'all')
    payload = {
        'barcodes': planned_counts,
        'stats': {
            'planned_count': len(planned_entries),
            'written_count': 0,
            'skipped_count': 0,
        },
    }

    # Concurrency guard: same lock + running check as /export/zip
    with _export_lock:
        running = session.query(ExportTask).filter(ExportTask.status == 'processing').first()
        if running:
            return jsonify({'error': '已有导出任务正在执行中，请等待完成'}), 409

        task = ExportTask(
            status='processing',
            total_images=len(planned_entries),
            barcode_data=json.dumps(payload, ensure_ascii=False),
        )
        session.add(task)
        try:
            session.commit()
        except Exception:
            session.rollback()
            raise

        try:
            threading.Thread(
                target=_build_zip,
                args=(task.id, img_data, flat, image_type or 'all'),
                daemon=True,
            ).start()
        except Exception as e:
            task.status = 'failed'
            task.error_message = f'导出线程启动失败: {e}'
            session.commit()
            return jsonify({'error': '导出任务启动失败', 'task_id': task.id}), 500

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

    # Match folder-delete filters via shared helper when deleting a full folder key
    from routes.batch import _check_disabled_scan_roots, _delete_folder_images
    items = [{'barcode': barcode, 'image_type': image_type, 'folder_ctime': folder_ctime}]
    if _check_disabled_scan_roots(items, sess=session) > 0:
        return jsonify({'error': 'scan root is disabled'}), 403

    deleted, failed_items = _delete_folder_images(
        barcode, image_type, folder_ctime, delete_file, sess=session,
    )
    session.commit()

    if deleted > 0:
        update_versions_for_barcode(barcode)

    if deleted == 0 and not failed_items:
        return jsonify({'error': 'no duplicate images found'}), 404

    return jsonify({
        'deleted': deleted,
        'file_deleted': delete_file,
        'failed_items': failed_items,
    })


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

    # Disabled-root guard (same as folder delete)
    from routes.batch import _check_disabled_scan_roots, _delete_folder_images
    items = [{'barcode': barcode, 'image_type': v.image_type, 'folder_ctime': folder_ctime}]
    if _check_disabled_scan_roots(items, sess=session) > 0:
        return jsonify({'error': 'scan root is disabled'}), 403

    # Unified folder-delete semantics (active+confirmed+enabled, partial ok, maybe-record)
    deleted, failed_items = _delete_folder_images(
        barcode, v.image_type, folder_ctime, delete_file, sess=session,
    )
    session.commit()

    if deleted > 0:
        update_versions_for_barcode(barcode)

    return jsonify({
        'deleted': deleted,
        'file_deleted': delete_file,
        'failed_items': failed_items,
    })

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
    try:
        data = require_json_object()
    except JSONPayloadError as e:
        return json_payload_error_response(e)
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
