import os, uuid, zipfile, datetime
from flask import Blueprint, request, jsonify, send_file
from openpyxl import load_workbook
from models import session, Image, ExportTask
from config import UPLOAD_DIR, ZIP_CLEANUP_HOURS

export_bp = Blueprint('export', __name__)

def _col_letter(idx):
    """Convert 0-based column index to Excel column letter(s). 0->A, 25->Z, 26->AA."""
    result = ''
    while idx >= 0:
        result = chr(65 + (idx % 26)) + result
        idx = idx // 26 - 1
    return result

@export_bp.route('/export/excel', methods=['POST'])
def upload_excel():
    file = request.files.get('file')
    if not file:
        return jsonify({'error': 'file required'}), 400
    upload_id = uuid.uuid4().hex[:12]
    upload_path = os.path.join(UPLOAD_DIR, f'{upload_id}.xlsx')
    file.save(upload_path)
    wb = load_workbook(upload_path, read_only=True)
    ws = wb.active
    headers = [str(cell.value) for cell in next(ws.iter_rows(min_row=1, max_row=1))]
    wb.close()
    column_names = [f'{_col_letter(i)}-{h}' for i, h in enumerate(headers)]
    return jsonify({'columns': column_names, 'upload_id': upload_id})

@export_bp.route('/export/zip', methods=['POST'])
def generate_zip():
    data = request.json
    barcode_col = data.get('barcode_column', '')
    image_type = data.get('image_type', '')
    upload_id = data.get('upload_id', '')
    selected = data.get('selected_barcodes')

    if not upload_id:
        return jsonify({'error': 'upload_id is required'}), 400

    # Parse barcode column letter
    col_letter = barcode_col.split('-')[0] if '-' in barcode_col else 'A'
    col_idx = ord(col_letter.upper()) - ord('A')

    # Read barcodes from Excel
    upload_path = os.path.join(UPLOAD_DIR, f'{upload_id}.xlsx')
    wb = load_workbook(upload_path, read_only=True)
    ws = wb.active
    barcodes = []
    for row in ws.iter_rows(min_row=2):
        val = str(row[col_idx].value).strip() if row[col_idx].value else ''
        if val:
            barcodes.append(val)
    wb.close()

    if selected:
        barcodes = [b for b in barcodes if b in selected]

    # Find matching images
    q = session.query(Image).filter(Image.barcode.in_(barcodes), Image.confirmed == True)
    if image_type and image_type != 'all':
        q = q.filter(Image.image_type == image_type)
    imgs = q.all()

    task = ExportTask(status='processing')
    session.add(task)
    session.commit()

    zip_name = f'export_{task.id}.zip'
    zip_path = os.path.join(UPLOAD_DIR, zip_name)
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for img in imgs:
            if os.path.exists(img.file_path):
                type_folder = '主图' if img.image_type == 'main' else '详情图'
                zf.write(img.file_path, f"{type_folder}/{img.filename}")

    task.status = 'done'
    task.zip_path = zip_path
    session.commit()
    return jsonify({'task_id': task.id})

@export_bp.route('/export/download/<int:task_id>')
def download_zip(task_id):
    task = session.get(ExportTask, task_id)
    if not task or task.status != 'done':
        return jsonify({'error': 'not ready'}), 404
    return send_file(task.zip_path, as_attachment=True, download_name=f'export_{task_id}.zip')

def cleanup_old_exports():
    """Remove export tasks and their files older than ZIP_CLEANUP_HOURS."""
    cutoff = datetime.datetime.now() - datetime.timedelta(hours=ZIP_CLEANUP_HOURS)
    old_tasks = session.query(ExportTask).filter(
        ExportTask.created_at < cutoff.isoformat()
    ).all()
    for task in old_tasks:
        if task.zip_path and os.path.exists(task.zip_path):
            os.remove(task.zip_path)
        session.delete(task)
    session.commit()
