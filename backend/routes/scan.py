import os, json, uuid, threading, datetime, traceback, logging
from flask import Blueprint, request, jsonify
from sqlalchemy.exc import IntegrityError
from models import session, ScanRoot, Image, ScanLog, ImageVersion, RejectedBarcode
from scanner import scan_root, count_image_files, ScanCancelled
from versioning import update_versions_for_barcode, update_all_versions
from routes._utils import (
    JSONPayloadError,
    json_payload_error_response,
    normalize_scan_root_path,
    path_contains,
    require_json_object,
)

scan_bp = Blueprint('scan', __name__)

_scan_lock = threading.Lock()
_scan_jobs = {}
_scan_cancel_flags = {}  # job_id -> True when cancel requested
_recent_scans = []  # 最近扫描记录（内存，最多 10 条）
_scan_config_lock = threading.RLock()
_log = logging.getLogger(__name__)

def _add_log(action, status, message, details=''):
    log = ScanLog(action=action, status=status, message=message, details=details)
    session.add(log)
    session.commit()


def _safe_add_log(action, status, message, details=''):
    """Best-effort audit logging that never aborts the scan worker."""
    try:
        _add_log(action, status, message, details)
    except Exception:
        try:
            session.rollback()
        except Exception:
            pass
        _log.exception('扫描日志写入失败: action=%s status=%s', action, status)


def _make_scan_job(root_ids):
    """Create the initial in-memory scan job dict (caller holds _scan_lock)."""
    return {
        'root_ids': list(root_ids),
        'status': 'running',
        'phase': 'counting',
        'current_root_path': '',
        'current_root_index': 0,
        'total_roots': len(root_ids),
        'current_file': '',
        'current_dir': '',
        'added': 0, 'skipped': 0, 'broken_cleaned': 0, 'broken_new': 0,
        'rejected': 0,
        'thumbnail_total': 0, 'thumbnail_current': 0,
        'total_files': 0, 'processed_files': 0, 'percent': 0,
        'eta_seconds': 0, 'speed': 0,
        'counted_files': 0, 'counting_current_dir': '',
        'counting_root_index': 0, 'counting_total_roots': len(root_ids),
        'error': None,
        'elapsed_seconds': 0,
        'started_at': datetime.datetime.now().isoformat(),
    }


