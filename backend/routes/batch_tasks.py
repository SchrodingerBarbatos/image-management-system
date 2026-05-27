import json, logging, os, datetime
from flask import Blueprint, request, jsonify
from sqlalchemy import or_, select, func
from models import (
    session, Image, ImageVersion, ScanRoot,
    BatchTask, DuplicateScanResult, LowVersionScanResult,
)
from versioning import update_versions_for_barcode
from task_engine import create_task, finish_task, update_task_progress, _get_thread_session

batch_tasks_bp = Blueprint('batch_tasks', __name__)
_log = logging.getLogger(__name__)


# ---------- Duplicate scan handler ----------

def _run_duplicate_scan(task_id):
    """Background handler for duplicate_scan tasks."""
    from task_engine import TASK_HANDLERS  # avoid circular at module level
    _log.info("Starting duplicate_scan task %d", task_id)

    sess = _get_thread_session()

    versions = sess.query(ImageVersion).filter(
        ImageVersion.duplicate_mtimes != '',
        ImageVersion.duplicate_mtimes != '[]',
    ).all()

    # Build deduplicated key -> version info map
    dup_map = {}
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

    total = len(dup_map)
    update_task_progress(task_id, progress=0, total=total)

    if not dup_map:
        finish_task(task_id, result_count=0)
        return

    # Batch query stats using chunked OR
    keys = list(dup_map.keys())
    chunk_size = 500
    stats = {}

    for chunk_start in range(0, len(keys), chunk_size):
        chunk = keys[chunk_start:chunk_start + chunk_size]
        conditions = [
            (Image.barcode == barcode) &
            (Image.image_type == image_type) &
            (Image.folder_ctime == folder_ctime)
            for barcode, image_type, folder_ctime in chunk
        ]
        rows = sess.query(
            Image.barcode, Image.image_type, Image.folder_ctime,
            func.count(Image.id).label('cnt'),
            func.sum(Image.file_size).label('total_sz'),
        ).filter(
            Image.status == 'active',
            Image.confirmed == True,
        ).join(ScanRoot, Image.scan_root_id == ScanRoot.id).filter(
            ScanRoot.enabled == True,
        ).filter(or_(*conditions)).group_by(
            Image.barcode, Image.image_type, Image.folder_ctime,
        ).all()

        for row in rows:
            stats[(row.barcode, row.image_type, row.folder_ctime)] = (row.cnt, row.total_sz or 0)

        update_task_progress(task_id, progress=chunk_start + len(chunk), total=total)

    # Build results and insert
    results = []
    for key, info in dup_map.items():
        count_sz = stats.get(key)
        if count_sz is None:
            continue
        count, total_sz = count_sz
        results.append(DuplicateScanResult(
            task_id=task_id,
            barcode=key[0],
            image_type=key[1],
            version_label=info['version_label'],
            version_folder_ctime=info['version_folder_ctime'],
            folder_ctime=key[2],
            image_count=count,
            total_file_size=total_sz,
        ))

    sess.add_all(results)
    sess.commit()
    update_task_progress(task_id, progress=total, total=total, result_count=len(results))
    finish_task(task_id, result_count=len(results))
    _log.info("duplicate_scan task %d done: %d results", task_id, len(results))


# ---------- Low version scan handler ----------

