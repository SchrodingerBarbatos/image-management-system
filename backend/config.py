import os, sys, json, logging, tempfile

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
DB_PATH = os.path.join(DATA_DIR, 'images.db')
THUMBNAIL_DIR = os.path.join(DATA_DIR, 'thumbnails')
UPLOAD_DIR = os.path.join(DATA_DIR, 'uploads')
EXPORT_XLSX_DIR = os.path.join(UPLOAD_DIR, 'xlsx')
EXPORT_ZIP_DIR = os.path.join(UPLOAD_DIR, 'zips')
LOG_DIR = os.path.join(DATA_DIR, 'logs')
CONFIG_PATH = os.path.join(DATA_DIR, 'app_config.json')
# Independent of app_config.json — survives config deletion/corruption without
# persisting the token secret itself.
AUTH_STATE_PATH = os.path.join(DATA_DIR, 'auth_state.json')

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(THUMBNAIL_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(EXPORT_XLSX_DIR, exist_ok=True)
os.makedirs(EXPORT_ZIP_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

THUMBNAIL_SIZE = (200, 200)
THUMBNAIL_QUALITY = 75
ZIP_CLEANUP_HOURS = 24
XLSX_TTL_HOURS = 1

_log = logging.getLogger(__name__)

# Last successfully loaded full config (any successful parse).
_cached_config: dict | None = None
# In-process last-known-good token (never written to auth_state.json).
_last_good_token: str | None = None


def _atomic_write_json(path, payload):
    """temp file → flush/fsync → os.replace."""
    directory = os.path.dirname(path) or '.'
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix='.tmp_', suffix='.json', dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _write_persisted_token_enabled(enabled: bool):
    """Atomically persist whether API token auth was intentionally enabled."""
    try:
        _atomic_write_json(AUTH_STATE_PATH, {'token_enabled': bool(enabled)})
    except Exception as e:
        _log.error("Failed to write auth_state.json: %s", e)
        raise


def _read_persisted_token_enabled():
    """Return True/False/None.

    None = no state file (first run).
    True/False = explicit persisted flag.
    Corrupt/unreadable state file → True (fail-closed, never fail-open).
    """
    if not os.path.exists(AUTH_STATE_PATH):
        return None
    try:
        with open(AUTH_STATE_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            _log.error("auth_state.json root is not an object — fail-closed")
            return True
        return bool(data.get('token_enabled'))
    except Exception as e:
        _log.error("Failed to read auth_state.json: %s — fail-closed", e)
        return True


def load_config():
    """Load app config from JSON file.

    Returns (config_dict, error_string).
    - Missing file is not an error (first-run open mode).
    - Parse/read failure returns last good config when available, with err set.
    - Never writes auth_state.json (only save_config mutates enable flag).
    """
    global _cached_config, _last_good_token
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        if not isinstance(cfg, dict):
            raise ValueError('config root must be an object')
        _cached_config = dict(cfg)
        raw = cfg.get('api_token')
        if raw:
            # Cache token for this process only — do NOT write auth_state here.
            # Enable/disable is intentional via save_config only.
            _last_good_token = str(raw)
        return cfg, None
    except FileNotFoundError:
        if _last_good_token:
            base = dict(_cached_config) if _cached_config else {"debug_mode": False}
            base['api_token'] = _last_good_token
            return base, 'config file missing; using last good token'
        return {"debug_mode": False}, None
    except Exception as e:
        _log.error("Failed to load app_config.json: %s", e)
        if _last_good_token:
            base = dict(_cached_config) if _cached_config else {"debug_mode": False}
            base['api_token'] = _last_good_token
            return base, f'config read failed, using last good token: {e}'
        if _cached_config is not None:
            return dict(_cached_config), f'config read failed, using cache: {e}'
        return {"debug_mode": False}, f'config read failed: {e}'


def save_config(cfg):
    """Atomically save app config and update auth_state for token enable/disable."""
    global _cached_config, _last_good_token
    if not isinstance(cfg, dict):
        raise ValueError('config must be a dict')
    _atomic_write_json(CONFIG_PATH, cfg)
    _cached_config = dict(cfg)
    raw = cfg.get('api_token')
    if raw:
        _last_good_token = str(raw)
        _write_persisted_token_enabled(True)
    else:
        # Explicit save of empty/missing token = intentional disable
        _last_good_token = None
        _write_persisted_token_enabled(False)


def get_api_token():
    """Return (token, fail_closed).

    fail_closed=True → mutating requests must be rejected even without a
    usable token (config broken after token was once enabled).
    """
    cfg, err = load_config()
    token = str((cfg or {}).get('api_token') or '')

    if token:
        return token, False

    if _last_good_token:
        return str(_last_good_token), False

    # No in-memory token — consult persisted enable flag (survives restart)
    persisted = _read_persisted_token_enabled()
    if persisted is True:
        return '', True

    if err and os.path.exists(CONFIG_PATH):
        # File exists but unreadable/corrupt and never had a usable token
        return '', True

    # persisted is False or None (first run / explicitly disabled)
    return '', False
