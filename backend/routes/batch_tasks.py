import json, logging, datetime, os, re
from collections import defaultdict
from flask import Blueprint, request, jsonify
from sqlalchemy.orm.exc import ObjectDeletedError

_ISO_RE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?$')
from sqlalchemy import or_, func
from models import (
    session, Image, ImageVersion, ScanRoot,
    BatchTask, DuplicateScanResult, LowVersionScanResult,
    DuplicateVersionScanResult,
)
from versioning import update_versions_for_barcode
from task_engine import finish_task, update_task_progress, _get_thread_session
from routes.batch import _check_disabled_scan_roots, delete_images_with_validation, _delete_folder_images, _compute_folder_stats, validate_image_paths, _classify_delete_error


def _cleanup_orphaned_images():
    """清理文件不存在的孤儿 Image 记录，保证索引和磁盘一致性。
    检查条件：file_size=0 且文件不存在（双重验证避免误删）。
    返回 (cleaned_images, cleaned_versions) 清理数量。"""
    sess = _get_thread_session()

    # 第一步：快速筛选 file_size=0 的记录
    candidates = sess.query(Image).filter(
        Image.status == 'active',
        Image.confirmed == True,
        Image.file_size == 0,
    ).all()

    # 第二步：验证文件确实不存在（避免误删合法的 0 字节文件）
    orphaned_images = []
    for img in candidates:
        if img.file_path and not os.path.exists(img.file_path):
            orphaned_images.append(img)

    if not orphaned_images:
        return 0, 0

    _log.info("Found %d orphaned images (file_size=0 and file missing), cleaning up", len(orphaned_images))

    # 收集受影响的 barcode
    affected_barcodes = set()
    for img in orphaned_images:
        affected_barcodes.add(img.barcode)
        sess.delete(img)

    sess.flush()

    # 优化：用单个查询找出没有 active 图片的版本，而不是 N+1 查询
    cleaned_versions = 0
    if affected_barcodes:
        # 子查询：有 active 图片的 (barcode, image_type, folder_ctime) 组合
        active_keys = sess.query(
            Image.barcode, Image.image_type, Image.folder_ctime
        ).filter(
            Image.status == 'active',
            Image.barcode.in_(affected_barcodes),
        ).distinct().subquery()

        # 找出不在 active_keys 中的版本
        orphaned_versions = sess.query(ImageVersion).filter(
            ImageVersion.barcode.in_(affected_barcodes),
            ~sess.query(active_keys).filter(
                active_keys.c.barcode == ImageVersion.barcode,
                active_keys.c.image_type == ImageVersion.image_type,
                active_keys.c.folder_ctime == ImageVersion.folder_ctime,
            ).exists()
        ).all()

        for v in orphaned_versions:
            try:
                sess.delete(v)
                cleaned_versions += 1
            except ObjectDeletedError:
                continue

    sess.commit()

    # 重新计算版本
    for bc in affected_barcodes:
        update_versions_for_barcode(bc)

    _log.info("Cleanup done: %d images, %d versions removed", len(orphaned_images), cleaned_versions)
    return len(orphaned_images), cleaned_versions


# ---------- Duplicate scan handler ----------

batch_tasks_bp = Blueprint('batch_tasks', __name__)
_log = logging.getLogger(__name__)


# ---------- Duplicate scan handler ----------

def _run_duplicate_scan(task_id):
    """Background handler for duplicate_scan tasks."""
    _log.info("Starting duplicate_scan task %d", task_id)

    # 清理文件不存在的孤儿记录
    _cleanup_orphaned_images()

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
        except ObjectDeletedError:
            _log.warning("ImageVersion %d deleted during scan, skipping", v.id)
            continue
        if not dup_ctimes:
            continue
        for dup_ctime in dup_ctimes:
            try:
                key = (v.barcode, v.image_type, dup_ctime)
                if key not in dup_map:
                    dup_map[key] = {'version_label': v.version_label, 'version_folder_ctime': v.folder_ctime}
            except ObjectDeletedError:
                _log.warning("ImageVersion %d deleted during scan, skipping", v.id)
                continue

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

    # 清理文件不存在的孤儿记录
    _cleanup_orphaned_images()

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
        try:
            by_barcode_type[(v.barcode, v.image_type)].append(v)
        except ObjectDeletedError:
            _log.warning("ImageVersion %d deleted during scan, skipping", v.id)
            continue

    results = []
    for (barcode, image_type), vers in by_barcode_type.items():
        threshold = main_threshold if image_type == 'main' else detail_threshold
        total_versions = len(vers)

        for v in vers:
            try:
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
            except ObjectDeletedError:
                _log.warning("ImageVersion deleted during scan, skipping")
                continue

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


# ---------- Async delete handlers ----------