def _run_low_version_scan(task_id):
    """Background handler for low_version_scan tasks."""
    _log.info("Starting low_version_scan task %d", task_id)

    sess = _get_thread_session()

    task = sess.get(BatchTask, task_id)
    if not task:
        finish_task(task_id, error_message='Task not found')
        return

    try:
        params = json.loads(task.params_json) if task.params_json else {}
    except (json.JSONDecodeError, TypeError):
        params = {}

    main_enabled = params.get('main_enabled', True)
    main_threshold = params.get('main_threshold', 3)
    detail_enabled = params.get('detail_enabled', True)
    detail_threshold = params.get('detail_threshold', 5)

    if not main_enabled:
        main_threshold = 0
    if not detail_enabled:
        detail_threshold = 0

    versions = sess.query(ImageVersion).all()
    total = len(versions)
    update_task_progress(task_id, progress=0, total=total)

    # Compute folder stats
    from routes.batch import _compute_folder_stats
    stats = _compute_folder_stats()

    from collections import defaultdict
    by_barcode_type = defaultdict(list)
    for v in versions:
        by_barcode_type[(v.barcode, v.image_type)].append(v)

    results = []
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

            results.append(LowVersionScanResult(
                task_id=task_id,
                barcode=barcode,
                image_type=image_type,
                version_label=v.version_label,
                folder_ctime=v.folder_ctime,
                image_count=count,
                total_file_size=total_file_size,
                is_latest=v.is_latest,
                is_only_version=(total_versions == 1),
                meets_threshold=(count >= threshold if threshold > 0 else False),
                main_threshold=main_threshold,
                detail_threshold=detail_threshold,
                status_tag=tag,
            ))

    # Insert in chunks to avoid memory issues
    chunk_size = 500
    for i in range(0, len(results), chunk_size):
        sess.add_all(results[i:i + chunk_size])
        sess.commit()
        update_task_progress(task_id, progress=min(i + chunk_size, total), total=total)

    update_task_progress(task_id, progress=total, total=total, result_count=len(results))
    finish_task(task_id, result_count=len(results))
    _log.info("low_version_scan task %d done: %d results", task_id, len(results))


# Register handler at import time
from task_engine import register_handler
register_handler('duplicate_scan', _run_duplicate_scan)
register_handler('low_version_scan', _run_low_version_scan)


# ---------- Common task routes ----------

@batch_tasks_bp.route('/tasks', methods=['GET'])
def list_tasks():
    task_type = request.args.get('type')
    status = request.args.get('status')
    from task_engine import get_tasks
    tasks = get_tasks(task_type=task_type, status=status)
    return jsonify(tasks)


@batch_tasks_bp.route('/tasks/<int:task_id>', methods=['GET'])
def get_task_route(task_id):
    from task_engine import get_task
    task = get_task(task_id)
    if not task:
        return jsonify({'error': 'not found'}), 404
    return jsonify(task)


@batch_tasks_bp.route('/tasks/<int:task_id>', methods=['DELETE'])
def delete_task_route(task_id):
    from task_engine import delete_task
    result = delete_task(task_id)
    if result is None:
        return jsonify({'error': 'not found'}), 404
    if 'error' in result:
        return jsonify(result), 409
    return jsonify(result)


@batch_tasks_bp.route('/tasks/<int:task_id>/cancel', methods=['POST'])
def cancel_task_route(task_id):
    from task_engine import cancel_task
    result = cancel_task(task_id)
    if result is None:
        return jsonify({'error': 'not found'}), 404
    if 'error' in result:
        return jsonify(result), 409
    return jsonify(result)


# ---------- Duplicate scan routes ----------

@batch_tasks_bp.route('/batch/duplicate-scan/tasks', methods=['POST'])
def create_duplicate_scan():
    from task_engine import create_task
    task_dict, is_new = create_task('duplicate_scan', params={})
    code = 201 if is_new else 200
    return jsonify(task_dict), code


@batch_tasks_bp.route('/batch/duplicate-scan/tasks', methods=['GET'])
def list_duplicate_scan_tasks():
    from task_engine import get_tasks
    tasks = get_tasks(task_type='duplicate_scan')
    return jsonify(tasks)


@batch_tasks_bp.route('/batch/duplicate-scan/tasks/<int:task_id>', methods=['GET'])
def get_duplicate_scan_task(task_id):
    from task_engine import get_task
    task = get_task(task_id)
    if not task:
        return jsonify({'error': 'not found'}), 404
    return jsonify(task)


