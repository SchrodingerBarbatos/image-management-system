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
# Independent last-known-good API token state. Survives config file deletion /
# corruption after a token was once successfully loaded.
_last_good_token: str | None = None
_token_was_enabled: bool = False


def load_config():
    """Load app config from JSON file.

    Returns (config_dict, error_string).
    - Missing file is not an error (first-run open mode).
    - Parse/read failure returns last good config when available, with err set.
    """
    global _cached_config, _last_good_token, _token_was_enabled
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        if not isinstance(cfg, dict):
            raise ValueError('config root must be an object')
        _cached_config = dict(cfg)
        # Only update token state on successful parse of a real file.
        raw = cfg.get('api_token')
        if raw:
            _last_good_token = str(raw)
            _token_was_enabled = True
        else:
            # Explicit empty/missing token after successful read = user disabled auth
            _last_good_token = None
            _token_was_enabled = False
        return cfg, None
    except FileNotFoundError:
        # File gone: if token was ever enabled, keep last good token / fail-closed.
        if _token_was_enabled and _last_good_token:
            base = dict(_cached_config) if _cached_config else {"debug_mode": False}
            base['api_token'] = _last_good_token
            return base, 'config file missing; using last good token'
        if _token_was_enabled:
            return {"debug_mode": False}, 'config file missing after token was enabled'
        return {"debug_mode": False}, None
    except Exception as e:
        _log.error("Failed to load app_config.json: %s", e)
        if _token_was_enabled and _last_good_token:
            base = dict(_cached_config) if _cached_config else {"debug_mode": False}
            base['api_token'] = _last_good_token
            return base, f'config read failed, using last good token: {e}'
        if _cached_config is not None:
            return dict(_cached_config), f'config read failed, using cache: {e}'
        return {"debug_mode": False}, f'config read failed: {e}'


def save_config(cfg):
    """Atomically save app config: temp file → flush/fsync → os.replace."""
    global _cached_config, _last_good_token, _token_was_enabled
    os.makedirs(DATA_DIR, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix='app_config.', suffix='.json', dir=DATA_DIR)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, CONFIG_PATH)
        _cached_config = dict(cfg)
        raw = cfg.get('api_token') if isinstance(cfg, dict) else None
        if raw:
            _last_good_token = str(raw)
            _token_was_enabled = True
        else:
            # Explicit save of empty token = intentional disable
            _last_good_token = None
            _token_was_enabled = False
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def get_api_token():
    """Return (token, fail_closed).

    fail_closed=True → mutating requests must be rejected even without a
    usable token (config broken after token was once enabled).
    """
    cfg, err = load_config()
    token = (cfg or {}).get('api_token') or ''
    if token:
        return str(token), False
    if _token_was_enabled and _last_good_token:
        return str(_last_good_token), False
    if _token_was_enabled:
        # Token was enabled but we no longer have a usable value
        return '', True
    if err and os.path.exists(CONFIG_PATH):
        # File exists but unreadable/corrupt and never had a token → fail closed
        return '', True
    return '', False
