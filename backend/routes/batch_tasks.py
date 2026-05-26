import json, logging
from flask import Blueprint, request, jsonify
from sqlalchemy import or_, select, func
from models import (
    session, Image, ImageVersion, ScanRoot,
    BatchTask, DuplicateScanResult, LowVersionScanResult,
)
from versioning import update_versions_for_barcode
from task_engine import create_task, finish_task, update_task_progress

batch_tasks_bp = Blueprint('batch_tasks', __name__)
_log = logging.getLogger(__name__)


# ---------- Duplicate scan handler ----------

def _run_duplicate_scan(task_id):
    """Background handler for duplicate_scan tasks."""
    from task_engine import TASK_HANDLERS  # avoid circular at module level
    _log.info("Starting duplicate_scan task %d", task_id)

    versions = session.query(ImageVersion).filter(
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
        rows = session.query(
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

    session.add_all(results)
    session.commit()
    update_task_progress(task_id, progress=total, total=total, result_count=len(results))
    finish_task(task_id, result_count=len(results))
    _log.info("duplicate_scan task %d done: %d results", task_id, len(results))


# Register handler at import time
from task_engine import register_handler
register_handler('duplicate_scan', _run_duplicate_scan)


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

    if mode != 'selected' or not result_ids:
        return jsonify({'error': 'mode must be "selected" and result_ids must be non-empty'}), 400

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

    # Check disabled scan roots for all items
    items = [{'barcode': r.barcode, 'image_type': r.image_type, 'folder_ctime': r.folder_ctime} for r in results]

    # Subquery for disabled root check
    conditions = []
    for item in items:
        conditions.append(
            (Image.barcode == item['barcode']) &
            (Image.image_type == item['image_type']) &
            (Image.folder_ctime == item['folder_ctime'])
        )
    disabled_count = session.query(Image.id).join(
        ScanRoot, Image.scan_root_id == ScanRoot.id,
    ).filter(
        ScanRoot.enabled == False,
        or_(*conditions),
    ).count() if conditions else 0

    if disabled_count > 0:
        return jsonify({'error': '部分图片属于已禁用的扫描目录，无法删除', 'disabled_count': disabled_count}), 403

    affected_barcodes = set()
    deleted_image_count = 0
    skipped_count = 0

    for r in results:
        key = (r.barcode, r.image_type, r.folder_ctime)
        if key not in valid_duplicates:
            r.delete_status = 'skipped'
            r.delete_message = '数据已变更，不再是有效重复'
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
        for img in imgs:
            if delete_files:
                try:
                    import os
                    os.remove(img.file_path)
                except OSError:
                    pass
            session.delete(img)
            deleted_image_count += 1

        r.delete_status = 'deleted'
        affected_barcodes.add(r.barcode)

    session.commit()

    for bc in affected_barcodes:
        update_versions_for_barcode(bc)

    return jsonify({
        'deleted_image_count': deleted_image_count,
        'skipped_count': skipped_count,
        'affected_barcodes': list(affected_barcodes),
    })