@batch_tasks_bp.route('/batch/duplicate-scan/tasks/<int:task_id>/results', methods=['GET'])
def get_duplicate_scan_results(task_id):
    task = session.get(BatchTask, task_id)
    if not task:
        return jsonify({'error': 'not found'}), 404
    page = request.args.get('page', 1, type=int)
    page_size = request.args.get('page_size', 100, type=int)
    page = max(1, page)
    page_size = max(1, min(500, page_size))

    total = session.query(DuplicateScanResult).filter(
        DuplicateScanResult.task_id == task_id,
    ).count()

    results = session.query(DuplicateScanResult).filter(
        DuplicateScanResult.task_id == task_id,
    ).order_by(DuplicateScanResult.id).offset((page - 1) * page_size).limit(page_size).all()

    return jsonify({
        'items': [{
            'id': r.id,
            'barcode': r.barcode,
            'image_type': r.image_type,
            'version_label': r.version_label,
            'version_folder_ctime': r.version_folder_ctime,
            'folder_ctime': r.folder_ctime,
            'image_count': r.image_count,
            'total_file_size': r.total_file_size,
            'delete_status': r.delete_status,
            'delete_message': r.delete_message,
            'deleted_at': r.deleted_at,
        } for r in results],
        'total': total,
        'page': page,
        'page_size': page_size,
    })


@batch_tasks_bp.route('/batch/duplicate-scan/tasks/<int:task_id>', methods=['DELETE'])
def delete_duplicate_scan_task(task_id):
    from task_engine import delete_task
    result = delete_task(task_id)
    if result is None:
        return jsonify({'error': 'not found'}), 404
    if 'error' in result:
        return jsonify(result), 409
    return jsonify(result)


_ISO_RE = __import__('re').compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?$')


def _validate_result_ids(result_ids):
    if not isinstance(result_ids, list) or not result_ids:
        return 'result_ids must be a non-empty list'
    if not all(isinstance(result_id, int) and not isinstance(result_id, bool) for result_id in result_ids):
        return 'result_ids must contain only integer ids'
    return None


