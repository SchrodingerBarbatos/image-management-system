import os, json, uuid, threading, datetime, traceback
from flask import Blueprint, request, jsonify
from models import session, ScanRoot, Image, ScanLog
from scanner import scan_root
from versioning import update_versions_for_barcode, update_all_versions

scan_bp = Blueprint('scan', __name__)

_scan_lock = threading.Lock()
_scan_jobs = {}

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
            'phase': 'starting',
            'current_root_path': '',
            'current_root_index': 0,
            'total_roots': len(root_ids),
            'current_file': '',
            'added': 0, 'skipped': 0, 'broken_cleaned': 0, 'broken_new': 0,
            'thumbnail_total': 0, 'thumbnail_current': 0,
            'error': None,
            'started_at': datetime.datetime.now().isoformat(),
        }

    if job_ready_event:
        job_ready_event.set()

    def progress(phase, **kw):
        with _scan_lock:
            job = _scan_jobs.get(job_id)
            if job:
                job['phase'] = phase
                job.update(kw)

    _add_log('scan', 'info',
        f"扫描开始 - {'全量' if full_scan else '增量'}模式",
        json.dumps({'job_id': job_id, 'root_ids': root_ids}))

    try:
        total = {'added': 0, 'skipped': 0, 'broken_cleaned': 0}
        all_affected = set()
        roots = session.query(ScanRoot).filter(ScanRoot.id.in_(root_ids)).all()

        for i, r in enumerate(roots):
            with _scan_lock:
                job = _scan_jobs.get(job_id)
                if job:
                    job['current_root_index'] = i + 1
                    job['current_root_path'] = r.path

            res = scan_root(r.id, full_scan=full_scan, progress_callback=progress)
            for k in total:
                total[k] += res.get(k, 0)
            all_affected.update(res.get('affected_barcodes', []))

        progress('versioning', versioning_total=len(all_affected))
        for idx, bc in enumerate(sorted(all_affected)):
            update_versions_for_barcode(bc)
            if (idx + 1) % 50 == 0:
                progress('versioning', versioning_total=len(all_affected), versioning_current=idx + 1)

        _add_log('scan', 'success',
            f"扫描完成: 新增 {total['added']}, 跳过 {total['skipped']}",
            json.dumps(total))

        with _scan_lock:
            job = _scan_jobs.get(job_id)
            if job:
                job['status'] = 'done'
                job['phase'] = 'done'
                job.update(total)

    except Exception as e:
        tb = traceback.format_exc()
        with _scan_lock:
            job = _scan_jobs.get(job_id)
            if job:
                job['status'] = 'error'
                job['phase'] = 'error'
                job['error'] = f"{e}\n{tb}"
        try:
            _add_log('scan', 'error', f'扫描失败: {str(e)}')
        except Exception:
            pass
    finally:
        session.remove()


def _cleanup_old_jobs():
    """Remove completed jobs older than 1 hour."""
    cutoff = datetime.datetime.now() - datetime.timedelta(hours=1)
    with _scan_lock:
        stale = [
            jid for jid, j in _scan_jobs.items()
            if j['status'] in ('done', 'error')
            and datetime.datetime.fromisoformat(j['started_at']) < cutoff
        ]
        for jid in stale:
            del _scan_jobs[jid]


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
    session.query(Image).filter(Image.scan_root_id == root_id).delete()
    session.delete(root)
    session.commit()
    _add_log('delete_root', 'info', f'已删除扫描目录: {root.path}')
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


@scan_bp.route('/scan-logs', methods=['GET'])
def list_scan_logs():
    logs = session.query(ScanLog).order_by(ScanLog.created_at.desc()).limit(100).all()
    return jsonify([{
        'id': l.id, 'action': l.action, 'status': l.status,
        'message': l.message, 'details': l.details, 'created_at': l.created_at,
    } for l in logs])
