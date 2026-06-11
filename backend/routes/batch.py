import json, os, re, logging, datetime
from collections import defaultdict
from flask import Blueprint, request, jsonify
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from models import session, Image, ImageVersion, ScanRoot, DeletedFolder
from versioning import update_versions_for_barcode
from task_engine import _get_thread_session
from db_retry import with_sqlite_lock_retry

_log = logging.getLogger(__name__)

batch_bp = Blueprint('batch', __name__)

_ISO_RE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?$')


def _classify_delete_error(filepath, error):
    """Classify an OSError into a human-readable reason."""
    if isinstance(error, FileNotFoundError):
        return '文件不存在'
    elif isinstance(error, PermissionError):
        return '权限不足'
    elif isinstance(error, OSError):
        msg = str(error).lower()
        if 'being used' in msg or '另一个程序正在使用' in msg:
            return '文件被占用'
        return f'系统错误: {error}'
    return f'未知错误: {error}'


def _record_deleted_folder(sess, barcode, image_type, folder_ctime):
    stmt = sqlite_insert(DeletedFolder).values(
        barcode=barcode,
        image_type=image_type,
        folder_ctime=folder_ctime,
        deleted_at=datetime.datetime.now().isoformat(),
    ).on_conflict_do_nothing(
        index_elements=['barcode', 'image_type', 'folder_ctime']
    )
    sess.execute(stmt)


def _delete_folder_images(barcode, image_type, folder_ctime, delete_files):
    """删除指定条码+类型+文件夹下 active+confirmed+enabled 的图片，返回删除数量。
    过滤条件与扫描端点一致，确保预览和实际操作匹配。
    使用 thread-local session 以支持后台任务调用。"""
    sess = _get_thread_session()
    # Subquery to get matching image IDs (join with ScanRoot for enabled check)
    match_ids = select(Image.id).where(
        Image.barcode == barcode,
        Image.image_type == image_type,
        Image.folder_ctime == folder_ctime,
        Image.status == 'active',
        Image.confirmed == True,
    ).join(ScanRoot, Image.scan_root_id == ScanRoot.id).where(
        ScanRoot.enabled == True,
    )
    if delete_files:
        imgs = sess.query(Image).filter(Image.id.in_(match_ids)).all()
        from routes._utils import safe_remove_image_file
        failed_items = []
        deleted_count = 0
        for img in imgs:
            ok, reason = safe_remove_image_file(img, sess)
            if ok:
                sess.delete(img)
                deleted_count += 1
            else:
                _log.warning("Refused or failed to delete file: %s — %s", img.file_path, reason)
                failed_items.append({'file': img.file_path, 'reason': reason or '删除失败'})
        if deleted_count > 0:
            _record_deleted_folder(sess, barcode, image_type, folder_ctime)
        return deleted_count, failed_items
    else:
        count = sess.query(Image).filter(Image.id.in_(match_ids)).delete(synchronize_session='fetch')
        if count > 0:
            _record_deleted_folder(sess, barcode, image_type, folder_ctime)
        return count, []


def _check_disabled_scan_roots(items):
    """检查是否有图片属于禁用的扫描目录，返回禁用目录中的图片数量。
    使用 thread-safe session 以支持后台任务调用。"""
    if not items:
        return 0
    sess = _get_thread_session()
    # Build OR conditions for (barcode, image_type, folder_ctime)
    # Chunked to stay under SQLite expression depth limit of ~1000
    disabled_count = 0
    chunk_size = 500
    for chunk_start in range(0, len(items), chunk_size):
        chunk = items[chunk_start:chunk_start + chunk_size]
        conditions = []
        for item in chunk:
            conditions.append(
                (Image.barcode == item['barcode']) &
                (Image.image_type == item['image_type']) &
                (Image.folder_ctime == item['folder_ctime'])
            )
        if conditions:
            disabled_count += sess.query(Image.id).join(
                ScanRoot, Image.scan_root_id == ScanRoot.id
            ).filter(
                ScanRoot.enabled == False,
                or_(*conditions),
            ).count()
    return disabled_count


def validate_image_paths(imgs, scan_roots):
    """验证图片路径是否安全（在扫描目录下）。
    返回 (is_valid, error_message) 元组。"""
    for img in imgs:
        root_path = scan_roots.get(img.scan_root_id)
        if not root_path:
            return False, f'无法找到扫描目录 (scan_root_id={img.scan_root_id})'
        real_file = os.path.realpath(img.file_path)
        real_root = os.path.realpath(root_path)
        try:
            safe = os.path.commonpath([real_file, real_root]) == real_root
        except ValueError:
            safe = False
        if not safe:
            return False, '文件路径不在扫描目录下，拒绝删除'
        if not os.path.exists(img.file_path):
            return False, f'文件不存在: {img.file_path}'
    return True, None