@batch_tasks_bp.route('/batch/duplicate-scan/tasks/<int:task_id>/delete', methods=['POST'])
def delete_duplicate_scan_results(task_id):
    """Delete images based on duplicate scan results. Re-validate before deletion."""
    task = session.get(BatchTask, task_id)
    if not task:
        return jsonify({'error': 'not found'}), 404

    data = request.json or {}
    mode = data.get('mode', 'selected')
    result_ids = data.get('result_ids', [])
    delete_files = data.get('delete_files', False)

    if mode != 'selected':
        return jsonify({'error': 'mode must be "selected" and result_ids must be non-empty'}), 400
    validation_error = _validate_result_ids(result_ids)
    if validation_error:
        return jsonify({'error': validation_error}), 400

    # Load selected results
    results = session.query(DuplicateScanResult).filter(
        DuplicateScanResult.task_id == task_id,
        DuplicateScanResult.id.in_(result_ids),
    ).all()
    if not results:
        return jsonify({'error': 'no valid result_ids'}), 400

    # Re-validate: check each result is still a valid duplicate
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

    # Check disabled scan roots for all items (chunked to stay under
    # SQLite expression depth limit of ~1000)
    items = [{'barcode': r.barcode, 'image_type': r.image_type, 'folder_ctime': r.folder_ctime} for r in results]
    disabled_count = 0
    _chunk_size = 500
    for _chunk_start in range(0, len(items), _chunk_size):
        _chunk = items[_chunk_start:_chunk_start + _chunk_size]
        _conds = []
        for item in _chunk:
            _conds.append(
                (Image.barcode == item['barcode']) &
                (Image.image_type == item['image_type']) &
                (Image.folder_ctime == item['folder_ctime'])
            )
        if _conds:
            disabled_count += session.query(Image.id).join(
                ScanRoot, Image.scan_root_id == ScanRoot.id,
            ).filter(
                ScanRoot.enabled == False,
                or_(*_conds),
            ).count()

    if disabled_count > 0:
        return jsonify({'error': '部分图片属于已禁用的扫描目录，无法删除', 'disabled_count': disabled_count}), 403

    affected_barcodes = set()
    deleted_image_count = 0
    skipped_count = 0

    # Pre-load scan roots for path validation
    scan_roots = {sr.id: sr.path for sr in session.query(ScanRoot).all()}

    for r in results:
        key = (r.barcode, r.image_type, r.folder_ctime)
        if key not in valid_duplicates:
            r.delete_status = 'skipped'
            r.delete_message = '数据已变更，不再是有效重复'
            skipped_count += 1
            continue

        # Delete images matching this key
        match_ids = select(Image.id).where(
            Image.barcode == r.barcode,
            Image.image_type == r.image_type,
            Image.folder_ctime == r.folder_ctime,
            Image.status == 'active',
            Image.confirmed == True,
        ).join(ScanRoot, Image.scan_root_id == ScanRoot.id).where(
            ScanRoot.enabled == True,
        )

        imgs = session.query(Image).filter(Image.id.in_(match_ids)).all()

        # ---- Phase 1: pre-validate every image before touching disk or DB ----
        validation_msg = None
        if delete_files and imgs:
            for img in imgs:
                root_path = scan_roots.get(img.scan_root_id)
                if not root_path:
                    validation_msg = f'无法找到扫描目录 (scan_root_id={img.scan_root_id})'
                    break
                real_file = os.path.realpath(img.file_path)
                real_root = os.path.realpath(root_path)
                try:
                    safe = os.path.commonpath([real_file, real_root]) == real_root
                except ValueError:
                    safe = False
                if not safe:
                    validation_msg = '文件路径不在扫描目录下，拒绝删除'
                    break
                if not os.path.exists(img.file_path):
                    validation_msg = f'文件不存在: {img.file_path}'
                    break

        if validation_msg:
            r.delete_status = 'failed'
            r.delete_message = validation_msg
            skipped_count += 1
            continue

        # ---- Phase 2: delete files from disk (best-effort, cannot rollback) ----
        file_errors = []
        if delete_files and imgs:
            for img in imgs:
                try:
                    os.remove(img.file_path)
                except OSError as e:
                    file_errors.append(f'{img.file_path}: {e}')

        if file_errors:
            r.delete_status = 'failed'
            r.delete_message = f'文件删除失败: {"; ".join(file_errors)}'
            skipped_count += 1
            continue

        # ---- Phase 3: all safe — delete DB indices ----
        for img in imgs:
            session.delete(img)
            deleted_image_count += 1

        r.delete_status = 'deleted'
        r.deleted_at = datetime.datetime.now().isoformat()
        affected_barcodes.add(r.barcode)

    session.commit()

    for bc in affected_barcodes:
        update_versions_for_barcode(bc)

    return jsonify({
        'deleted_image_count': deleted_image_count,
        'skipped_count': skipped_count,
        'affected_barcodes': list(affected_barcodes),
    })


# ---------- Low version scan routes ----------

@batch_tasks_bp.route('/batch/low-version-scan/tasks', methods=['POST'])
def create_low_version_scan():
    data = request.json or {}
    main_enabled = data.get('main_enabled', True)
    main_threshold = data.get('main_threshold', 3)
    detail_enabled = data.get('detail_enabled', True)
    detail_threshold = data.get('detail_threshold', 5)

    if not main_enabled and not detail_enabled:
        return jsonify({'error': '至少启用一项阈值'}), 400
    if not isinstance(main_threshold, int) or not isinstance(detail_threshold, int):
        return jsonify({'error': 'threshold must be integers'}), 400
    if main_threshold < 0 or detail_threshold < 0:
        return jsonify({'error': 'threshold must be >= 0'}), 400

    params = {
        'main_enabled': main_enabled,
        'main_threshold': main_threshold if main_enabled else 0,
        'detail_enabled': detail_enabled,
        'detail_threshold': detail_threshold if detail_enabled else 0,
    }
    from task_engine import create_task
    task_dict, is_new = create_task('low_version_scan', params=params)
    code = 201 if is_new else 200
    return jsonify(task_dict), code


@batch_tasks_bp.route('/batch/low-version-scan/tasks', methods=['GET'])
def list_low_version_scan_tasks():
    from task_engine import get_tasks
    tasks = get_tasks(task_type='low_version_scan')
    return jsonify(tasks)


@batch_tasks_bp.route('/batch/low-version-scan/tasks/<int:task_id>', methods=['GET'])
def get_low_version_scan_task(task_id):
    from task_engine import get_task
    task = get_task(task_id)
    if not task:
        return jsonify({'error': 'not found'}), 404
    return jsonify(task)


