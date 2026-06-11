import os, json, uuid, threading, datetime, traceback
from flask import Blueprint, request, jsonify
from models import session, ScanRoot, Image, ScanLog, ImageVersion
from scanner import scan_root, count_image_files, ScanCancelled
from versioning import update_versions_for_barcode, update_all_versions

scan_bp = Blueprint('scan', __name__)

_scan_lock = threading.Lock()
_scan_jobs = {}
_scan_cancel_flags = {}  # job_id -> True when cancel requested
_recent_scans = []  # 最近扫描记录（内存，最多 10 条）

def _add_log(action, status, message, details=''):
    log = ScanLog(action=action, status=status, message=message, details=details)
    session.add(log)
    session.commit()


def _run_scan(root_ids, scan_mode, job_ready_event=None):
    """Execute scan in background thread with progress reporting."""
    job_id = str(uuid.uuid4())
    full_scan = scan_mode == 'full'

    with _scan_lock:
        _scan_jobs[job_id] = {
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

    if job_ready_event:
        job_ready_event.set()

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

    _add_log('scan', 'info',
        f"扫描开始 - {'全量' if full_scan else '增量'}模式",
        json.dumps({'job_id': job_id, 'root_ids': root_ids}))

    started_at = datetime.datetime.now()

    try:
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
        _add_log('scan', 'success',
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
        session.rollback()  # 仅回滚当前未提交事务，已通过分段 commit 保存的扫描结果不受影响
        with _scan_lock:
            job = _scan_jobs.get(job_id)
            if job:
                job['status'] = 'cancelled'
                job['phase'] = 'cancelled'
                job['elapsed_seconds'] = elapsed
        _add_log('scan', 'info', f'扫描已取消（耗时 {elapsed}秒）', json.dumps({'elapsed_seconds': elapsed}))
    except Exception as e:
        elapsed = round((datetime.datetime.now() - started_at).total_seconds())
        tb = traceback.format_exc()
        with _scan_lock:
            job = _scan_jobs.get(job_id)
            if job:
                job['status'] = 'error'
                job['phase'] = 'error'
                job['error'] = f"{e}\n{tb}"
                job['elapsed_seconds'] = elapsed
        try:
            _add_log('scan', 'error', f'扫描失败: {str(e)}')
        except Exception:
            pass
    finally:
        # 清理取消标记
        with _scan_lock:
            _scan_cancel_flags.pop(job_id, None)
        session.remove()


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


@scan_bp.route('/scan-roots', methods=['GET'])
def list_scan_roots():
    roots = session.query(ScanRoot).all()
    return jsonify([{
        'id': r.id, 'path': r.path, 'recursive': r.recursive, 'enabled': r.enabled,
        'allow_fuzzy': r.allow_fuzzy, 'fuzzy_image_type': r.fuzzy_image_type,
    } for r in roots])


@scan_bp.route('/scan-roots', methods=['POST'])
def add_scan_root():
    data = request.json
    if not data or 'path' not in data:
        return jsonify({'error': 'path is required'}), 400
    if not os.path.isdir(data['path']):
        return jsonify({'error': 'path does not exist'}), 400
    root = ScanRoot(
        path=data['path'],
        recursive=data.get('recursive', True),
        enabled=True,
        allow_fuzzy=data.get('allow_fuzzy', False),
        fuzzy_image_type=data.get('fuzzy_image_type', 'main'),
    )
    session.add(root)
    session.commit()
    _add_log('add_root', 'success', f'已添加扫描目录: {root.path}')
    return jsonify({
        'id': root.id, 'path': root.path,
        'recursive': root.recursive, 'enabled': root.enabled,
        'allow_fuzzy': root.allow_fuzzy, 'fuzzy_image_type': root.fuzzy_image_type,
    }), 201


@scan_bp.route('/scan-roots/<int:root_id>', methods=['PUT'])
def update_scan_root(root_id):
    root = session.get(ScanRoot, root_id)
    if not root:
        return jsonify({'error': 'not found'}), 404
    data = request.json
    if 'recursive' in data:
        root.recursive = data['recursive']
    if 'enabled' in data:
        root.enabled = data['enabled']
        enabled_changed = True
    else:
        enabled_changed = False
    if 'allow_fuzzy' in data:
        root.allow_fuzzy = data['allow_fuzzy']
    if 'fuzzy_image_type' in data:
        root.fuzzy_image_type = data['fuzzy_image_type']
    session.commit()
    # Invalidate ScanRoot.enabled TTL cache so the change takes effect immediately
    from routes.images import _invalidate_root_cache
    _invalidate_root_cache(root_id)
    if enabled_changed:
        update_all_versions()
    return jsonify({
        'id': root.id, 'path': root.path,
        'recursive': root.recursive, 'enabled': root.enabled,
        'allow_fuzzy': root.allow_fuzzy, 'fuzzy_image_type': root.fuzzy_image_type,
    })


@scan_bp.route('/scan-roots/<int:root_id>', methods=['DELETE'])
def delete_scan_root(root_id):
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

    # Record deleted folders BEFORE deleting images
    from routes.batch import _record_deleted_folder
    for bc, it, ctime in deleted_folder_keys:
        _record_deleted_folder(session, bc, it, ctime)

    session.query(Image).filter(Image.scan_root_id == root_id).delete()
    session.delete(root)
    session.commit()
    # Invalidate ScanRoot.enabled TTL cache — root is deleted, don't serve stale data
    from routes.images import _invalidate_root_cache
    _invalidate_root_cache(root_id)

    # Rebuild versions for affected barcodes to clean up orphan ImageVersion records
    for bc in affected_barcodes:
        update_versions_for_barcode(bc)

    _add_log('delete_root', 'info', f'已删除扫描目录: {root_path}')
    return jsonify({'message': 'deleted'})


@scan_bp.route('/scan-roots/check-new', methods=['POST'])
def check_new_roots():
    """Check which root_ids have no images (never scanned)."""
    data = request.get_json(silent=True) or {}
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
    data = request.get_json(silent=True) or {}
    root_ids = data.get('root_ids')
    scan_mode = data.get('scan_mode', 'full')

    if not root_ids:
        return jsonify({'error': '请指定要扫描的目录'}), 400

    roots = session.query(ScanRoot).filter(ScanRoot.id.in_(root_ids)).all()
    if not roots:
        return jsonify({'error': '没有可扫描的目录'}), 400

    # Check for existing running scan
    _cleanup_old_jobs()
    with _scan_lock:
        running = [jid for jid, j in _scan_jobs.items() if j['status'] == 'running']
    if running:
        return jsonify({'error': '已有扫描任务正在执行中，请等待完成'}), 409

    # Launch background scan
    job_ready = threading.Event()
    thread = threading.Thread(
        target=_run_scan,
        args=(root_ids, scan_mode, job_ready),
        daemon=True,
    )
    thread.start()

    # Wait for job entry to be created
    if not job_ready.wait(timeout=5.0):
        return jsonify({'error': '扫描启动超时'}), 500

    with _scan_lock:
        active_jobs = [jid for jid, j in _scan_jobs.items() if j['status'] == 'running']

    if active_jobs:
        job_id = active_jobs[0]
        return jsonify({'job_id': job_id, 'status': 'started'}), 202
    return jsonify({'error': '扫描启动失败'}), 500


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