def delete_images_with_validation(barcode, image_type, folder_ctime, delete_files):
    """删除图片，包含路径安全验证。
    返回 (deleted_count, error_message) 元组。
    注意：文件删除是 best-effort，无法回滚。"""
    sess = _get_thread_session()

    # 获取匹配的图片
    match_ids = select(Image.id).where(
        Image.barcode == barcode,
        Image.image_type == image_type,
        Image.folder_ctime == folder_ctime,
        Image.status == 'active',
        Image.confirmed == True,
    ).join(ScanRoot, Image.scan_root_id == ScanRoot.id).where(
        ScanRoot.enabled == True,
    )
    imgs = sess.query(Image).filter(Image.id.in_(match_ids)).all()

    if not imgs:
        return 0, '已无有效图片'

    # Phase 1: 验证路径安全
    if delete_files:
        scan_roots = {sr.id: sr.path for sr in sess.query(ScanRoot).all()}
        is_valid, error_msg = validate_image_paths(imgs, scan_roots)
        if not is_valid:
            return 0, error_msg

    # Phase 2: 删除文件（best-effort，无法回滚）
    file_errors = []
    if delete_files:
        from routes._utils import safe_remove_image_file
        for img in imgs:
            ok, reason = safe_remove_image_file(img, sess)
            if not ok:
                file_errors.append(f'{img.file_path}: {reason or "删除失败"}')

    if file_errors:
        return 0, f'文件删除失败: {"; ".join(file_errors)}'

    # Phase 3: 删除索引
    for img in imgs:
        sess.delete(img)

    _record_deleted_folder(sess, barcode, image_type, folder_ctime)
    return len(imgs), None


def _build_image_stats_query():
    """Return a base GROUP BY query for (barcode, image_type, folder_ctime) stats,
    filtered to active+confirmed images in enabled scan roots."""
    sess = _get_thread_session()
    return sess.query(
        Image.barcode, Image.image_type, Image.folder_ctime,
        func.count(Image.id).label('cnt'),
        func.sum(Image.file_size).label('total_sz'),
    ).filter(
        Image.status == 'active',
        Image.confirmed == True,
    ).join(ScanRoot, Image.scan_root_id == ScanRoot.id).filter(
        ScanRoot.enabled == True,
    ).group_by(Image.barcode, Image.image_type, Image.folder_ctime)


def _compute_folder_stats():
    """Return {(barcode, image_type, folder_ctime): (count, total_size)}
    for all active+confirmed images in enabled scan roots."""
    rows = _build_image_stats_query().all()
    stats = {}
    for row in rows:
        stats[(row.barcode, row.image_type, row.folder_ctime)] = (row.cnt, row.total_sz or 0)
    return stats


@batch_bp.route('/batch/duplicates', methods=['GET'])
def list_duplicates():
    """扫描所有有重复文件夹的版本，返回分组数据"""
    versions = session.query(ImageVersion).filter(
        ImageVersion.duplicate_mtimes != '',
        ImageVersion.duplicate_mtimes != '[]',
    ).all()

    # Build deduplicated key -> version info map
    dup_map = {}  # (barcode, image_type, dup_ctime) -> {version_label, version_folder_ctime}
    for v in versions:
        try:
            dup_ctimes = json.loads(v.duplicate_mtimes)
        except (json.JSONDecodeError, TypeError):
            continue
        if not dup_ctimes:
            continue
        for dup_ctime in dup_ctimes:
            key = (v.barcode, v.image_type, dup_ctime)
            if key not in dup_map:
                dup_map[key] = {'version_label': v.version_label, 'version_folder_ctime': v.folder_ctime}

    if not dup_map:
        return jsonify({'groups': [], 'total_duplicate_count': 0, 'total_barcode_count': 0})

    # Batch query: chunked GROUP BY to avoid N+1 queries.
    # SQLite expression-tree depth is limited, so split into chunks of 500 ORs.
    keys = list(dup_map.keys())
    chunk_size = 500
    stats = {}  # (barcode, image_type, folder_ctime) -> (count, total_size)

    for chunk_start in range(0, len(keys), chunk_size):
        chunk = keys[chunk_start:chunk_start + chunk_size]
        conditions = [
            (Image.barcode == barcode) &
            (Image.image_type == image_type) &
            (Image.folder_ctime == folder_ctime)
            for barcode, image_type, folder_ctime in chunk
        ]
        rows = _build_image_stats_query().filter(or_(*conditions)).all()

        for row in rows:
            stats[(row.barcode, row.image_type, row.folder_ctime)] = (row.cnt, row.total_sz or 0)

    # Build response from precomputed stats
    groups = []
    for key, info in dup_map.items():
        count_sz = stats.get(key)
        if count_sz is None:
            continue
        count, total_sz = count_sz
        groups.append({
            'barcode': key[0],
            'image_type': key[1],
            'version_label': info['version_label'],
            'version_folder_ctime': info['version_folder_ctime'],
            'folder_ctime': key[2],
            'image_count': count,
            'total_file_size': total_sz,
        })

    groups.sort(key=lambda g: (g['barcode'], g['image_type'], g['folder_ctime']))

    barcodes = set(g['barcode'] for g in groups)
    return jsonify({
        'groups': groups,
        'total_duplicate_count': len(groups),
        'total_barcode_count': len(barcodes),
    })


