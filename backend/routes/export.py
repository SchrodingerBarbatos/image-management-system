import os, sys, uuid, zipfile, datetime, threading, logging, traceback, re, json, io
from flask import Blueprint, request, jsonify, send_file
from openpyxl import load_workbook, Workbook
from models import session, Image, ExportTask, ScanRoot, BarcodeSetting, ImageVersion
from config import UPLOAD_DIR, ZIP_CLEANUP_HOURS

export_bp = Blueprint('export', __name__)
_log = logging.getLogger(__name__)

_export_lock = threading.Lock()


def filter_to_single_version(imgs, barcodes, session):
    """Return subset of imgs containing only one version per (barcode, image_type).

    Priority chain (first match wins):
    1. BarcodeSetting.default_{main,detail}_ctime — user-chosen default per barcode
    2. ImageVersion.is_latest — most recent version detected by scanner
    3. Pass-through — if no version data exists for a barcode, keep all its images

    Relies on ImageVersion and BarcodeSetting tables for version resolution.
    When a barcode has neither, images are kept as-is (fallback to "all images").
    """
    settings = session.query(
        BarcodeSetting.barcode, BarcodeSetting.default_main_ctime, BarcodeSetting.default_detail_ctime
    ).filter(
        BarcodeSetting.barcode.in_(barcodes)
    ).all()
    latest_versions = session.query(
        ImageVersion.barcode, ImageVersion.image_type, ImageVersion.folder_ctime
    ).filter(
        ImageVersion.barcode.in_(barcodes),
        ImageVersion.is_latest == True,
    ).all()
    allowed = {}  # {barcode: {image_type: folder_ctime}}
    for v in latest_versions:
        allowed.setdefault(v.barcode, {})[v.image_type] = v.folder_ctime
    for s in settings:
        if s.default_main_ctime:
            allowed.setdefault(s.barcode, {})['main'] = s.default_main_ctime
        if s.default_detail_ctime:
            allowed.setdefault(s.barcode, {})['detail'] = s.default_detail_ctime
    filtered = []
    for img in imgs:
        type_map = allowed.get(img.barcode)
        if type_map:
            allowed_ctime = type_map.get(img.image_type)
            if allowed_ctime and img.folder_ctime != allowed_ctime:
                continue
        filtered.append(img)
    return filtered

def _compute_barcode_counts(imgs, all_barcodes=None):
    """Compute per-barcode main/detail match counts.

    If all_barcodes is provided, unmatched barcodes are included with zero counts.
    Otherwise, only barcodes that appear in imgs are included.
    """
    counts = {}
    if all_barcodes:
        for b in all_barcodes:
            counts[b] = {'main': 0, 'detail': 0}
    for img in imgs:
        counts.setdefault(img.barcode, {'main': 0, 'detail': 0})
        if img.image_type in ('main', 'detail'):
            counts[img.barcode][img.image_type] += 1
    return counts

def _col_letter(idx):
    """Convert 0-based column index to Excel column letter(s). 0->A, 25->Z, 26->AA."""
    result = ''
    while idx >= 0:
        result = chr(65 + (idx % 26)) + result
        idx = idx // 26 - 1
    return result


def _col_letter_to_idx(s):
    """Convert Excel column letter(s) to 0-based index. A->0, Z->25, AA->26."""
    s = s.strip().upper()
    if not s or not s.isalpha():
        raise ValueError('invalid column letter')
    idx = 0
    for c in s:
        idx = idx * 26 + (ord(c) - ord('A') + 1)
    return idx - 1

