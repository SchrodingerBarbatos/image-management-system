# 导出详情Excel添加主图/详情图匹配Sheet 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在历史导出任务的详情Excel中新增两个sheet，分别显示主图匹配和详情图匹配信息

**Architecture:** 修改 `backend/routes/export.py` 的 `download_detail` 函数，在生成Excel时添加两个新的worksheet

**Tech Stack:** Python, Flask, openpyxl

---

## 文件结构

- Modify: `backend/routes/export.py:378-420` — `download_detail` 函数

## Task 1: 修改 download_detail 函数添加新sheet

**Files:**
- Modify: `backend/routes/export.py:378-420`

- [ ] **Step 1: 读取现有代码**

```python
# 现有代码在 backend/routes/export.py:378-420
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
    ws = wb.active
    ws.title = '导出详情'
    ws.append(['条码', '匹配主图数量', '匹配详情图数量'])

    for barcode, counts in barcode_counts.items():
        ws.append([barcode, counts.get('main', 0), counts.get('detail', 0)])

    # Auto-fit column widths
    for col_idx, _ in enumerate(ws[1], start=1):
        max_width = 0
        for row in ws.iter_rows(min_col=col_idx, max_col=col_idx):
            for cell in row:
                if cell.value:
                    # Estimate width: CJK chars ~2, ASCII ~1
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
```

- [ ] **Step 2: 修改函数添加两个新sheet**

将 `download_detail` 函数修改为：

```python
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
```

- [ ] **Step 3: 运行测试验证修改**

```bash
cd D:\图片库系统
python -m pytest backend/tests/test_batch_tasks.py -v -k "export"
```

Expected: 所有export相关测试通过

- [ ] **Step 4: 提交代码**

```bash
git add backend/routes/export.py
git commit -m "feat: add main/detail match sheets to export detail Excel"
```