@batch_bp.route('/batch/delete-duplicates', methods=['POST'])
@with_sqlite_lock_retry()
def delete_duplicates():
    """删除指定的重复文件夹"""
    data = request.json
    items = data.get('items', [])
    delete_files = data.get('delete_files', False)

    if not items:
        return jsonify({'error': 'items required'}), 400

    for i, item in enumerate(items):
        if not isinstance(item, dict):
            return jsonify({'error': f'item {i}: must be an object'}), 400
        if not item.get('barcode') or not item.get('image_type') or not item.get('folder_ctime'):
            return jsonify({'error': f'item {i}: barcode, image_type, folder_ctime required'}), 400
        if item['image_type'] not in ('main', 'detail'):
            return jsonify({'error': f'item {i}: image_type must be main or detail'}), 400
        if not _ISO_RE.match(item['folder_ctime']):
            return jsonify({'error': f'item {i}: folder_ctime must be ISO8601 format'}), 400

    # Reject if any images belong to disabled scan roots
    disabled_count = _check_disabled_scan_roots(items)
    if disabled_count > 0:
        return jsonify({
            'error': '部分图片属于已禁用的扫描目录，无法删除',
            'disabled_count': disabled_count,
        }), 403

    # Build valid duplicate candidate set from current ImageVersion state.
    # Reject items that are no longer recognized as duplicates — the data may
    # have changed since the preview was loaded.
    valid_duplicates = set()
    versions = session.query(ImageVersion).filter(
        ImageVersion.duplicate_mtimes != '',
        ImageVersion.duplicate_mtimes != '[]',
    ).all()
    for v in versions:
        try:
            dup_ctimes = json.loads(v.duplicate_mtimes)
        except (json.JSONDecodeError, TypeError):
            continue
        for dup_ctime in dup_ctimes:
            valid_duplicates.add((v.barcode, v.image_type, dup_ctime))

    for i, item in enumerate(items):
        key = (item['barcode'], item['image_type'], item['folder_ctime'])
        if key not in valid_duplicates:
            return jsonify({
                'error': f'第{i}项不是有效的重复文件夹，数据可能已变更，请刷新后重试',
                'invalid_index': i,
            }), 409

    affected_barcodes = set()
    total_deleted = 0

    for item in items:
        count, _failed = _delete_folder_images(
            item['barcode'], item['image_type'], item['folder_ctime'], delete_files
        )
        total_deleted += count
        affected_barcodes.add(item['barcode'])

    session.commit()

    for bc in affected_barcodes:
        update_versions_for_barcode(bc)

    return jsonify({
        'deleted_image_count': total_deleted,
        'deleted_item_count': len(items),
        'affected_barcodes': list(affected_barcodes),
    })