def _build_zip(task_id, img_data, flat):
    """Build ZIP file in a background thread, updating task progress.

    img_data: list of (file_path, barcode, image_type, sequence, ext) tuples — plain
    data so the thread doesn't need access to the request-scoped session.
    """
    from models import session as sess, ExportTask
    try:
        task = sess.get(ExportTask, task_id)
        if not task:
            _log.warning("_build_zip: task %s not found (may have been deleted)", task_id)
            return
        total = len(img_data)
        if total == 0:
            task.status = 'done'
            task.total_images = 0
            task.progress = 0
            zip_path = os.path.join(UPLOAD_DIR, f'export_{task_id}.zip')
            task.zip_path = zip_path
            # Create empty zip
            with zipfile.ZipFile(zip_path, 'w'):
                pass
            sess.commit()
            return
        task.total_images = total
        task.progress = 0
        sess.commit()

        zip_name = f'export_{task_id}.zip'
        zip_path = os.path.join(UPLOAD_DIR, zip_name)
        task.zip_path = zip_path
        sess.commit()

        written = 0
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_STORED) as zf:  # no compression — JPEG/PNG are already compressed
            for i, (file_path, barcode, image_type, sequence, ext) in enumerate(img_data):
                if os.path.exists(file_path):
                    safe_type = image_type if image_type in ('main', 'detail') else 'detail'
                    if safe_type != image_type:
                        _log.warning("_build_zip: unexpected image_type %r for barcode %s, defaulting to 'detail'", image_type, barcode)
                    # Main: barcode_seq.ext  Detail: barcode_详情图_seq.ext
                    if safe_type == 'main':
                        display_name = f"{barcode}_{sequence}.{ext}"
                    else:
                        display_name = f"{barcode}_详情图_{sequence}.{ext}"
                    if flat:
                        arcname = display_name
                    else:
                        type_folder = '主图' if safe_type == 'main' else '详情图'
                        arcname = f"{type_folder}/{display_name}"
                    zf.write(file_path, arcname)
                    written += 1
                task.progress = i + 1
                if (i + 1) % 100 == 0:
                    sess.commit()

        if written == 0:
            _log.warning("_build_zip task %s: all %d files missing from disk", task_id, total)
            task.status = 'failed'
            task.error_message = '所有匹配的图片文件均不存在（可能已被移动或删除）'
        else:
            task.status = 'done'
        sess.commit()
    except Exception as e:
        _log.error("_build_zip task %s failed:\n%s", task_id, traceback.format_exc())
        try:
            task = sess.get(ExportTask, task_id)
            if task:
                task.status = 'failed'
                if getattr(sys, 'frozen', False):
                    task.error_message = f"导出失败: {e}"
                else:
                    task.error_message = traceback.format_exc()
                sess.commit()
        except Exception:
            pass
    finally:
        sess.remove()


@export_bp.route('/export/excel', methods=['POST'])
def upload_excel():
    file = request.files.get('file')
    if not file:
        return jsonify({'error': 'file required'}), 400

    # Validate extension
    if not file.filename or not file.filename.lower().endswith('.xlsx'):
        return jsonify({'error': '只允许 .xlsx 文件'}), 400

    # Size limit: 10 MB
    file.seek(0, 2)
    size = file.tell()
    file.seek(0)
    if size > 10 * 1024 * 1024:
        return jsonify({'error': '文件大小不能超过 10MB'}), 400

    upload_id = uuid.uuid4().hex[:12]
    upload_path = os.path.join(UPLOAD_DIR, f'{upload_id}.xlsx')
    file.save(upload_path)

    try:
        wb = load_workbook(upload_path, read_only=True)
    except Exception:
        return jsonify({'error': '无法解析 Excel 文件，请确认文件格式正确'}), 400

    sheet_names = wb.sheetnames
    if not sheet_names:
        wb.close()
        return jsonify({'error': 'Excel 文件中没有工作表'}), 400

    sheet_columns = {}
    for sname in sheet_names:
        ws = wb[sname]
        row = next(ws.iter_rows(min_row=1, max_row=1), None)
        if row is None:
            sheet_columns[sname] = []
        else:
            headers = [str(cell.value) for cell in row]
            if not headers or all(h == 'None' for h in headers):
                sheet_columns[sname] = []
            else:
                sheet_columns[sname] = [f'{_col_letter(i)}-{h}' for i, h in enumerate(headers)]
    wb.close()

    if not sheet_columns or all(v == [] for v in sheet_columns.values()):
        return jsonify({'error': '所有工作表的表头均为空，请检查 Excel 文件'}), 400

    first_sheet = sheet_names[0] if sheet_names else ''
    return jsonify({
        'columns': sheet_columns.get(first_sheet, []),
        'sheets': sheet_names,
        'sheet_columns': sheet_columns,
        'upload_id': upload_id,
    })