def _run_scan(root_ids, scan_mode, job_id=None):
    """Execute scan in background thread with progress reporting.

    job_id must already be registered in _scan_jobs under _scan_lock by the
    request handler (atomic claim) to prevent TOCTOU dual-running scans.
    """
    if job_id is None:
        # Backward-compatible path (tests / direct calls): claim under lock
        job_id = str(uuid.uuid4())
        with _scan_lock:
            _scan_jobs[job_id] = _make_scan_job(root_ids)
    full_scan = scan_mode == 'full'

    def is_cancelled():
        with _scan_lock:
            return _scan_cancel_flags.get(job_id, False)

    def progress(phase, **kw):
        with _scan_lock:
            job = _scan_jobs.get(job_id)
            if job:
                job['phase'] = phase
                job.update(kw)
                # 计算真实进度
                total_files = job.get('total_files', 0)
                processed_files = job.get('processed_files', 0)
                if total_files > 0 and phase == 'scanning':
                    job['percent'] = min(99, round(processed_files / total_files * 100))
                    # 计算速度和 ETA
                    try:
                        started = datetime.datetime.fromisoformat(job['started_at'])
                        elapsed = (datetime.datetime.now() - started).total_seconds()
                        if elapsed > 0 and processed_files > 0:
                            job['speed'] = round(processed_files / elapsed, 1)
                            remaining = total_files - processed_files
                            if job['speed'] > 0 and remaining > 0:
                                job['eta_seconds'] = round(remaining / job['speed'])
                            else:
                                job['eta_seconds'] = 0
                    except (ValueError, TypeError):
                        pass

    started_at = datetime.datetime.now()

    try:
        # Keep the initial audit write inside the same exception boundary as
        # the worker.  Audit storage is useful, but must not strand a running
        # in-memory job when the database/log table is temporarily unavailable.
        _safe_add_log('scan', 'info',
                      f"扫描开始 - {'全量' if full_scan else '增量'}模式",
                      json.dumps({'job_id': job_id, 'root_ids': root_ids}))
        roots = session.query(ScanRoot).filter(ScanRoot.id.in_(root_ids)).all()

        # 阶段1: 统计文件数量（额外遍历一次目录，用于计算真实扫描百分比）
        progress('counting')
        total_files = count_image_files(roots, progress_callback=progress,
                                        is_cancelled=is_cancelled)
        with _scan_lock:
            job = _scan_jobs.get(job_id)
            if job:
                job['total_files'] = total_files
                job['counting_current_dir'] = ''

        # 阶段2: 执行扫描（重置 started_at 以获得准确的 ETA）
        with _scan_lock:
            job = _scan_jobs.get(job_id)
            if job:
                job['started_at'] = datetime.datetime.now().isoformat()
        total = {'added': 0, 'skipped': 0, 'broken_cleaned': 0, 'rejected': 0}
        all_affected = set()
        processed_offset = 0  # 跨 root 的已处理文件累计数

        for i, r in enumerate(roots):
            with _scan_lock:
                job = _scan_jobs.get(job_id)
                if job:
                    job['current_root_index'] = i + 1
                    job['current_root_path'] = r.path

            res = scan_root(r.id, full_scan=full_scan, progress_callback=progress,
                            processed_offset=processed_offset,
                            is_cancelled=is_cancelled)
            for k in total:
                total[k] += res.get(k, 0)
            all_affected.update(res.get('affected_barcodes', []))
            # 更新 offset：使用回调中最后报告的 processed_files 值
            with _scan_lock:
                job = _scan_jobs.get(job_id)
                if job:
                    processed_offset = job.get('processed_files', processed_offset)

        # 阶段3: 更新版本
        versioning_total = len(all_affected)
        progress('versioning', versioning_total=versioning_total)
        for idx, bc in enumerate(sorted(all_affected)):
            if is_cancelled():
                raise ScanCancelled()
            update_versions_for_barcode(bc)
            # 每 10 个或最后一个更新进度（提高小集合的粒度）
            if (idx + 1) % 10 == 0 or idx + 1 == versioning_total:
                progress('versioning', versioning_total=versioning_total, versioning_current=idx + 1)

        # Final progress update — mark all barcodes as processed
        progress('versioning', versioning_total=versioning_total, versioning_current=versioning_total)

        # elapsed_seconds 从原始 started_at 计算（包含 counting 阶段的总耗时）
        # 注意：job['started_at'] 在 scanning 阶段被重置用于 ETA 计算，但 elapsed 使用原始值
        elapsed = round((datetime.datetime.now() - started_at).total_seconds())
        _safe_add_log('scan', 'success',
                      f"扫描完成: 新增 {total['added']}, 跳过 {total['skipped']}, 拒绝 {total['rejected']}",
                      json.dumps(total))

        with _scan_lock:
            job = _scan_jobs.get(job_id)
            if job:
                job['status'] = 'done'
                job['phase'] = 'done'
                job['percent'] = 100
                job['elapsed_seconds'] = elapsed
                job.update(total)

        # 记录最近扫描
        _record_scan(scan_mode, total, elapsed, started_at)

    except ScanCancelled:
        elapsed = round((datetime.datetime.now() - started_at).total_seconds())
        try:
            session.rollback()  # 仅回滚当前未提交事务，已通过分段 commit 保存的扫描结果不受影响
        except Exception:
            _log.exception('扫描取消时事务回滚失败: job_id=%s', job_id)
        with _scan_lock:
            job = _scan_jobs.get(job_id)
            if job:
                job['status'] = 'cancelled'
                job['phase'] = 'cancelled'
                job['elapsed_seconds'] = elapsed
        _safe_add_log('scan', 'info', f'扫描已取消（耗时 {elapsed}秒）', json.dumps({'elapsed_seconds': elapsed}))
    except Exception as e:
        try:
            elapsed = round((datetime.datetime.now() - started_at).total_seconds())
            tb = traceback.format_exc()
            with _scan_lock:
                job = _scan_jobs.get(job_id)
                if job:
                    job['status'] = 'error'
                    job['phase'] = 'error'
                    job['error'] = f"{e}\n{tb}"
                    job['elapsed_seconds'] = elapsed
            _safe_add_log('scan', 'error', f'扫描失败: {str(e)}')
        except Exception:
            # The worker must never leak an exception while trying to report
            # an earlier failure.  The in-memory job is best-effort updated.
            _log.exception('扫描错误状态收敛失败: job_id=%s', job_id)
    finally:
        # 清理取消标记
        try:
            with _scan_lock:
                _scan_cancel_flags.pop(job_id, None)
        finally:
            try:
                session.remove()
            except Exception:
                _log.exception('扫描线程清理 session 失败: job_id=%s', job_id)


