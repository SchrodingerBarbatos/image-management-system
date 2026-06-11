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
        True if the file was successfully removed (or was already absent).
        False if deletion was refused (path outside root) or failed.
    """
    from models import ScanRoot

    root = sess.get(ScanRoot, img.scan_root_id)
    if not root:
        _log.warning("safe_remove: no ScanRoot for id=%s, refusing to delete %s",
                     img.scan_root_id, img.file_path)
        return False

    real_file = os.path.realpath(img.file_path)
    real_root = os.path.realpath(root.path)
    try:
        if os.path.commonpath([real_file, real_root]) != real_root:
            _log.warning("safe_remove: path %s is outside root %s, refusing",
                         img.file_path, root.path)
            return False
    except ValueError:
        # Different drives on Windows → commonpath raises ValueError
        _log.warning("safe_remove: path %s and root %s on different drives, refusing",
                     img.file_path, root.path)
        return False

    try:
        os.remove(img.file_path)
        return True
    except FileNotFoundError:
        return True  # already gone — not an error
    except OSError as e:
        _log.warning("safe_remove: failed to delete %s: %s", img.file_path, e)
        return False
