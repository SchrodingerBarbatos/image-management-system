import os
import subprocess
import logging
from flask import Blueprint, request, jsonify
from config import LOG_DIR, load_config, save_config
from routes._utils import JSONPayloadError, json_payload_error_response, require_json_object

settings_bp = Blueprint('settings', __name__)

# Reference to the duplicate_version_detector module logger
_dvd_log = logging.getLogger('duplicate_version_detector')
_debug_handler = None


def _ensure_debug_handler():
    """Attach a FileHandler for duplicate_version_debug.log if not already present."""
    global _debug_handler
    if _debug_handler is not None:
        return
    debug_log_path = os.path.join(LOG_DIR, 'duplicate_version_debug.log')
    _debug_handler = logging.FileHandler(debug_log_path, encoding='utf-8')
    _debug_handler.setLevel(logging.DEBUG)
    _debug_handler.setFormatter(logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
    ))
    _dvd_log.addHandler(_debug_handler)
    _dvd_log.setLevel(logging.DEBUG)


def _remove_debug_handler():
    """Remove the debug FileHandler if present."""
    global _debug_handler
    if _debug_handler is None:
        return
    handler_to_remove = _debug_handler
    _dvd_log.removeHandler(handler_to_remove)
    handler_to_remove.close()
    _debug_handler = None
    # Reset logger level — only remove DEBUG if no other FileHandlers remain
    if not any(isinstance(h, logging.FileHandler) and h is not handler_to_remove
               for h in _dvd_log.handlers):
        _dvd_log.setLevel(logging.INFO)


@settings_bp.route('/settings/debug-mode', methods=['GET'])
def get_debug_mode():
    cfg, _ = load_config()
    return jsonify({"debug_mode": cfg.get("debug_mode", False)})


@settings_bp.route('/settings/debug-mode', methods=['PUT'])
def set_debug_mode():
    try:
        data = require_json_object()
    except JSONPayloadError as e:
        return json_payload_error_response(e)
    debug_mode = data.get('debug_mode', False)
    if not isinstance(debug_mode, bool):
        return jsonify({'error': 'debug_mode must be a boolean'}), 400

    cfg, _ = load_config()
    cfg['debug_mode'] = debug_mode
    save_config(cfg)

    if debug_mode:
        _ensure_debug_handler()
        return jsonify({"debug_mode": True, "message": "调试模式已开启"})
    else:
        _remove_debug_handler()
        return jsonify({"debug_mode": False, "message": "调试模式已关闭"})


@settings_bp.route('/settings/log-dir', methods=['GET'])
def get_log_dir():
    files = []
    if os.path.isdir(LOG_DIR):
        for name in sorted(os.listdir(LOG_DIR)):
            if not name.endswith('.log'):
                continue
            path = os.path.join(LOG_DIR, name)
            try:
                stat = os.stat(path)
                files.append({
                    "name": name,
                    "size": stat.st_size,
                    "modified": stat.st_mtime,
                })
            except OSError:
                pass
    return jsonify({"log_dir": LOG_DIR, "files": files})


@settings_bp.route('/settings/clear-logs', methods=['POST'])
def clear_logs():
    cleared = 0
    failed = []
    if os.path.isdir(LOG_DIR):
        for name in os.listdir(LOG_DIR):
            if not name.endswith('.log'):
                continue
            path = os.path.join(LOG_DIR, name)
            try:
                # Truncate rather than delete to avoid issues with open file handles
                with open(path, 'w', encoding='utf-8'):
                    pass
                cleared += 1
            except OSError:
                failed.append(name)
    return jsonify({"cleared_count": cleared, "failed": failed})


@settings_bp.route('/settings/open-log-dir', methods=['POST'])
def open_log_dir():
    os.makedirs(LOG_DIR, exist_ok=True)
    try:
        subprocess.Popen(['explorer', LOG_DIR])
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