def _cleanup_old_jobs():
    """Remove completed jobs older than 1 hour."""
    cutoff = datetime.datetime.now() - datetime.timedelta(hours=1)
    with _scan_lock:
        stale = [
            jid for jid, j in _scan_jobs.items()
            if j['status'] in ('done', 'error', 'cancelled')
            and datetime.datetime.fromisoformat(j['started_at']) < cutoff
        ]
        for jid in stale:
            del _scan_jobs[jid]


def _record_scan(scan_mode, totals, elapsed_seconds, started_at):
    """Record a completed scan to the recent scan history (max 10)."""
    record = {
        'started_at': started_at.isoformat(),
        'finished_at': datetime.datetime.now().isoformat(),
        'scan_mode': scan_mode,
        'added': totals.get('added', 0),
        'skipped': totals.get('skipped', 0),
        'rejected': totals.get('rejected', 0),
        'broken_cleaned': totals.get('broken_cleaned', 0),
        'elapsed_seconds': elapsed_seconds,
    }
    with _scan_lock:
        _recent_scans.insert(0, record)
        if len(_recent_scans) > 10:
            _recent_scans.pop()

_IN_CHUNK_SIZE = 500


def _scan_root_response(root):
    return {
        'id': root.id,
        'path': root.path,
        'recursive': root.recursive,
        'enabled': root.enabled,
        'allow_fuzzy': root.allow_fuzzy,
        'fuzzy_image_type': root.fuzzy_image_type,
    }


def _display_scan_root_path(path):
    """Normalize the stored display path without resolving symlinks."""
    return os.path.normpath(os.path.abspath(os.path.expanduser(path.strip())))


def _find_root_overlap(path_key, recursive, ignore_id=None):
    """Return a user-facing conflict message for duplicate/overlapping roots."""
    for existing in session.query(ScanRoot).all():
        if ignore_id is not None and existing.id == ignore_id:
            continue
        existing_key = existing.path_key
        if not existing_key:
            try:
                existing_key = normalize_scan_root_path(existing.path)
            except ValueError:
                _log.warning('跳过无法规范化的历史扫描目录: id=%s path=%r', existing.id, existing.path)
                continue
        if existing_key == path_key:
            return '扫描目录已存在（路径规范化后重复）'
        if existing.recursive and path_contains(existing_key, path_key):
            return '新目录位于已有递归扫描目录内，不能重复建立索引'
        if recursive and path_contains(path_key, existing_key):
            return '新递归目录包含已有扫描目录，不能重复建立索引'
    return None


def _validate_scan_root_data(data, existing=None):
    """Validate scan-root fields and return normalized values."""
    if not isinstance(data, dict):
        raise ValueError('请求体必须是 JSON 对象')

    if existing is None or 'path' in data:
        raw_path = data.get('path')
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError('path 必须为非空字符串')
        display_path = _display_scan_root_path(raw_path)
        path_key = normalize_scan_root_path(raw_path)
        if not os.path.isdir(display_path):
            raise ValueError('path does not exist')
    else:
        display_path = existing.path
        path_key = existing.path_key or normalize_scan_root_path(existing.path)

    values = {'path': display_path, 'path_key': path_key}
    defaults = {'recursive': True, 'enabled': True, 'allow_fuzzy': False,
                'fuzzy_image_type': 'main'}
    for field, default in defaults.items():
        if field in data:
            value = data[field]
            if field == 'fuzzy_image_type':
                if value not in ('main', 'detail'):
                    raise ValueError('fuzzy_image_type 必须为 main 或 detail')
            elif not isinstance(value, bool):
                raise ValueError(f'{field} 必须为布尔值')
            values[field] = value
        elif existing is None:
            values[field] = default
        else:
            values[field] = getattr(existing, field)
    return values


@scan_bp.route('/scan-roots', methods=['GET'])
def list_scan_roots():
    roots = session.query(ScanRoot).all()
    return jsonify([{
        'id': r.id, 'path': r.path, 'recursive': r.recursive, 'enabled': r.enabled,
        'allow_fuzzy': r.allow_fuzzy, 'fuzzy_image_type': r.fuzzy_image_type,
    } for r in roots])