@export_bp.route('/export/zip', methods=['POST'])
def generate_zip():
    data = request.json
    barcode_col = data.get('barcode_column', '')
    image_type = data.get('image_type', '')
    upload_id = data.get('upload_id', '')
    sheet_name = data.get('sheet_name', '')
    selected = data.get('selected_barcodes')
    flat = data.get('flat', False)

    if not upload_id:
        return jsonify({'error': 'upload_id is required'}), 400

    # Validate upload_id format
    if not re.match(r'^[0-9a-f]{12}$', upload_id):
        return jsonify({'error': 'invalid upload_id'}), 400

    # Parse barcode column letter
    col_letter = barcode_col.split('-')[0] if '-' in barcode_col else 'A'
    try:
        col_idx = _col_letter_to_idx(col_letter)
    except ValueError:
        return jsonify({'error': f'invalid column letter: {col_letter}'}), 400

    # Read barcodes from Excel
    upload_path = os.path.join(UPLOAD_DIR, f'{upload_id}.xlsx')
    # Resolve real path and ensure it stays within UPLOAD_DIR
    real_upload = os.path.realpath(upload_path)
    real_upload_dir = os.path.realpath(UPLOAD_DIR)
    if os.path.commonpath([real_upload, real_upload_dir]) != real_upload_dir:
        return jsonify({'error': 'invalid upload_id'}), 400
    if not os.path.isfile(real_upload):
        return jsonify({'error': 'upload file not found'}), 404

    try:
        wb = load_workbook(real_upload, read_only=True)
    except Exception:
        return jsonify({'error': '无法解析 Excel 文件'}), 400

    if sheet_name and sheet_name not in wb.sheetnames:
        wb.close()
        return jsonify({'error': f'工作表 "{sheet_name}" 不存在'}), 400

    ws = wb[sheet_name] if sheet_name and sheet_name in wb.sheetnames else wb.active
    # Get header row to validate column index
    header_row = next(ws.iter_rows(min_row=1, max_row=1))
    if col_idx >= len(header_row):
        wb.close()
        return jsonify({'error': f'列索引 {col_letter}({col_idx}) 超出表头范围(共 {len(header_row)} 列)'}), 400

    barcodes = []
    for row in ws.iter_rows(min_row=2):
        if col_idx >= len(row):
            continue
        val = str(row[col_idx].value).strip() if row[col_idx].value else ''
        if val:
            barcodes.append(val)
    wb.close()

    if selected:
        barcodes = [b for b in barcodes if b in selected]

    if not barcodes:
        return jsonify({'error': 'Excel 中未找到任何条码数据'}), 400

    # Find matching images
    q = session.query(Image).filter(Image.barcode.in_(barcodes), Image.confirmed == True).join(
        ScanRoot, Image.scan_root_id == ScanRoot.id
    ).filter(ScanRoot.enabled == True)
    if image_type and image_type != 'all':
        q = q.filter(Image.image_type == image_type)
    imgs = q.all()

    # Filter to single version: user-chosen default, or latest version as fallback
    imgs = filter_to_single_version(imgs, barcodes, session)
    matched_barcodes = set(img.barcode for img in imgs)
    excluded_barcodes = len(barcodes) - len(matched_barcodes)

    # Compute per-barcode match counts (include all barcodes, even unmatched)
    barcode_counts = _compute_barcode_counts(imgs, barcodes)

    # Concurrency guard: check + create + commit must be atomic
    with _export_lock:
        running = session.query(ExportTask).filter(ExportTask.status == 'processing').first()
        if running:
            return jsonify({'error': '已有导出任务正在执行中，请等待完成'}), 409

        task = ExportTask(status='processing', barcode_data=json.dumps(barcode_counts, ensure_ascii=False))
        session.add(task)
        session.commit()

    img_data = [(img.file_path, img.barcode, img.image_type, img.sequence, img.ext) for img in imgs]
    threading.Thread(target=_build_zip, args=(task.id, img_data, flat), daemon=True).start()

    return jsonify({'task_id': task.id, 'total_images': len(imgs), 'total_barcodes': len(matched_barcodes), 'excluded_barcodes': excluded_barcodes})


@export_bp.route('/export/progress/<int:task_id>')
def export_progress(task_id):
    task = session.get(ExportTask, task_id)
    if not task:
        return jsonify({'error': 'not found'}), 404
    return jsonify({'status': task.status, 'progress': task.progress, 'total': task.total_images, 'error_message': task.error_message})


@export_bp.route('/export/tasks')
def list_tasks():
    tasks = session.query(ExportTask).order_by(ExportTask.id.desc()).limit(30).all()
    result = []
    for t in tasks:
        file_available = bool(t.zip_path and os.path.exists(t.zip_path))
        result.append({
            'id': t.id, 'status': t.status, 'total_images': t.total_images,
            'created_at': t.created_at, 'file_available': file_available,
            'error_message': t.error_message,
            'has_detail': bool(t.barcode_data),
        })
    return jsonify(result)


