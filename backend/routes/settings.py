import os
import subprocess
import logging
from flask import Blueprint, request, jsonify
from config import AuthStateError, LOG_DIR, load_config, save_config
from routes._utils import JSONPayloadError, json_payload_error_response, require_json_object

settings_bp = Blueprint('settings', __name__)
_log = logging.getLogger(__name__)

# Reference to the duplicate_version_detector module logger
_dvd_log = logging.getLogger('duplicate_version_detector')
_debug_handler = None
_MIN_API_TOKEN_LENGTH = 16
_MAX_API_TOKEN_LENGTH = 256


def _network_settings_payload(cfg, *, restart_required=False, message=None):
    """Return public network settings without exposing the API token."""
    payload = {
        'lan_mode': cfg.get('lan_mode') is True,
        'api_token_configured': bool(cfg.get('api_token')),
        'restart_required': bool(restart_required),
    }
    if message:
        payload['message'] = message
    return payload


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


@settings_bp.route('/settings/network', methods=['GET'])
def get_network_settings():
    cfg, err = load_config()
    if err:
        _log.error('读取网络设置失败: %s', err)
        return jsonify({'error': '配置读取失败，无法加载网络设置'}), 503
    return jsonify(_network_settings_payload(cfg))


@settings_bp.route('/settings/network', methods=['PUT'])
def set_network_settings():
    try:
        data = require_json_object()
    except JSONPayloadError as e:
        return json_payload_error_response(e)

    if 'lan_mode' not in data or not isinstance(data['lan_mode'], bool):
        return jsonify({'error': 'lan_mode must be a boolean'}), 400

    cfg, err = load_config()
    if err:
        _log.error('保存网络设置前读取配置失败: %s', err)
        return jsonify({'error': '配置读取失败，拒绝覆盖网络设置'}), 503

    old_lan_mode = cfg.get('lan_mode') is True
    if 'api_token' in data:
        raw_token = data['api_token']
        if not isinstance(raw_token, str):
            return jsonify({'error': 'api_token must be a string'}), 400
        token = raw_token.strip()
        if token and len(token) < _MIN_API_TOKEN_LENGTH:
            return jsonify({
                'error': f'API Token 至少需要 {_MIN_API_TOKEN_LENGTH} 个字符',
            }), 400
        if len(token) > _MAX_API_TOKEN_LENGTH or '\r' in token or '\n' in token:
            return jsonify({
                'error': f'API Token 不能超过 {_MAX_API_TOKEN_LENGTH} 个字符且不能包含换行',
            }), 400
        cfg['api_token'] = token

    lan_mode = data['lan_mode']
    cfg['lan_mode'] = lan_mode
    restart_required = old_lan_mode != lan_mode
    try:
        save_config(cfg)
    except AuthStateError:
        _log.exception('无法持久化 API Token 鉴权状态')
        return jsonify({'error': '无法持久化鉴权状态，请检查 data 目录写权限'}), 500
    except Exception:
        _log.exception('保存网络设置失败')
        return jsonify({'error': '保存网络设置失败'}), 500

    message = '网络设置已保存'
    if restart_required:
        message += '，请重启应用后生效'
    return jsonify(_network_settings_payload(
        cfg,
        restart_required=restart_required,
        message=message,
    ))


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