@scan_bp.route('/scan-roots', methods=['POST'])
def add_scan_root():
    try:
        data = require_json_object()
        if 'path' not in data:
            return jsonify({'error': 'path is required'}), 400
        values = _validate_scan_root_data(data)
    except JSONPayloadError as e:
        return json_payload_error_response(e)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    with _scan_config_lock:
        conflict = _find_root_overlap(values['path_key'], values['recursive'])
        if conflict:
            return jsonify({'error': conflict}), 409
        root = ScanRoot(**values)
        session.add(root)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            return jsonify({'error': '扫描目录已存在（并发请求冲突）'}), 409
    _safe_add_log('add_root', 'success', f'已添加扫描目录: {root.path}')
    return jsonify(_scan_root_response(root)), 201


@scan_bp.route('/scan-roots/<int:root_id>', methods=['PUT'])
def update_scan_root(root_id):
    root = session.get(ScanRoot, root_id)
    if not root:
        return jsonify({'error': 'not found'}), 404
    try:
        data = require_json_object()
        values = _validate_scan_root_data(data, existing=root)
    except JSONPayloadError as e:
        return json_payload_error_response(e)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    enabled_changed = 'enabled' in data and values['enabled'] != root.enabled
    with _scan_config_lock:
        conflict = _find_root_overlap(
            values['path_key'], values['recursive'], ignore_id=root_id,
        )
        if conflict:
            return jsonify({'error': conflict}), 409
        for field, value in values.items():
            setattr(root, field, value)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            return jsonify({'error': '扫描目录已存在（并发请求冲突）'}), 409
    # Invalidate ScanRoot.enabled TTL cache so the change takes effect immediately
    from routes.images import _invalidate_root_cache
    _invalidate_root_cache(root_id)
    if enabled_changed:
        update_all_versions()
    return jsonify(_scan_root_response(root))


@scan_bp.route('/scan-roots/<int:root_id>', methods=['DELETE'])
def delete_scan_root(root_id):
    with _scan_config_lock:
        with _scan_lock:
            running = [
                jid for jid, job in _scan_jobs.items()
                if job.get('status') == 'running' and root_id in job.get('root_ids', [])
            ]
        if running:
            return jsonify({'error': '该扫描目录正在扫描中，暂不能删除', 'job_id': running[0]}), 409

        root = session.get(ScanRoot, root_id)
        if not root:
            return jsonify({'error': 'not found'}), 404

        # Collect affected barcodes BEFORE deleting images so we can rebuild versions
        affected_barcodes = {r[0] for r in session.query(Image.barcode).filter(
            Image.scan_root_id == root_id
        ).distinct().all()}

        # Collect unique (barcode, image_type, folder_ctime) for deleted_folders tracking
        deleted_folder_keys = {
            (r.barcode, r.image_type, r.folder_ctime)
            for r in session.query(Image.barcode, Image.image_type, Image.folder_ctime)
            .filter(Image.scan_root_id == root_id).distinct().all()
        }

        # Save path before deletion to avoid accessing expired ORM object
        root_path = root.path

        try:
            # Record deleted folders BEFORE deleting images (scoped to this root)
            from routes.batch import _record_deleted_folder
            for bc, it, ctime in deleted_folder_keys:
                _record_deleted_folder(session, bc, it, ctime, root_id)

            # Rejected metadata belongs to the root; remove it in the same
            # transaction, but never touch the physical rejected files.
            session.query(RejectedBarcode).filter(
                RejectedBarcode.scan_root_id == root_id
            ).delete(synchronize_session=False)
            session.query(Image).filter(Image.scan_root_id == root_id).delete(
                synchronize_session=False
            )
            session.delete(root)
            session.commit()
        except Exception:
            session.rollback()
            raise
    # Invalidate ScanRoot.enabled TTL cache — root is deleted, don't serve stale data
    from routes.images import _invalidate_root_cache
    _invalidate_root_cache(root_id)

    # Rebuild versions for affected barcodes to clean up orphan ImageVersion records
    for bc in affected_barcodes:
        update_versions_for_barcode(bc)

    _safe_add_log('delete_root', 'info', f'已删除扫描目录: {root_path}')
    return jsonify({'message': 'deleted'})


@scan_bp.route('/scan-roots/check-new', methods=['POST'])
def check_new_roots():
    """Check which root_ids have no images (never scanned)."""
    try:
        data = require_json_object()
    except JSONPayloadError as e:
        return json_payload_error_response(e)
    root_ids = data.get('root_ids', [])
    if not root_ids:
        return jsonify({'new_root_ids': []})
    scanned = session.query(Image.scan_root_id).filter(
        Image.scan_root_id.in_(root_ids)
    ).distinct().all()
    scanned_ids = {r[0] for r in scanned}
    new_ids = [rid for rid in root_ids if rid not in scanned_ids]
    return jsonify({'new_root_ids': new_ids})