@export_bp.route('/export/tasks/<int:task_id>', methods=['DELETE'])
def delete_task(task_id):
    task = session.get(ExportTask, task_id)
    if not task:
        return jsonify({'error': 'not found'}), 404
    if request.args.get('force') != 'true' and task.status == 'processing':
        return jsonify({'error': 'cannot delete processing task'}), 409
    if task.zip_path and os.path.exists(task.zip_path):
        try:
            os.remove(task.zip_path)
        except OSError:
            pass
    session.delete(task)
    session.commit()
    return jsonify({'ok': True})


@export_bp.route('/export/download/<int:task_id>')
def download_zip(task_id):
    task = session.get(ExportTask, task_id)
    if not task or task.status != 'done':
        return jsonify({'error': 'not ready'}), 404
    if not os.path.exists(task.zip_path):
        return jsonify({'error': 'file has been cleaned up'}), 404
    return send_file(
        task.zip_path,
        as_attachment=True,
        download_name=f'export_{task_id}.zip',
        mimetype='application/zip',
        conditional=True,
    )


@export_bp.route('/export/tasks/<int:task_id>/detail')
def download_detail(task_id):
    task = session.get(ExportTask, task_id)
    if not task:
        return jsonify({'error': 'not found'}), 404
    if not task.barcode_data:
        return jsonify({'error': 'no barcode data available'}), 404

    try:
        barcode_counts = json.loads(task.barcode_data)
    except (json.JSONDecodeError, TypeError):
        return jsonify({'error': 'barcode data is corrupted'}), 500

    wb = Workbook()

    # Sheet 1: 导出详情（保留原有）
    ws_all = wb.active
    ws_all.title = '导出详情'
    ws_all.append(['条码', '匹配主图数量', '匹配详情图数量'])
    for barcode, counts in barcode_counts.items():
        ws_all.append([barcode, counts.get('main', 0), counts.get('detail', 0)])

    # Sheet 2: 主图匹配（新增）
    ws_main = wb.create_sheet('主图匹配')
    ws_main.append(['条码', '主图数量'])
    for barcode, counts in barcode_counts.items():
        ws_main.append([barcode, counts.get('main', 0)])

    # Sheet 3: 详情图匹配（新增）
    ws_detail = wb.create_sheet('详情图匹配')
    ws_detail.append(['条码', '详情图数量'])
    for barcode, counts in barcode_counts.items():
        ws_detail.append([barcode, counts.get('detail', 0)])

    # Auto-fit column widths for all sheets
    for ws in [ws_all, ws_main, ws_detail]:
        for col_idx, _ in enumerate(ws[1], start=1):
            max_width = 0
            for row in ws.iter_rows(min_col=col_idx, max_col=col_idx):
                for cell in row:
                    if cell.value:
                        val = str(cell.value)
                        width = sum(2 if ord(c) > 127 else 1 for c in val)
                        max_width = max(max_width, width)
            ws.column_dimensions[ws.cell(row=1, column=col_idx).column_letter].width = min(max_width + 4, 60)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    return send_file(
        output,
        as_attachment=True,
        download_name=f'export_detail_{task_id}.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )


def reset_stale_processing():
    """Startup helper — mark ALL processing export tasks as failed
    so they don't permanently block new exports."""
    with _export_lock:
        stale = session.query(ExportTask).filter(
            ExportTask.status == 'processing',
        ).all()
        if stale:
            for task in stale:
                task.status = 'failed'
                task.error_message = '程序重启或导出进程异常中断'
            session.commit()
            _log.info("Reset %d stale processing export tasks to failed", len(stale))


def cleanup_old_exports():
    """Remove export tasks and their files older than ZIP_CLEANUP_HOURS.
    Processing tasks are skipped (they should already have been handled by
    reset_stale_processing)."""
    with _export_lock:
        cutoff = datetime.datetime.now() - datetime.timedelta(hours=ZIP_CLEANUP_HOURS)
        old_tasks = session.query(ExportTask).filter(
            ExportTask.created_at < cutoff.isoformat(),
            ExportTask.status != 'processing',
        ).all()
        for task in old_tasks:
            if task.zip_path and os.path.exists(task.zip_path):
                try:
                    os.remove(task.zip_path)
                except OSError:
                    pass
            session.delete(task)
        if old_tasks:
            session.commit()
            _log.info("Cleaned up %d old export tasks", len(old_tasks))