@batch_tasks_bp.route('/batch/low-version-scan/tasks/<int:task_id>/results', methods=['GET'])
def get_low_version_scan_results(task_id):
    task = session.get(BatchTask, task_id)
    if not task:
        return jsonify({'error': 'not found'}), 404

    page = request.args.get('page', 1, type=int)
    page_size = request.args.get('page_size', 100, type=int)
    page = max(1, page)
    page_size = max(1, min(500, page_size))

    # Optional filters
    status_tag = request.args.get('status_tag')
    delete_status = request.args.get('delete_status')

    q = session.query(LowVersionScanResult).filter(
        LowVersionScanResult.task_id == task_id,
    )
    if status_tag:
        q = q.filter(LowVersionScanResult.status_tag == status_tag)
    if delete_status:
        q = q.filter(LowVersionScanResult.delete_status == delete_status)

    total = q.count()
    results = q.order_by(LowVersionScanResult.id).offset((page - 1) * page_size).limit(page_size).all()

    return jsonify({
        'items': [{
            'id': r.id,
            'barcode': r.barcode,
            'image_type': r.image_type,
            'version_label': r.version_label,
            'folder_ctime': r.folder_ctime,
            'image_count': r.image_count,
            'total_file_size': r.total_file_size,
            'is_latest': r.is_latest,
            'is_only_version': r.is_only_version,
            'meets_threshold': r.meets_threshold,
            'main_threshold': r.main_threshold,
            'detail_threshold': r.detail_threshold,
            'status_tag': r.status_tag,
            'delete_status': r.delete_status,
            'delete_message': r.delete_message,
            'deleted_at': r.deleted_at,
        } for r in results],
        'total': total,
        'page': page,
        'page_size': page_size,
    })


@batch_tasks_bp.route('/batch/low-version-scan/tasks/<int:task_id>', methods=['DELETE'])
def delete_low_version_scan_task(task_id):
    from task_engine import delete_task
    result = delete_task(task_id)
    if result is None:
        return jsonify({'error': 'not found'}), 404
    if 'error' in result:
        return jsonify(result), 409
    return jsonify(result)


