"""Shared route utilities."""

import os
import logging
from flask import request, jsonify

_log = logging.getLogger(__name__)


class JSONPayloadError(ValueError):
    """A client-side JSON payload error with an HTTP status code."""

    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


def _require_json(expected_type, expected_name):
    """Parse a JSON request body and require the requested top-level type."""
    if not request.is_json:
        raise JSONPayloadError('请求必须使用 application/json Content-Type', 415)
    data = request.get_json(silent=True)
    if data is None:
        raise JSONPayloadError('请求体必须是有效 JSON', 400)
    if not isinstance(data, expected_type):
        raise JSONPayloadError(f'请求体必须是 JSON {expected_name}', 400)
    return data


def require_json_object():
    """Return a JSON object or raise JSONPayloadError for a malformed body."""
    return _require_json(dict, '对象')


def require_json_array():
    """Return a JSON array or raise JSONPayloadError for a malformed body."""
    return _require_json(list, '数组')


def json_payload_error_response(error):
    """Convert JSONPayloadError to the standard API error response."""
    return jsonify({'error': str(error)}), error.status_code


def require_positive_int_list(value, field='ids', max_items=10000):
    """Validate and deduplicate a non-empty list of positive integer IDs."""
    if not isinstance(value, list) or not value:
        raise ValueError(f'{field} 必须为非空整数数组')
    if len(value) > max_items:
        raise ValueError(f'{field} 数量不能超过 {max_items}')
    result = []
    seen = set()
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or item < 1:
            raise ValueError(f'{field} 必须只包含正整数')
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def normalize_scan_root_path(path):
    """Return a stable absolute real-path key for a scan root.

    ``normcase`` handles Windows drive-letter/case aliases, while
    ``realpath`` makes symlink aliases compare consistently.  ``normpath``
    preserves filesystem roots without stripping their required separator.
    """
    if not isinstance(path, str) or not path.strip():
        raise ValueError('path 必须为非空字符串')
    expanded = os.path.expanduser(path.strip())
    resolved = os.path.realpath(os.path.abspath(expanded))
    return os.path.normcase(os.path.normpath(resolved))


def path_contains(parent_key, child_key):
    """Return whether child_key is equal to or below parent_key."""
    try:
        return os.path.commonpath([parent_key, child_key]) == parent_key
    except ValueError:
        return False


def exportable_image_query(sess):
    """Return the shared active+confirmed+enabled export query."""
    from models import Image, ScanRoot

    return sess.query(Image).join(
        ScanRoot, Image.scan_root_id == ScanRoot.id
    ).filter(
        Image.status == 'active',
        Image.confirmed.is_(True),
        ScanRoot.enabled.is_(True),
    )


def is_path_under_root(file_path, root_path):
    """Return (True, None) if realpath(file_path) is under realpath(root_path).

    Used by safe_remove_* and serve_file for consistent path confinement.
    """
    real_file = os.path.realpath(file_path)
    real_root = os.path.realpath(root_path)
    try:
        if os.path.commonpath([real_file, real_root]) != real_root:
            return False, '文件路径不在所属扫描目录下'
    except ValueError:
        # Different drives on Windows → commonpath raises ValueError
        return False, '文件路径与扫描目录不在同一驱动器'
    return True, None


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

    ok, reason = is_path_under_root(img.file_path, root.path)
    if not ok:
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