@scan_bp.route('/scan', methods=['POST'])
def trigger_scan():
    try:
        data = require_json_object()
    except JSONPayloadError as e:
        return json_payload_error_response(e)
    root_ids = data.get('root_ids')
    scan_mode = data.get('scan_mode', 'full')

    # Validate root_ids
    if not isinstance(root_ids, list) or not root_ids:
        return jsonify({'error': 'root_ids 必须为非空数组'}), 400
    clean_ids = []
    seen = set()
    for rid in root_ids:
        if isinstance(rid, bool) or not isinstance(rid, int) or rid < 1:
            return jsonify({'error': f'root_ids 包含非法值: {rid}'}), 400
        if rid not in seen:
            seen.add(rid)
            clean_ids.append(rid)
    root_ids = clean_ids

    # Validate scan_mode
    if scan_mode not in ('full', 'incremental'):
        return jsonify({'error': 'scan_mode 必须为 full 或 incremental'}), 400

    roots = session.query(ScanRoot).filter(ScanRoot.id.in_(root_ids)).all()
    found_ids = {root.id for root in roots}
    missing_root_ids = [rid for rid in root_ids if rid not in found_ids]
    if missing_root_ids:
        return jsonify({
            'error': 'root_ids 中包含不存在的扫描目录',
            'missing_root_ids': missing_root_ids,
        }), 404
    roots_by_id = {root.id: root for root in roots}
    roots = [roots_by_id[rid] for rid in root_ids]

    # Atomically claim the single running-scan slot under the lock (no TOCTOU)
    _cleanup_old_jobs()
    job_id = str(uuid.uuid4())
    with _scan_lock:
        running = [jid for jid, j in _scan_jobs.items() if j['status'] == 'running']
        if running:
            return jsonify({'error': '已有扫描任务正在执行中，请等待完成'}), 409
        _scan_jobs[job_id] = _make_scan_job(root_ids)

    thread = threading.Thread(
        target=_run_scan,
        args=(root_ids, scan_mode, job_id),
        daemon=True,
    )
    try:
        thread.start()
    except Exception as e:
        with _scan_lock:
            job = _scan_jobs.get(job_id)
            if job:
                job.update({'status': 'error', 'phase': 'error', 'error': str(e)})
            _scan_cancel_flags.pop(job_id, None)
        _log.exception('扫描线程启动失败: job_id=%s', job_id)
        return jsonify({'error': '扫描任务启动失败'}), 500
    return jsonify({'job_id': job_id, 'status': 'started'}), 202


@scan_bp.route('/scan/status', methods=['GET'])
def get_active_scan():
    """返回当前正在运行的扫描任务（用于页面刷新后恢复进度）。"""
    _cleanup_old_jobs()
    with _scan_lock:
        running = [
            {'job_id': jid, **job}
            for jid, job in _scan_jobs.items()
            if job['status'] == 'running'
        ]
    if running:
        return jsonify(running[0])
    return jsonify(None)


@scan_bp.route('/scan/status/<job_id>', methods=['GET'])
def get_scan_status(job_id):
    _cleanup_old_jobs()
    with _scan_lock:
        job = _scan_jobs.get(job_id)
    if not job:
        return jsonify({'error': 'job not found'}), 404
    return jsonify(job)


@scan_bp.route('/scan/cancel/<job_id>', methods=['POST'])
def cancel_scan(job_id):
    """Request cancellation of a running scan."""
    with _scan_lock:
        job = _scan_jobs.get(job_id)
        if not job:
            return jsonify({'error': 'job not found'}), 404
        if job['status'] != 'running':
            return jsonify({'error': '只能取消正在运行的扫描'}), 409
        _scan_cancel_flags[job_id] = True
        job['cancel_requested'] = True
    return jsonify({'message': '取消请求已发送'})


@scan_bp.route('/scan/history', methods=['GET'])
def get_scan_history():
    """返回最近扫描记录（最多 10 条）。"""
    with _scan_lock:
        return jsonify(list(_recent_scans))


@scan_bp.route('/scan-logs', methods=['GET'])
def list_scan_logs():
    logs = session.query(ScanLog).order_by(ScanLog.created_at.desc()).limit(100).all()
    return jsonify([{
        'id': l.id, 'action': l.action, 'status': l.status,
        'message': l.message, 'details': l.details, 'created_at': l.created_at,
    } for l in logs])