@batch_tasks_bp.route('/batch/low-version-scan/tasks/<int:task_id>/delete', methods=['POST'])
def delete_low_version_scan_results(task_id):
    """Delete images based on low version scan results. Re-validate before deletion."""
    task = session.get(BatchTask, task_id)
    if not task:
        return jsonify({'error': 'not found'}), 404

    data = request.json or {}
    mode = data.get('mode', 'selected')
    result_ids = data.get('result_ids', [])
    delete_files = data.get('delete_files', False)

    if mode != 'selected':
        return jsonify({'error': 'mode must be "selected" and result_ids must be non-empty'}), 400
    validation_error = _validate_result_ids(result_ids)
    if validation_error:
        return jsonify({'error': validation_error}), 400

    results = session.query(LowVersionScanResult).filter(
        LowVersionScanResult.task_id == task_id,
        LowVersionScanResult.id.in_(result_ids),
    ).all()
    if not results:
        return jsonify({'error': 'no valid result_ids'}), 400

    # Re-validate with current thresholds
    try:
        params = json.loads(task.params_json) if task.params_json else {}
    except (json.JSONDecodeError, TypeError):
        params = {}
    main_threshold = params.get('main_threshold', 3)
    detail_threshold = params.get('detail_threshold', 5)

    # Re-compute current state
    from routes.batch import _compute_folder_stats
    stats = _compute_folder_stats()
    versions = session.query(ImageVersion).all()
    from collections import defaultdict
    by_barcode_type = defaultdict(list)
    for v in versions:
        by_barcode_type[(v.barcode, v.image_type)].append(v)

    # Check disabled scan roots for all items (chunked to stay under
    # SQLite expression depth limit of ~1000)
    items = [{'barcode': r.barcode, 'image_type': r.image_type, 'folder_ctime': r.folder_ctime} for r in results]
    disabled_count = 0
    _chunk_size = 500
    for _chunk_start in range(0, len(items), _chunk_size):
        _chunk = items[_chunk_start:_chunk_start + _chunk_size]
        _conds = []
        for i in _chunk:
            _conds.append(
                (Image.barcode == i['barcode']) &
                (Image.image_type == i['image_type']) &
                (Image.folder_ctime == i['folder_ctime'])
            )
        if _conds:
            disabled_count += session.query(Image.id).join(
                ScanRoot, Image.scan_root_id == ScanRoot.id,
            ).filter(
                ScanRoot.enabled == False,
                or_(*_conds),
            ).count()

    if disabled_count > 0:
        return jsonify({'error': '部分图片属于已禁用的扫描目录，无法删除', 'disabled_count': disabled_count}), 403

    affected_barcodes = set()
    deleted_image_count = 0
    skipped_count = 0

    # Pre-load scan roots for path validation
    scan_roots = {sr.id: sr.path for sr in session.query(ScanRoot).all()}

    for r in results:
        barcode, image_type, folder_ctime = r.barcode, r.image_type, r.folder_ctime
        key = (barcode, image_type, folder_ctime)
        count, _ = stats.get(key, (0, 0))
        threshold = main_threshold if image_type == 'main' else detail_threshold
        total_versions = len(by_barcode_type.get((barcode, image_type), []))

        # Validate: still qualifies for deletion
        if count == 0:
            r.delete_status = 'skipped'
            r.delete_message = '已无有效图片'
            skipped_count += 1
            continue
        if threshold == 0 or total_versions <= 1 or count >= threshold:
            r.delete_status = 'skipped'
            r.delete_message = f'不符合删除条件（阈值={threshold}，版本数={total_versions}，图片数={count}）'
            skipped_count += 1
            continue

        # Delete images
        match_ids = select(Image.id).where(
            Image.barcode == barcode,
            Image.image_type == image_type,
            Image.folder_ctime == folder_ctime,
            Image.status == 'active',
            Image.confirmed == True,
        ).join(ScanRoot, Image.scan_root_id == ScanRoot.id).where(
            ScanRoot.enabled == True,
        )

        imgs = session.query(Image).filter(Image.id.in_(match_ids)).all()

        # ---- Phase 1: pre-validate every image before touching disk or DB ----
        validation_msg = None
        if delete_files and imgs:
            for img in imgs:
                root_path = scan_roots.get(img.scan_root_id)
                if not root_path:
                    validation_msg = f'无法找到扫描目录 (scan_root_id={img.scan_root_id})'
                    break
                real_file = os.path.realpath(img.file_path)
                real_root = os.path.realpath(root_path)
                try:
                    safe = os.path.commonpath([real_file, real_root]) == real_root
                except ValueError:
                    safe = False
                if not safe:
                    validation_msg = '文件路径不在扫描目录下，拒绝删除'
                    break
                if not os.path.exists(img.file_path):
                    validation_msg = f'文件不存在: {img.file_path}'
                    break

        if validation_msg:
            r.delete_status = 'failed'
            r.delete_message = validation_msg
            skipped_count += 1
            continue

        # ---- Phase 2: delete files from disk (best-effort, cannot rollback) ----
        file_errors = []
        if delete_files and imgs:
            for img in imgs:
                try:
                    os.remove(img.file_path)
                except OSError as e:
                    file_errors.append(f'{img.file_path}: {e}')

        if file_errors:
            r.delete_status = 'failed'
            r.delete_message = f'文件删除失败: {"; ".join(file_errors)}'
            skipped_count += 1
            continue

        # ---- Phase 3: all safe — delete DB indices ----
        for img in imgs:
            session.delete(img)
            deleted_image_count += 1

        r.delete_status = 'deleted'
        r.deleted_at = datetime.datetime.now().isoformat()
        affected_barcodes.add(barcode)

    session.commit()

    for bc in affected_barcodes:
        update_versions_for_barcode(bc)

    return jsonify({
        'deleted_image_count': deleted_image_count,
        'skipped_count': skipped_count,
        'affected_barcodes': list(affected_barcodes),
    })