@batch_bp.route('/batch/low-versions', methods=['GET'])
def list_low_versions():
    """扫描低于阈值的版本"""
    try:
        main_threshold = int(request.args.get('main_threshold', 0))
        detail_threshold = int(request.args.get('detail_threshold', 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'threshold must be an integer'}), 400

    if main_threshold < 0 or detail_threshold < 0:
        return jsonify({'error': 'threshold must be >= 0'}), 400
    if main_threshold == 0 and detail_threshold == 0:
        return jsonify({'error': 'at least one threshold must be > 0'}), 400

    # Get all versions
    versions = session.query(ImageVersion).all()
    stats = _compute_folder_stats()

    # Group by (barcode, image_type)
    by_barcode_type = defaultdict(list)
    for v in versions:
        by_barcode_type[(v.barcode, v.image_type)].append(v)

    groups = []
    summary = {'will_delete': 0, 'keep_threshold': 0, 'keep_only': 0, 'keep_disabled': 0}

    for (barcode, image_type), vers in by_barcode_type.items():
        threshold = main_threshold if image_type == 'main' else detail_threshold
        total_versions = len(vers)

        for v in vers:
            count, total_file_size = stats.get(
                (v.barcode, v.image_type, v.folder_ctime), (0, 0)
            )

            if threshold == 0:
                tag = 'keep_disabled'
            elif total_versions == 1:
                tag = 'keep_only'
            elif count >= threshold:
                tag = 'keep_threshold'
            else:
                tag = 'will_delete'

            groups.append({
                'barcode': barcode,
                'image_type': image_type,
                'version_label': v.version_label,
                'folder_ctime': v.folder_ctime,
                'image_count': count,
                'total_file_size': total_file_size,
                'is_latest': v.is_latest,
                'is_only_version': total_versions == 1,
                'meets_threshold': count >= threshold if threshold > 0 else False,
                'threshold': threshold,
                'status_tag': tag,
            })
            summary[tag] = summary.get(tag, 0) + 1

    groups.sort(key=lambda g: (g['barcode'], g['image_type'], g['folder_ctime']))
    return jsonify({'groups': groups, 'summary': summary})


@batch_bp.route('/batch/delete-low-versions', methods=['POST'])
@with_sqlite_lock_retry()
def delete_low_versions():
    """删除指定的低版本。必须传入与预览时相同的阈值以重新验证。"""
    data = request.json
    items = data.get('items', [])
    delete_files = data.get('delete_files', False)
    main_threshold = data.get('main_threshold', 0)
    detail_threshold = data.get('detail_threshold', 0)

    if not items:
        return jsonify({'error': 'items required'}), 400
    if not isinstance(main_threshold, int) or not isinstance(detail_threshold, int):
        return jsonify({'error': 'main_threshold and detail_threshold must be integers'}), 400
    if main_threshold < 0 or detail_threshold < 0:
        return jsonify({'error': 'threshold must be >= 0'}), 400
    if main_threshold == 0 and detail_threshold == 0:
        return jsonify({'error': 'at least one threshold must be > 0'}), 400

    for i, item in enumerate(items):
        if not isinstance(item, dict):
            return jsonify({'error': f'item {i}: must be an object'}), 400
        if not item.get('barcode') or not item.get('image_type') or not item.get('folder_ctime'):
            return jsonify({'error': f'item {i}: barcode, image_type, folder_ctime required'}), 400
        if item['image_type'] not in ('main', 'detail'):
            return jsonify({'error': f'item {i}: image_type must be main or detail'}), 400
        if not _ISO_RE.match(item['folder_ctime']):
            return jsonify({'error': f'item {i}: folder_ctime must be ISO8601 format'}), 400

    # Reject if any images belong to disabled scan roots
    disabled_count = _check_disabled_scan_roots(items)
    if disabled_count > 0:
        return jsonify({
            'error': '部分图片属于已禁用的扫描目录，无法删除',
            'disabled_count': disabled_count,
        }), 403

    # Re-validate eligibility using current database state.
    # An item is only safe to delete when it still qualifies as "will_delete"
    # under the same thresholds the user previewed.
    stats = _compute_folder_stats()
    versions = session.query(ImageVersion).all()
    by_barcode_type = defaultdict(list)
    for v in versions:
        by_barcode_type[(v.barcode, v.image_type)].append(v)

    for i, item in enumerate(items):
        barcode, image_type, folder_ctime = item['barcode'], item['image_type'], item['folder_ctime']
        count, _ = stats.get((barcode, image_type, folder_ctime), (0, 0))
        threshold = main_threshold if image_type == 'main' else detail_threshold
        total_versions = len(by_barcode_type.get((barcode, image_type), []))

        if count == 0:
            return jsonify({
                'error': f'第{i}项已无有效图片，数据可能已变更，请刷新后重试',
                'invalid_index': i,
            }), 409
        if threshold == 0 or total_versions <= 1 or count >= threshold:
            return jsonify({
                'error': f'第{i}项不符合删除条件（阈值={threshold}，版本数={total_versions}，图片数={count}），数据可能已变更，请刷新后重试',
                'invalid_index': i,
            }), 409

    affected_barcodes = set()
    total_deleted = 0

    for item in items:
        count, _failed = _delete_folder_images(
            item['barcode'], item['image_type'], item['folder_ctime'], delete_files
        )
        total_deleted += count
        affected_barcodes.add(item['barcode'])

    session.commit()

    for bc in affected_barcodes:
        update_versions_for_barcode(bc)

    return jsonify({
        'deleted_image_count': total_deleted,
        'deleted_item_count': len(items),
        'affected_barcodes': list(affected_barcodes),
    })