def _run_batch_delete_duplicates(task_id):
    """Background handler for batch_delete_duplicates tasks."""
    _log.info("Starting batch_delete_duplicates task %d", task_id)

    sess = _get_thread_session()
    task = sess.get(BatchTask, task_id)
    if not task:
        finish_task(task_id, error_message='Task not found')
        return

    try:
        params = json.loads(task.params_json) if task.params_json else {}
    except (json.JSONDecodeError, TypeError):
        params = {}

    items = params.get('items', [])
    delete_files = params.get('delete_files', False)

    if not items:
        finish_task(task_id, error_message='No items to delete')
        return

    total = len(items)
    update_task_progress(task_id, progress=0, total=total)

    # Check disabled scan roots
    disabled_count = _check_disabled_scan_roots(items)
    if disabled_count > 0:
        finish_task(task_id, error_message=f'部分图片属于已禁用的扫描目录（{disabled_count}个）')
        return

    # Build valid duplicate candidate set
    valid_duplicates = set()
    versions = sess.query(ImageVersion).filter(
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

    affected_barcodes = set()
    total_deleted = 0
    skipped_count = 0
    failed_count = 0

    for i, item in enumerate(items):
        key = (item['barcode'], item['image_type'], item['folder_ctime'])
        if key not in valid_duplicates:
            skipped_count += 1
            update_task_progress(task_id, progress=i + 1, current_item=item['barcode'])
            continue

        count, failed_items = _delete_folder_images(
            item['barcode'], item['image_type'], item['folder_ctime'], delete_files
        )
        total_deleted += count
        if failed_items:
            failed_count += len(failed_items)
            for fi in failed_items:
                update_task_progress(task_id, failed_count=failed_count, failed_item=fi)
        affected_barcodes.add(item['barcode'])
        update_task_progress(task_id, progress=i + 1, current_item=item['barcode'])

    sess.commit()

    for bc in affected_barcodes:
        update_versions_for_barcode(bc)

    finish_task(task_id, result_count=total_deleted)
    _log.info("batch_delete_duplicates task %d done: deleted %d images, skipped %d", task_id, total_deleted, skipped_count)


def _run_batch_delete_low_versions(task_id):
    """Background handler for batch_delete_low_versions tasks."""
    _log.info("Starting batch_delete_low_versions task %d", task_id)

    sess = _get_thread_session()
    task = sess.get(BatchTask, task_id)
    if not task:
        finish_task(task_id, error_message='Task not found')
        return

    try:
        params = json.loads(task.params_json) if task.params_json else {}
    except (json.JSONDecodeError, TypeError):
        params = {}

    items = params.get('items', [])
    delete_files = params.get('delete_files', False)
    main_threshold = params.get('main_threshold', 0)
    detail_threshold = params.get('detail_threshold', 0)

    if not items:
        finish_task(task_id, error_message='No items to delete')
        return

    total = len(items)
    update_task_progress(task_id, progress=0, total=total)

    # Check disabled scan roots
    disabled_count = _check_disabled_scan_roots(items)
    if disabled_count > 0:
        finish_task(task_id, error_message=f'部分图片属于已禁用的扫描目录（{disabled_count}个）')
        return

    # Re-validate eligibility
    stats = _compute_folder_stats()
    versions = sess.query(ImageVersion).all()
    by_barcode_type = defaultdict(list)
    for v in versions:
        by_barcode_type[(v.barcode, v.image_type)].append(v)

    affected_barcodes = set()
    total_deleted = 0
    skipped_count = 0
    failed_count = 0

    for i, item in enumerate(items):
        barcode, image_type, folder_ctime = item['barcode'], item['image_type'], item['folder_ctime']
        key = (barcode, image_type, folder_ctime)
        count, _ = stats.get(key, (0, 0))
        threshold = main_threshold if image_type == 'main' else detail_threshold
        total_versions = len(by_barcode_type.get((barcode, image_type), []))

        # Validate: still qualifies for deletion
        if count == 0 or threshold == 0 or total_versions <= 1 or count >= threshold:
            skipped_count += 1
            update_task_progress(task_id, progress=i + 1, current_item=barcode)
            continue

        count, failed_items = _delete_folder_images(barcode, image_type, folder_ctime, delete_files)
        total_deleted += count
        if failed_items:
            failed_count += len(failed_items)
            for fi in failed_items:
                update_task_progress(task_id, failed_count=failed_count, failed_item=fi)
        affected_barcodes.add(barcode)
        update_task_progress(task_id, progress=i + 1, current_item=barcode)

    sess.commit()

    for bc in affected_barcodes:
        update_versions_for_barcode(bc)

    finish_task(task_id, result_count=total_deleted)
    _log.info("batch_delete_low_versions task %d done: deleted %d images, skipped %d", task_id, total_deleted, skipped_count)


def _run_delete_version(task_id):
    """Background handler for delete_version tasks."""
    _log.info("Starting delete_version task %d", task_id)

    sess = _get_thread_session()
    task = sess.get(BatchTask, task_id)
    if not task:
        finish_task(task_id, error_message='Task not found')
        return

    try:
        params = json.loads(task.params_json) if task.params_json else {}
    except (json.JSONDecodeError, TypeError):
        params = {}

    version_id = params.get('version_id')
    delete_files = params.get('delete_files', False)

    if not version_id:
        finish_task(task_id, error_message='version_id required')
        return

    v = sess.get(ImageVersion, version_id)
    if not v:
        finish_task(task_id, error_message='Version not found')
        return

    barcode = v.barcode
    folder_ctime = v.folder_ctime
    image_type = v.image_type

    # 检查是否属于禁用的扫描目录
    items = [{'barcode': barcode, 'image_type': image_type, 'folder_ctime': folder_ctime}]
    disabled_count = _check_disabled_scan_roots(items)
    if disabled_count > 0:
        finish_task(task_id, error_message='该版本属于已禁用的扫描目录，无法删除')
        return

    # Delete all images belonging to this version
    imgs = sess.query(Image).filter(
        Image.barcode == barcode,
        Image.folder_ctime == folder_ctime,
        Image.image_type == image_type,
    ).all()

    total = len(imgs)
    update_task_progress(task_id, progress=0, total=total)

    deleted_count = 0
    failed_count = 0
    from routes.batch import _classify_delete_error
    for i, img in enumerate(imgs):
        if delete_files:
            try:
                os.remove(img.file_path)
                sess.delete(img)
                deleted_count += 1
            except OSError as e:
                failed_count += 1
                update_task_progress(task_id, failed_count=failed_count,
                    failed_item={'file': img.file_path, 'reason': _classify_delete_error(img.file_path, e)})
        else:
            sess.delete(img)
            deleted_count += 1
        update_task_progress(task_id, progress=i + 1, current_item=f'image_id={img.id}')

    sess.commit()

    # 重建版本：如果图片全部删除，ImageVersion 会被清理；部分失败时保留版本
    update_versions_for_barcode(barcode)

    finish_task(task_id, result_count=deleted_count)
    _log.info("delete_version task %d done: deleted %d images", task_id, deleted_count)


def _run_batch_delete_images(task_id):
    """Background handler for batch_delete_images tasks."""
    _log.info("Starting batch_delete_images task %d", task_id)

    sess = _get_thread_session()
    task = sess.get(BatchTask, task_id)
    if not task:
        finish_task(task_id, error_message='Task not found')
        return

    try:
        params = json.loads(task.params_json) if task.params_json else {}
    except (json.JSONDecodeError, TypeError):
        params = {}

    ids = params.get('ids', [])
    delete_files = params.get('delete_files', False)

    if not ids:
        finish_task(task_id, error_message='No image IDs provided')
        return

    total = len(ids)
    update_task_progress(task_id, progress=0, total=total)

    # Reject if any IDs belong to disabled scan roots
    disabled = sess.query(Image.id).join(
        ScanRoot, Image.scan_root_id == ScanRoot.id
    ).filter(
        Image.id.in_(ids), ScanRoot.enabled == False
    ).all()
    disabled_ids = {r[0] for r in disabled}
    if disabled_ids:
        finish_task(task_id, error_message=f'部分图片属于已禁用的扫描目录（{len(disabled_ids)}个）')
        return

    # Collect barcodes before deletion
    barcodes = {r[0] for r in sess.query(Image.barcode).filter(
        Image.id.in_(ids)).distinct().all()}

    # Delete files if requested (with path safety validation)
    failed_count = 0
    delete_db_ids = ids  # 默认删除所有传入 IDs 的索引（delete_files=False 时）
    if delete_files:
        imgs = sess.query(Image).filter(Image.id.in_(ids)).all()
        scan_roots = {sr.id: sr.path for sr in sess.query(ScanRoot).all()}
        from routes.batch import validate_image_paths, _classify_delete_error
        is_valid, error_msg = validate_image_paths(imgs, scan_roots)
        if not is_valid:
            finish_task(task_id, error_message=f'路径安全验证失败: {error_msg}')
            return
        delete_db_ids = []
        for i, img in enumerate(imgs):
            try:
                os.remove(img.file_path)
                delete_db_ids.append(img.id)
            except OSError as e:
                failed_count += 1
                update_task_progress(task_id, failed_count=failed_count,
                    failed_item={'file': img.file_path, 'reason': _classify_delete_error(img.file_path, e)})
            update_task_progress(task_id, progress=i + 1, current_item=img.barcode)
    else:
        # Even when not deleting files, report progress for consistency
        for i in range(total):
            update_task_progress(task_id, progress=i + 1, current_item=f'image_id={ids[i]}')

    # Delete database records（仅删除文件删除成功的记录）
    deleted = sess.query(Image).filter(Image.id.in_(delete_db_ids)).delete(synchronize_session='fetch')
    sess.commit()

    # Re-sequence remaining versions
    for bc in barcodes:
        update_versions_for_barcode(bc)

    finish_task(task_id, result_count=deleted)
    _log.info("batch_delete_images task %d done: deleted %d images", task_id, deleted)


# ---------- Duplicate version scan handler ----------

def _run_duplicate_version_scan(task_id):
    """Background handler for duplicate_version_scan tasks."""
    _log.info("Starting duplicate_version_scan task %d", task_id)

    # Clean up orphaned images first (consistent with other scan handlers)
    _cleanup_orphaned_images()

    from duplicate_version_detector import detect_duplicate_versions

    sess = _get_thread_session()

    def _progress(current, total):
        update_task_progress(task_id, progress=current, total=total)

    groups = detect_duplicate_versions(sess, progress_callback=_progress)

    if not groups:
        finish_task(task_id, result_count=0)
        return

    # Persist results
    total_results = 0
    for group in groups:
        for member in group['members']:
            sess.add(DuplicateVersionScanResult(
                task_id=task_id,
                group_id=group['group_id'],
                barcode=group['barcode'],
                image_type=group['image_type'],
                folder_ctime=member['folder_ctime'],
                version_label=member['version_label'],
                image_count=member['image_count'],
                total_file_size=member['total_file_size'],
                is_latest=member['is_latest'],
                role=member['role'],
                keep_reason=member['keep_reason'],
            ))
            total_results += 1

    sess.commit()
    finish_task(task_id, result_count=total_results)
    _log.info("duplicate_version_scan task %d done: %d results in %d groups",
              task_id, total_results, len(groups))


def _run_batch_delete_duplicate_versions(task_id):
    """Background handler for batch_delete_duplicate_versions tasks.
    Soft-deletes by marking Image.status = 'duplicate_version'.
    Re-validates duplicate relationships before deletion."""
    _log.info("Starting batch_delete_duplicate_versions task %d", task_id)

    from duplicate_version_detector import are_duplicate_versions, _get_ordered_images

    sess = _get_thread_session()
    task = sess.get(BatchTask, task_id)
    if not task:
        finish_task(task_id, error_message='Task not found')
        return

    try:
        params = json.loads(task.params_json) if task.params_json else {}
    except (json.JSONDecodeError, TypeError):
        params = {}

    result_ids = params.get('result_ids', [])
    scan_task_id = params.get('scan_task_id')

    if not result_ids or not scan_task_id:
        finish_task(task_id, error_message='result_ids and scan_task_id required')
        return

    total = len(result_ids)
    update_task_progress(task_id, progress=0, total=total)

    # Load the submitted clean results
    results = sess.query(DuplicateVersionScanResult).filter(
        DuplicateVersionScanResult.task_id == scan_task_id,
        DuplicateVersionScanResult.id.in_(result_ids),
        DuplicateVersionScanResult.role == 'clean',
    ).all()

    if not results:
        finish_task(task_id, error_message='No valid clean results found')
        return

    # Load ALL group members for each referenced group to find the kept version.
    # The submitted result_ids only contain clean members; we need the keep members too.
    group_ids = list({r.group_id for r in results})
    all_group_members = sess.query(DuplicateVersionScanResult).filter(
        DuplicateVersionScanResult.task_id == scan_task_id,
        DuplicateVersionScanResult.group_id.in_(group_ids),
    ).all()

    # Build group_id → {kept_version_ctime} lookup from all members
    kept_by_group = {}
    for m in all_group_members:
        if m.role in ('keep', 'user_selected'):
            kept_by_group[m.group_id] = m.folder_ctime

    affected_barcodes = set()
    deleted_image_count = 0
    skipped_count = 0
    failed_count = 0
    processed = 0

    for r in results:
        processed += 1

        if r.delete_status == 'deleted':
            skipped_count += 1
            update_task_progress(task_id, progress=processed, current_item=r.barcode)
            continue

        # Find the kept version for this group
        kept_ctime = kept_by_group.get(r.group_id, '')
        if not kept_ctime:
            r.delete_status = 'skipped'
            r.delete_message = '未找到保留版本'
            skipped_count += 1
            update_task_progress(task_id, progress=processed, current_item=r.barcode)
            continue

        # Fix 1: Re-validate duplicate relationship before deletion.
        # Load current active images for both the kept version and this version,
        # then re-run are_duplicate_versions to confirm they're still duplicates.
        kept_imgs = _get_ordered_images(sess, r.barcode, r.image_type, kept_ctime)
        clean_imgs = _get_ordered_images(sess, r.barcode, r.image_type, r.folder_ctime)

        if not clean_imgs:
            r.delete_status = 'skipped'
            r.delete_message = '已无有效图片'
            skipped_count += 1
            update_task_progress(task_id, progress=processed, current_item=r.barcode)
            continue

        if not kept_imgs:
            r.delete_status = 'skipped'
            r.delete_message = '保留版本已无有效图片'
            skipped_count += 1
            update_task_progress(task_id, progress=processed, current_item=r.barcode)
            continue

        if not are_duplicate_versions(kept_imgs, clean_imgs):
            r.delete_status = 'skipped'
            r.delete_message = '数据已变更，不再是重复版本'
            skipped_count += 1
            update_task_progress(task_id, progress=processed, current_item=r.barcode)
            continue

        # Soft delete: mark images as 'duplicate_version'
        try:
            for img in clean_imgs:
                img.status = 'duplicate_version'
                img.updated_at = datetime.datetime.now().isoformat()
            r.delete_status = 'deleted'
            r.deleted_at = datetime.datetime.now().isoformat()
            r.kept_version_ctime = kept_ctime
            deleted_image_count += len(clean_imgs)
            affected_barcodes.add(r.barcode)
        except Exception as e:
            r.delete_status = 'failed'
            r.delete_message = str(e)
            failed_count += 1

        update_task_progress(task_id, progress=processed, current_item=r.barcode)

    sess.commit()

    # Rebuild versions for affected barcodes
    for bc in affected_barcodes:
        update_versions_for_barcode(bc)

    finish_task(task_id, result_count=deleted_image_count)
    _log.info("batch_delete_duplicate_versions task %d done: soft-deleted %d images, skipped %d, failed %d",
              task_id, deleted_image_count, skipped_count, failed_count)


register_handler('batch_delete_duplicates', _run_batch_delete_duplicates)
register_handler('batch_delete_low_versions', _run_batch_delete_low_versions)
register_handler('delete_version', _run_delete_version)
register_handler('batch_delete_images', _run_batch_delete_images)
register_handler('duplicate_version_scan', _run_duplicate_version_scan)
register_handler('batch_delete_duplicate_versions', _run_batch_delete_duplicate_versions)


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


def _validate_result_ids(result_ids):
    """Validate that result_ids is a non-empty list of positive integers.
    Returns (None, error_response) on failure, or (list, None) on success."""
    if not isinstance(result_ids, list):
        return None, (jsonify({'error': 'result_ids must be a non-empty list of positive integers'}), 400)
    if not result_ids:
        return None, (jsonify({'error': 'result_ids must be a non-empty list of positive integers'}), 400)
    for rid in result_ids:
        if not isinstance(rid, int) or isinstance(rid, bool) or rid <= 0:
            return None, (jsonify({'error': 'result_ids must be a non-empty list of positive integers'}), 400)
    return result_ids, None


def _check_result_id_consistency(requested_ids, result_model, task_id):
    """Query results by task_id + requested_ids, return (results, error_response).
    error_response is None if all IDs are valid, otherwise a (jsonify, 400) tuple."""
    requested_ids = list(dict.fromkeys(requested_ids))  # dedupe, preserve order
    results = session.query(result_model).filter(
        result_model.task_id == task_id,
        result_model.id.in_(requested_ids),
    ).all()
    found_ids = {r.id for r in results}
    missing_ids = [rid for rid in requested_ids if rid not in found_ids]
    if missing_ids:
        return results, (jsonify({'error': 'result_ids 中包含无效 ID（不存在或不属于当前任务）', 'missing_ids': missing_ids}), 400)
    if not results:
        return [], (jsonify({'error': 'no valid result_ids'}), 400)
    return results, None


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
        return jsonify({'error': 'mode must be "selected"'}), 400

    result_ids, err = _validate_result_ids(result_ids)
    if err:
        return err

    results, err = _check_result_id_consistency(result_ids, DuplicateScanResult, task_id)
    if err:
        return err

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

    # Check disabled scan roots using shared helper
    items = [{'barcode': r.barcode, 'image_type': r.image_type, 'folder_ctime': r.folder_ctime} for r in results]
    disabled_count = _check_disabled_scan_roots(items)
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
            skipped_count += 1
            continue

        # Use shared helper for deletion with validation
        deleted, error_msg = delete_images_with_validation(
            r.barcode, r.image_type, r.folder_ctime, delete_files
        )

        if error_msg:
            r.delete_status = 'failed'
            r.delete_message = error_msg
            skipped_count += 1
        else:
            r.delete_status = 'deleted'
            r.deleted_at = datetime.datetime.now().isoformat()
            deleted_image_count += deleted
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

    # 计算各状态的数量（仅在无筛选时返回 summary）
    summary = None
    if not status_tag and not delete_status:
        from sqlalchemy import func
        status_counts = session.query(
            LowVersionScanResult.status_tag,
            func.count(LowVersionScanResult.id)
        ).filter(
            LowVersionScanResult.task_id == task_id,
        ).group_by(LowVersionScanResult.status_tag).all()
        summary = {tag: count for tag, count in status_counts}

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
        'summary': summary,
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
        return jsonify({'error': 'mode must be "selected"'}), 400

    result_ids, err = _validate_result_ids(result_ids)
    if err:
        return err

    results, err = _check_result_id_consistency(result_ids, LowVersionScanResult, task_id)
    if err:
        return err

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

    # Check disabled scan roots using shared helper
    items = [{'barcode': r.barcode, 'image_type': r.image_type, 'folder_ctime': r.folder_ctime} for r in results]
    disabled_count = _check_disabled_scan_roots(items)
    if disabled_count > 0:
        return jsonify({'error': '部分图片属于已禁用的扫描目录，无法删除', 'disabled_count': disabled_count}), 403

    affected_barcodes = set()
    deleted_image_count = 0
    skipped_count = 0

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

        # Use shared helper for deletion with validation
        deleted, error_msg = delete_images_with_validation(barcode, image_type, folder_ctime, delete_files)

        if error_msg:
            r.delete_status = 'failed'
            r.delete_message = error_msg
            skipped_count += 1
        else:
            r.delete_status = 'deleted'
            r.deleted_at = datetime.datetime.now().isoformat()
            deleted_image_count += deleted
            affected_barcodes.add(barcode)

    session.commit()

    for bc in affected_barcodes:
        update_versions_for_barcode(bc)

    return jsonify({
        'deleted_image_count': deleted_image_count,
        'skipped_count': skipped_count,
        'affected_barcodes': list(affected_barcodes),
    })


# ---------- Async delete task routes ----------

@batch_tasks_bp.route('/batch/delete-duplicates/tasks', methods=['POST'])
def create_batch_delete_duplicates_task():
    """Create an async task to delete duplicate folders."""
    data = request.json or {}
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

    params = {
        'items': items,
        'delete_files': delete_files,
    }
    from task_engine import create_task
    task_dict, is_new = create_task('batch_delete_duplicates', params=params)
    code = 201 if is_new else 200
    return jsonify(task_dict), code


@batch_tasks_bp.route('/batch/delete-low-versions/tasks', methods=['POST'])
def create_batch_delete_low_versions_task():
    """Create an async task to delete low version folders."""
    data = request.json or {}
    items = data.get('items', [])
    delete_files = data.get('delete_files', False)
    main_threshold = data.get('main_threshold', 0)
    detail_threshold = data.get('detail_threshold', 0)

    if not items:
        return jsonify({'error': 'items required'}), 400
    if not isinstance(main_threshold, int) or not isinstance(detail_threshold, int):
        return jsonify({'error': 'main_threshold and detail_threshold must be integers'}), 400

    for i, item in enumerate(items):
        if not isinstance(item, dict):
            return jsonify({'error': f'item {i}: must be an object'}), 400
        if not item.get('barcode') or not item.get('image_type') or not item.get('folder_ctime'):
            return jsonify({'error': f'item {i}: barcode, image_type, folder_ctime required'}), 400
        if item['image_type'] not in ('main', 'detail'):
            return jsonify({'error': f'item {i}: image_type must be main or detail'}), 400
        if not _ISO_RE.match(item['folder_ctime']):
            return jsonify({'error': f'item {i}: folder_ctime must be ISO8601 format'}), 400

    params = {
        'items': items,
        'delete_files': delete_files,
        'main_threshold': main_threshold,
        'detail_threshold': detail_threshold,
    }
    from task_engine import create_task
    task_dict, is_new = create_task('batch_delete_low_versions', params=params)
    code = 201 if is_new else 200
    return jsonify(task_dict), code


@batch_tasks_bp.route('/versions/<int:version_id>/delete-task', methods=['POST'])
def create_delete_version_task(version_id):
    """Create an async task to delete a version and its images."""
    data = request.json or {}
    delete_files = data.get('delete_files', False)

    # Verify version exists
    v = session.get(ImageVersion, version_id)
    if not v:
        return jsonify({'error': 'Version not found'}), 404

    params = {
        'version_id': version_id,
        'delete_files': delete_files,
    }
    from task_engine import create_task
    task_dict, is_new = create_task('delete_version', params=params)
    code = 201 if is_new else 200
    return jsonify(task_dict), code


@batch_tasks_bp.route('/images/batch-delete-task', methods=['POST'])
def create_batch_delete_images_task():
    """Create an async task to delete multiple images by ID."""
    data = request.json or {}
    ids = data.get('ids', [])
    delete_files = data.get('delete_files', False)

    if not ids:
        return jsonify({'error': 'ids required'}), 400

    params = {
        'ids': ids,
        'delete_files': delete_files,
    }
    from task_engine import create_task
    task_dict, is_new = create_task('batch_delete_images', params=params)
    code = 201 if is_new else 200
    return jsonify(task_dict), code


# ---------- Duplicate version scan routes ----------

@batch_tasks_bp.route('/batch/duplicate-version-scan/tasks', methods=['POST'])
def create_duplicate_version_scan():
    from task_engine import create_task
    task_dict, is_new = create_task('duplicate_version_scan', params={})
    code = 201 if is_new else 200
    return jsonify(task_dict), code


@batch_tasks_bp.route('/batch/duplicate-version-scan/tasks', methods=['GET'])
def list_duplicate_version_scan_tasks():
    from task_engine import get_tasks
    tasks = get_tasks(task_type='duplicate_version_scan')
    return jsonify(tasks)


@batch_tasks_bp.route('/batch/duplicate-version-scan/tasks/<int:task_id>', methods=['GET'])
def get_duplicate_version_scan_task(task_id):
    from task_engine import get_task
    task = get_task(task_id)
    if not task:
        return jsonify({'error': 'not found'}), 404
    return jsonify(task)


@batch_tasks_bp.route('/batch/duplicate-version-scan/tasks/<int:task_id>', methods=['DELETE'])
def delete_duplicate_version_scan_task(task_id):
    from task_engine import delete_task
    result = delete_task(task_id)
    if result is None:
        return jsonify({'error': 'not found'}), 404
    if 'error' in result:
        return jsonify(result), 409
    return jsonify(result)


@batch_tasks_bp.route('/batch/duplicate-version-scan/tasks/<int:task_id>/results', methods=['GET'])
def get_duplicate_version_scan_results(task_id):
    """Get duplicate version scan results, grouped by group_id."""
    task = session.get(BatchTask, task_id)
    if not task:
        return jsonify({'error': 'not found'}), 404

    # Load all results for this task
    results = session.query(DuplicateVersionScanResult).filter(
        DuplicateVersionScanResult.task_id == task_id,
    ).order_by(DuplicateVersionScanResult.group_id, DuplicateVersionScanResult.id).all()

    # Group by group_id
    groups_dict = defaultdict(list)
    for r in results:
        groups_dict[r.group_id].append(r)

    groups = []
    for gid in sorted(groups_dict.keys()):
        members = groups_dict[gid]
        first = members[0]
        groups.append({
            'group_id': gid,
            'barcode': first.barcode,
            'image_type': first.image_type,
            'image_count': first.image_count,
            'members': [{
                'id': m.id,
                'folder_ctime': m.folder_ctime,
                'version_label': m.version_label,
                'image_count': m.image_count,
                'total_file_size': m.total_file_size,
                'is_latest': m.is_latest,
                'role': m.role,
                'keep_reason': m.keep_reason,
                'delete_status': m.delete_status,
                'delete_message': m.delete_message,
                'deleted_at': m.deleted_at,
                'kept_version_ctime': m.kept_version_ctime,
            } for m in members],
        })

    # Summary
    total_groups = len(groups)
    total_clean = sum(1 for g in groups for m in g['members'] if m['role'] == 'clean')
    total_keep = sum(1 for g in groups for m in g['members'] if m['role'] in ('keep', 'user_selected'))
    total_deleted = sum(1 for g in groups for m in g['members'] if m['delete_status'] == 'deleted')

    return jsonify({
        'groups': groups,
        'summary': {
            'total_groups': total_groups,
            'total_clean': total_clean,
            'total_keep': total_keep,
            'total_deleted': total_deleted,
        },
    })


@batch_tasks_bp.route('/batch/duplicate-version-scan/tasks/<int:task_id>/change-keep', methods=['POST'])
def change_keep_version(task_id):
    """Change which version is kept in a duplicate group."""
    task = session.get(BatchTask, task_id)
    if not task:
        return jsonify({'error': 'not found'}), 404

    data = request.json or {}
    group_id = data.get('group_id')
    new_keep_ctime = data.get('folder_ctime')

    if not group_id or not new_keep_ctime:
        return jsonify({'error': 'group_id and folder_ctime required'}), 400

    # Get all members of this group
    members = session.query(DuplicateVersionScanResult).filter(
        DuplicateVersionScanResult.task_id == task_id,
        DuplicateVersionScanResult.group_id == group_id,
    ).all()

    if not members:
        return jsonify({'error': 'group not found'}), 404

    # Verify the new keep version exists in the group
    new_keep = next((m for m in members if m.folder_ctime == new_keep_ctime), None)
    if not new_keep:
        return jsonify({'error': 'folder_ctime not found in group'}), 404

    # Update roles
    for m in members:
        if m.folder_ctime == new_keep_ctime:
            m.role = 'user_selected'
            m.keep_reason = '用户手动选择'
        elif m.role in ('keep', 'user_selected'):
            m.role = 'clean'
            m.keep_reason = ''

    session.commit()

    return jsonify({'ok': True})


@batch_tasks_bp.route('/batch/duplicate-version-scan/tasks/<int:task_id>/restore', methods=['POST'])
def restore_duplicate_versions(task_id):
    """Restore soft-deleted duplicate versions."""
    task = session.get(BatchTask, task_id)
    if not task:
        return jsonify({'error': 'not found'}), 404

    data = request.json or {}
    result_ids = data.get('result_ids', [])

    result_ids, err = _validate_result_ids(result_ids)
    if err:
        return err

    results, err = _check_result_id_consistency(result_ids, DuplicateVersionScanResult, task_id)
    if err:
        return err

    affected_barcodes = set()
    restored_count = 0

    for r in results:
        if r.delete_status != 'deleted':
            continue
        # Restore images
        imgs = session.query(Image).filter(
            Image.barcode == r.barcode,
            Image.image_type == r.image_type,
            Image.folder_ctime == r.folder_ctime,
            Image.status == 'duplicate_version',
        ).all()
        for img in imgs:
            img.status = 'active'
            img.updated_at = datetime.datetime.now().isoformat()
        r.delete_status = 'restored'
        r.delete_message = '已恢复'
        r.deleted_at = ''
        restored_count += len(imgs)
        affected_barcodes.add(r.barcode)

    session.commit()

    for bc in affected_barcodes:
        update_versions_for_barcode(bc)

    return jsonify({
        'restored_count': restored_count,
        'affected_barcodes': list(affected_barcodes),
    })


@batch_tasks_bp.route('/batch/duplicate-version-scan/tasks/<int:task_id>/permanent-delete', methods=['POST'])
def permanent_delete_duplicate_versions(task_id):
    """Permanently delete soft-deleted duplicate versions (index only or index+files)."""
    task = session.get(BatchTask, task_id)
    if not task:
        return jsonify({'error': 'not found'}), 404

    data = request.json or {}
    result_ids = data.get('result_ids', [])
    delete_files = bool(data.get('delete_files', False))

    result_ids, err = _validate_result_ids(result_ids)
    if err:
        return err

    results, err = _check_result_id_consistency(result_ids, DuplicateVersionScanResult, task_id)
    if err:
        return err

    # Only operate on soft-deleted results
    results = [r for r in results if r.delete_status == 'deleted']
    if not results:
        return jsonify({'error': '没有可永久删除的结果'}), 400

    affected_barcodes = set()
    permanently_deleted_count = 0
    failed_count = 0

    # Pre-validate paths if deleting files
    if delete_files:
        scan_roots = {sr.id: sr.path for sr in session.query(ScanRoot).all()}

    for r in results:
        imgs = session.query(Image).filter(
            Image.barcode == r.barcode,
            Image.image_type == r.image_type,
            Image.folder_ctime == r.folder_ctime,
            Image.status == 'duplicate_version',
        ).all()

        if not imgs:
            # No matching images — mark as permanently deleted (already gone)
            r.delete_status = 'permanently_deleted'
            r.delete_message = '已永久删除（索引已不存在）'
            r.deleted_at = datetime.datetime.now().isoformat()
            permanently_deleted_count += 1
            affected_barcodes.add(r.barcode)
            continue

        if delete_files:
            # Validate paths are inside scan roots
            is_valid, error_msg = validate_image_paths(imgs, scan_roots)
            if not is_valid:
                r.delete_status = 'failed'
                r.delete_message = f'路径验证失败: {error_msg}'
                failed_count += 1
                continue

            # Per-file atomicity: delete DB row immediately after each successful file delete
            file_errors = []
            deleted_db_count = 0
            for img in imgs:
                try:
                    os.remove(img.file_path)
                    session.delete(img)
                    deleted_db_count += 1
                except FileNotFoundError:
                    # File already gone — still clean up DB row
                    session.delete(img)
                    deleted_db_count += 1
                except OSError as e:
                    file_errors.append(f'{img.file_path}: {_classify_delete_error(img.file_path, e)}')

            if file_errors:
                r.delete_status = 'failed'
                r.delete_message = f'部分文件删除失败: {"; ".join(file_errors)}'
                failed_count += 1
                continue
        else:
            # Index-only: delete all DB rows
            for img in imgs:
                session.delete(img)

        r.delete_status = 'permanently_deleted'
        r.delete_message = '已永久删除'
        r.deleted_at = datetime.datetime.now().isoformat()
        permanently_deleted_count += 1
        affected_barcodes.add(r.barcode)

    session.commit()

    for bc in affected_barcodes:
        update_versions_for_barcode(bc)

    return jsonify({
        'permanently_deleted_count': permanently_deleted_count,
        'failed_count': failed_count,
        'affected_barcodes': list(affected_barcodes),
    })


@batch_tasks_bp.route('/batch/delete-duplicate-versions/tasks', methods=['POST'])
def create_batch_delete_duplicate_versions_task():
    """Create an async task to soft-delete duplicate versions."""
    data = request.json or {}
    scan_task_id = data.get('scan_task_id')
    result_ids = data.get('result_ids', [])

    if not scan_task_id:
        return jsonify({'error': 'scan_task_id required'}), 400

    result_ids, err = _validate_result_ids(result_ids)
    if err:
        return err

    params = {
        'scan_task_id': scan_task_id,
        'result_ids': result_ids,
    }
    from task_engine import create_task
    task_dict, is_new = create_task('batch_delete_duplicate_versions', params=params)
    code = 201 if is_new else 200
    return jsonify(task_dict), code
