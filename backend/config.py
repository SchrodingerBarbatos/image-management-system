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

# Last successfully loaded config; used as fail-closed fallback when re-read fails
# after a token was previously configured.
_cached_config: dict | None = None
_config_read_error: str | None = None


def load_config():
    """Load app config from JSON file.

    Returns (config_dict, error_string). error_string is set when the file
    exists but cannot be parsed/read (fail-closed for token enforcement).
    Missing file is not an error — returns defaults.
    """
    global _cached_config, _config_read_error
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        if not isinstance(cfg, dict):
            raise ValueError('config root must be an object')
        _cached_config = dict(cfg)
        _config_read_error = None
        return cfg, None
    except FileNotFoundError:
        _config_read_error = None
        return {"debug_mode": False}, None
    except Exception as e:
        _config_read_error = str(e)
        _log.error("Failed to load app_config.json: %s", e)
        if _cached_config is not None:
            return dict(_cached_config), f'config read failed, using cache: {e}'
        return {"debug_mode": False}, f'config read failed: {e}'


def save_config(cfg):
    """Atomically save app config: temp file → flush/fsync → os.replace."""
    global _cached_config, _config_read_error
    os.makedirs(DATA_DIR, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix='app_config.', suffix='.json', dir=DATA_DIR)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, CONFIG_PATH)
        _cached_config = dict(cfg)
        _config_read_error = None
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def get_api_token():
    """Return (token, enforce_fail_closed).

    enforce_fail_closed=True means mutating requests must be rejected even if
    token is empty (config is corrupted after a token was once configured, or
    current read failed with no usable cache).
    """
    cfg, err = load_config()
    token = (cfg or {}).get('api_token') or ''
    if token:
        return str(token), False
    # No token in current/cached config
    if err and _cached_config is not None and (_cached_config.get('api_token') or ''):
        # Should not reach: load_config returns cached with token above
        return str(_cached_config.get('api_token') or ''), False
    if err:
        # Config unreadable and no prior token — still open for first-run, but
        # if a previous successful load had a token, load_config already returned it.
        # When file exists but is corrupt and never successfully cached: fail closed
        # only if the file exists (someone tried to configure).
        if os.path.exists(CONFIG_PATH):
            return '', True
    return '', False
