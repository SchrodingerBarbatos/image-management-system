"""Shared route utilities."""

import os
import logging
from flask import request, jsonify

_log = logging.getLogger(__name__)


def parse_pagination(default_page_size=50, max_page_size=500):
    """Parse and validate page/page_size query parameters.

    Returns (page, page_size) on success.

    Raises:
        ValueError with a user-facing message on validation failure.
    """
    raw_page = request.args.get('page', '1')
    raw_page_size = request.args.get('page_size', str(default_page_size))

    try:
        page = int(raw_page)
    except (ValueError, TypeError):
        raise ValueError('page 必须为正整数')

    try:
        page_size = int(raw_page_size)
    except (ValueError, TypeError):
        raise ValueError('page_size 必须为正整数')

    if page < 1:
        raise ValueError('page 必须 >= 1')
    if page_size < 1 or page_size > max_page_size:
        raise ValueError(f'page_size 必须在 1~{max_page_size} 之间')

    return page, page_size


def safe_remove_image_file(img, sess):
    """Remove an image file from disk only if its path is inside its scan root.

    Validates that the real path of img.file_path is under the scan root's
    real path before deleting.  This prevents accidental deletion of files
    outside the scan root due to DB corruption, symlink tricks, or path
    traversal.

    Args:
        img: Image ORM object with .file_path and .scan_root_id.
        sess: SQLAlchemy session to look up the ScanRoot.

    Returns:
        (True, None) on success (file removed or already absent).
        (False, reason_string) if deletion was refused or failed.
    """
    from models import ScanRoot

    root = sess.get(ScanRoot, img.scan_root_id)
    if not root:
        reason = f'找不到扫描目录 (scan_root_id={img.scan_root_id})'
        _log.warning("safe_remove: %s, refusing to delete %s", reason, img.file_path)
        return False, reason

    real_file = os.path.realpath(img.file_path)
    real_root = os.path.realpath(root.path)
    try:
        if os.path.commonpath([real_file, real_root]) != real_root:
            reason = '文件路径不在所属扫描目录下'
            _log.warning("safe_remove: %s — file=%s root=%s", reason, img.file_path, root.path)
            return False, reason
    except ValueError:
        # Different drives on Windows → commonpath raises ValueError
        reason = '文件路径与扫描目录不在同一驱动器'
        _log.warning("safe_remove: %s — file=%s root=%s", reason, img.file_path, root.path)
        return False, reason

    try:
        os.remove(img.file_path)
        return True, None
    except FileNotFoundError:
        return True, None  # already gone — not an error
    except OSError as e:
        reason = f'系统删除失败: {e}'
        _log.warning("safe_remove: %s — %s", reason, img.file_path)
        return False, reason
