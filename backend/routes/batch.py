import json, os, re
from collections import defaultdict
from flask import Blueprint, request, jsonify
from models import session, Image, ImageVersion, ScanRoot
from versioning import update_versions_for_barcode

batch_bp = Blueprint('batch', __name__)

_ISO_RE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?$')


def _delete_folder_images(barcode, image_type, folder_ctime, delete_files):
    """删除指定条码+类型+文件夹下 active+confirmed+enabled 的图片，返回删除数量。
    过滤条件与扫描端点一致，确保预览和实际操作匹配。"""
    imgs = session.query(Image).filter(
        Image.barcode == barcode,
        Image.image_type == image_type,
        Image.folder_ctime == folder_ctime,
        Image.status == 'active',
        Image.confirmed == True,
    ).join(ScanRoot, Image.scan_root_id == ScanRoot.id).filter(
        ScanRoot.enabled == True,
    ).all()
    count = 0
    for img in imgs:
        if delete_files:
            try:
                os.remove(img.file_path)
            except OSError:
                pass
        session.delete(img)
        count += 1
    return count


def _check_disabled_scan_roots(items):
    """检查是否有图片属于禁用的扫描目录，返回禁用目录中的图片数量。"""
    if not items:
        return 0
    # Build OR conditions for (barcode, image_type, folder_ctime)
    from sqlalchemy import or_
    conditions = []
    for item in items:
        conditions.append(
            (Image.barcode == item['barcode']) &
            (Image.image_type == item['image_type']) &
            (Image.folder_ctime == item['folder_ctime'])
        )
    if not conditions:
        return 0
    disabled_count = session.query(Image.id).join(
        ScanRoot, Image.scan_root_id == ScanRoot.id
    ).filter(
        ScanRoot.enabled == False,
        or_(*conditions),
    ).count()
    return disabled_count


def _compute_folder_stats():
    """Return {(barcode, image_type, folder_ctime): (count, total_size)}
    for all active+confirmed images in enabled scan roots."""
    from sqlalchemy import func
    rows = session.query(
        Image.barcode, Image.image_type, Image.folder_ctime,
        func.count(Image.id).label('cnt'),
        func.sum(Image.file_size).label('total_sz'),
    ).filter(
        Image.status == 'active',
        Image.confirmed == True,
    ).join(ScanRoot, Image.scan_root_id == ScanRoot.id).filter(
        ScanRoot.enabled == True,
    ).group_by(Image.barcode, Image.image_type, Image.folder_ctime).all()
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

    groups = []
    seen = set()

    for v in versions:
        try:
            dup_ctimes = json.loads(v.duplicate_mtimes)
        except (json.JSONDecodeError, TypeError):
            continue
        if not dup_ctimes:
            continue
        for dup_ctime in dup_ctimes:
            key = (v.barcode, v.image_type, dup_ctime)
            if key in seen:
                continue
            seen.add(key)
            imgs = session.query(Image).filter(
                Image.barcode == v.barcode,
                Image.image_type == v.image_type,
                Image.folder_ctime == dup_ctime,
                Image.status == 'active',
                Image.confirmed == True,
            ).join(ScanRoot, Image.scan_root_id == ScanRoot.id).filter(
                ScanRoot.enabled == True,
            ).all()
            if imgs:
                groups.append({
                    'barcode': v.barcode,
                    'image_type': v.image_type,
                    'version_label': v.version_label,
                    'version_folder_ctime': v.folder_ctime,
                    'folder_ctime': dup_ctime,
                    'image_count': len(imgs),
                    'total_file_size': sum(img.file_size for img in imgs),
                })

    # Sort by barcode then image_type
    groups.sort(key=lambda g: (g['barcode'], g['image_type'], g['folder_ctime']))

    barcodes = set(g['barcode'] for g in groups)
    return jsonify({
        'groups': groups,
        'total_duplicate_count': len(groups),
        'total_barcode_count': len(barcodes),
    })


@batch_bp.route('/batch/delete-duplicates', methods=['POST'])
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
        count = _delete_folder_images(
            item['barcode'], item['image_type'], item['folder_ctime'], delete_files
        )
        total_deleted += count
        affected_barcodes.add(item['barcode'])

    session.commit()

    for bc in affected_barcodes:
        update_versions_for_barcode(bc)

    return jsonify({
        'deleted_image_count': total_deleted,
        'deleted_folder_count': len(items),
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
        key = (item['barcode'], item['image_type'], item['folder_ctime'])
        count, _ = stats.get(key, (0, 0))
        threshold = main_threshold if item['image_type'] == 'main' else detail_threshold
        total_versions = len(by_barcode_type.get(key[:2], []))

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
        count = _delete_folder_images(
            item['barcode'], item['image_type'], item['folder_ctime'], delete_files
        )
        total_deleted += count
        affected_barcodes.add(item['barcode'])

    session.commit()

    for bc in affected_barcodes:
        update_versions_for_barcode(bc)

    return jsonify({
        'deleted_image_count': total_deleted,
        'deleted_version_count': len(items),
        'affected_barcodes': list(affected_barcodes),
    })
